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

# Сильные ссылки на все запущенные и ещё не завершившиеся потоки —
# см. _KeepAliveThread.
_RUNNING_THREADS: set = set()


class _KeepAliveThread(QThread):
    """QThread, который нельзя случайно уничтожить, пока он ещё работает.

    Все фоновые потоки в этом модуле создаются без Qt-родителя и живут
    только за счёт Python-ссылки владельца (self._expand_threads[path],
    self._yandex_upload_thread и т.п.). Владельцы сбрасывают эту ссылку
    прямо в слоте финального сигнала потока (resolved/failed/finished_*),
    но сигнал эмитится из run() ДО фактической остановки потока: C++-часть
    QThread ещё завершает работу, а sip ждёт GIL, который в этот момент
    держит главный поток, исполняющий слот. Если сброшенная ссылка была
    последней, вместе с обёрткой уничтожается и C++-объект — Qt валит
    процесс с "QThread: Destroyed while thread is still running"
    (реальный краш: _ListFolderThread при ленивом листинге папок).

    Поэтому start() регистрирует поток в модульном реестре (вторая
    сильная ссылка), а слот на finished снимает регистрацию. Слот
    доставляется queued в главный поток (объект QThread живёт в нём),
    т.е. выполняется уже после эмита finished; остаточную микро-щель до
    фактической остановки закрывает wait() — PyQt отпускает GIL внутри
    него, давая потоку дозавершиться.
    """

    def start(self, *args, **kwargs):
        if self not in _RUNNING_THREADS:
            _RUNNING_THREADS.add(self)
            self.finished.connect(self._forget_self)
        super().start(*args, **kwargs)

    def _forget_self(self):
        try:
            self.wait()
        except RuntimeError:
            pass  # C++-обёртка уже уничтожена — держать больше нечего
        _RUNNING_THREADS.discard(self)


class _YandexWorkerThread(_KeepAliveThread):
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


class _SetTagThread(_YandexWorkerThread):
    """Асинхронный client.set_custom_properties(path, properties) —

    сохраняет/убирает тег (цвет+комментарий) на ресурсе, не блокируя GUI.
    """

    finished_set_tag = pyqtSignal(bool, str)  # (success, path_or_error)

    def __init__(self, client, path: str, properties: dict):
        super().__init__()
        self.client = client
        self.path = path
        self.properties = properties

    def _work(self):
        self.client.set_custom_properties(self.path, self.properties)
        return self.path

    def _emit_success(self, result) -> None:
        self.finished_set_tag.emit(True, result)

    def _emit_failure(self, message: str) -> None:
        self.finished_set_tag.emit(False, message)


class _ListFolderThread(_YandexWorkerThread):
    """Асинхронный client.list_folder(path) — для ленивого разворачивания

    папок в деревьях диалогов. Раньше листинг делался синхронно прямо в
    обработчике itemExpanded и на медленной сети замораживал GUI-поток
    до таймаута клиента (~15 с).

    Переопределяет run() целиком (не использует общий _work()/_emit_*
    шаблон _YandexWorkerThread) — нужно различить 404 (папка больше не
    существует на Диске, например удалена мимо приложения) от прочих
    ошибок отдельным сигналом not_found, а не строковым сопоставлением
    текста сообщения (см. SeriesFolderNotFoundError — тем же принципом
    руководствовались раньше в этой кодовой базе).
    """

    resolved = pyqtSignal(str, list)  # (path, children)
    failed = pyqtSignal(str, str)  # (path, error message)
    not_found = pyqtSignal(str)  # path — папка больше не существует на Диске (404)

    def __init__(self, client, path: str):
        super().__init__()
        self.client = client
        self.path = path

    def run(self):
        from src.yandex_disk_client import YandexDiskError

        try:
            children = self.client.list_folder(self.path)
            self.resolved.emit(self.path, children)
        except YandexDiskError as exc:
            if exc.status_code == 404:
                self.not_found.emit(self.path)
            else:
                self.failed.emit(self.path, str(exc))
        except Exception as exc:
            logger.error("Ошибка листинга папки на Яндекс.Диске: %s", exc, exc_info=True)
            self.failed.emit(self.path, str(exc))


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


class _PublishThread(_YandexWorkerThread):
    """Публикация ресурса (client.publish) — получить ссылку «поделиться»

    на конкретный файл/папку по запросу пользователя (ПКМ в просмотрщике
    Диска). В отличие от отклонённой раньше идеи «автоматически предлагать
    публичную ссылку сразу после отправки отчёта» — это ручное действие
    для одного выбранного элемента, ничего не публикует само по себе.
    """

    resolved = pyqtSignal(str, str)  # (path, public_url)
    failed = pyqtSignal(str, str)  # (path, error message)

    def __init__(self, client, path: str):
        super().__init__()
        self.client = client
        self.path = path

    def _work(self):
        return self.client.publish(self.path)

    def _emit_success(self, result) -> None:
        self.resolved.emit(self.path, result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(self.path, message)


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


class _FolderSizeThread(_YandexWorkerThread):
    """Рекурсивный подсчёт суммарного размера папки — по запросу (ПКМ

    «Посчитать размер» в просмотрщике), не автоматически для каждой
    видимой папки: полный рекурсивный обход может быть медленным для
    больших деревьев.
    """

    resolved = pyqtSignal(str, int)  # (path, total_bytes)
    failed = pyqtSignal(str, str)  # (path, error message)

    def __init__(self, client, path: str):
        super().__init__()
        self.client = client
        self.path = path

    def _work(self):
        return self._sum_folder(self.path)

    def _sum_folder(self, path: str) -> int:
        total = 0
        for entry in self.client.list_folder(path):
            if entry.get("type") == "dir":
                child_path = entry.get("path") or f"{path}/{entry.get('name', '')}"
                total += self._sum_folder(child_path)
            else:
                total += entry.get("size") or 0
        return total

    def _emit_success(self, result) -> None:
        self.resolved.emit(self.path, result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(self.path, message)


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
    """Фоновый поиск папки серии/эпизода и списка всех версий отчёта на Диске.

    list_report_versions вызывается БЕЗ meta — episode_path и так уже
    указывает ровно на папку нужного эпизода (сезон/эпизод фильтровать не
    нужно), а фильтрация по variant внутри list_report_versions требует
    строгого совпадения имени с REPORT_PATTERN у КАЖДОГО кандидата и
    молча выбрасывает версии с нестандартным именем (частое дело —
    другой формат даты, составной тег вроде "cens_AD" и т.п.), даже если
    они на самом деле того же варианта, что и текущий черновик. Разбор по
    variant теперь делает вызывающий код (BeastApp._handle_yandex_versions)
    через group_versions_by_category — то же лёгкое сканирование имени,
    что и в просмотрщике Диска, вместо строгого REPORT_PATTERN целиком.
    """

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
            versions = list_report_versions(client, episode_path)
        return {
            "series_path": series_path,
            "episode_path": episode_path,
            "versions": versions,
        }

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexCombinedFindThread(_YandexWorkerThread):
    """Резолвит папку отчёта И папку npr-проекта одним проходом.

    Используется только когда есть npr-файлы для отправки вместе с
    отчётом (см. BeastApp._send_report_to_disk) — решить, нужен ли
    комбинированный пикер папок, можно только когда известны ОБА исхода
    сразу; npr резолвится и грузится атомарно в NprUploadThread.run(),
    поэтому его нельзя "приостановить" на середине и спросить пользователя
    только про отчёт. Версии здесь не запрашиваются (в отличие от
    YandexDiskFindVersionsThread) — при отправке ("send") они не нужны.
    """

    resolved = pyqtSignal(dict)  # {"series_path", "episode_path", "npr_folder"} — любое может быть None
    failed = pyqtSignal(str)

    def __init__(
        self, token: str, report_lookup_key: str, report_roots: list, meta,
        npr_key: str, npr_root: str,
    ):
        super().__init__()
        self.token = token
        self.report_lookup_key = report_lookup_key
        self.report_roots = report_roots or ["/отчеты"]
        self.meta = meta
        self.npr_key = npr_key
        self.npr_root = npr_root

    def _work(self):
        from src.yandex_disk_client import YandexDiskClient
        from src.report_uploader import find_series_folder, NPR_ALIASES_FILE

        client = YandexDiskClient(self.token)
        series_path = find_series_folder(client, self.report_lookup_key, roots=self.report_roots)
        episode_path = None
        if series_path is not None:
            episode_path = f"{series_path}/e{self.meta.episode:02d}" if self.meta else series_path
        npr_folder = find_series_folder(
            client, self.npr_key, roots=[self.npr_root], aliases_path=NPR_ALIASES_FILE,
        )
        return {"series_path": series_path, "episode_path": episode_path, "npr_folder": npr_folder}

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexDiskFolderVersionsThread(_YandexWorkerThread):
    """Фоновый поиск списка версий отчёта в вручную выбранной папке.

    meta принимается для обратной совместимости (вызывающий код может
    знать вариант выбранного элемента — см.
    YandexDiskBrowserDialog._fetch_versions_for_selected), но больше НЕ
    передаётся в list_report_versions для фильтрации: строгая фильтрация
    по variant там требует, чтобы КАЖДЫЙ кандидат тоже совпал с
    REPORT_PATTERN целиком, и молча выбрасывала версии с нестандартным
    именем (частое дело — другой формат даты, составной тег вроде
    "cens_AD" и т.п.), даже если они на самом деле того же варианта.
    Группировку по варианту теперь делает вызывающий код через
    group_versions_by_category — то же лёгкое сканирование имени, что и
    в остальном просмотрщике Диска, вместо строгого REPORT_PATTERN.
    """

    resolved = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, token: str, target_folder_path: str, meta=None):
        super().__init__()
        self.token = token
        self.target_folder_path = target_folder_path
        self.meta = meta

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


class VersionSummaryThread(_YandexWorkerThread):
    """Фоновая генерация LLM-сводки различий между двумя версиями отчёта.

    Сеть Диска здесь не участвует (данные уже собраны в ReportComparison),
    но вызов LLM — особенно локальной Ollama — может идти десятки секунд,
    поэтому выполняется вне GUI-потока, как и все операции модуля.
    """

    resolved = pyqtSignal(str)  # текст сводки
    failed = pyqtSignal(str)  # error message

    def __init__(self, generator, comparison, old_label: str = None, new_label: str = None):
        super().__init__()
        self.generator = generator
        self.comparison = comparison
        self.old_label = old_label
        self.new_label = new_label

    def _work(self):
        return self.generator.summarize_version_changes(
            self.comparison, old_label=self.old_label, new_label=self.new_label
        )

    def _emit_success(self, result) -> None:
        self.resolved.emit(result)

    def _emit_failure(self, message: str) -> None:
        self.failed.emit(message)


class YandexDiskUploadThread(_KeepAliveThread):
    """Фоновая загрузка папки готового отчёта (все файлы) на Яндекс.Диск.

    Либо путь папки на Диске определяется автоматически по meta
    (series/episode), либо передаётся напрямую (target_folder_path) —
    когда папку выбрал пользователь вручную.
    """

    finished_upload = pyqtSignal(bool, str)
    needs_folder = pyqtSignal(str)  # error message — папка сериала не найдена, create_if_missing=False
    network_unavailable = pyqtSignal(str)  # error message — не удалось подключиться к Диску вообще (не ошибка API)
    auth_expired = pyqtSignal(str)  # error message — 401: токен отозван/истёк, нужен повторный вход
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
        from src.marker_registry_sync import (
            publish_local_chain_version,
            reconcile_report_folder_before_upload,
        )

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
            if self.local_folder_path is None:
                raise ValueError("Не указана локальная папка отчёта для отправки")
            local_folder_path = Path(self.local_folder_path)
            if not local_folder_path.is_dir():
                raise ValueError(f"Локальная папка отчёта недоступна: {local_folder_path}")
            marker_chain_key, _renumbered = reconcile_report_folder_before_upload(
                client, local_folder_path, episode_path
            )
            report_folder_path = f"{episode_path}/{local_folder_path.name}"
            client.mkdir(report_folder_path)
            upload_folder(client, local_folder_path, report_folder_path,
                           progress_callback=lambda sent, total: self.progress.emit(sent, total))
            if marker_chain_key:
                publish_local_chain_version(client, marker_chain_key, report_folder_path)
            self.finished_upload.emit(True, report_folder_path)
        except SeriesFolderNotFoundError as exc:
            self.needs_folder.emit(str(exc))
        except YandexDiskError as exc:
            if exc.status_code == 401:
                # Токен отозван/истёк — retry-логика очереди это не вылечит
                # (запросы будут падать снова и снова). Отдельный сигнал, чтобы
                # менеджер поставил очередь на паузу и попросил войти заново.
                self.auth_expired.emit(str(exc))
            elif exc.status_code is None:
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


class NprUploadThread(_KeepAliveThread):
    """Загрузка .npr-файлов проекта Nuendo в папку сезона на отдельном

    корне Диска (NPR_ROOT из настроек) — в отличие от YandexDiskUploadThread,
    без под-папки на эпизод (весь сезон в одной папке отдельно от
    /отчеты). Папка резолвится тем же механизмом алиасов, что и отчёты
    с нераспознанным именем файла (см. fallback_series_key,
    NPR_ALIASES_FILE в src/report_uploader.py) — из-за отдельного корня и
    несвязанной с meta.series схемы имени алиасы хранятся отдельно от
    алиасов серий отчётов.
    """

    finished_upload = pyqtSignal(bool, str)
    needs_folder = pyqtSignal(str)  # папка сезона не найдена ни по алиасу, ни нечётким поиском
    network_unavailable = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # (файлов загружено, всего файлов) — раньше не эмитился вообще

    def __init__(
        self, token: str, npr_root: str, npr_key: str, local_paths, *,
        target_folder_path: str = None,
    ):
        super().__init__()
        self.token = token
        self.npr_root = npr_root
        self.npr_key = npr_key
        self.local_paths = list(local_paths)
        self.target_folder_path = target_folder_path

    def run(self):
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError
        from src.report_uploader import NPR_ALIASES_FILE, find_series_folder, remember_series_alias

        try:
            client = YandexDiskClient(self.token)
            if self.target_folder_path is not None:
                folder = self.target_folder_path
            else:
                folder = find_series_folder(
                    client, self.npr_key, roots=[self.npr_root], aliases_path=NPR_ALIASES_FILE,
                )
                if folder is None:
                    self.needs_folder.emit(f"Папка для проекта «{self.npr_key}» не найдена на Диске")
                    return
            total = len(self.local_paths)
            for i, local_path in enumerate(self.local_paths):
                remote_path = f"{folder}/{Path(local_path).name}"
                client.upload(Path(local_path), remote_path)
                self.progress.emit(i + 1, total)
            remember_series_alias(self.npr_key, folder, NPR_ALIASES_FILE)
            self.finished_upload.emit(True, folder)
        except YandexDiskError as exc:
            if exc.status_code is None:
                self.network_unavailable.emit(str(exc))
            else:
                self.finished_upload.emit(False, str(exc))
        except Exception as exc:
            logger.error("Ошибка отправки npr-файлов на Яндекс.Диск: %s", exc, exc_info=True)
            self.finished_upload.emit(False, str(exc))


class FinderDropUploadThread(_KeepAliveThread):
    """Загрузка файлов/папок, перетащенных из Finder, в уже известную папку

    на Диске — в отличие от NprUploadThread, путь назначения всегда уже
    резолвлен (это папка в уже открытом дереве YandexDiskBrowserDialog),
    и загружаемые локальные пути могут быть папками (рекурсивно, см.
    upload_paths_recursive в src/report_uploader.py), а не только файлами.
    """

    finished_upload = pyqtSignal(bool, str)
    progress = pyqtSignal(int, int)  # (файлов загружено, всего файлов)

    def __init__(self, token: str, local_paths, remote_folder: str):
        super().__init__()
        self.token = token
        self.local_paths = list(local_paths)
        self.remote_folder = remote_folder

    def run(self):
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError
        from src.report_uploader import upload_paths_recursive

        try:
            client = YandexDiskClient(self.token)
            upload_paths_recursive(
                client, self.local_paths, self.remote_folder,
                progress_callback=lambda done, total: self.progress.emit(done, total),
            )
            self.finished_upload.emit(True, "")
        except YandexDiskError as exc:
            self.finished_upload.emit(False, str(exc))
        except Exception as exc:
            logger.error("Ошибка загрузки из Finder на Яндекс.Диск: %s", exc, exc_info=True)
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


class YandexDiskSyncUploadThread(_KeepAliveThread):
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


class ConfigSyncThread(_KeepAliveThread):
    """Один проход синхронизации всех "выученных" локальных конфигов

    (алиасы серий/NPR, ручные варианты, словарь спелчекера, реестр
    отправленных отчётов) с Яндекс.Диском — см. src/config_sync.py.
    Все файлы небольшие, синхронизируются последовательно за один вызов
    sync_all(), поэтому один поток на весь цикл, а не по потоку на файл.

    Оффлайн/просроченный токен получают отдельные сигналы (как в
    YandexDiskUploadThread) — ConfigSyncController должен различать
    "сеть недоступна, попробуем на следующем тике таймера" и "токен умер,
    нужен повторный вход", а не просто показывать сырую ошибку.
    """

    resolved = pyqtSignal(object)  # SyncSummary
    network_unavailable = pyqtSignal(str)
    auth_expired = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def run(self):
        from src.config_sync import sync_all
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError

        try:
            client = YandexDiskClient(self.token)
            summary = sync_all(client)
            self.resolved.emit(summary)
        except YandexDiskError as exc:
            if exc.status_code == 401:
                self.auth_expired.emit(str(exc))
            elif exc.status_code is None:
                self.network_unavailable.emit(str(exc))
            else:
                self.failed.emit(str(exc))
        except Exception as exc:
            logger.error("Ошибка синхронизации конфигов с Яндекс.Диском: %s", exc, exc_info=True)
            self.failed.emit(str(exc))


class MarkerIdentityPrepareThread(_KeepAliveThread):
    """Synchronize and analyse one marker chain before report generation.

    Network/auth failures deliberately fall back to the local registry: the
    product policy allows offline generation, but the returned plan carries a
    visible warning and marks newly allocated IDs as pending.
    """

    prepared = pyqtSignal(object)  # MarkerIdentityPlan
    conflict = pyqtSignal(object)  # MarkerRegistryConflictError
    failed = pyqtSignal(str)

    def __init__(self, token: str, source_path: str, roots: list[str]):
        super().__init__()
        self.token = token or ""
        self.source_path = source_path
        self.roots = list(roots or ["/отчеты"])

    def run(self):
        from src.marker_registry_sync import (
            MarkerRegistryConflictError,
            prepare_identity_plan_offline,
            prepare_identity_plan_with_disk,
        )
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError

        try:
            if not self.token:
                self.prepared.emit(prepare_identity_plan_offline(self.source_path))
                return
            try:
                plan = prepare_identity_plan_with_disk(
                    YandexDiskClient(self.token), self.source_path, roots=self.roots
                )
            except MarkerRegistryConflictError as exc:
                self.conflict.emit(exc)
                return
            except YandexDiskError as exc:
                if exc.status_code == 401:
                    warning = (
                        "Не удалось проверить ID: требуется повторный вход в Яндекс Диск. "
                        "Используется локальная история."
                    )
                else:
                    warning = (
                        "Не удалось подключиться к Яндекс Диску. Используется локальная "
                        "история ID; перед загрузкой будет выполнена повторная проверка."
                    )
                plan = prepare_identity_plan_offline(self.source_path, warning=warning)
            self.prepared.emit(plan)
        except Exception as exc:
            logger.error("Ошибка подготовки постоянных ID маркеров: %s", exc, exc_info=True)
            self.failed.emit(str(exc))


class MarkerIdentityConflictResolveThread(_KeepAliveThread):
    resolved = pyqtSignal(object)  # renumber mapping
    failed = pyqtSignal(str)

    def __init__(self, token: str, conflict, choices: dict[str, str]):
        super().__init__()
        self.token = token
        self.conflict = conflict
        self.choices = dict(choices)

    def run(self):
        from src.marker_registry_sync import resolve_chain_conflicts
        from src.yandex_disk_client import YandexDiskClient

        try:
            mapping = resolve_chain_conflicts(
                YandexDiskClient(self.token), self.conflict, self.choices
            )
            self.resolved.emit(mapping)
        except Exception as exc:
            logger.error("Ошибка разрешения конфликта ID: %s", exc, exc_info=True)
            self.failed.emit(str(exc))
