"""Фоновые QThread-классы для операций с Яндекс.Диском."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from src.yandex_ui.helpers import _format_disk_file_size

logger = logging.getLogger(__name__)

# Сентинел "текущий, ещё не отправленный черновик" — используется и здесь
# (YandexDiskCompareThread), и в YandexVersionPickerDialog (src/yandex_ui/
# dialogs.py, которая присваивает свой CURRENT_DRAFT = этому же значению) —
# вынесен сюда как общий модуль, чтобы threads.py не импортировал dialogs.py
# (dialogs.py и так импортирует threads.py — цикл иначе неизбежен).
CURRENT_DRAFT = "__current_draft__"


class _YandexWorkerThread(QThread):
    """Общий каркас для фоновых операций с Яндекс.Диском.

    run() вызывает self._work() и одинаково ловит ошибки, диспатчя
    результат через _emit_success/_emit_failure. Конкретные сигналы (их
    имена и форма — где-то один "resolved"+"failed", где-то один сигнал
    "finished_X(bool, str)") остаются за подклассом — это только убирает
    дублирование try/except/логирования, не трогая уже написанный в
    остальном приложении код подписки на сигналы.
    Подходит не всем потокам: там, где логика требует нескольких разных
    исходов-исключений с разными сигналами (YandexDiskUploadThread) или
    ветвления без исключений (YandexDiskSyncUploadThread с конфликтами) —
    такие классы переопределяют run() целиком, как раньше.
    """

    def run(self):
        from src.yandex_disk_client import YandexDiskError

        try:
            result = self._work()
            self._emit_success(result)
        except YandexDiskError as exc:
            self._emit_failure(str(exc))
        except Exception as exc:
            logger.error("Ошибка фоновой операции с Яндекс.Диском: %s", exc, exc_info=True)
            self._emit_failure(str(exc))

    def _work(self):
        raise NotImplementedError

    def _emit_success(self, result) -> None:
        raise NotImplementedError

    def _emit_failure(self, message: str) -> None:
        raise NotImplementedError


class _MkdirThread(_YandexWorkerThread):
    """Асинхронный client.mkdir(path) — чтобы не блокировать GUI-поток

    на медленной сети (создание папки в ручном выборе на Диске раньше
    делалось синхронно прямо в обработчике клика).
    """

    finished_mkdir = pyqtSignal(bool, str)  # (success, path_or_error)

    def __init__(self, client, path: str):
        super().__init__()
        self.client = client
        self.path = path

    def _work(self):
        self.client.mkdir(self.path)
        return self.path

    def _emit_success(self, result) -> None:
        self.finished_mkdir.emit(True, result)

    def _emit_failure(self, message: str) -> None:
        self.finished_mkdir.emit(False, message)


class _ListFolderThread(_YandexWorkerThread):
    """Асинхронный client.list_folder(path) — для ленивого разворачивания

    папок в деревьях диалогов. Раньше листинг делался синхронно прямо в
    обработчике itemExpanded и на медленной сети замораживал GUI-поток
    до таймаута клиента (~15 с).
    """

    resolved = pyqtSignal(str, list)  # (path, children)
    failed = pyqtSignal(str, str)  # (path, error message)

    def __init__(self, client, path: str):
        super().__init__()
        self.client = client
        self.path = path

    def _work(self):
        return self.client.list_folder(self.path)

    def _emit_success(self, result) -> None:
        self.resolved.emit(self.path, result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(self.path, message)


class _RenameThread(_YandexWorkerThread):
    """Переименование/перемещение ресурса (client.move) — файла или папки."""

    finished_rename = pyqtSignal(bool, str)  # (success, new_path_or_error)

    def __init__(self, token: str, from_path: str, to_path: str):
        super().__init__()
        self.token = token
        self.from_path = from_path
        self.to_path = to_path

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        client = YandexDiskClient(self.token)
        client.move(self.from_path, self.to_path)
        return self.to_path

    def _emit_success(self, result) -> None:
        self.finished_rename.emit(True, result)

    def _emit_failure(self, message: str) -> None:
        self.finished_rename.emit(False, message)


class _DeleteThread(_YandexWorkerThread):
    """Удаление ресурса (client.delete) — по умолчанию в Корзину, обратимо."""

    finished_delete = pyqtSignal(bool, str)  # (success, path_or_error)

    def __init__(self, token: str, path: str):
        super().__init__()
        self.token = token
        self.path = path

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        client = YandexDiskClient(self.token)
        client.delete(self.path, permanently=False)
        return self.path

    def _emit_success(self, result) -> None:
        self.finished_delete.emit(True, result)

    def _emit_failure(self, message: str) -> None:
        self.finished_delete.emit(False, message)


class _FallbackFolderFindThread(_YandexWorkerThread):
    """Поиск папки по алиасу/нечёткому совпадению для отчётов, чьё имя

    файла не распознано (meta is None) — используется fallback_series_key
    вместо meta.series. Результат не содержит подпапки eNN: для meta=None
    resolve_manual_pick_target трактует выбранную/найденную папку как есть
    (episode_path == series_path), без деления на серию/эпизод.
    """

    resolved = pyqtSignal(str)  # найденный путь, либо "" если не найдено
    failed = pyqtSignal(str)

    def __init__(self, token: str, fallback_key: str, series_roots: list = None):
        super().__init__()
        self.token = token
        self.fallback_key = fallback_key
        self.series_roots = series_roots or ["/отчеты"]

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        from src.report_uploader import find_series_folder

        client = YandexDiskClient(self.token)
        found = find_series_folder(client, self.fallback_key, roots=self.series_roots)
        return found or ""

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexDiskFindVersionsThread(_YandexWorkerThread):
    """Фоновый поиск папки серии/эпизода и списка всех версий отчёта на Диске."""

    resolved = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, token: str, meta, series_roots: list = None):
        super().__init__()
        self.token = token
        self.meta = meta
        self.series_roots = series_roots or ["/отчеты"]

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        from src.report_uploader import find_series_folder, list_report_versions

        client = YandexDiskClient(self.token)
        series_path = find_series_folder(client, self.meta.series, roots=self.series_roots)
        episode_path = None
        versions = []
        if series_path is not None:
            episode_path = f"{series_path}/e{self.meta.episode:02d}"
            versions = list_report_versions(client, episode_path, self.meta)
        return {
            "series_path": series_path,
            "episode_path": episode_path,
            "versions": versions,
        }

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexDiskFolderVersionsThread(_YandexWorkerThread):
    """Фоновый поиск списка версий отчёта в вручную выбранной папке."""

    resolved = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, token: str, target_folder_path: str):
        super().__init__()
        self.token = token
        self.target_folder_path = target_folder_path

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        from src.report_uploader import list_report_versions

        client = YandexDiskClient(self.token)
        return list_report_versions(client, self.target_folder_path)

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexDiskCompareThread(_YandexWorkerThread):
    """Фоновое построение сравнения между двумя выбранными версиями отчёта.

    Если new_path — текущий черновик (CURRENT_DRAFT, см. также
    YandexVersionPickerDialog.CURRENT_DRAFT в src/yandex_ui/dialogs.py),
    сравнивает его с old_path (версия на Диске) через compare_with_previous.
    Иначе сравнивает две версии на Диске между собой через compare_two_versions
    (например, первую версию с четвёртой — без участия локального черновика).
    """

    resolved = pyqtSignal(object)  # ReportComparison | None
    failed = pyqtSignal(str)

    def __init__(self, token: str, old_path: str, new_path: str, local_docx_path: Path):
        super().__init__()
        self.token = token
        self.old_path = old_path
        self.new_path = new_path
        self.local_docx_path = local_docx_path

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        from src.report_uploader import compare_with_previous, compare_two_versions

        client = YandexDiskClient(self.token)
        if self.new_path == CURRENT_DRAFT:
            return compare_with_previous(client, self.old_path, self.local_docx_path)
        return compare_two_versions(client, self.old_path, self.new_path)

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexDiskUploadThread(QThread):
    """Фоновая загрузка папки готового отчёта (все файлы) на Яндекс.Диск.

    Либо путь папки на Диске определяется автоматически по meta
    (series/episode), либо передаётся напрямую (target_folder_path) —
    когда папку выбрал пользователь вручную.
    """

    finished_upload = pyqtSignal(bool, str)
    needs_folder = pyqtSignal(str)  # error message — папка сериала не найдена, create_if_missing=False
    network_unavailable = pyqtSignal(str)  # error message — не удалось подключиться к Диску вообще (не ошибка API)
    progress = pyqtSignal(int, int)  # (bytes_sent, total_bytes)

    def __init__(
        self, token: str, local_folder_path: Path, *,
        meta=None, create_if_missing: bool = False, series_roots: list = None,
        target_folder_path: str = None,
    ):
        super().__init__()
        self.token = token
        self.local_folder_path = local_folder_path
        self.meta = meta
        self.create_if_missing = create_if_missing
        self.series_roots = series_roots or ["/отчеты"]
        self.target_folder_path = target_folder_path

    def run(self):
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError
        from src.report_uploader import SeriesFolderNotFoundError, resolve_target_path, upload_folder

        try:
            client = YandexDiskClient(self.token)
            if self.target_folder_path is not None:
                episode_path = self.target_folder_path
                # Папку эпизода создаёт сам поток (mkdir идемпотентен) —
                # вызывающий код (queue_manager.upload_to_folder) раньше
                # делал это синхронно в GUI-потоке и замораживал окно.
                client.mkdir(episode_path)
            else:
                episode_path, _created = resolve_target_path(
                    client, self.meta, create_if_missing=self.create_if_missing, roots=self.series_roots
                )
            report_folder_path = f"{episode_path}/{self.local_folder_path.name}"
            client.mkdir(report_folder_path)
            upload_folder(client, self.local_folder_path, report_folder_path,
                           progress_callback=lambda sent, total: self.progress.emit(sent, total))
            self.finished_upload.emit(True, report_folder_path)
        except SeriesFolderNotFoundError as exc:
            self.needs_folder.emit(str(exc))
        except YandexDiskError as exc:
            if exc.status_code is None:
                # URLError на уровне YandexDiskClient._request — не удалось
                # подключиться вообще (DNS/обрыв связи), а не ответ API
                # с кодом ошибки. Не тратим на это retry-попытки очереди.
                self.network_unavailable.emit(str(exc))
            else:
                self.finished_upload.emit(False, str(exc))
        except ValueError as exc:
            self.finished_upload.emit(False, str(exc))
        except Exception as exc:
            logger.error("Ошибка отправки на Яндекс.Диск: %s", exc, exc_info=True)
            self.finished_upload.emit(False, str(exc))


class YandexDiskDownloadThread(_YandexWorkerThread):
    """Фоновое скачивание одного файла с Яндекс.Диска на локальный путь."""

    finished_download = pyqtSignal(bool, str)  # (success, local_path_or_error)
    progress = pyqtSignal(int, object)  # (bytes_received, total_bytes_or_None)

    def __init__(self, token: str, remote_path: str, local_path: Path):
        super().__init__()
        self.token = token
        self.remote_path = remote_path
        self.local_path = local_path

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient

        client = YandexDiskClient(self.token)
        client.download_to_file(
            self.remote_path, self.local_path,
            progress_callback=lambda received, total: self.progress.emit(received, total),
        )
        return str(self.local_path)

    def _emit_success(self, result) -> None:
        self.finished_download.emit(True, result)

    def _emit_failure(self, message: str) -> None:
        self.finished_download.emit(False, message)


class YandexDiskSyncUploadThread(QThread):
    """Заливает один локальный файл на уже известный путь на Диске

    (перезапись открытого/правленого файла) — в отличие от
    YandexDiskUploadThread не резолвит серию/эпизод и не грузит папку целиком.
    """

    finished_sync = pyqtSignal(bool, str)  # (success, remote_path_or_error)
    conflict_detected = pyqtSignal(str, str)  # (remote_path, actual_modified_on_disk)
    progress = pyqtSignal(int, int)  # (bytes_sent, total_bytes)

    def __init__(self, token: str, local_path: Path, remote_path: str,
                 expected_modified: str = None, force: bool = False):
        super().__init__()
        self.token = token
        self.local_path = local_path
        self.remote_path = remote_path
        self.expected_modified = expected_modified
        self.force = force
        self.new_modified = None  # заполняется после успешной загрузки

    def run(self):
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError

        try:
            client = YandexDiskClient(self.token)

            if self.expected_modified and not self.force:
                try:
                    meta = client.get_meta(self.remote_path)
                except YandexDiskError:
                    meta = None
                if meta:
                    actual_modified = meta.get("modified")
                    if actual_modified and actual_modified != self.expected_modified:
                        self.conflict_detected.emit(self.remote_path, actual_modified)
                        return

            client.upload(self.local_path, self.remote_path,
                          progress_callback=lambda sent, total: self.progress.emit(sent, total))
            try:
                self.new_modified = client.get_meta(self.remote_path).get("modified")
            except YandexDiskError:
                self.new_modified = None
            self.finished_sync.emit(True, self.remote_path)
        except YandexDiskError as exc:
            self.finished_sync.emit(False, str(exc))
        except Exception as exc:
            logger.error("Ошибка синхронизации правок на Яндекс.Диск: %s", exc, exc_info=True)
            self.finished_sync.emit(False, str(exc))


class YandexDiskTokenCheckThread(_YandexWorkerThread):
    """Проверяет валидность OAuth-токена самым дешёвым запросом (инфо о Диске)."""

    finished_check = pyqtSignal(bool, str)  # (success, message_or_error)

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient

        client = YandexDiskClient(self.token)
        info = client.get_disk_info()
        total = info.get("total_space")
        used = info.get("used_space")
        if isinstance(total, (int, float)) and isinstance(used, (int, float)):
            free = total - used
            return f"Токен рабочий. Свободно: {_format_disk_file_size(free)}"
        return "Токен рабочий."

    def _emit_success(self, result) -> None:
        self.finished_check.emit(True, result)

    def _emit_failure(self, message: str) -> None:
        self.finished_check.emit(False, message)


class _IntegrityCheckThread(_YandexWorkerThread):
    """Тихая проверка, что уже отправленные на Диск отчёты (последние N,

    см. remember_uploaded_report в src/report_uploader.py) всё ещё
    существуют на месте — не удалил ли их кто-то мимо приложения.
    Запускается один раз за сессию, ошибки по отдельным записям (сеть,
    авторизация) молча пропускаются — не хотим ложных предупреждений
    "отчёт пропал" из-за временного сбоя проверки одной записи.
    """

    finished_check = pyqtSignal(list)  # список пропавших entries (может быть пустым)
    check_failed = pyqtSignal(str)  # не удалось проверить вообще (нет токена и т.п.)

    def __init__(self, token: str, entries: list):
        super().__init__()
        self.token = token
        self.entries = entries

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError

        client = YandexDiskClient(self.token)
        missing = []
        for entry in self.entries:
            remote_path = entry.get("remote_path")
            if not remote_path:
                continue
            try:
                client.get_meta(remote_path)
            except YandexDiskError as exc:
                if exc.status_code == 404:
                    missing.append(entry)
        return missing

    def _emit_success(self, result) -> None:
        self.finished_check.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.check_failed.emit(message)
