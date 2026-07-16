"""Очередь автозагрузки отчётов на Яндекс.Диск."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from src.app_paths import CONFIG_DIR, migrate_legacy_config_file
from src.report_uploader import (
    REPORTS_ROOT, UploadQueue, fallback_series_key, load_queue_state, remember_series_alias,
    remember_uploaded_report, resolve_manual_pick_target, save_queue_state,
)
from src.yandex_ui.threads import YandexDiskUploadThread, _FallbackFolderFindThread


class YandexUploadQueueManager(QObject):
    """Ведёт очередь автозагрузки отчётов на Яндекс.Диск: один активный

    аплоад за раз, с автоматическим повтором при временных ошибках
    (см. UploadQueue.mark_failed_or_retry) и без всплывающих диалогов —
    если папка сериала не найдена, job просто ждёт в очереди со статусом
    "needs_folder" до ручного решения через YandexUploadQueueDialog.
    """

    queue_changed = pyqtSignal()
    job_needs_attention = pyqtSignal()
    job_uploaded = pyqtSignal(str, str)  # (local_folder, remote_folder_path)
    queue_paused_offline = pyqtSignal(bool)

    STATE_FILE = CONFIG_DIR / "yandex_queue.json"
    _LEGACY_STATE_FILE = Path.home() / ".beast_auto_reporter_yandex_queue.json"
    OFFLINE_RETRY_DELAY_MS = 20000

    def __init__(self, get_token, get_roots=None, schedule=None, parent=None, state_file: Path = None):
        super().__init__(parent)
        self._get_token = get_token
        self._get_roots = get_roots or (lambda: [REPORTS_ROOT])
        self._schedule = schedule or (lambda delay_ms, fn: QTimer.singleShot(delay_ms, fn))
        self._state_file = state_file or self.STATE_FILE
        if self._state_file == self.STATE_FILE:
            # Миграция только для реального пути по умолчанию — не трогаем
            # произвольные пути, которые передают тесты (tmp_path и т.п.).
            migrate_legacy_config_file(self._LEGACY_STATE_FILE, self._state_file)
        self.queue = load_queue_state(self._state_file)
        self._active_thread = None
        self._offline = False
        self._offline_check_scheduled = False
        self._closing = False
        self.queue_changed.connect(self._persist_queue)
        # Восстановленные после перезапуска job'ы (queued/needs_folder) —
        # продолжаем обработку сразу, если токен уже есть.
        self._process_next()

    def _persist_queue(self) -> None:
        save_queue_state(self.queue, self._state_file)

    def shutdown(self) -> None:
        """Останавливает менеджер перед закрытием приложения.

        Ставит флаг _closing ДО остановки потоков: пока _stop_thread ждёт
        активный поток, тот успевает эмитнуть финальный сигнал (queued),
        и его обработчик исполнится уже после closeEvent — без флага он
        запустил бы новый поток (_process_next/upload_to_folder), который
        никто не остановит, и при teardown интерпретатора процесс упал бы
        с "QThread: Destroyed while thread is still running". То же с
        отложенными retry-колбэками (_schedule).
        """
        from src.yandex_ui.helpers import _stop_thread

        self._closing = True
        _stop_thread(self._active_thread)
        for thread in list(getattr(self, "_manual_threads", {}).values()):
            _stop_thread(thread)
        for thread in list(getattr(self, "_fallback_find_threads", {}).values()):
            _stop_thread(thread)

    def enqueue(self, local_folder: Path, meta) -> None:
        self.queue.enqueue(local_folder, meta)
        self.queue_changed.emit()
        self._process_next()

    def retry(self, job) -> None:
        self.queue.retry(job)
        self.queue_changed.emit()
        self._process_next()

    def _process_next(self) -> None:
        if self._closing:
            return
        if self.queue.is_uploading():
            return
        job = self.queue.next_queued()
        if job is None:
            return

        if job.meta is None:
            # Имя файла не распознано (нет sNN/eNN-меток) — прежде чем
            # требовать ручного решения, проверяем алиас/нечёткое совпадение
            # по имени файла отчёта (см. fallback_series_key) — так job
            # того же ролика, что уже отправляли раньше, не будет каждый
            # раз заново упираться в needs_folder.
            self._try_fallback_alias_for_job(job)
            return

        token = self._get_token()
        if not token:
            self.queue.mark_needs_folder(job)
            self.queue_changed.emit()
            self.job_needs_attention.emit()
            self._process_next()
            return

        self.queue.mark_uploading(job)
        self.queue_changed.emit()

        self._active_thread = YandexDiskUploadThread(
            token, job.local_folder, meta=job.meta, create_if_missing=False, series_roots=self._get_roots(),
        )
        self._active_thread.finished_upload.connect(lambda success, message: self._on_job_finished(job, success, message))
        self._active_thread.needs_folder.connect(lambda message: self._on_job_needs_folder(job, message))
        self._active_thread.network_unavailable.connect(lambda message: self._on_job_network_unavailable(job, message))
        self._active_thread.start()

    def _try_fallback_alias_for_job(self, job) -> None:
        token = self._get_token()
        docx_candidates = sorted(job.local_folder.glob("отчет_*.docx")) if job.local_folder.is_dir() else []
        if not token or not docx_candidates:
            self.queue.mark_needs_folder(job)
            self.queue_changed.emit()
            self.job_needs_attention.emit()
            self._process_next()
            return

        # "uploading" здесь не совсем точно (идёт только поиск папки, не
        # сама загрузка), но так же, как и для обычной загрузки, исключает
        # job из next_queued()/блокирует is_uploading() на время проверки —
        # без этого повторный _process_next() запустил бы вторую такую же
        # проверку для того же job параллельно.
        self.queue.mark_uploading(job)
        self.queue_changed.emit()

        fallback_key = fallback_series_key(docx_candidates[0].name)
        self._fallback_find_threads = getattr(self, "_fallback_find_threads", {})
        thread = _FallbackFolderFindThread(token, fallback_key, series_roots=self._get_roots())
        thread.resolved.connect(lambda path: self._on_fallback_alias_resolved(job, path))
        thread.failed.connect(lambda _message: self._on_fallback_alias_resolved(job, ""))
        self._fallback_find_threads[job.id] = thread
        thread.start()

    def _on_fallback_alias_resolved(self, job, episode_path: str) -> None:
        self._fallback_find_threads.pop(job.id, None)
        if not episode_path:
            self.queue.mark_needs_folder(job)
            self.queue_changed.emit()
            self.job_needs_attention.emit()
            self._process_next()
            return
        self.upload_to_folder(job, episode_path)

    def _on_job_network_unavailable(self, job, message: str) -> None:
        # Нет связи с Диском вообще — не тратим на это retry-попытки job'а
        # (mark_failed_or_retry), просто ждём восстановления сети и пробуем
        # снова тот же job без изменения его статуса/счётчика попыток.
        # queue.retry() возвращает job в "queued" (иначе он навсегда
        # останется "uploading", и is_uploading() заблокирует всю очередь).
        self.queue.retry(job)
        self.queue_changed.emit()
        if not self._offline:
            self._offline = True
            self.queue_paused_offline.emit(True)
        if not self._offline_check_scheduled:
            self._offline_check_scheduled = True
            self._schedule(self.OFFLINE_RETRY_DELAY_MS, self._retry_after_offline)

    def _retry_after_offline(self) -> None:
        if self._closing:
            return
        self._offline_check_scheduled = False
        # self._offline остаётся True до первого реально успешного job'а
        # (_on_job_finished) — иначе пришлось бы синхронно гадать об исходе
        # ещё не начавшейся асинхронной попытки, что даёт ложное "снова
        # онлайн" на долю секунды перед повторным падением в офлайн.
        self._process_next()

    def _on_job_needs_folder(self, job, message: str) -> None:
        # Папка сериала не найдена и не должна создаваться сама по себе —
        # не ошибка сети, а ситуация "нужно решение человека".
        self.queue.mark_needs_folder(job)
        job.error = message  # mark_needs_folder сбрасывает error — выставляем после
        self.queue_changed.emit()
        self.job_needs_attention.emit()
        self._process_next()

    def _on_job_finished(self, job, success: bool, message: str) -> None:
        if success:
            job.target_folder_path = message
            self.queue.mark_done(job)
            self.queue_changed.emit()
            self.job_uploaded.emit(str(job.local_folder), message)
            remember_uploaded_report(str(job.local_folder), message)
            if self._offline:
                self._offline = False
                self.queue_paused_offline.emit(False)
            self._process_next()
            return

        delay_ms = self.queue.mark_failed_or_retry(job, message)
        self.queue_changed.emit()
        if delay_ms is not None:
            self._schedule(delay_ms, self._process_next)
        else:
            self.job_needs_attention.emit()
            self._process_next()

    def upload_to_folder(self, job, target_folder_path: str) -> None:
        """Ручная отправка job'а в выбранную пользователем папку — для случая,

        когда имя файла не распознано и автоматически определить папку
        нельзя (job застрял в "needs_folder"). Идёт отдельным потоком от
        обычной последовательной обработки очереди, чтобы не мешать ей.

        Папку эпизода создаёт сам YandexDiskUploadThread (mkdir раньше
        выполнялся здесь синхронно и замораживал GUI-поток на медленной
        сети); его ошибка приходит через finished_upload(False, ...) и,
        как и раньше, возвращает job в "needs_folder" с текстом ошибки.
        """
        if self._closing:
            return
        token = self._get_token()
        if not token:
            return

        episode_path, series_path = resolve_manual_pick_target(target_folder_path, job.meta)

        if job.meta is not None:
            remember_series_alias(job.meta.series, series_path)
        else:
            docx_candidates = sorted(job.local_folder.glob("отчет_*.docx")) if job.local_folder.is_dir() else []
            if docx_candidates:
                remember_series_alias(fallback_series_key(docx_candidates[0].name), series_path)

        self.queue.mark_uploading(job)
        self.queue_changed.emit()
        self._manual_threads = getattr(self, "_manual_threads", {})
        thread = YandexDiskUploadThread(token, job.local_folder, target_folder_path=episode_path)
        thread.finished_upload.connect(
            lambda success, message: self._on_manual_upload_finished(job, success, message)
        )
        self._manual_threads[job.id] = thread
        thread.start()

    def _on_manual_upload_finished(self, job, success: bool, message: str) -> None:
        self._manual_threads.pop(job.id, None)
        if success:
            job.target_folder_path = message
            self.queue.mark_done(job)
            self.queue_changed.emit()
            self.job_uploaded.emit(str(job.local_folder), message)
            remember_uploaded_report(str(job.local_folder), message)
        else:
            self.queue.mark_needs_folder(job)
            job.error = message  # mark_needs_folder сбрасывает error — выставляем после
            self.queue_changed.emit()


