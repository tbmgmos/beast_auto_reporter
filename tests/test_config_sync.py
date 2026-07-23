from unittest.mock import MagicMock

import pytest

import src.config_sync as cs
from src.yandex_disk_client import YandexDiskError


# ---------------------------------------------------------------------------
# merge_dicts — чистая функция, все ветки 3-way merge
# ---------------------------------------------------------------------------

def test_merge_dicts_no_changes_keeps_value():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "1"}, {"a": "1"})
    assert merged == {"a": "1"}
    assert conflicts == []


def test_merge_dicts_only_local_changed_wins():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "2"}, {"a": "1"})
    assert merged == {"a": "2"}
    assert conflicts == []


def test_merge_dicts_only_remote_changed_wins():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "1"}, {"a": "2"})
    assert merged == {"a": "2"}
    assert conflicts == []


def test_merge_dicts_only_local_added_key():
    merged, conflicts = cs.merge_dicts({}, {"a": "1"}, {})
    assert merged == {"a": "1"}
    assert conflicts == []


def test_merge_dicts_only_remote_added_key():
    merged, conflicts = cs.merge_dicts({}, {}, {"a": "1"})
    assert merged == {"a": "1"}
    assert conflicts == []


def test_merge_dicts_only_local_deleted_key():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {}, {"a": "1"})
    assert merged == {}
    assert conflicts == []


def test_merge_dicts_only_remote_deleted_key():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "1"}, {})
    assert merged == {}
    assert conflicts == []


def test_merge_dicts_both_deleted_same_key_not_a_conflict():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {}, {})
    assert merged == {}
    assert conflicts == []


def test_merge_dicts_both_changed_to_same_value_not_a_conflict():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "2"}, {"a": "2"})
    assert merged == {"a": "2"}
    assert conflicts == []


def test_merge_dicts_both_changed_differently_local_wins_and_flags_conflict():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "local"}, {"a": "remote"})
    assert merged == {"a": "local"}
    assert conflicts == ["a"]


def test_merge_dicts_local_deleted_remote_edited_is_conflict_deletion_wins():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {}, {"a": "remote"})
    assert merged == {}
    assert conflicts == ["a"]


def test_merge_dicts_local_edited_remote_deleted_is_conflict_local_wins():
    merged, conflicts = cs.merge_dicts({"a": "1"}, {"a": "local"}, {})
    assert merged == {"a": "local"}
    assert conflicts == ["a"]


def test_merge_dicts_new_key_added_differently_on_both_sides_is_conflict():
    merged, conflicts = cs.merge_dicts({}, {"a": "local"}, {"a": "remote"})
    assert merged == {"a": "local"}
    assert conflicts == ["a"]


def test_merge_dicts_empty_base_adopts_all_remote_entries():
    # Новая машина: base ещё не было, локально пусто -> подтягивает всё,
    # что накопили другие машины.
    merged, conflicts = cs.merge_dicts({}, {}, {"show_a": "/отчеты/Show A", "show_b": "/отчеты/Show B"})
    assert merged == {"show_a": "/отчеты/Show A", "show_b": "/отчеты/Show B"}
    assert conflicts == []


def test_merge_dicts_untouched_keys_pass_through_multiple():
    merged, conflicts = cs.merge_dicts(
        {"a": "1", "b": "2", "c": "3"},
        {"a": "1", "b": "local", "c": "3"},
        {"a": "1", "b": "2", "c": "remote"},
    )
    assert merged == {"a": "1", "b": "local", "c": "remote"}
    assert conflicts == []


# ---------------------------------------------------------------------------
# base snapshot round-trip
# ---------------------------------------------------------------------------

def test_base_snapshot_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    assert cs.load_base_snapshot("series_aliases") == {}

    cs.save_base_snapshot("series_aliases", {"a": "1"})
    assert cs.load_base_snapshot("series_aliases") == {"a": "1"}


def test_base_snapshot_corrupt_file_is_tolerated(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    path = cs._base_snapshot_path("series_aliases")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")

    assert cs.load_base_snapshot("series_aliases") == {}


# ---------------------------------------------------------------------------
# sync_dict_config
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self, data):
        self.data = dict(data)
        self.saved_with = None

    def load(self):
        return dict(self.data)

    def save(self, data):
        self.saved_with = dict(data)
        self.data = dict(data)


def _config(store, name="test_config"):
    return cs.SyncableDictConfig(name, store.load, store.save, f"{name}.json")


def test_sync_dict_config_first_sync_no_remote_file_uploads_local(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    store = _FakeStore({"a": "1"})
    client = MagicMock()
    client.download_bytes.side_effect = YandexDiskError("not found", status_code=404)

    result = cs.sync_dict_config(client, _config(store))

    assert result.changed is True
    assert result.conflicts == []
    assert store.saved_with is None  # merged == local, no local rewrite needed
    client.upload_bytes.assert_called_once()
    uploaded_bytes, remote_path = client.upload_bytes.call_args[0]
    assert remote_path == f"{cs.SYNC_REMOTE_ROOT}/test_config.json"
    assert b'"a"' in uploaded_bytes
    assert cs.load_base_snapshot("test_config") == {"a": "1"}


def test_sync_dict_config_merges_remote_additions_into_local(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    store = _FakeStore({"a": "1"})
    client = MagicMock()
    client.download_bytes.return_value = b'{"b": "2"}'

    result = cs.sync_dict_config(client, _config(store))

    assert result.changed is True
    assert store.saved_with == {"a": "1", "b": "2"}
    client.upload_bytes.assert_called_once()


def test_sync_dict_config_reraises_non_404_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    store = _FakeStore({"a": "1"})
    client = MagicMock()
    client.download_bytes.side_effect = YandexDiskError("auth expired", status_code=401)

    with pytest.raises(YandexDiskError):
        cs.sync_dict_config(client, _config(store))


def test_sync_dict_config_conflict_local_wins_and_overwrites_remote(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    cs.save_base_snapshot("test_config", {"a": "old"})
    store = _FakeStore({"a": "local_new"})
    client = MagicMock()
    client.download_bytes.return_value = b'{"a": "remote_new"}'

    result = cs.sync_dict_config(client, _config(store))

    assert result.conflicts == ["a"]
    assert store.saved_with is None  # merged == local already, no rewrite needed
    client.upload_bytes.assert_called_once()
    uploaded_bytes, _ = client.upload_bytes.call_args[0]
    assert b"local_new" in uploaded_bytes


def test_sync_dict_config_nothing_changed_does_not_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    store = _FakeStore({"a": "1"})
    client = MagicMock()
    client.download_bytes.return_value = b'{"a": "1"}'

    result = cs.sync_dict_config(client, _config(store))

    assert result.changed is False
    assert store.saved_with is None
    client.upload_bytes.assert_not_called()


# ---------------------------------------------------------------------------
# sync_uploaded_reports
# ---------------------------------------------------------------------------

def test_sync_uploaded_reports_no_remote_file_uploads_local_without_local_folder(monkeypatch):
    local_entries = [{"local_folder": "/Users/me/report", "remote_path": "/отчеты/Show/e01", "uploaded_at": "2026-07-20T10:00:00+00:00"}]
    monkeypatch.setattr(cs, "load_uploaded_reports", lambda: local_entries)
    saved = []
    monkeypatch.setattr(cs, "save_uploaded_reports", lambda entries: saved.append(entries))
    client = MagicMock()
    client.download_bytes.side_effect = YandexDiskError("not found", status_code=404)

    changed = cs.sync_uploaded_reports(client)

    assert changed is False  # merged == local_entries, локально ничего не меняем
    assert saved == []
    client.upload_bytes.assert_called_once()
    uploaded_bytes, _ = client.upload_bytes.call_args[0]
    import json as _json
    payload = _json.loads(uploaded_bytes.decode("utf-8"))
    assert payload == [{"remote_path": "/отчеты/Show/e01", "uploaded_at": "2026-07-20T10:00:00+00:00"}]


def test_sync_uploaded_reports_adopts_foreign_entry_without_local_folder(monkeypatch):
    local_entries = [{"local_folder": "/Users/me/a", "remote_path": "/отчеты/A/e01", "uploaded_at": "2026-07-20T10:00:00+00:00"}]
    remote_entries = [{"remote_path": "/отчеты/B/e02", "uploaded_at": "2026-07-21T10:00:00+00:00"}]
    monkeypatch.setattr(cs, "load_uploaded_reports", lambda: local_entries)
    saved = []
    monkeypatch.setattr(cs, "save_uploaded_reports", lambda entries: saved.append(entries))
    client = MagicMock()
    import json
    client.download_bytes.return_value = json.dumps(remote_entries).encode("utf-8")

    changed = cs.sync_uploaded_reports(client)

    assert changed is True
    assert len(saved) == 1
    by_path = {e["remote_path"]: e for e in saved[0]}
    assert by_path["/отчеты/A/e01"]["local_folder"] == "/Users/me/a"
    assert "local_folder" not in by_path["/отчеты/B/e02"]


def test_sync_uploaded_reports_dedupes_by_remote_path_keeping_newer_and_own_local_folder(monkeypatch):
    local_entries = [{"local_folder": "/Users/me/a", "remote_path": "/отчеты/A/e01", "uploaded_at": "2026-07-20T10:00:00+00:00"}]
    remote_entries = [{"remote_path": "/отчеты/A/e01", "uploaded_at": "2026-07-22T10:00:00+00:00"}]
    monkeypatch.setattr(cs, "load_uploaded_reports", lambda: local_entries)
    saved = []
    monkeypatch.setattr(cs, "save_uploaded_reports", lambda entries: saved.append(entries))
    client = MagicMock()
    import json
    client.download_bytes.return_value = json.dumps(remote_entries).encode("utf-8")

    changed = cs.sync_uploaded_reports(client)

    assert changed is True
    assert len(saved[0]) == 1
    entry = saved[0][0]
    assert entry["uploaded_at"] == "2026-07-22T10:00:00+00:00"  # более новая запись выигрывает
    assert entry["local_folder"] == "/Users/me/a"  # но local_folder свой сохраняется


def test_sync_uploaded_reports_caps_at_max_entries(monkeypatch):
    monkeypatch.setattr(cs, "_SHARED_UPLOADED_REPORTS_MAX", 2)
    local_entries = [
        {"remote_path": "/a", "uploaded_at": "2026-07-20T10:00:00+00:00"},
        {"remote_path": "/b", "uploaded_at": "2026-07-21T10:00:00+00:00"},
        {"remote_path": "/c", "uploaded_at": "2026-07-22T10:00:00+00:00"},
    ]
    monkeypatch.setattr(cs, "load_uploaded_reports", lambda: local_entries)
    saved = []
    monkeypatch.setattr(cs, "save_uploaded_reports", lambda entries: saved.append(entries))
    client = MagicMock()
    client.download_bytes.side_effect = YandexDiskError("not found", status_code=404)

    cs.sync_uploaded_reports(client)

    assert len(saved[0]) == 2
    assert [e["remote_path"] for e in saved[0]] == ["/c", "/b"]


# ---------------------------------------------------------------------------
# sync_all
# ---------------------------------------------------------------------------

def test_sync_all_creates_remote_root_and_aggregates_conflicts(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_DIR", tmp_path)
    store_a = _FakeStore({"a": "local"})
    store_b = _FakeStore({})
    monkeypatch.setattr(cs, "SYNCABLE_DICT_CONFIGS", [_config(store_a, "cfg_a"), _config(store_b, "cfg_b")])
    monkeypatch.setattr(cs, "load_uploaded_reports", lambda: [])
    monkeypatch.setattr(cs, "save_uploaded_reports", lambda entries: None)

    client = MagicMock()

    def fake_download(remote_path):
        if remote_path.endswith("cfg_a.json"):
            return b'{"a": "remote"}'
        raise YandexDiskError("not found", status_code=404)

    client.download_bytes.side_effect = fake_download

    summary = cs.sync_all(client)

    client.mkdir.assert_any_call(cs.SYNC_REMOTE_PARENT)
    client.mkdir.assert_any_call(cs.SYNC_REMOTE_ROOT)
    assert summary.conflicts == ["cfg_a:a"]
    assert summary.changed is True
    assert {r.name for r in summary.dict_results} == {"cfg_a", "cfg_b"}
