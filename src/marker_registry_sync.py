"""Per-chain marker registry synchronization through Yandex Disk."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.app_paths import atomic_write_text
from src.marker_identity import BEAST_ID_RE, read_marker_csv_bytes
from src.marker_registry import (
    MARKER_REGISTRY_FILE,
    MARKER_VERSION_ROOT,
    MarkerIdentityPlan,
    chain_key_from_filename,
    empty_chain,
    load_registry,
    migrate_legacy_registry,
    prepare_identity_plan,
    save_registry,
    utc_now,
)
from src.report_filename import parse_report_filename
from src.yandex_disk_client import YandexDiskClient, YandexDiskError


logger = logging.getLogger(__name__)

REMOTE_ROOT = "/Beast Auto Reporter/marker_registry_v3"
SERIES_UID_PROPERTY = "beast_series_uid"
LOCK_TTL_SECONDS = 120
LEGACY_REMOTE_REGISTRY = "/Beast Auto Reporter/sync/marker_identities.json"


class MarkerRegistryBusyError(RuntimeError):
    pass


class MarkerRegistryConflictError(RuntimeError):
    def __init__(self, chain_key: str, details: list[dict]):
        self.chain_key = chain_key
        self.details = details
        ids = ", ".join(item["marker_id"] for item in details)
        super().__init__(f"Конфликт подтверждённых ID в цепочке: {ids}")


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _download_json(client: YandexDiskClient, path: str) -> dict:
    try:
        value = json.loads(client.download_bytes(path).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except YandexDiskError as exc:
        if exc.status_code == 404:
            return {}
        raise
    except (ValueError, UnicodeDecodeError):
        logger.warning("Повреждён JSON реестра маркеров на Диске: %s", path)
        return {}


def bootstrap_legacy_remote_registry(
    client: YandexDiskClient,
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> None:
    """Adopt the existing synced v2 registry before the first v3 chain."""
    if load_registry(registry_path).get("chains"):
        return
    remote = _download_json(client, LEGACY_REMOTE_REGISTRY)
    if not isinstance(remote.get("projects"), dict):
        return
    legacy_path = registry_path.with_name("marker_identities.json")
    try:
        local = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        local = {}
    projects = {}
    if isinstance(local, dict) and isinstance(local.get("projects"), dict):
        projects.update(local["projects"])
    # Uploaded history is authoritative during migration.
    projects.update(remote["projects"])
    payload = {"version": 2, "projects": projects}
    atomic_write_text(legacy_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    migrate_legacy_registry(registry_path, legacy_path)


def _mkdirs(client: YandexDiskClient, path: str) -> None:
    current = ""
    for part in path.strip("/").split("/"):
        current += f"/{part}"
        client.mkdir(current)


def _chain_parts(chain: dict) -> tuple[str, str, str, str, str]:
    key_parts = str(chain.get("chain_key", "")).split("|")
    if len(key_parts) >= 5:
        return tuple(key_parts[:5])
    digest = hashlib.sha256(str(chain.get("chain_key", "unknown")).encode("utf-8")).hexdigest()[:20]
    return digest, "unknown", "unknown", "unknown", "unknown"


def remote_chain_dir(chain: dict) -> str:
    series_uid, season, episode, report_type, language = _chain_parts(chain)
    return f"{REMOTE_ROOT}/{series_uid}/{season}/{episode}/{report_type}/{language}"


def _history_key(snapshot: dict) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_marker_entry(local: dict, remote: dict) -> dict:
    result = deepcopy(remote)
    histories = {}
    for snapshot in list(remote.get("history", [])) + list(local.get("history", [])):
        if isinstance(snapshot, dict):
            histories[_history_key(snapshot)] = deepcopy(snapshot)
    result["history"] = list(histories.values())[-100:]
    local_newer = str(local.get("updated_at", "")) >= str(remote.get("updated_at", ""))
    winner = local if local_newer else remote
    for key in (
        "status", "confirmed", "first_seen_at", "last_seen_at", "updated_at",
        "current", "deleted_at", "restored_at", "restored_count",
    ):
        if key in winner:
            result[key] = deepcopy(winner[key])
    result["first_seen_at"] = min(
        value for value in (local.get("first_seen_at"), remote.get("first_seen_at")) if value
    ) if local.get("first_seen_at") or remote.get("first_seen_at") else ""
    result["restored_count"] = max(
        int(local.get("restored_count", 0) or 0), int(remote.get("restored_count", 0) or 0)
    )
    result["confirmed"] = bool(local.get("confirmed", True) or remote.get("confirmed", True))
    return result


def merge_chain_states(local: dict, remote: dict) -> tuple[dict, list[str], dict[str, str]]:
    """Merge one chain and renumber only unconfirmed local collisions."""
    if not remote:
        return deepcopy(local), [], {}
    if not local:
        return deepcopy(remote), [], {}
    result = deepcopy(remote)
    result.setdefault("markers", {})
    conflicts = []
    renumbered: dict[str, str] = {}
    used = set(result["markers"])
    next_number = max(
        int(local.get("next_id", 1) or 1),
        int(remote.get("next_id", 1) or 1),
        int(local.get("max_issued", 0) or 0) + 1,
        int(remote.get("max_issued", 0) or 0) + 1,
    )

    for marker_id, local_entry in local.get("markers", {}).items():
        remote_entry = result["markers"].get(marker_id)
        if remote_entry is None:
            result["markers"][marker_id] = deepcopy(local_entry)
            used.add(marker_id)
            continue
        if local_entry.get("uid") == remote_entry.get("uid"):
            result["markers"][marker_id] = _merge_marker_entry(local_entry, remote_entry)
            continue
        if not local_entry.get("confirmed", True) or local_entry.get("status") == "pending":
            while f"M{next_number}" in used:
                next_number += 1
            new_id = f"M{next_number}"
            next_number += 1
            moved = deepcopy(local_entry)
            moved["id"] = new_id
            moved["confirmed"] = False
            moved["status"] = "pending"
            if isinstance(moved.get("current"), dict):
                moved["current"]["id"] = new_id
            for snapshot in moved.get("history", []):
                if isinstance(snapshot, dict):
                    snapshot["id"] = new_id
            result["markers"][new_id] = moved
            used.add(new_id)
            renumbered[marker_id] = new_id
        else:
            conflicts.append(marker_id)

    result["max_issued"] = max(
        int(local.get("max_issued", 0) or 0),
        int(remote.get("max_issued", 0) or 0),
        max((int(value[1:]) for value in used if BEAST_ID_RE.fullmatch(value)), default=0),
    )
    result["next_id"] = max(next_number, result["max_issued"] + 1)
    result["revision"] = max(int(local.get("revision", 0) or 0), int(remote.get("revision", 0) or 0)) + 1
    versions = {}
    for item in list(remote.get("versions", [])) + list(local.get("versions", [])):
        if isinstance(item, dict):
            versions[item.get("version_uid") or _history_key(item)] = deepcopy(item)
    result["versions"] = sorted(versions.values(), key=lambda item: item.get("created_at", ""))[-500:]
    result["updated_at"] = utc_now()
    result["health"] = "conflict" if conflicts else ("offline" if renumbered else "ok")
    return result, conflicts, renumbered


def ensure_series_uid(client: YandexDiskClient, series_path: str) -> str:
    meta = client.get_meta(series_path)
    properties = meta.get("custom_properties") or {}
    series_uid = properties.get(SERIES_UID_PROPERTY)
    if series_uid:
        return str(series_uid)
    series_uid = str(uuid.uuid4())
    client.set_custom_properties(series_path, {SERIES_UID_PROPERTY: series_uid})
    return series_uid


def _upload_bytes(client: YandexDiskClient, data: bytes, path: str, *, overwrite: bool) -> None:
    try:
        client.upload_bytes(data, path, overwrite=overwrite)
    except TypeError:
        # Compatibility with test doubles and clients from an older build.
        client.upload_bytes(data, path)


@contextmanager
def chain_lock(client: YandexDiskClient, chain: dict):
    folder = remote_chain_dir(chain)
    lock_path = f"{folder}/allocation.lock"
    _mkdirs(client, folder)
    payload = {"owner": str(uuid.uuid4()), "created_at": utc_now(), "epoch": time.time()}
    try:
        _upload_bytes(client, _json_bytes(payload), lock_path, overwrite=False)
    except YandexDiskError as exc:
        if exc.status_code != 409:
            raise
        existing = _download_json(client, lock_path)
        if time.time() - float(existing.get("epoch", time.time())) <= LOCK_TTL_SECONDS:
            raise MarkerRegistryBusyError("Реестр ID этой версии сейчас обновляется на другом компьютере")
        client.delete(lock_path)
        _upload_bytes(client, _json_bytes(payload), lock_path, overwrite=False)
    try:
        yield
    finally:
        try:
            client.delete(lock_path)
        except YandexDiskError:
            logger.warning("Не удалось удалить lock реестра маркеров: %s", lock_path)


def sync_chain(
    client: YandexDiskClient,
    chain_key: str,
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
    use_lock: bool = True,
) -> tuple[dict, list[str], dict[str, str]]:
    registry = migrate_legacy_registry(registry_path)
    local = deepcopy(registry.get("chains", {}).get(chain_key) or empty_chain(chain_key))
    folder = remote_chain_dir(local)
    _mkdirs(client, folder)

    def _run():
        remote = _download_json(client, f"{folder}/state.json")
        merged, conflicts, renumbered = merge_chain_states(local, remote)
        if conflicts:
            details = [
                {
                    "marker_id": marker_id,
                    "local": deepcopy(local.get("markers", {}).get(marker_id, {})),
                    "remote": deepcopy(remote.get("markers", {}).get(marker_id, {})),
                }
                for marker_id in conflicts
            ]
            raise MarkerRegistryConflictError(chain_key, details)
        _upload_bytes(client, _json_bytes(merged), f"{folder}/state.json", overwrite=True)
        registry.setdefault("chains", {})[chain_key] = merged
        save_registry(registry, registry_path)
        return merged, conflicts, renumbered

    if use_lock:
        with chain_lock(client, local):
            return _run()
    return _run()


def resolve_chain_conflicts(
    client: YandexDiskClient,
    conflict: MarkerRegistryConflictError,
    choices: dict[str, str],
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> dict[str, str]:
    """Resolve confirmed collisions as same identity or renumber local history."""
    registry = load_registry(registry_path)
    local = deepcopy(registry.get("chains", {}).get(conflict.chain_key) or empty_chain(conflict.chain_key))
    folder = remote_chain_dir(local)
    remote = _download_json(client, f"{folder}/state.json")
    renumbered = {}
    used = set(local.get("markers", {})) | set(remote.get("markers", {}))
    next_number = max(
        int(local.get("next_id", 1) or 1), int(remote.get("next_id", 1) or 1),
        int(local.get("max_issued", 0) or 0) + 1, int(remote.get("max_issued", 0) or 0) + 1,
    )
    for detail in conflict.details:
        marker_id = detail["marker_id"]
        action = choices.get(marker_id)
        if action == "same":
            local_entry = local["markers"][marker_id]
            remote_entry = remote["markers"][marker_id]
            local_entry["uid"] = remote_entry.get("uid")
            local["markers"][marker_id] = _merge_marker_entry(local_entry, remote_entry)
        elif action == "renumber_local":
            while f"M{next_number}" in used:
                next_number += 1
            new_id = f"M{next_number}"
            next_number += 1
            moved = local["markers"].pop(marker_id)
            moved["id"] = new_id
            moved["status"] = "pending"
            moved["confirmed"] = False
            if isinstance(moved.get("current"), dict):
                moved["current"]["id"] = new_id
            for snapshot in moved.get("history", []):
                if isinstance(snapshot, dict):
                    snapshot["id"] = new_id
            local["markers"][new_id] = moved
            used.add(new_id)
            renumbered[marker_id] = new_id
        else:
            raise ValueError(f"Не выбрано решение для {marker_id}")
    local["next_id"] = max(next_number, int(local.get("next_id", 1) or 1))
    local["max_issued"] = max(int(local.get("max_issued", 0) or 0), next_number - 1)
    local["updated_at"] = utc_now()
    registry.setdefault("chains", {})[conflict.chain_key] = local
    save_registry(registry, registry_path)
    sync_chain(client, conflict.chain_key, registry_path=registry_path)
    return renumbered


def _episode_path_for_series(series_path: str, episode: int) -> str:
    return f"{series_path}/e{episode:02d}"


def _find_report_csv(client: YandexDiskClient, report_path: str) -> str | None:
    try:
        meta = client.get_meta(report_path)
    except YandexDiskError as exc:
        if exc.status_code == 404:
            return None
        raise
    if meta.get("type") == "file" and report_path.lower().endswith(".csv"):
        return report_path
    if meta.get("type") != "dir":
        return None
    candidates = [
        item for item in client.listdir(report_path)
        if item.get("type") == "file" and str(item.get("name", "")).lower().endswith(".csv")
    ]
    if not candidates:
        return None
    preferred = next((item for item in candidates if str(item.get("name", "")).lower().startswith("отчет_")), None)
    item = preferred or candidates[0]
    return item.get("path") or f"{report_path}/{item.get('name')}"


def recover_chain_from_reports(
    client: YandexDiskClient,
    episode_path: str,
    chain: dict,
) -> dict:
    """Bootstrap/tighten history from authoritative uploaded CSV copies."""
    try:
        items = client.listdir(episode_path)
    except YandexDiskError as exc:
        if exc.status_code == 404:
            return chain
        raise
    report_type = chain.get("report_type", "main")
    language = chain.get("language", "")
    versions = []
    for item in items:
        name = item.get("name", "")
        parsed = parse_report_filename(name)
        if parsed is None:
            # Never mix an unclassified folder into a typed/language chain.
            # It remains available for manual recovery instead.
            continue
        item_type = (parsed.variant or "main").strip().lower()
        item_type = "me" if item_type in {"mne", "m&e", "me", "me_ours"} else item_type
        item_language = {"ru": "rus", "en": "eng"}.get(
            (parsed.lang or "").strip().lower(), (parsed.lang or "").strip().lower()
        )
        if item_type != report_type or item_language != language:
            continue
        path = item.get("path") or f"{episode_path}/{name}"
        csv_path = _find_report_csv(client, path)
        if csv_path:
            versions.append((item.get("modified", ""), name, csv_path, path))
    versions.sort()
    recovered = deepcopy(chain)
    recovered.setdefault("markers", {})
    active_last = set()
    for modified, name, csv_path, report_path in versions:
        try:
            markers = read_marker_csv_bytes(client.download_bytes(csv_path))
        except (YandexDiskError, ValueError, UnicodeDecodeError) as exc:
            logger.warning("Не удалось восстановить маркеры из %s: %s", csv_path, exc)
            continue
        present = set()
        for marker in markers:
            marker_id = marker.get("id", "")
            if not BEAST_ID_RE.fullmatch(marker_id):
                continue
            present.add(marker_id)
            entry = recovered["markers"].setdefault(marker_id, {
                "uid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"beast:{chain.get('chain_key')}:{marker_id}")),
                "id": marker_id,
                "first_seen_at": modified or utc_now(),
                "history": [],
                "restored_count": 0,
            })
            if entry.get("status") == "deleted":
                entry["restored_count"] = int(entry.get("restored_count", 0) or 0) + 1
            if marker not in entry.get("history", []):
                entry.setdefault("history", []).append(deepcopy(marker))
            entry.update({
                "status": "active",
                "confirmed": True,
                "current": deepcopy(marker),
                "last_seen_at": modified or utc_now(),
                "updated_at": modified or utc_now(),
            })
        for marker_id in active_last - present:
            if marker_id in recovered["markers"]:
                recovered["markers"][marker_id]["status"] = "deleted"
        active_last = present
        digest = hashlib.sha256(client.download_bytes(csv_path)).hexdigest()
        version_uid = "recovered-" + hashlib.sha256(
            f"{report_path}|{modified}|{digest}".encode("utf-8")
        ).hexdigest()[:20]
        if not any(item.get("version_uid") == version_uid for item in recovered.get("versions", [])):
            recovered.setdefault("versions", []).append({
                "version_uid": version_uid,
                "created_at": modified or utc_now(),
                "source_name": name,
                "source_digest": digest,
                "status": "confirmed",
                "remote_report_path": report_path,
                "recovered": True,
            })
    highest = max((int(value[1:]) for value in recovered["markers"] if BEAST_ID_RE.fullmatch(value)), default=0)
    recovered["max_issued"] = max(int(recovered.get("max_issued", 0) or 0), highest)
    recovered["next_id"] = max(int(recovered.get("next_id", 1) or 1), highest + 1)
    return recovered


def prepare_identity_plan_with_disk(
    client: YandexDiskClient,
    source_path: str | Path,
    *,
    roots: list[str],
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> MarkerIdentityPlan:
    """Synchronize the exact chain, recover report CSV history, then plan."""
    source = Path(source_path)
    bootstrap_legacy_remote_registry(client, registry_path)
    parsed = parse_report_filename(source.name)
    if parsed is None:
        # Non-standard names still get a dedicated remote chain, but cannot
        # be tied to a series folder without user input.
        key = chain_key_from_filename(source.name)
        sync_chain(client, key, registry_path=registry_path)
        return prepare_identity_plan(source, chain_key=key, registry_path=registry_path)

    from src.report_uploader import find_series_folder

    series_path = find_series_folder(client, parsed.series, roots=roots)
    registry = migrate_legacy_registry(registry_path)
    if series_path is None:
        key = chain_key_from_filename(source.name)
    else:
        series_uid = ensure_series_uid(client, series_path)
        key = chain_key_from_filename(source.name, series_uid=series_uid)
        registry.setdefault("aliases", {})[parsed.series.strip().lower()] = series_uid
        legacy_key = chain_key_from_filename(source.name)
        if key not in registry.get("chains", {}) and legacy_key in registry.get("chains", {}):
            moved = registry["chains"].pop(legacy_key)
            moved["chain_key"] = key
            moved["series_uid"] = series_uid
            registry["chains"][key] = moved
        save_registry(registry, registry_path)

    # Adopt the remote UID mapping first.  Report CSV recovery then enriches
    # those entries instead of inventing different UIDs for the same M-ID.
    sync_chain(client, key, registry_path=registry_path)
    registry = load_registry(registry_path)
    chain = registry.get("chains", {}).get(key) or empty_chain(key, source.name)
    if series_path is not None:
        chain = recover_chain_from_reports(client, _episode_path_for_series(series_path, parsed.episode), chain)
        registry.setdefault("chains", {})[key] = chain
        save_registry(registry, registry_path)
    sync_chain(client, key, registry_path=registry_path)
    return prepare_identity_plan(source, chain_key=key, registry_path=registry_path)


def prepare_identity_plan_offline(
    source_path: str | Path,
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
    warning: str = "",
) -> MarkerIdentityPlan:
    source = Path(source_path)
    registry = migrate_legacy_registry(registry_path)
    parsed = parse_report_filename(source.name)
    key = None
    if parsed is not None:
        series_uid = registry.get("aliases", {}).get(parsed.series.strip().lower())
        key = chain_key_from_filename(source.name, series_uid=series_uid)
    return prepare_identity_plan(
        source, chain_key=key, offline=True, warning=warning, registry_path=registry_path
    )


def sync_all_marker_chains(
    client: YandexDiskClient,
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> tuple[bool, list[str]]:
    registry = migrate_legacy_registry(registry_path)
    changed = False
    conflicts = []
    for key in list(registry.get("chains", {})):
        before = registry.get("chains", {}).get(key)
        try:
            merged, chain_conflicts, _renumbered = sync_chain(client, key, registry_path=registry_path)
        except MarkerRegistryConflictError as exc:
            conflicts.extend(f"{key}:{item['marker_id']}" for item in exc.details)
            continue
        changed = changed or merged != before
        conflicts.extend(f"{key}:{marker_id}" for marker_id in chain_conflicts)
    return changed, conflicts


def publish_local_chain_version(
    client: YandexDiskClient,
    chain_key: str,
    remote_report_path: str,
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> None:
    registry = load_registry(registry_path)
    chain = registry.get("chains", {}).get(chain_key)
    if not chain:
        return
    folder = remote_chain_dir(chain)
    with chain_lock(client, chain):
        remote = _download_json(client, f"{folder}/state.json")
        merged, conflicts, renumbered = merge_chain_states(chain, remote)
        if conflicts:
            raise ValueError("Конфликт подтверждённых ID: " + ", ".join(conflicts))
        if renumbered:
            # Reconciliation must happen before the report folder is uploaded;
            # reaching this branch indicates another writer slipped in after
            # preflight, so retry the whole upload with rewritten artifacts.
            raise ValueError("ID изменились во время загрузки; повторите отправку отчёта")
        now = utc_now()
        for entry in merged.get("markers", {}).values():
            if entry.get("status") == "pending":
                entry["status"] = "active"
            entry["confirmed"] = True
        if merged.get("versions"):
            merged["versions"][-1]["status"] = "confirmed"
            merged["versions"][-1]["remote_report_path"] = remote_report_path
        merged["health"] = "ok"
        merged["updated_at"] = now
        merged["revision"] = int(merged.get("revision", 0) or 0) + 1
        _mkdirs(client, f"{folder}/versions")
        if merged.get("versions"):
            version = merged["versions"][-1]
            local_version_path = (
                MARKER_VERSION_ROOT
                / hashlib.sha256(chain_key.encode("utf-8")).hexdigest()[:20]
                / f"{version.get('version_uid')}.json"
            )
            if local_version_path.exists():
                version_payload = local_version_path.read_bytes()
            else:
                version_payload = _json_bytes({
                    "chain_key": chain_key,
                    "remote_report_path": remote_report_path,
                    **version,
                    "markers": [
                        deepcopy(entry.get("current", {}))
                        for entry in merged.get("markers", {}).values()
                        if entry.get("status") in {"active", "restored", "pending"}
                    ],
                })
            try:
                _upload_bytes(
                    client,
                    version_payload,
                    f"{folder}/versions/{version.get('version_uid', uuid.uuid4().hex)}.json",
                    overwrite=False,
                )
            except YandexDiskError as exc:
                if exc.status_code != 409:
                    raise
        _upload_bytes(client, _json_bytes(merged), f"{folder}/state.json", overwrite=True)
        registry["chains"][chain_key] = merged
        save_registry(registry, registry_path)


def _local_marker_csv(local_folder: Path) -> Path | None:
    candidates = sorted(local_folder.glob("*.csv"))
    return candidates[0] if candidates else None


def _rewrite_csv_ids(path: Path, mapping: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8-sig")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in first_line else (";" if ";" in first_line else ",")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return
    id_index = next((index for index, value in enumerate(rows[0]) if value.strip().casefold() == "id"), None)
    if id_index is None:
        return
    for row in rows[1:]:
        if id_index < len(row) and row[id_index].strip() in mapping:
            row[id_index] = mapping[row[id_index].strip()]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerows(rows)


def _rewrite_docx_ids(local_folder: Path, mapping: dict[str, str]) -> None:
    if not mapping:
        return
    try:
        from docx import Document
    except ImportError:
        return
    for path in local_folder.glob("*.docx"):
        document = Document(str(path))
        changed = False
        for table in document.tables:
            if not table.rows:
                continue
            header_row = None
            id_column = None
            for row_index, row in enumerate(table.rows[:4]):
                for column, cell in enumerate(row.cells):
                    if cell.text.strip().casefold() == "id":
                        header_row = row_index
                        id_column = column
                        break
                if id_column is not None:
                    break
            if id_column is None:
                continue
            for row in table.rows[(header_row or 0) + 1:]:
                if id_column >= len(row.cells):
                    continue
                value = row.cells[id_column].text.strip()
                if value in mapping:
                    row.cells[id_column].text = mapping[value]
                    changed = True
        if changed:
            document.save(str(path))


def reconcile_report_folder_before_upload(
    client: YandexDiskClient,
    local_folder: str | Path,
    episode_path: str,
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> tuple[str | None, dict[str, str]]:
    """Confirm pending IDs against the remote high-water mark before upload."""
    local_folder = Path(local_folder)
    csv_path = _local_marker_csv(local_folder)
    if csv_path is None:
        return None, {}
    parsed = parse_report_filename(csv_path.name)
    if parsed is None:
        key = chain_key_from_filename(csv_path.name)
    else:
        series_path = episode_path.rsplit("/", 1)[0]
        series_uid = ensure_series_uid(client, series_path)
        key = chain_key_from_filename(csv_path.name, series_uid=series_uid)
        registry = migrate_legacy_registry(registry_path)
        registry.setdefault("aliases", {})[parsed.series.strip().lower()] = series_uid
        name_key = chain_key_from_filename(csv_path.name)
        if key not in registry.get("chains", {}):
            source_key = next(
                (
                    candidate_key for candidate_key, chain in registry.get("chains", {}).items()
                    if chain.get("source_name") == csv_path.name
                ),
                name_key if name_key in registry.get("chains", {}) else None,
            )
            if source_key:
                moved = registry["chains"].pop(source_key)
                moved["chain_key"] = key
                moved["series_uid"] = series_uid
                registry["chains"][key] = moved
        save_registry(registry, registry_path)

    merged, conflicts, renumbered = sync_chain(client, key, registry_path=registry_path)
    if conflicts:
        raise ValueError(
            "Обнаружен конфликт подтверждённых ID маркеров: " + ", ".join(conflicts)
        )
    if renumbered:
        _rewrite_csv_ids(csv_path, renumbered)
        _rewrite_docx_ids(local_folder, renumbered)
        registry = load_registry(registry_path)
        chain = registry.get("chains", {}).get(key, merged)
        chain["health"] = "offline"
        chain["updated_at"] = utc_now()
        registry.setdefault("chains", {})[key] = chain
        save_registry(registry, registry_path)
    return key, renumbered
