import csv
import json

from src.marker_identity import enrich_marker_csv
from src.marker_registry import (
    apply_ambiguity_choices,
    chain_key_from_filename,
    chain_statistics,
    load_registry,
    migrate_legacy_registry,
    prepare_identity_plan,
)
from src.marker_registry import empty_chain
from src.marker_registry_sync import merge_chain_states, recover_chain_from_reports


def _write(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _apply(source, output, registry, *, offline=False, key="show|s01|e01|main|rus"):
    plan = prepare_identity_plan(
        source, chain_key=key, offline=offline, registry_path=registry
    )
    enrich_marker_csv(
        source,
        output,
        identity_plan=plan.to_dict(),
        identity_registry_path=registry,
    )
    return plan


def test_chain_namespaces_are_independent_for_type_and_language():
    main_rus = chain_key_from_filename("отчет_Show_s01_e02_2026_08_14_rus.csv", "series-1")
    me_rus = chain_key_from_filename("отчет_Show_s01_e02_MnE_2026_08_14_rus.csv", "series-1")
    main_eng = chain_key_from_filename("отчет_Show_s01_e02_2026_08_14_eng.csv", "series-1")

    assert main_rus == "series-1|s01|e02|main|rus"
    assert me_rus == "series-1|s01|e02|me|rus"
    assert main_eng == "series-1|s01|e02|main|eng"
    assert len({main_rus, me_rus, main_eng}) == 3


def test_deleted_marker_is_tombstoned_and_restores_its_permanent_id(tmp_path):
    registry = tmp_path / "registry.json"
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    third = tmp_path / "third.csv"
    headers = ["ID", "Timecode In", "Description"]
    _write(first, [headers, ["", "01:00:01:00", "A"], ["", "01:00:02:00", "B"]])
    first_plan = _apply(first, tmp_path / "first_out.csv", registry)
    assert first_plan.assignments == ["M1", "M2"]

    _write(second, [headers, ["M1", "01:00:01:00", "A"]])
    _apply(second, tmp_path / "second_out.csv", registry)
    state = load_registry(registry)["chains"]["show|s01|e01|main|rus"]
    assert state["markers"]["M2"]["status"] == "deleted"
    assert state["next_id"] == 3

    _write(third, [headers, ["M1", "01:00:01:00", "A"], ["", "01:00:02:00", "B"]])
    third_plan = prepare_identity_plan(
        third, chain_key="show|s01|e01|main|rus", registry_path=registry
    )
    assert third_plan.assignments == ["M1", "M2"]
    assert third_plan.restored_ids == ["M2"]
    assert third_plan.new_ids == []
    enrich_marker_csv(
        third,
        tmp_path / "third_out.csv",
        identity_plan=third_plan.to_dict(),
        identity_registry_path=registry,
    )
    restored = load_registry(registry)["chains"]["show|s01|e01|main|rus"]["markers"]["M2"]
    assert restored["status"] == "restored"
    assert restored["restored_count"] == 1


def test_deleted_id_is_never_reused_for_a_different_marker(tmp_path):
    registry = tmp_path / "registry.json"
    headers = ["ID", "Timecode In", "Description"]
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    third = tmp_path / "third.csv"
    _write(first, [headers, ["", "01:00:01:00", "A"], ["", "01:00:02:00", "B"]])
    _apply(first, tmp_path / "one.csv", registry)
    _write(second, [headers, ["M1", "01:00:01:00", "A"]])
    _apply(second, tmp_path / "two.csv", registry)
    _write(third, [headers, ["M1", "01:00:01:00", "A"], ["", "01:10:00:00", "C"]])

    plan = prepare_identity_plan(third, chain_key="show|s01|e01|main|rus", registry_path=registry)

    assert plan.assignments == ["M1", "M3"]
    assert plan.new_ids == ["M3"]


def test_offline_new_marker_is_pending_and_visible_in_statistics(tmp_path):
    registry = tmp_path / "registry.json"
    source = tmp_path / "source.csv"
    _write(source, [["Timecode In", "Description"], ["01:00:01:00", "A"]])

    plan = _apply(source, tmp_path / "out.csv", registry, offline=True)
    state = load_registry(registry)["chains"]["show|s01|e01|main|rus"]
    stats = chain_statistics(registry)[0]

    assert plan.warning
    assert state["markers"]["M1"]["status"] == "pending"
    assert state["markers"]["M1"]["confirmed"] is False
    assert stats["pending"] == 1
    assert stats["health"] == "offline"


def test_merge_renumbers_only_unconfirmed_local_collision():
    local = {
        "chain_key": "show|s01|e01|main|rus",
        "revision": 2,
        "next_id": 2,
        "max_issued": 1,
        "markers": {"M1": {"id": "M1", "uid": "local", "status": "pending", "confirmed": False}},
        "versions": [],
    }
    remote = {
        "chain_key": "show|s01|e01|main|rus",
        "revision": 4,
        "next_id": 2,
        "max_issued": 1,
        "markers": {"M1": {"id": "M1", "uid": "remote", "status": "active", "confirmed": True}},
        "versions": [],
    }

    merged, conflicts, renumbered = merge_chain_states(local, remote)

    assert conflicts == []
    assert renumbered == {"M1": "M2"}
    assert set(merged["markers"]) == {"M1", "M2"}
    assert merged["next_id"] == 3


def test_merge_flags_collision_between_two_confirmed_identities():
    local = {"chain_key": "x", "markers": {"M1": {"uid": "a", "confirmed": True}}, "versions": []}
    remote = {"chain_key": "x", "markers": {"M1": {"uid": "b", "confirmed": True}}, "versions": []}

    _merged, conflicts, renumbered = merge_chain_states(local, remote)

    assert conflicts == ["M1"]
    assert renumbered == {}


def test_v2_registry_migrates_without_rewriting_legacy_file(tmp_path):
    legacy = tmp_path / "marker_identities.json"
    registry = tmp_path / "marker_registry_v3.json"
    payload = {
        "version": 2,
        "projects": {
            "show|s01|e01|main|rus": {
                "next_id": 5,
                "markers": [{"id": "M1", "tc_in": "01:00:01:00", "description": "A"}],
            }
        },
    }
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_legacy_registry(registry, legacy)

    chain = migrated["chains"]["show|s01|e01|main|rus"]
    assert chain["markers"]["M1"]["status"] == "active"
    assert chain["next_id"] == 5
    assert json.loads(legacy.read_text(encoding="utf-8")) == payload


def test_report_csv_history_recovers_deleted_and_restored_marker():
    class Client:
        def __init__(self):
            self.files = {
                "/reports/show/e01/v1/markers.csv": (
                    "ID,Timecode In,Description\nM1,01:00:01:00,A\nM2,01:00:02:00,B\n"
                ).encode(),
                "/reports/show/e01/v2/markers.csv": (
                    "ID,Timecode In,Description\nM1,01:00:01:00,A\n"
                ).encode(),
                "/reports/show/e01/v3/markers.csv": (
                    "ID,Timecode In,Description\nM1,01:00:01:00,A\nM2,01:00:02:00,B\n"
                ).encode(),
            }

        def listdir(self, path):
            if path == "/reports/show/e01":
                return [
                    {"name": "отчет_Show_s01_e01_2026_08_01_rus", "path": f"{path}/v1", "modified": "2026-08-01", "type": "dir"},
                    {"name": "отчет_Show_s01_e01_2026_08_02_rus", "path": f"{path}/v2", "modified": "2026-08-02", "type": "dir"},
                    {"name": "отчет_Show_s01_e01_2026_08_03_rus", "path": f"{path}/v3", "modified": "2026-08-03", "type": "dir"},
                    {"name": "отчет_Show_s01_e01_MnE_2026_08_03_rus", "path": f"{path}/me", "modified": "2026-08-03", "type": "dir"},
                ]
            return [{"name": "markers.csv", "path": f"{path}/markers.csv", "type": "file"}]

        def get_meta(self, path):
            return {"type": "dir"}

        def download_bytes(self, path):
            return self.files[path]

    chain = empty_chain("series|s01|e01|main|rus")

    recovered = recover_chain_from_reports(Client(), "/reports/show/e01", chain)

    assert recovered["markers"]["M2"]["status"] == "active"
    assert recovered["markers"]["M2"]["restored_count"] == 1
    assert len(recovered["versions"]) == 3
    assert recovered["next_id"] == 3
