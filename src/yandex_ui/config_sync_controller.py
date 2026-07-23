"""Qt-контроллер фоновой синхронизации локальных конфигов с Яндекс.Диском.

По структуре — упрощённый аналог YandexEditSyncController (см.
src/yandex_ui/edit_sync.py): та же роль (один статус-сигнал для UI,
системные уведомления, обработка оффлайна/просроченного токена), но без
слежения за файловой системой — здесь триггеры синхронизации это
периодический таймер, явный вызов ("Синхронизировать сейчас") и пул при
старте приложения, а не изменение конкретного локального файла.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from src.app_paths import CONFIG_DIR, atomic_write_text
from src.yandex_ui.helpers import _relative_time_label, _send_system_notification, _stop_thread
from src.yandex_ui.threads import ConfigSyncThread

logger = logging.getLogger(__name__)

SYNC_STATE_FILE = CONFIG_DIR / "sync_state.json"

DEFAULT_SYNC_INTERVAL_MS = 10 * 60 * 1000  # 10 минут


def _load_sync_state() -> dict:
    """{"last_synced_at": ISO-строка|None, "last_error": str|None} — только

    для отображения статуса между запусками приложения (до первой реальной
    попытки синка в новой сессии). Отсутствующий/битый файл — не ошибка.
    """
    if not SYNC_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Не удалось прочитать состояние синхронизации: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _save_sync_state(last_synced_at: str | None, last_error: str | None) -> None:
    try:
        atomic_write_text(
            SYNC_STATE_FILE,
            json.dumps({"last_synced_at": last_synced_at, "last_error": last_error}, ensure_ascii=False, indent=2),
        )
    except OSError as exc:
        logger.warning(f"Не удалось сохранить состояние синхронизации: {exc}")


class ConfigSyncController(QObject):
    """Синхронизирует алиасы/варианты/словарь спелчекера/реестр отчётов

    с Яндекс.Диском: при старте приложения (пул), периодически (таймер) и
    по явному запросу пользователя (кнопка "Синхронизировать сейчас").

    Сигналы:
        status_changed(str) — человекочитаемый статус для лейбла в футере.
        auth_expired(str) — токен истёк/отозван; UI должен подключить это
            к уже существующему флоу повторного входа, не дублировать его.
    """

    status_changed = pyqtSignal(str)
    auth_expired = pyqtSignal(str)

    def __init__(self, get_token, parent=None):
        """get_token: () -> str — текущий OAuth-токен (пустая строка, если не задан)."""
        super().__init__(parent)
        self._get_token = get_token
        self._thread: ConfigSyncThread | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.sync_now)
        self._closing = False

        state = _load_sync_state()
        self._last_synced_at = state.get("last_synced_at")
        self._last_error = state.get("last_error")

    def last_status_text(self) -> str:
        """Текст для лейбла до первой реальной попытки синка в этой сессии

        (например, сразу после запуска приложения, пока пул ещё не дошёл).
        """
        if self._last_error:
            return f"Не удалось синхронизировать: {self._last_error[:60]}"
        if self._last_synced_at:
            return f"Синхронизировано {_relative_time_label(self._last_synced_at)}"
        return "Ещё не синхронизировалось"

    def start_periodic(self, interval_ms: int = DEFAULT_SYNC_INTERVAL_MS) -> None:
        self._timer.start(interval_ms)

    def sync_now(self) -> None:
        if self._closing:
            return
        if self._thread is not None and self._thread.isRunning():
            return  # синхронизация уже идёт — не запускаем вторую поверх
        token = self._get_token()
        if not token:
            return

        self.status_changed.emit("Синхронизируем конфиги с Яндекс.Диском…")
        thread = ConfigSyncThread(token)
        thread.resolved.connect(self._on_resolved)
        thread.network_unavailable.connect(self._on_network_unavailable)
        thread.auth_expired.connect(self._on_auth_expired)
        thread.failed.connect(self._on_failed)
        self._thread = thread
        thread.start()

    def _on_resolved(self, summary) -> None:
        self._last_synced_at = datetime.now(timezone.utc).isoformat()
        self._last_error = None
        _save_sync_state(self._last_synced_at, None)

        if summary.changed:
            self.status_changed.emit(f"Синхронизировано ✓ {datetime.now().strftime('%H:%M')}")
        else:
            self.status_changed.emit(f"Синхронизировано, без изменений · {datetime.now().strftime('%H:%M')}")

        if summary.conflicts:
            shown = ", ".join(summary.conflicts[:5])
            _send_system_notification(
                "Конфликт синхронизации",
                f"{len(summary.conflicts)} значение(й) отличались на разных машинах — оставлено локальное: {shown}",
            )

    def _on_network_unavailable(self, message: str) -> None:
        self.status_changed.emit("Не удалось синхронизироваться — нет сети")

    def _on_auth_expired(self, message: str) -> None:
        self._last_error = "истёк токен Яндекс.Диска"
        _save_sync_state(self._last_synced_at, self._last_error)
        self.status_changed.emit("Синхронизация приостановлена — нужен повторный вход в Яндекс.Диск")
        self.auth_expired.emit(message)

    def _on_failed(self, message: str) -> None:
        self._last_error = message
        _save_sync_state(self._last_synced_at, message)
        self.status_changed.emit(f"Не удалось синхронизировать: {message[:60]}")

    def stop_all(self) -> None:
        self._closing = True
        self._timer.stop()
        _stop_thread(self._thread)
