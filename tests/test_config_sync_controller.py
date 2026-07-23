import json
from unittest.mock import MagicMock, patch

import pytest

import src.yandex_ui.config_sync_controller as controller_module
from src.yandex_disk_client import YandexDiskError
from src.yandex_ui.config_sync_controller import ConfigSyncController
from src.yandex_ui.threads import ConfigSyncThread


@pytest.fixture(autouse=True)
def _isolated_sync_state(tmp_path, monkeypatch):
    # Ни один тест не должен трогать реальный ~/.beast_auto_reporter/sync_state.json.
    monkeypatch.setattr(controller_module, "SYNC_STATE_FILE", tmp_path / "sync_state.json")


# ---------------------------------------------------------------------------
# ConfigSyncThread — распознавание исходов sync_all() в сигналы
# ---------------------------------------------------------------------------

def test_config_sync_thread_emits_resolved_on_success():
    thread = ConfigSyncThread("token")
    results = []
    thread.resolved.connect(lambda summary: results.append(summary))
    fake_summary = object()

    with patch("src.config_sync.sync_all", return_value=fake_summary):
        thread.run()  # синхронный вызов run() напрямую — без реального QThread

    assert results == [fake_summary]


def test_config_sync_thread_emits_network_unavailable_when_status_code_is_none():
    thread = ConfigSyncThread("token")
    results = []
    thread.network_unavailable.connect(lambda msg: results.append(msg))

    with patch("src.config_sync.sync_all", side_effect=YandexDiskError("не удалось подключиться")):
        thread.run()

    assert len(results) == 1


def test_config_sync_thread_emits_auth_expired_on_401():
    thread = ConfigSyncThread("token")
    results = []
    thread.auth_expired.connect(lambda msg: results.append(msg))

    with patch("src.config_sync.sync_all", side_effect=YandexDiskError("expired", status_code=401)):
        thread.run()

    assert len(results) == 1


def test_config_sync_thread_emits_failed_on_other_api_error():
    thread = ConfigSyncThread("token")
    results = []
    thread.failed.connect(lambda msg: results.append(msg))

    with patch("src.config_sync.sync_all", side_effect=YandexDiskError("boom", status_code=500)):
        thread.run()

    assert len(results) == 1


def test_config_sync_thread_emits_failed_on_generic_exception():
    thread = ConfigSyncThread("token")
    results = []
    thread.failed.connect(lambda msg: results.append(msg))

    with patch("src.config_sync.sync_all", side_effect=RuntimeError("bug")):
        thread.run()

    assert len(results) == 1


# ---------------------------------------------------------------------------
# ConfigSyncController — статус-текст, персист, guard-логика
# ---------------------------------------------------------------------------

def test_last_status_text_no_prior_state():
    controller = ConfigSyncController(get_token=lambda: "")
    assert controller.last_status_text() == "Ещё не синхронизировалось"


def test_last_status_text_reads_persisted_last_error(tmp_path):
    controller_module.SYNC_STATE_FILE.write_text(
        json.dumps({"last_synced_at": None, "last_error": "boom"}), encoding="utf-8"
    )
    controller = ConfigSyncController(get_token=lambda: "")
    assert "boom" in controller.last_status_text()


def test_sync_now_is_noop_without_token():
    controller = ConfigSyncController(get_token=lambda: "")
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))

    controller.sync_now()

    assert statuses == []
    assert controller._thread is None


def test_sync_now_starts_thread_when_token_present():
    controller = ConfigSyncController(get_token=lambda: "token")
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = False

    with patch("src.yandex_ui.config_sync_controller.ConfigSyncThread", return_value=fake_thread):
        controller.sync_now()

    assert statuses[-1].startswith("Синхронизируем")
    fake_thread.start.assert_called_once()


def test_sync_now_skips_second_run_while_one_in_flight():
    controller = ConfigSyncController(get_token=lambda: "token")
    running_thread = MagicMock()
    running_thread.isRunning.return_value = True
    controller._thread = running_thread
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))

    controller.sync_now()

    assert statuses == []


def test_sync_now_is_noop_after_stop_all():
    controller = ConfigSyncController(get_token=lambda: "token")
    controller.stop_all()
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))

    controller.sync_now()

    assert statuses == []


def test_stop_all_stops_timer():
    controller = ConfigSyncController(get_token=lambda: "token")
    controller.start_periodic(interval_ms=1000)

    controller.stop_all()

    assert controller._closing is True
    assert not controller._timer.isActive()


# ---------------------------------------------------------------------------
# Обработчики исходов синхронизации
# ---------------------------------------------------------------------------

def test_on_resolved_with_changes_updates_status_and_persists_state():
    controller = ConfigSyncController(get_token=lambda: "token")
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))
    summary = MagicMock(changed=True, conflicts=[])

    controller._on_resolved(summary)

    assert statuses[-1].startswith("Синхронизировано ✓")
    state = json.loads(controller_module.SYNC_STATE_FILE.read_text(encoding="utf-8"))
    assert state["last_synced_at"] is not None
    assert state["last_error"] is None


def test_on_resolved_without_changes_says_so():
    controller = ConfigSyncController(get_token=lambda: "token")
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))
    summary = MagicMock(changed=False, conflicts=[])

    controller._on_resolved(summary)

    assert "без изменений" in statuses[-1]


def test_on_resolved_with_conflicts_sends_notification():
    controller = ConfigSyncController(get_token=lambda: "token")
    summary = MagicMock(changed=True, conflicts=["series_aliases:show_a"])
    notified = []

    with patch("src.yandex_ui.config_sync_controller._send_system_notification",
               lambda title, message: notified.append((title, message))):
        controller._on_resolved(summary)

    assert len(notified) == 1
    assert "show_a" in notified[0][1]


def test_on_network_unavailable_sets_offline_status():
    controller = ConfigSyncController(get_token=lambda: "token")
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))

    controller._on_network_unavailable("не удалось подключиться")

    assert "нет сети" in statuses[-1]


def test_on_auth_expired_emits_signal_and_persists_error():
    controller = ConfigSyncController(get_token=lambda: "token")
    auth_events = []
    controller.auth_expired.connect(lambda m: auth_events.append(m))

    controller._on_auth_expired("token expired")

    assert auth_events == ["token expired"]
    state = json.loads(controller_module.SYNC_STATE_FILE.read_text(encoding="utf-8"))
    assert "истёк" in state["last_error"]


def test_on_failed_sets_error_status_and_persists():
    controller = ConfigSyncController(get_token=lambda: "token")
    statuses = []
    controller.status_changed.connect(lambda s: statuses.append(s))

    controller._on_failed("something broke")

    assert "Не удалось синхронизировать" in statuses[-1]
    state = json.loads(controller_module.SYNC_STATE_FILE.read_text(encoding="utf-8"))
    assert state["last_error"] == "something broke"


# ---------------------------------------------------------------------------
# _load_sync_state / _save_sync_state
# ---------------------------------------------------------------------------

def test_save_and_load_sync_state_round_trip():
    controller_module._save_sync_state("2026-07-23T10:00:00+00:00", None)
    assert controller_module._load_sync_state() == {
        "last_synced_at": "2026-07-23T10:00:00+00:00", "last_error": None,
    }


def test_load_sync_state_missing_file_returns_empty():
    assert controller_module._load_sync_state() == {}


def test_load_sync_state_corrupt_file_returns_empty():
    controller_module.SYNC_STATE_FILE.write_text("not json", encoding="utf-8")
    assert controller_module._load_sync_state() == {}
