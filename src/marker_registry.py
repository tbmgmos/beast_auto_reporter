"""Versioned, permanent marker identity registry.

The legacy :mod:`src.marker_identity` registry stores only the latest marker
list.  This module keeps a complete per-chain history so an ID can be restored
after any number of versions in which its marker was absent.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.app_paths import CONFIG_DIR, atomic_write_text
from src.marker_identity import (
    BEAST_ID_RE,
    MARKER_IDENTITIES_FILE,
    _fingerprint,
    _global_time_shift,
    _header_index,
    _id_number,
    _marker_record,
    _match_score,
    _read_csv,
    marker_list_key,
)
from src.report_filename import parse_report_filename


REGISTRY_VERSION = 3
MARKER_REGISTRY_FILE = CONFIG_DIR / "marker_registry_v3.json"
MARKER_VERSION_ROOT = CONFIG_DIR / "marker_versions_v3"
_SAFE_PART_RE = re.compile(r"[^a-z0-9а-яё._-]+", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_all_marker_rows(path: Path) -> list[dict]:
    headers, rows, _delimiter = _read_csv(path)
    id_index = _header_index(headers, "id")
    return [
        _marker_record(headers, row, index, row[id_index].strip() if id_index is not None else "")
        for index, row in enumerate(rows)
    ]


def _safe_part(value: str, fallback: str = "unknown") -> str:
    value = _SAFE_PART_RE.sub("_", str(value).strip().lower()).strip("_.-")
    return value or fallback


def chain_key_from_filename(filename: str, series_uid: str | None = None) -> str:
    """Return the permanent namespace for a report chain.

    Main/ME and RUS/ENG are deliberately independent.  When a Disk-backed
    series UID is known it replaces the human series name, so renaming a
    series does not split its history.
    """
    meta = parse_report_filename(Path(filename).name)
    if meta is None:
        return marker_list_key(filename)
    report_type = (meta.variant or "main").strip().lower()
    report_type = "me" if report_type in {"mne", "m&e", "me", "me_ours"} else report_type
    language = (meta.lang or "unknown").strip().lower()
    language = {"ru": "rus", "en": "eng"}.get(language, language)
    series_part = series_uid or _safe_part(meta.series)
    return f"{series_part}|s{meta.season:02d}|e{meta.episode:02d}|{report_type}|{language}"


def empty_registry() -> dict:
    return {"version": REGISTRY_VERSION, "aliases": {}, "chains": {}}


def load_registry(path: Path = MARKER_REGISTRY_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
        data = empty_registry()
    data.setdefault("aliases", {})
    data.setdefault("chains", {})
    return data


def save_registry(data: dict, path: Path = MARKER_REGISTRY_FILE) -> None:
    data["version"] = REGISTRY_VERSION
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def empty_chain(chain_key: str, source_name: str = "") -> dict:
    parts = chain_key.split("|")
    return {
        "schema": REGISTRY_VERSION,
        "chain_key": chain_key,
        "series_uid": parts[0] if len(parts) >= 5 else "",
        "season": parts[1] if len(parts) >= 5 else "",
        "episode": parts[2] if len(parts) >= 5 else "",
        "report_type": parts[3] if len(parts) >= 5 else "",
        "language": parts[4] if len(parts) >= 5 else "",
        "source_name": source_name,
        "revision": 0,
        "next_id": 1,
        "max_issued": 0,
        "markers": {},
        "versions": [],
        "updated_at": utc_now(),
        "health": "ok",
    }


def _legacy_marker_entry(chain_key: str, marker: dict, now: str) -> tuple[str, dict] | None:
    marker_id = marker.get("id", "") if isinstance(marker, dict) else ""
    if not BEAST_ID_RE.fullmatch(marker_id):
        return None
    snapshot = deepcopy(marker)
    snapshot["id"] = marker_id
    return marker_id, {
        "uid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"beast:{chain_key}:{marker_id}")),
        "id": marker_id,
        "status": "active",
        "confirmed": True,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
        "current": snapshot,
        "history": [snapshot],
        "restored_count": 0,
    }


def migrate_legacy_registry(
    registry_path: Path = MARKER_REGISTRY_FILE,
    legacy_path: Path | None = None,
) -> dict:
    """Import the v2 latest snapshots once without modifying the old file."""
    current = load_registry(registry_path)
    if legacy_path is None:
        legacy_path = registry_path.with_name(MARKER_IDENTITIES_FILE.name)
    if current.get("chains"):
        return current
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return current
    projects = legacy.get("projects", {}) if isinstance(legacy, dict) else {}
    if not isinstance(projects, dict):
        return current
    now = utc_now()
    for chain_key, project in projects.items():
        if not isinstance(project, dict):
            continue
        chain = empty_chain(chain_key, project.get("source_name", ""))
        for marker in project.get("markers", []):
            migrated = _legacy_marker_entry(chain_key, marker, now)
            if migrated:
                marker_id, entry = migrated
                chain["markers"][marker_id] = entry
        highest = max((_id_number(value) or 0 for value in chain["markers"]), default=0)
        try:
            stored_next = int(project.get("next_id", 1))
        except (TypeError, ValueError):
            stored_next = 1
        chain["max_issued"] = highest
        chain["next_id"] = max(stored_next, highest + 1)
        chain["revision"] = 1
        chain["updated_at"] = project.get("updated_at", now)
        current["chains"][chain_key] = chain
    if current["chains"]:
        save_registry(current, registry_path)
    return current


@dataclass
class MarkerCandidate:
    marker_id: str
    score: float
    status: str
    description: str = ""
    tc_in: str = ""
    last_seen_at: str = ""


@dataclass
class MarkerAmbiguity:
    row_index: int
    tc_in: str
    description: str
    proposed_id: str
    candidates: list[MarkerCandidate] = field(default_factory=list)


@dataclass
class MarkerIdentityPlan:
    chain_key: str
    source_name: str
    source_digest: str
    assignments: list[str]
    new_ids: list[str]
    restored_ids: list[str]
    ambiguities: list[MarkerAmbiguity]
    offline: bool = False
    warning: str = ""
    base_revision: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "MarkerIdentityPlan":
        ambiguities = []
        for item in value.get("ambiguities", []):
            candidates = [MarkerCandidate(**candidate) for candidate in item.get("candidates", [])]
            ambiguities.append(MarkerAmbiguity(**{**item, "candidates": candidates}))
        return cls(**{**value, "ambiguities": ambiguities})


def _entry_snapshots(entry: dict) -> list[dict]:
    history = entry.get("history", [])
    result = [item for item in history if isinstance(item, dict)]
    current = entry.get("current")
    if isinstance(current, dict) and current not in result:
        result.append(current)
    return result


def _best_entry_score(entry: dict, marker: dict, shift: int, old_count: int, new_count: int) -> float:
    return max(
        (_match_score(old, marker, shift, old_count, new_count) for old in _entry_snapshots(entry)),
        default=0.0,
    )


def _exact_entry_matches(entry: dict, marker: dict) -> bool:
    fingerprint = _fingerprint(marker)
    return any(_fingerprint(old) == fingerprint for old in _entry_snapshots(entry))


def _next_marker_id(used: set[str], number: int) -> tuple[str, int]:
    number = max(number, 1)
    while f"M{number}" in used:
        number += 1
    marker_id = f"M{number}"
    used.add(marker_id)
    return marker_id, number + 1


def prepare_identity_plan(
    source_path: str | Path,
    *,
    chain_key: str | None = None,
    offline: bool = False,
    warning: str = "",
    registry_path: Path = MARKER_REGISTRY_FILE,
) -> MarkerIdentityPlan:
    """Plan IDs without modifying the CSV or advancing registry history."""
    source = Path(source_path)
    registry = migrate_legacy_registry(registry_path)
    key = chain_key or chain_key_from_filename(source.name)
    chain = deepcopy(registry.get("chains", {}).get(key) or empty_chain(key, source.name))
    current = _read_all_marker_rows(source)
    entries = chain.get("markers", {})
    used = {marker_id for marker_id in entries if BEAST_ID_RE.fullmatch(marker_id)}
    carried = [marker.get("id", "") if BEAST_ID_RE.fullmatch(marker.get("id", "")) else "" for marker in current]
    seen = set()
    for marker_id in carried:
        if marker_id and marker_id in seen:
            raise ValueError(f"Duplicate marker ID in CSV: {marker_id}")
        if marker_id:
            seen.add(marker_id)
            used.add(marker_id)

    assignments = list(carried)
    occupied = set(seen)
    ambiguities: list[MarkerAmbiguity] = []
    restored: list[str] = []
    active_records = [entry.get("current", {}) for entry in entries.values() if entry.get("status") == "active"]
    shift = _global_time_shift(active_records, current)

    # A blank cell in a carried-ID CSV is new relative to the immediately
    # previous active version.  It may still restore an ID that had already
    # become a tombstone in an earlier version.
    eligible_statuses = {"deleted"} if seen else {"active", "deleted"}
    for row_index, marker in enumerate(current):
        if assignments[row_index]:
            continue
        exact = [
            marker_id for marker_id, entry in entries.items()
            if marker_id not in occupied
            and entry.get("status") in eligible_statuses
            and _exact_entry_matches(entry, marker)
        ]
        if len(exact) == 1:
            assignments[row_index] = exact[0]
            occupied.add(exact[0])
            if entries[exact[0]].get("status") == "deleted":
                restored.append(exact[0])
            continue

        scored = []
        for marker_id, entry in entries.items():
            if marker_id in occupied or entry.get("status") not in eligible_statuses:
                continue
            score = _best_entry_score(entry, marker, shift, max(len(entries), 1), max(len(current), 1))
            if score >= 0.50:
                snapshot = entry.get("current", {})
                scored.append(MarkerCandidate(
                    marker_id=marker_id,
                    score=round(score, 4),
                    status=entry.get("status", "deleted"),
                    description=snapshot.get("description", ""),
                    tc_in=snapshot.get("tc_in", ""),
                    last_seen_at=entry.get("last_seen_at", ""),
                ))
        scored.sort(key=lambda candidate: candidate.score, reverse=True)
        best = scored[0].score if scored else 0.0
        runner_up = scored[1].score if len(scored) > 1 else 0.0
        best_status = scored[0].status if scored else ""
        automatic = (
            scored
            and ((best_status == "active" and best >= 0.68 and best - runner_up >= 0.10)
                 or (best_status == "deleted" and best >= 0.88 and best - runner_up >= 0.15))
        )
        if automatic:
            assignments[row_index] = scored[0].marker_id
            occupied.add(scored[0].marker_id)
            if best_status == "deleted":
                restored.append(scored[0].marker_id)
        elif scored:
            ambiguities.append(MarkerAmbiguity(
                row_index=row_index,
                tc_in=marker.get("tc_in", ""),
                description=marker.get("description", ""),
                proposed_id="",
                candidates=scored[:5],
            ))

    highest = max(chain.get("max_issued", 0), max((_id_number(value) or 0 for value in used), default=0))
    next_number = max(int(chain.get("next_id", 1) or 1), highest + 1)
    new_ids = []
    for index, marker_id in enumerate(assignments):
        if marker_id:
            continue
        marker_id, next_number = _next_marker_id(used, next_number)
        assignments[index] = marker_id
        new_ids.append(marker_id)
        for ambiguity in ambiguities:
            if ambiguity.row_index == index:
                ambiguity.proposed_id = marker_id
                break

    if offline and not warning:
        warning = (
            "Яндекс Диск недоступен. Используется локальная история ID; "
            "новые номера будут подтверждены перед загрузкой."
            if entries else
            "История этой цепочки недоступна. Новые ID назначены предварительно "
            "и могут быть изменены после синхронизации."
        )
    return MarkerIdentityPlan(
        chain_key=key,
        source_name=source.name,
        source_digest=_source_digest(source),
        assignments=assignments,
        new_ids=new_ids,
        restored_ids=sorted(set(restored), key=lambda value: _id_number(value) or 0),
        ambiguities=ambiguities,
        offline=offline,
        warning=warning,
        base_revision=int(chain.get("revision", 0) or 0),
    )


def apply_ambiguity_choices(plan: MarkerIdentityPlan, choices: dict[int, str]) -> MarkerIdentityPlan:
    """Apply UI choices; an empty value keeps the proposed new ID."""
    result = deepcopy(plan)
    used = set(result.assignments)
    restored = set(result.restored_ids)
    new_ids = set(result.new_ids)
    for ambiguity in result.ambiguities:
        chosen = choices.get(ambiguity.row_index, "")
        if not chosen or chosen == ambiguity.proposed_id:
            continue
        if chosen in used and chosen != result.assignments[ambiguity.row_index]:
            raise ValueError(f"Marker ID {chosen} is already assigned to another row")
        old = result.assignments[ambiguity.row_index]
        used.discard(old)
        new_ids.discard(old)
        result.assignments[ambiguity.row_index] = chosen
        used.add(chosen)
        restored.add(chosen)
    result.new_ids = sorted(new_ids, key=lambda value: _id_number(value) or 0)
    result.restored_ids = sorted(restored, key=lambda value: _id_number(value) or 0)
    return result


def _append_unique_history(entry: dict, snapshot: dict) -> None:
    fingerprints = {_fingerprint(item) for item in _entry_snapshots(entry)}
    if _fingerprint(snapshot) not in fingerprints:
        entry.setdefault("history", []).append(deepcopy(snapshot))
        # Bound pathological growth while retaining long-lived restoration data.
        entry["history"] = entry["history"][-100:]


def commit_identity_plan(
    source_path: str | Path,
    markers: list[dict],
    plan: MarkerIdentityPlan | dict,
    *,
    registry_path: Path = MARKER_REGISTRY_FILE,
    versions_root: Path | None = None,
) -> dict:
    """Commit an applied plan, tombstoning missing IDs and writing a snapshot."""
    if isinstance(plan, dict):
        plan = MarkerIdentityPlan.from_dict(plan)
    if versions_root is None:
        versions_root = (
            MARKER_VERSION_ROOT
            if registry_path == MARKER_REGISTRY_FILE
            else registry_path.parent / MARKER_VERSION_ROOT.name
        )
    if _source_digest(source_path) != plan.source_digest:
        raise ValueError("Marker CSV changed after ID review; run the review again")
    if len(markers) != len(plan.assignments):
        raise ValueError("Marker CSV row count changed after ID review")

    registry = migrate_legacy_registry(registry_path)
    chains = registry.setdefault("chains", {})
    chain = deepcopy(chains.get(plan.chain_key) or empty_chain(plan.chain_key, plan.source_name))
    now = utc_now()
    current_ids = set(plan.assignments)
    for marker_id, entry in chain.get("markers", {}).items():
        if entry.get("status") in {"active", "restored", "pending"} and marker_id not in current_ids:
            entry["status"] = "deleted"
            entry["deleted_at"] = now
            entry["updated_at"] = now

    chain.setdefault("markers", {})
    plan_new = set(plan.new_ids)
    plan_restored = set(plan.restored_ids)
    committed_markers = []
    for marker, marker_id in zip(markers, plan.assignments):
        snapshot = deepcopy(marker)
        snapshot["id"] = marker_id
        entry = chain["markers"].get(marker_id)
        if entry is None:
            entry = {
                "uid": str(uuid.uuid4()),
                "id": marker_id,
                "first_seen_at": now,
                "history": [],
                "restored_count": 0,
            }
            chain["markers"][marker_id] = entry
        was_deleted = entry.get("status") == "deleted" or marker_id in plan_restored
        if was_deleted:
            entry["restored_count"] = int(entry.get("restored_count", 0) or 0) + 1
            entry["restored_at"] = now
        entry.update({
            # A newly allocated sequential number is provisional until the
            # chain state/report snapshot reaches Yandex Disk.  This is true
            # even when planning happened online: another machine may allocate
            # concurrently between planning and upload.
            "status": "pending" if marker_id in plan_new else ("restored" if was_deleted else "active"),
            "confirmed": marker_id not in plan_new,
            "last_seen_at": now,
            "updated_at": now,
            "current": snapshot,
        })
        _append_unique_history(entry, snapshot)
        committed_markers.append(snapshot)

    highest = max((_id_number(value) or 0 for value in chain["markers"]), default=0)
    version_uid = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{plan.source_digest[:12]}"
    version = {
        "version_uid": version_uid,
        "created_at": now,
        "source_name": plan.source_name,
        "source_digest": plan.source_digest,
        "status": "pending" if plan_new else "confirmed",
        "markers": committed_markers,
    }
    chain["versions"] = [item for item in chain.get("versions", []) if item.get("version_uid") != version_uid]
    chain["versions"].append({key: value for key, value in version.items() if key != "markers"})
    chain["versions"] = chain["versions"][-500:]
    chain.update({
        "revision": int(chain.get("revision", 0) or 0) + 1,
        "next_id": max(int(chain.get("next_id", 1) or 1), highest + 1),
        "max_issued": max(int(chain.get("max_issued", 0) or 0), highest),
        "source_name": plan.source_name,
        "updated_at": now,
        "health": "offline" if plan.offline else ("attention" if plan.ambiguities else ("pending" if plan_new else "ok")),
    })
    chains[plan.chain_key] = chain
    save_registry(registry, registry_path)

    version_dir = versions_root / hashlib.sha256(plan.chain_key.encode("utf-8")).hexdigest()[:20]
    version_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        version_dir / f"{version_uid}.json",
        json.dumps({"chain_key": plan.chain_key, **version}, ensure_ascii=False, indent=2, sort_keys=True),
    )
    return chain


def chain_statistics(registry_path: Path = MARKER_REGISTRY_FILE) -> list[dict]:
    registry = migrate_legacy_registry(registry_path)
    result = []
    for chain_key, chain in registry.get("chains", {}).items():
        markers = list(chain.get("markers", {}).values())
        result.append({
            "chain_key": chain_key,
            "versions": len(chain.get("versions", [])),
            "active": sum(entry.get("status") in {"active", "restored"} for entry in markers),
            "deleted": sum(entry.get("status") == "deleted" for entry in markers),
            "restored": sum(int(entry.get("restored_count", 0) or 0) > 0 for entry in markers),
            "pending": sum(entry.get("status") == "pending" or not entry.get("confirmed", True) for entry in markers),
            "max_issued": int(chain.get("max_issued", 0) or 0),
            "updated_at": chain.get("updated_at", ""),
            "health": chain.get("health", "ok"),
        })
    return sorted(result, key=lambda item: item["chain_key"])
