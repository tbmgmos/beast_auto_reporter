import csv
import json
from pathlib import Path

from src.marker_identity import BEAST_ID_RE, enrich_marker_csv, read_marker_csv
from src.report_uploader import diff_markers


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(headers)
        writer.writerows(rows)


def _read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def test_enrich_places_beast_id_immediately_before_timecode_and_keeps_nuendo_id(tmp_path):
    source = tmp_path / "show_s01_e01_2026_08_12_rus.csv"
    destination = tmp_path / "out.csv"
    registry = tmp_path / "registry.json"
    _write_csv(
        source,
        ["Track name", "ID", "Timecode In", "Timecode Out", "Description"],
        [["Markers", "2", "01:00:01:00", "", "Щелчок"]],
    )

    assigned = enrich_marker_csv(source, destination, registry_path=registry)
    rows = _read_rows(destination)

    assert rows[0] == ["Track name", "ID", "Beast ID", "Timecode In", "Timecode Out", "Description"]
    assert rows[1][1] == "2"
    assert rows[1][2] == assigned[0]
    assert assigned == ["M1"]
    assert BEAST_ID_RE.fullmatch(assigned[0])


def test_enrich_migrates_legacy_beast_and_nuendo_id_headers(tmp_path):
    source = tmp_path / "legacy.csv"
    destination = tmp_path / "out.csv"
    _write_csv(
        source,
        ["Track name", "Nuendo ID", "ID", "Timecode In", "Description"],
        [["Markers", "7", "M42", "01:00:01:00", "Щелчок"]],
    )

    assigned = enrich_marker_csv(
        source, destination, registry_path=tmp_path / "registry.json"
    )
    rows = _read_rows(destination)

    assert rows[0] == ["Track name", "ID", "Beast ID", "Timecode In", "Description"]
    assert rows[1][1:3] == ["7", "M42"]
    assert assigned == ["M42"]


def test_enrich_can_remove_dcp_audio_columns_from_export_only(tmp_path):
    source = tmp_path / "dcp.csv"
    destination = tmp_path / "out.csv"
    _write_csv(
        source,
        ["Timecode In", "Description", "2.0 C", "2.0 UC", "5.1 C", "5.1 UC", "БЛОКЕР"],
        [["01:00:01:00", "Щелчок", "", "", "*", "", "*"]],
    )

    enrich_marker_csv(
        source,
        destination,
        registry_path=tmp_path / "registry.json",
        exclude_headers=("2.0 C", "2.0 UC", "5.1 C", "5.1 UC"),
    )

    rows = _read_rows(destination)
    assert rows[0] == ["Beast ID", "Timecode In", "Description", "БЛОКЕР"]
    assert rows[1][1:] == ["01:00:01:00", "Щелчок", "*"]


def test_next_version_keeps_carried_ids_and_assigns_new_marker_next_id(tmp_path):
    registry = tmp_path / "registry.json"
    old_source = tmp_path / "old.csv"
    new_source = tmp_path / "new.csv"
    old_output = tmp_path / "old_out.csv"
    new_output = tmp_path / "new_out.csv"
    headers = ["ID", "Timecode In", "Timecode Out", "Description", "БЛОКЕР"]
    _write_csv(old_source, headers, [
        ["1", "01:00:01:00", "", "Щелчок", ""],
        ["2", "01:00:02:00", "", "Шум в диалоге", ""],
        ["3", "01:00:03:00", "", "Треск", "*"],
    ])
    old_ids = enrich_marker_csv(
        old_source, old_output, registry_path=registry, project_key="show|s01|e01|main|rus"
    )

    # The second review carries Beast IDs. Nuendo reuses native ID 2 for a
    # new marker, but the empty Beast ID makes it unambiguously new.
    second_headers = ["ID", "Beast ID", "Timecode In", "Timecode Out", "Description", "БЛОКЕР"]
    _write_csv(new_source, second_headers, [
        ["1", old_ids[0], "01:00:01:10", "", "Полностью изменённое описание", ""],
        ["2", "", "01:00:20:00", "", "Новый маркер", ""],
        ["3", old_ids[2], "01:00:03:00", "", "Треск", "*"],
    ])
    new_ids = enrich_marker_csv(
        new_source, new_output, registry_path=registry, project_key="show|s01|e01|main|rus"
    )

    assert new_ids[0] == old_ids[0]
    assert new_ids[2] == old_ids[2]
    assert old_ids == ["M1", "M2", "M3"]
    assert new_ids == ["M1", "M4", "M3"]


def test_blank_id_never_inherits_deleted_id_when_csv_carries_ids(tmp_path):
    registry = tmp_path / "registry.json"
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_csv(first, ["Timecode In", "Description"], [
        ["01:00:01:00", "Первый"],
        ["01:00:02:00", "Удаляемый"],
    ])
    first_ids = enrich_marker_csv(first, tmp_path / "first_out.csv", registry, "show")

    _write_csv(second, ["ID", "Timecode In", "Description"], [
        [first_ids[0], "01:00:01:00", "Первый"],
        ["", "01:00:02:00", "Удаляемый"],
    ])
    second_ids = enrich_marker_csv(second, tmp_path / "second_out.csv", registry, "show")

    assert first_ids == ["M1", "M2"]
    assert second_ids == ["M1", "M3"]


def test_duplicate_carried_ids_are_rejected(tmp_path):
    source = tmp_path / "source.csv"
    _write_csv(source, ["ID", "Timecode In", "Description"], [
        ["M1", "01:00:01:00", "Первый"],
        ["M1", "01:00:02:00", "Второй"],
    ])

    try:
        enrich_marker_csv(source, tmp_path / "out.csv", tmp_path / "registry.json", "show")
    except ValueError as exc:
        assert "Duplicate marker ID" in str(exc)
    else:
        raise AssertionError("duplicate marker IDs must be rejected")


def test_existing_beast_ids_are_preserved_and_read_back(tmp_path):
    source = tmp_path / "source.csv"
    destination = tmp_path / "out.csv"
    registry = tmp_path / "registry.json"
    marker_id = "M42"
    _write_csv(
        source,
        ["Timecode In", "ID", "Description"],
        [["01:00:01:00", marker_id, "Щелчок"]],
    )

    assigned = enrich_marker_csv(source, destination, registry_path=registry, project_key="show")
    markers = read_marker_csv(destination)

    assert assigned == [marker_id]
    assert _read_rows(destination)[0] == ["Beast ID", "Timecode In", "Description"]
    assert markers[0]["id"] == marker_id


def test_new_id_continues_after_highest_existing_sequential_id(tmp_path):
    source = tmp_path / "source.csv"
    destination = tmp_path / "out.csv"
    registry = tmp_path / "registry.json"
    _write_csv(
        source,
        ["ID", "Timecode In", "Description"],
        [
            ["M42", "01:00:01:00", "Существующий маркер"],
            ["", "01:00:02:00", "Новый маркер"],
        ],
    )

    assigned = enrich_marker_csv(
        source, destination, registry_path=registry, project_key="show"
    )

    assert assigned == ["M42", "M43"]


def test_legacy_random_ids_migrate_to_sequential_ids(tmp_path):
    source = tmp_path / "source.csv"
    destination = tmp_path / "out.csv"
    registry = tmp_path / "registry.json"
    project_key = "show"
    registry.write_text(json.dumps({
        "version": 1,
        "projects": {
            project_key: {
                "markers": [
                    {
                        "id": "BAR-ABCDEFGH23",
                        "tc_in": "01:00:01:00",
                        "tc_out": "",
                        "description": "Щелчок",
                        "comments": "",
                        "flags": {},
                        "order": 0,
                    },
                    {
                        "id": "BAR-NULKR835H9",
                        "tc_in": "01:00:02:00",
                        "tc_out": "",
                        "description": "Шум",
                        "comments": "",
                        "flags": {},
                        "order": 1,
                    },
                ]
            }
        },
    }, ensure_ascii=False), encoding="utf-8")
    _write_csv(source, ["Timecode In", "Description"], [
        ["01:00:01:00", "Щелчок"],
        ["01:00:02:00", "Шум"],
        ["01:00:03:00", "Новый маркер"],
    ])

    assigned = enrich_marker_csv(
        source, destination, registry_path=registry, project_key=project_key
    )
    saved = json.loads(registry.read_text(encoding="utf-8"))

    assert assigned == ["M1", "M2", "M3"]
    assert saved["version"] == 2
    assert saved["projects"][project_key]["next_id"] == 4


def test_diff_uses_persistent_id_when_timecode_changes():
    old = [{
        "id": "M1", "tc_in": "01:00:01:00", "tc_out": "",
        "description": "Щелчок", "blocker": False, "comments": "",
    }]
    new = [{
        "id": "M1", "tc_in": "01:00:02:00", "tc_out": "",
        "description": "Щелчок исправлен", "blocker": False, "comments": "",
    }]

    result = diff_markers(old, new)

    assert result["added"] == []
    assert result["removed"] == []
    assert result["changed"][0]["id"] == "M1"
    assert {change["field"] for change in result["changed"][0]["changes"]} == {
        "Timecode In", "Описание",
    }
