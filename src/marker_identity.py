"""Persistent identities for CSV marker-list rows.

Nuendo marker IDs are deliberately not used as identities: deleted IDs are
reused.  This module keeps Beast IDs in the exported report copy of the CSV
and in a small local snapshot registry, then inherits them by matching the
next version of the same marker list.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import statistics
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from src.app_paths import CONFIG_DIR, atomic_write_text
from src.report_filename import parse_report_filename

logger = logging.getLogger(__name__)

MARKER_IDENTITIES_FILE = CONFIG_DIR / "marker_identities.json"
BEAST_ID_RE = re.compile(r"^M[1-9]\d*$")
_LEGACY_BEAST_ID_RE = re.compile(r"^BAR-[A-Z2-9]{10}$")
_VERSION_SUFFIX_RE = re.compile(
    r"(?:_\d{4}_\d{2}_\d{2})(?:_v\d+)?(?:_\d+)?(?:_(?:rus|eng|ru|en))?$",
    re.IGNORECASE,
)

_TC_IN_HEADERS = ("timecode in", "tc in", "tc_in")
_TC_OUT_HEADERS = ("timecode out", "tc out", "tc_out")
_DESCRIPTION_HEADERS = ("description", "описание проблемы", "описание")
_COMMENTS_HEADERS = ("комментарии", "comments")
_FLAG_HEADERS = (
    "2.0 c", "2.0 uc", "5.1 c", "5.1 uc", "блокер", "blocker",
    "требует исправления", "fix required", "требует комментария", "comment required",
)


def load_marker_identities(path: Path = MARKER_IDENTITIES_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_marker_identities(data: dict, path: Path = MARKER_IDENTITIES_FILE) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def marker_list_key(filename: str) -> str:
    """Stable series/episode/variant key derived from a marker-list name."""
    meta = parse_report_filename(Path(filename).name)
    if meta is not None:
        variant = (meta.variant or "main").strip().lower()
        return f"{meta.series.lower()}|s{meta.season:02d}|e{meta.episode:02d}|{variant}|{meta.lang.lower()}"

    stem = Path(filename).stem
    if stem.lower().startswith("отчет_"):
        stem = stem[len("отчет_"):]
    stem = _VERSION_SUFFIX_RE.sub("", stem)
    return re.sub(r"[^a-zа-яё0-9]+", "_", stem.lower(), flags=re.IGNORECASE).strip("_")


def _delimiter(first_line: str) -> str:
    if "\t" in first_line:
        return "\t"
    if ";" in first_line:
        return ";"
    return ","


def _header_index(headers: list[str], *names: str) -> int | None:
    wanted = {name.casefold() for name in names}
    return next((index for index, name in enumerate(headers) if name.strip().casefold() in wanted), None)


def _unique_header(headers: list[str], preferred: str) -> str:
    existing = {header.casefold() for header in headers}
    if preferred.casefold() not in existing:
        return preferred
    counter = 2
    while f"{preferred} {counter}".casefold() in existing:
        counter += 1
    return f"{preferred} {counter}"


def _read_csv(path: Path) -> tuple[list[str], list[list[str]], str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        first_line = handle.readline()
        handle.seek(0)
        delimiter = _delimiter(first_line)
        rows = list(csv.reader(handle, delimiter=delimiter))
    if not rows:
        return [], [], delimiter
    headers = [header.strip() for header in rows[0]]
    width = len(headers)
    data_rows = [(row + [""] * width)[:width] for row in rows[1:]]
    return headers, data_rows, delimiter


def _value(headers: list[str], row: list[str], *names: str) -> str:
    index = _header_index(headers, *names)
    return row[index].strip() if index is not None and index < len(row) else ""


def _normalized_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold(), flags=re.IGNORECASE).split())


def _timecode_units(value: str) -> int | None:
    parts = re.findall(r"\d+", value)
    if len(parts) < 4:
        return None
    hours, minutes, seconds, frames = (int(part) for part in parts[-4:])
    return ((hours * 60 + minutes) * 60 + seconds) * 25 + frames


def _marker_record(headers: list[str], row: list[str], index: int, marker_id: str = "") -> dict:
    flags = {
        name: _value(headers, row, name).strip().casefold()
        for name in _FLAG_HEADERS
        if _header_index(headers, name) is not None
    }
    stable_id = marker_id if BEAST_ID_RE.fullmatch(marker_id) else ""
    native_id = _value(headers, row, "nuendo id", "marker id")
    if not native_id and marker_id and not stable_id:
        native_id = marker_id
    return {
        "id": stable_id,
        "tc_in": _value(headers, row, *_TC_IN_HEADERS),
        "tc_out": _value(headers, row, *_TC_OUT_HEADERS),
        "description": _value(headers, row, *_DESCRIPTION_HEADERS),
        "comments": _value(headers, row, *_COMMENTS_HEADERS),
        "flags": flags,
        "blocker": flags.get("блокер", flags.get("blocker", "")) == "*",
        "nuendo_id": native_id,
        "order": index,
    }


def _fingerprint(marker: dict) -> tuple:
    return (
        marker.get("tc_in", ""), marker.get("tc_out", ""),
        _normalized_text(marker.get("description", "")),
        _normalized_text(marker.get("comments", "")),
        tuple(sorted((marker.get("flags") or {}).items())),
    )


def _id_number(marker_id: str) -> int | None:
    if not BEAST_ID_RE.fullmatch(marker_id or ""):
        return None
    return int(marker_id[1:])


def _new_id(used: set[str], next_number: int) -> str:
    next_number = max(int(next_number), 1)
    while f"M{next_number}" in used:
        next_number += 1
    marker_id = f"M{next_number}"
    used.add(marker_id)
    return marker_id


def _migrate_project_ids(project: dict) -> list[dict]:
    """Convert legacy random IDs to short, monotonic marker numbers."""
    markers = project.get("markers", [])
    if not isinstance(markers, list):
        markers = []

    used = {
        marker.get("id", "")
        for marker in markers
        if isinstance(marker, dict) and BEAST_ID_RE.fullmatch(marker.get("id", ""))
    }
    next_number = 1
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        marker_id = marker.get("id", "")
        if not _LEGACY_BEAST_ID_RE.fullmatch(marker_id):
            continue
        replacement = _new_id(used, next_number)
        marker["id"] = replacement
        next_number = (_id_number(replacement) or next_number) + 1

    highest = max((_id_number(marker_id) or 0 for marker_id in used), default=0)
    stored_next = project.get("next_id", 1)
    try:
        stored_next = max(int(stored_next), 1)
    except (TypeError, ValueError):
        stored_next = 1
    project["markers"] = markers
    project["next_id"] = max(stored_next, highest + 1)
    return markers


def _global_time_shift(old: list[dict], new: list[dict]) -> int:
    """Estimate a common edit shift from unique, unchanged descriptions."""
    old_by_description: dict[str, list[dict]] = {}
    new_by_description: dict[str, list[dict]] = {}
    for marker in old:
        text = _normalized_text(marker.get("description", ""))
        if text:
            old_by_description.setdefault(text, []).append(marker)
    for marker in new:
        text = _normalized_text(marker.get("description", ""))
        if text:
            new_by_description.setdefault(text, []).append(marker)

    shifts = []
    for text in set(old_by_description) & set(new_by_description):
        if len(old_by_description[text]) != 1 or len(new_by_description[text]) != 1:
            continue
        old_tc = _timecode_units(old_by_description[text][0].get("tc_in", ""))
        new_tc = _timecode_units(new_by_description[text][0].get("tc_in", ""))
        if old_tc is not None and new_tc is not None:
            shifts.append(new_tc - old_tc)
    return int(statistics.median(shifts)) if len(shifts) >= 2 else 0


def _match_score(old: dict, new: dict, shift: int, old_count: int, new_count: int) -> float:
    old_description = _normalized_text(old.get("description", ""))
    new_description = _normalized_text(new.get("description", ""))
    description_ratio = SequenceMatcher(None, old_description, new_description).ratio()
    if old_description and old_description == new_description:
        score = 0.42
    elif description_ratio >= 0.85:
        score = 0.34
    elif description_ratio >= 0.65:
        score = 0.22
    else:
        score = 0.0

    old_tc = _timecode_units(old.get("tc_in", ""))
    new_tc = _timecode_units(new.get("tc_in", ""))
    if old_tc is not None and new_tc is not None:
        distance = abs((old_tc + shift) - new_tc)
        score += 0.30 if distance <= 1 else (0.22 if distance <= 25 else (0.10 if distance <= 75 else 0.0))

    old_comments = _normalized_text(old.get("comments", ""))
    new_comments = _normalized_text(new.get("comments", ""))
    if old_comments and old_comments == new_comments:
        score += 0.10
    elif old_comments and new_comments:
        score += 0.08 * SequenceMatcher(None, old_comments, new_comments).ratio()

    if (old.get("flags") or {}) == (new.get("flags") or {}):
        score += 0.08
    old_position = old.get("order", 0) / max(old_count - 1, 1)
    new_position = new.get("order", 0) / max(new_count - 1, 1)
    score += 0.08 * max(0.0, 1.0 - abs(old_position - new_position) * 3)

    # A reused Nuendo number can never establish identity by itself.
    if old.get("nuendo_id") and old.get("nuendo_id") == new.get("nuendo_id"):
        score += 0.02
    return score


def assign_marker_ids(
    current: list[dict], previous: list[dict], used: set[str], next_number: int = 1
) -> list[str]:
    """Keep carried IDs and give rows without an ID fresh monotonic IDs."""
    try:
        next_number = max(int(next_number), 1)
    except (TypeError, ValueError):
        next_number = 1
    assigned = [marker.get("id", "") if BEAST_ID_RE.fullmatch(marker.get("id", "")) else "" for marker in current]
    seen_current_ids: set[str] = set()
    for index, marker_id in enumerate(assigned):
        if marker_id and marker_id in seen_current_ids:
            raise ValueError(f"Duplicate marker ID in CSV: {marker_id}")
        elif marker_id:
            seen_current_ids.add(marker_id)
    occupied_old: set[int] = set()

    # A repeated review carries our IDs in the CSV. In that mode they are the
    # only source of identity: a blank ID is always a genuinely new marker,
    # even if its timecode and text resemble a deleted marker.
    if seen_current_ids:
        used.update(seen_current_ids)
    else:
        # Compatibility path for old report CSVs that predate carried M-IDs.
        previous_by_fingerprint: dict[tuple, list[int]] = {}
        for old_index, marker in enumerate(previous):
            if BEAST_ID_RE.fullmatch(marker.get("id", "")):
                previous_by_fingerprint.setdefault(_fingerprint(marker), []).append(old_index)

        for current_index, marker in enumerate(current):
            candidates = [
                index
                for index in previous_by_fingerprint.get(_fingerprint(marker), [])
                if index not in occupied_old
            ]
            if len(candidates) == 1:
                old_index = candidates[0]
                assigned[current_index] = previous[old_index]["id"]
                occupied_old.add(old_index)

        shift = _global_time_shift(previous, current)
        for current_index, marker in enumerate(current):
            if assigned[current_index]:
                continue
            scored = sorted(
                (
                    (_match_score(old, marker, shift, len(previous), len(current)), old_index)
                    for old_index, old in enumerate(previous)
                    if old_index not in occupied_old and BEAST_ID_RE.fullmatch(old.get("id", ""))
                ),
                reverse=True,
            )
            best_score = scored[0][0] if scored else 0.0
            runner_up = scored[1][0] if len(scored) > 1 else 0.0
            if scored and best_score >= 0.58 and best_score - runner_up >= 0.07:
                old_index = scored[0][1]
                assigned[current_index] = previous[old_index]["id"]
                occupied_old.add(old_index)

    highest_known = max(
        (_id_number(marker_id) or 0 for marker_id in used | set(assigned)),
        default=0,
    )
    next_number = max(next_number, highest_known + 1)
    for index, marker_id in enumerate(assigned):
        if not marker_id:
            assigned[index] = _new_id(used, next_number)
            next_number = (_id_number(assigned[index]) or next_number) + 1
    return assigned


def enrich_marker_csv(
    source_path: str | Path,
    destination_path: str | Path,
    registry_path: Path = MARKER_IDENTITIES_FILE,
    project_key: str | None = None,
    identity_plan: dict | None = None,
    identity_registry_path: Path | None = None,
    exclude_headers: tuple[str, ...] = (),
) -> list[str]:
    """Write a marker-list copy with Beast ``ID`` before ``Timecode In``.

    ``exclude_headers`` affects only the exported copy. Identity matching and
    the registry snapshot still use the complete source marker list.
    """
    source = Path(source_path)
    destination = Path(destination_path)
    headers, rows, delimiter = _read_csv(source)
    if not headers:
        raise ValueError(f"CSV marker list is empty: {source}")

    tc_index = _header_index(headers, *_TC_IN_HEADERS)
    if tc_index is None:
        raise ValueError("CSV marker list has no Timecode In column")

    id_index = _header_index(headers, "id")
    existing_values = [row[id_index].strip() for row in rows] if id_index is not None else []
    has_beast_ids = any(BEAST_ID_RE.fullmatch(value) for value in existing_values)
    has_foreign_ids = any(value and not BEAST_ID_RE.fullmatch(value) for value in existing_values)
    if has_beast_ids and has_foreign_ids:
        raise ValueError("ID column mixes Beast marker IDs with foreign/Nuendo IDs")
    if id_index is not None and has_foreign_ids:
        headers[id_index] = _unique_header(headers, "Nuendo ID")
        id_index = None

    if id_index is None:
        tc_index = _header_index(headers, *_TC_IN_HEADERS)
        headers.insert(tc_index, "ID")
        for row in rows:
            row.insert(tc_index, "")
        id_index = tc_index
    elif id_index != tc_index - 1:
        header = headers.pop(id_index)
        values = [row.pop(id_index) for row in rows]
        tc_index = _header_index(headers, *_TC_IN_HEADERS)
        headers.insert(tc_index, header)
        for row, value in zip(rows, values):
            row.insert(tc_index, value)
        id_index = tc_index

    current = [_marker_record(headers, row, index, row[id_index].strip()) for index, row in enumerate(rows)]
    if identity_plan is not None:
        # Lazy import avoids a module cycle: marker_registry reuses the CSV
        # parsing and matching primitives from this compatibility module.
        from src.marker_registry import MarkerIdentityPlan, commit_identity_plan

        plan = MarkerIdentityPlan.from_dict(identity_plan) if isinstance(identity_plan, dict) else identity_plan
        assigned = list(plan.assignments)
        if len(assigned) != len(current):
            raise ValueError("Marker ID review no longer matches the CSV row count")
        if len(set(assigned)) != len(assigned) or any(not BEAST_ID_RE.fullmatch(value) for value in assigned):
            raise ValueError("Marker ID review contains duplicate or invalid IDs")
    else:
        registry = load_marker_identities(registry_path)
        projects = registry.setdefault("projects", {})
        key = project_key or marker_list_key(source.name)
        project = projects.setdefault(key, {})
        previous = _migrate_project_ids(project)
        used = {
            marker.get("id")
            for marker in previous
            if isinstance(marker, dict) and BEAST_ID_RE.fullmatch(marker.get("id", ""))
        }
        assigned = assign_marker_ids(current, previous, used, project.get("next_id", 1))
    for row, marker_id in zip(rows, assigned):
        row[id_index] = marker_id

    snapshot = [_marker_record(headers, row, index, row[id_index]) for index, row in enumerate(rows)]
    if identity_plan is not None:
        if identity_registry_path is None:
            commit_identity_plan(source, snapshot, plan)
        else:
            commit_identity_plan(source, snapshot, plan, registry_path=identity_registry_path)
    else:
        highest_assigned = max((_id_number(marker_id) or 0 for marker_id in assigned), default=0)
        project.update({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source_name": source.name,
            "markers": snapshot,
            "next_id": max(project.get("next_id", 1), highest_assigned + 1),
        })
        registry["version"] = 2
        save_marker_identities(registry, registry_path)

    if exclude_headers:
        excluded = {name.strip().casefold() for name in exclude_headers}
        keep_indexes = [
            index for index, header in enumerate(headers)
            if header.strip().casefold() not in excluded
        ]
        headers = [headers[index] for index in keep_indexes]
        rows = [[row[index] for index in keep_indexes] for row in rows]

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)
    logger.info("Assigned persistent IDs to %d markers: %s", len(rows), destination)
    return assigned


def read_marker_csv(path: str | Path) -> list[dict]:
    headers, rows, _delimiter_name = _read_csv(Path(path))
    id_index = _header_index(headers, "id")
    return [
        _marker_record(headers, row, index, row[id_index].strip() if id_index is not None else "")
        for index, row in enumerate(rows)
        if _value(headers, row, *_TC_IN_HEADERS)
    ]


def read_marker_csv_bytes(data: bytes) -> list[dict]:
    """Read a downloaded UTF-8 CSV without creating a temporary file."""
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    if not lines:
        return []
    delimiter = _delimiter(lines[0])
    parsed = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not parsed:
        return []
    headers = [header.strip() for header in parsed[0]]
    width = len(headers)
    rows = [(row + [""] * width)[:width] for row in parsed[1:]]
    id_index = _header_index(headers, "id")
    return [
        _marker_record(headers, row, index, row[id_index].strip() if id_index is not None else "")
        for index, row in enumerate(rows)
        if _value(headers, row, *_TC_IN_HEADERS)
    ]
