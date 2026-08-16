"""Тесты обработки протухшего токена (401) в очереди автозагрузки.

401 от Диска — не временная ошибка: без нового токена любая попытка упадёт
так же, поэтому вместо обычного retry очередь должна встать на паузу
(сигнал auth_expired -> UI предлагает войти заново) и продолжиться с того
же job'а после смены токена.
"""

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

import src.yandex_ui.queue_manager as queue_manager_module
from src.report_filename import ReportMeta
from src.yandex_disk_client import YandexDiskError
from src.yandex_ui.queue_manager import YandexUploadQueueManager
from src.yandex_ui.threads import YandexDiskUploadThread


def _get_app():
    return QApplication.instance() or QApplication(sys.argv)


def _meta():
    return ReportMeta(series="Show", season=1, episode=2, date=date(2026, 6, 1), lang="rus")


# ---------------------------------------------------------------------------
# YandexDiskUploadThread: ветка 401


class _FakeClient:
    """Заглушка YandexDiskClient, падающая с заданной ошибкой на mkdir."""

    error = None

    def __init__(self, token, **kwargs):
        pass

    def mkdir(self, path):
        raise self.error


def _run_upload_thread_with_error(monkeypatch, error):
    """Прогоняет run() потока синхронно и собирает, какой сигнал он эмитнул."""
    monkeypatch.setattr("src.yandex_disk_client.YandexDiskClient", _FakeClient)
    _FakeClient.error = error

    thread = YandexDiskUploadThread("token", Path("/tmp/whatever"), target_folder_path="/remote/x")
    emitted = {}
    thread.auth_expired.connect(lambda message: emitted.setdefault("auth_expired", message))
    thread.network_unavailable.connect(lambda message: emitted.setdefault("network_unavailable", message))
    thread.finished_upload.connect(lambda success, message: emitted.setdefault("finished_upload", (success, message)))
    thread.needs_folder.connect(lambda message: emitted.setdefault("needs_folder", message))
    thread.run()  # синхронно, без start() — сигналы доставляются напрямую
    return emitted


def test_upload_thread_401_goes_to_auth_expired(monkeypatch):
    _get_app()
    emitted = _run_upload_thread_with_error(
        monkeypatch, YandexDiskError("401 Unauthorized", status_code=401, error_code="UnauthorizedError")
    )
    assert list(emitted) == ["auth_expired"]


def test_upload_thread_connection_failure_still_goes_to_network_unavailable(monkeypatch):
    _get_app()
    emitted = _run_upload_thread_with_error(
        monkeypatch, YandexDiskError("Не удалось подключиться")  # status_code=None
    )
    assert list(emitted) == ["network_unavailable"]


def test_upload_thread_other_api_errors_still_fail_normally(monkeypatch):
    _get_app()
    emitted = _run_upload_thread_with_error(
        monkeypatch, YandexDiskError("500 Internal", status_code=500)
    )
    success, _message = emitted["finished_upload"]
    assert success is False
    assert "auth_expired" not in emitted


# ---------------------------------------------------------------------------
# YandexUploadQueueManager: пауза до смены токена


class _FakeUploadThread(QObject):
    """Тот же набор сигналов, что у YandexDiskUploadThread, без сети."""

    finished_upload = pyqtSignal(bool, str)
    needs_folder = pyqtSignal(str)
    network_unavailable = pyqtSignal(str)
    auth_expired = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    instances = []

    def __init__(self, token, local_folder, **kwargs):
        super().__init__()
        self.token = token
        _FakeUploadThread.instances.append(self)

    def start(self):
        pass  # ничего не делаем — тест сам эмитит сигналы


def _make_manager(monkeypatch, tmp_path, tokens):
    monkeypatch.setattr(queue_manager_module, "YandexDiskUploadThread", _FakeUploadThread)
    _FakeUploadThread.instances.clear()
    manager = YandexUploadQueueManager(
        get_token=lambda: tokens["value"],
        schedule=lambda _ms, _fn: None,  # отложенные retry в тестах не нужны
        state_file=tmp_path / "queue.json",
    )
    return manager


def test_queue_pauses_on_401_and_resumes_after_relogin(monkeypatch, tmp_path):
    _get_app()
    tokens = {"value": "old-token"}
    manager = _make_manager(monkeypatch, tmp_path, tokens)
    signals = []
    manager.auth_expired.connect(signals.append)

    manager.enqueue(tmp_path, _meta())
    job = manager.queue.jobs[0]
    assert job.status == "uploading"
    assert len(_FakeUploadThread.instances) == 1

    manager._active_thread.auth_expired.emit("401 Unauthorized")

    # Job вернулся в очередь без потери попыток, очередь на паузе, сигнал UI.
    assert signals == ["401 Unauthorized"]
    assert job.status == "queued"
    assert job.attempts == 0
    assert manager._auth_expired is True

    # Пауза: пока токен тот же, новых попыток нет — даже при новых событиях.
    manager._process_next()
    manager.enqueue(tmp_path, _meta())
    assert len(_FakeUploadThread.instances) == 1

    # Пустой токен (пользователь просто вышел) — тоже не повод продолжать.
    tokens["value"] = ""
    manager._process_next()
    assert len(_FakeUploadThread.instances) == 1

    # Повторный вход: после смены токена resume запускает job заново.
    tokens["value"] = "new-token"
    manager.resume_after_relogin()
    assert manager._auth_expired is False
    assert len(_FakeUploadThread.instances) == 2
    assert _FakeUploadThread.instances[1].token == "new-token"
    assert job.status == "uploading"


def test_queue_auto_resumes_when_token_changed_elsewhere(monkeypatch, tmp_path):
    """Перелогин через настройки (не через наш диалог): следующий же

    _process_next (например, от enqueue) сам замечает новый токен."""
    _get_app()
    tokens = {"value": "old-token"}
    manager = _make_manager(monkeypatch, tmp_path, tokens)
    manager.auth_expired.connect(lambda _message: None)

    manager.enqueue(tmp_path, _meta())
    manager._active_thread.auth_expired.emit("401")
    assert manager._auth_expired is True

    tokens["value"] = "new-token"
    manager._process_next()  # без явного resume_after_relogin
    assert manager._auth_expired is False
    assert len(_FakeUploadThread.instances) == 2


def test_auth_expired_signal_fires_once_per_pause(monkeypatch, tmp_path):
    _get_app()
    tokens = {"value": "old-token"}
    manager = _make_manager(monkeypatch, tmp_path, tokens)
    signals = []
    manager.auth_expired.connect(signals.append)

    manager.enqueue(tmp_path, _meta())
    manager._active_thread.auth_expired.emit("401")
    # Повторное срабатывание (например, от второго потока до реакции UI)
    # не должно плодить диалоги повторного входа.
    manager._on_job_auth_expired(manager.queue.jobs[0], "401")
    assert signals == ["401"]
