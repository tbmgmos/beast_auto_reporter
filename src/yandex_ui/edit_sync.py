"""Отслеживание локальных файлов на изменение + retry-политика для

автосинхронизации правок с Яндекс.Диском при сохранении.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from src.report_uploader import UploadQueue
from src.yandex_ui.helpers import _send_system_notification, _stop_thread
from src.yandex_ui.threads import YandexDiskSyncUploadThread


class EditSyncWatcher(QObject):
    """Следит за локальным файлом (открытым во внешнем редакторе) и, после

    паузы без новых изменений (debounce), один раз сигнализирует, что файл
    пора синхронизировать. Редакторы вроде Word/Pages сохраняют атомарно
    (удаляют и пересоздают файл), из-за чего Qt снимает путь с наблюдения —
    поэтому в обработчике переподписываемся, если путь исчез из списка.
    """

    changed = pyqtSignal(str)  # local_path

    def __init__(self, debounce_ms: int = 2000, parent=None):
        super().__init__(parent)
        self.debounce_ms = debounce_ms
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._timers = {}  # path -> QTimer
        self._watched_path = None

    def watch(self, path: str):
        """Следить за одним файлом (сбрасывает все предыдущие подписки)."""
        self.unwatch_all()
        self._watched_path = path
        if os.path.exists(path):
            self._watcher.addPath(path)

    def watch_many(self, paths) -> None:
        """Следить сразу за несколькими файлами (сбрасывает предыдущие подписки) —

        например, за всеми файлами папки готового отчёта, чтобы заметить
        замену любого из них (не только того, что открыт «на Правку»).
        """
        self.unwatch_all()
        for path in paths:
            if os.path.exists(path):
                self._watcher.addPath(path)

    def unwatch_all(self):
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()
        self._watched_path = None

    def _on_file_changed(self, path: str):
        if path not in self._watcher.files() and os.path.exists(path):
            self._watcher.addPath(path)

        timer = self._timers.get(path)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=path: self.changed.emit(p))
            self._timers[path] = timer
        timer.start(self.debounce_ms)


def _next_edit_sync_retry_delay_ms(attempts: int) -> int | None:
    """Задержка перед следующей попыткой синхронизации правок (в мс) после

    attempts неудачных попыток подряд, либо None — попытки исчерпаны.
    Переиспользует тайминги UploadQueue (очередь автозагрузки), чтобы не
    заводить отдельную копию retry-политики для другого сценария.
    """
    if attempts >= UploadQueue.MAX_ATTEMPTS:
        return None
    return UploadQueue.RETRY_DELAYS_MS[min(attempts - 1, len(UploadQueue.RETRY_DELAYS_MS) - 1)]


class YandexEditSyncController(QObject):
    """Общая логика автосинхронизации правок при сохранении файла —

    раньше независимо дублировалась в BeastApp (следит за всей папкой
    готового отчёта, несколько файлов сразу) и в YandexDiskBrowserDialog
    (следит за одним открытым «на Правку» файлом). Инкапсулирует
    EditSyncWatcher, догрузку по сохранению, retry с бэкоффом
    (_next_edit_sync_retry_delay_ms) и обработку конфликтов версий.

    Различие между вызывающими классами — только в том, ОТКУДА берётся
    remote_path для локального файла (в браузере Диска он известен заранее
    по факту скачивания конкретного файла; в главном окне вычисляется на
    лету из папки, куда был отправлен весь отчёт) — это внешний callback
    resolve_remote_path, а не дублирование логики синхронизации.

    Сигналы:
        status_changed(str) — человекочитаемый статус для строки в UI.
        conflict(str, str) — (local_path, actual_modified_on_disk); UI сам
            решает, как спросить пользователя (свой QMessageBox) и должен
            вызвать resolve_conflict(local_path, overwrite=...) с ответом.
        synced(str, str) — (local_path, remote_path) при успешной синхронизации,
            для тех вызывающих классов, которым нужно отреагировать (записать
            новый remote_path и т.п.), а не только показать статус.
    """

    status_changed = pyqtSignal(str)
    conflict = pyqtSignal(str, str)
    synced = pyqtSignal(str, str)

    def __init__(self, get_token, resolve_remote_path, parent=None):
        """
        get_token: () -> str — текущий OAuth-токен (пустая строка, если не задан).
        resolve_remote_path: (local_path: str) -> str | None — путь файла на
            Диске для данного локального файла, либо None, если синхронизировать
            пока некуда (например, отчёт ещё не отправлялся на Диск).
        """
        super().__init__(parent)
        self._get_token = get_token
        self._resolve_remote_path = resolve_remote_path
        self.watcher = EditSyncWatcher(parent=self)
        self.watcher.changed.connect(self._on_file_changed)
        self._threads = {}  # local_path (str) -> YandexDiskSyncUploadThread
        self._attempts = {}  # local_path (str) -> число неудачных попыток подряд
        self._known_modified = {}  # remote_path (str) -> известный modified
        self._closing = False

    def watch(self, path: str) -> None:
        self.watcher.watch(path)

    def watch_many(self, paths) -> None:
        self.watcher.watch_many(paths)

    def unwatch_all(self) -> None:
        self.watcher.unwatch_all()

    def note_known_modified(self, remote_path: str, modified: str | None) -> None:
        """Запоминает modified для remote_path заранее (например, сразу

        после скачивания файла для редактирования) — используется как
        отправная точка для защиты от конфликтов при первом сохранении.
        """
        if modified is not None:
            self._known_modified[remote_path] = modified

    def _on_file_changed(self, path: str, force: bool = False) -> None:
        if self._closing:
            # stop_all() уже вызван (окно закрывается) — сюда мог долететь
            # отложенный retry-таймер из _on_synced; новый поток в умирающем
            # приложении никто не остановит.
            return
        remote_path = self._resolve_remote_path(path)
        if not remote_path:
            self.status_changed.emit("Изменения сохранены локально — отчёт ещё не отправлялся на Диск")
            return

        token = self._get_token()
        if not token:
            return

        self.status_changed.emit(f"Синхронизируем {Path(path).name} с Яндекс.Диском…")
        thread = YandexDiskSyncUploadThread(
            token, Path(path), remote_path,
            expected_modified=self._known_modified.get(remote_path), force=force,
        )
        thread.finished_sync.connect(lambda success, message: self._on_synced(path, success, message))
        thread.conflict_detected.connect(lambda rpath, actual_modified: self._on_conflict(path, actual_modified))
        thread.progress.connect(lambda sent, total: self._on_progress(path, sent, total))
        self._threads[path] = thread
        thread.start()

    def _on_progress(self, path: str, sent: int, total: int) -> None:
        percent = int(sent * 100 / total) if total else 0
        self.status_changed.emit(f"Синхронизируем {Path(path).name} с Яндекс.Диском… {percent}%")

    def _on_synced(self, path: str, success: bool, message: str) -> None:
        name = Path(path).name
        thread = self._threads.pop(path, None)
        if success:
            self._attempts.pop(path, None)
            if thread is not None:
                self._known_modified[message] = thread.new_modified
            self.status_changed.emit(f"Обновлено на Диске ✓ {datetime.now().strftime('%H:%M')}")
            _send_system_notification("Отчёт синхронизирован", f"{name} обновлён на Яндекс.Диске")
            self.synced.emit(path, message)
            return

        attempts = self._attempts.get(path, 0) + 1
        self._attempts[path] = attempts
        delay_ms = _next_edit_sync_retry_delay_ms(attempts)
        if delay_ms is not None:
            self.status_changed.emit(f"Не удалось синхронизировать, повтор через {delay_ms // 1000} с…")
            QTimer.singleShot(delay_ms, lambda: self._on_file_changed(path))
        else:
            self._attempts.pop(path, None)
            self.status_changed.emit(f"Не удалось синхронизировать: {message[:60]}")
            _send_system_notification("Ошибка синхронизации", f"{name}: {message[:120]}")

    def _on_conflict(self, path: str, actual_modified: str) -> None:
        self.status_changed.emit("Конфликт: файл на Диске изменён другим человеком")
        _send_system_notification("Конфликт версий", f"{Path(path).name}: файл на Диске изменён — нужно решение")
        self.conflict.emit(path, actual_modified)

    def resolve_conflict(self, path: str, overwrite: bool) -> None:
        """Вызывается UI после того, как пользователь ответил на сигнал conflict."""
        if overwrite:
            self._on_file_changed(path, force=True)
        else:
            self.status_changed.emit("Правки НЕ отправлены — разрешите конфликт вручную")

    def stop_all(self) -> None:
        self._closing = True
        for thread in list(self._threads.values()):
            _stop_thread(thread)
