"""
Yandex.Disk Client Module

Тонкая обёртка над REST API Яндекс.Диска (cloud-api.yandex.net)
для листинга папок, создания папок и загрузки/скачивания файлов.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote

logger = logging.getLogger(__name__)

API_BASE = "https://cloud-api.yandex.net/v1/disk"

# Ключи внутри custom_properties ресурса, под которыми хранится тег
# (цвет + короткий комментарий) — namespaced префиксом "beast_", чтобы не
# столкнуться с custom_properties, которые мог бы выставить другой клиент
# Яндекс.Диска на тот же ресурс.
TAG_COLOR_PROPERTY = "beast_tag_color"
TAG_COMMENT_PROPERTY = "beast_tag_comment"


def parse_tag(custom_properties: dict | None) -> tuple[str, str] | None:
    """(цвет, комментарий) из custom_properties ресурса, или None — тега нет.

    Цвет обязателен для существования тега (комментарий — опционален,
    пустая строка по умолчанию); просто custom_properties от другого
    приложения без нашего цветового ключа тегом не считается.
    """
    if not custom_properties:
        return None
    color = custom_properties.get(TAG_COLOR_PROPERTY)
    if not color:
        return None
    return color, custom_properties.get(TAG_COMMENT_PROPERTY) or ""


# 423 (DiskResourceLockedError) — "над ресурсом сейчас выполняется другая
# операция" — почти всегда временное состояние (например, тот же путь
# только что создавался/менялся другим запросом) и обычно проходит само
# за несколько секунд. 5xx — временный сбой на стороне серверов Диска
# (например, InternalServerError), не связан с самим запросом. Оба случая
# ретраим с паузой вместо того, чтобы сразу показывать пользователю сырую
# ошибку API — ретраить безопасно даже для не-идемпотентных по своей сути
# методов (move и т.п.), потому что мы ретраим только явный ответ "ошибка"
# от сервера, а не таймаут с неизвестным исходом операции.
_TRANSIENT_ERROR_RETRY_DELAYS_SEC = [2, 5, 10]


class YandexDiskError(Exception):
    """Ошибка при обращении к API Яндекс.Диска.

    error_code — машиночитаемое поле "error" из тела ответа API
    (например, "DiskPathDoesntExistsError"), если его удалось разобрать:
    один и тот же HTTP-статус у Диска означает разные ситуации
    (409 — и «папка уже есть», и «родительский путь не существует»).
    """

    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class YandexDiskClient:
    """Клиент для работы с ресурсами Яндекс.Диска через REST API."""

    def __init__(self, token: str, timeout: int = 15):
        if not token:
            raise YandexDiskError("Не задан OAuth-токен Яндекс.Диска")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"OAuth {self.token}"}

    def _request(self, method: str, url: str, *, data: bytes = None, headers: dict = None) -> dict:
        req_headers = self._headers()
        if headers:
            req_headers.update(headers)

        attempt = 0
        while True:
            request = Request(url, data=data, headers=req_headers, method=method)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                try:
                    error_code = json.loads(body).get("error")
                except (ValueError, AttributeError):
                    error_code = None

                # 423 означает, что операция не началась из-за блокировки,
                # поэтому повтор безопасен для любого метода. После 5xx исход
                # мутации неизвестен: POST (сейчас это move) мог успеть
                # выполниться, и его повтор даст ложную ошибку или повторный
                # побочный эффект. Такие ответы автоматически повторяем только
                # для идемпотентных запросов этого клиента.
                idempotent_method = method.upper() in {"GET", "PUT", "PATCH", "DELETE"}
                is_retryable = exc.code == 423 or (500 <= exc.code < 600 and idempotent_method)
                if is_retryable and attempt < len(_TRANSIENT_ERROR_RETRY_DELAYS_SEC):
                    delay = _TRANSIENT_ERROR_RETRY_DELAYS_SEC[attempt]
                    attempt += 1
                    reason = "ресурс заблокирован" if exc.code == 423 else "ошибка сервера"
                    logger.info(
                        f"Yandex.Disk API {method} {url} -> {exc.code} ({reason}), "
                        f"повтор через {delay} с (попытка {attempt}/{len(_TRANSIENT_ERROR_RETRY_DELAYS_SEC)})"
                    )
                    time.sleep(delay)
                    continue

                logger.warning(f"Yandex.Disk API {method} {url} -> HTTP {exc.code}: {body}")
                raise YandexDiskError(
                    f"Яндекс.Диск вернул ошибку {exc.code}: {body}\nЗапрос: {method} {url}",
                    status_code=exc.code,
                    error_code=error_code,
                ) from exc
            except URLError as exc:
                logger.warning(f"Yandex.Disk API {method} {url} -> {exc}")
                raise YandexDiskError(f"Не удалось подключиться к Яндекс.Диску: {exc}") from exc

    def get_disk_info(self) -> dict:
        """Общая информация о Диске (общее/занятое место и т.п.) — самый

        дешёвый запрос для проверки, что токен вообще действителен.
        """
        return self._request("GET", API_BASE)

    def get_meta(self, path: str) -> dict:
        """Метаданные ресурса (файла или папки): modified, size и т.п. —

        используется для проверки, не изменил ли кто-то файл на Диске
        параллельно, перед тем как перезаписать его правками.
        """
        url = f"{API_BASE}/resources?path={quote(path, safe='/:')}"
        return self._request("GET", url)

    def set_custom_properties(self, path: str, properties: dict) -> None:
        """Обновляет custom_properties ресурса — Диск сам мёрджит переданные

        ключи с уже существующими (не упомянутые ключи остаются нетронуты);
        чтобы удалить конкретный ключ, передать для него None. Используется
        для тегов (цвет+комментарий) — в отличие от вариантов/алиасов
        (только локальный конфиг этого приложения), custom_properties
        хранятся на самом ресурсе и видны любому пользователю с доступом
        к этой папке на Диске.
        """
        url = f"{API_BASE}/resources?path={quote(path, safe='/:')}"
        body = json.dumps({"custom_properties": properties}).encode("utf-8")
        self._request("PATCH", url, data=body, headers={"Content-Type": "application/json"})

    def list_folder(self, path: str) -> list[dict]:
        """Возвращает список элементов (файлов и папок) в указанной папке.

        Постранично (limit=1000 за запрос): раньше папка с более чем 1000
        элементами молча обрезалась, и, например, папка сериала «не
        находилась» при поиске по корню.
        """
        items: list[dict] = []
        limit = 1000
        while True:
            url = (f"{API_BASE}/resources?path={quote(path, safe='/:')}"
                   f"&limit={limit}&offset={len(items)}")
            result = self._request("GET", url)
            embedded = result.get("_embedded", {})
            page = embedded.get("items", [])
            items.extend(page)
            total = embedded.get("total")
            if not page or total is None or len(items) >= total:
                return items

    def mkdir(self, path: str) -> None:
        """Создаёт папку. Не поднимает ошибку, если папка уже существует.

        Важно: 409 у API Диска означает и «папка уже существует» (это
        глотаем — mkdir идемпотентен), и «родительский путь не существует»
        (DiskPathDoesntExistsError — это настоящая ошибка, глотать её
        нельзя, иначе она всплывёт позже в непонятном месте при загрузке).
        """
        url = f"{API_BASE}/resources?path={quote(path, safe='/:')}"
        try:
            self._request("PUT", url)
        except YandexDiskError as exc:
            if exc.status_code != 409 or exc.error_code == "DiskPathDoesntExistsError":
                raise

    def publish(self, path: str) -> str:
        """Делает ресурс общедоступным по ссылке и возвращает саму ссылку.

        Идемпотентно: если ресурс уже был опубликован раньше, Диск не
        создаёт вторую ссылку — просто возвращает ту же самую. Сам ответ
        publish не содержит ссылку напрямую (только href для проверки
        статуса асинхронной операции) — публичный URL появляется в
        метаданных ресурса (get_meta) сразу после публикации.
        """
        url = f"{API_BASE}/resources/publish?path={quote(path, safe='/:')}"
        self._request("PUT", url)
        meta = self.get_meta(path)
        public_url = meta.get("public_url")
        if not public_url:
            raise YandexDiskError("Яндекс.Диск не вернул публичную ссылку после публикации")
        return public_url

    def move(self, from_path: str, to_path: str) -> None:
        """Перемещает/переименовывает файл или папку."""
        url = (f"{API_BASE}/resources/move?from={quote(from_path, safe='/:')}"
               f"&path={quote(to_path, safe='/:')}&overwrite=false")
        self._request("POST", url)

    def delete(self, path: str, permanently: bool = False) -> None:
        """Удаляет файл или папку — по умолчанию в Корзину (обратимо)."""
        permanently_flag = "true" if permanently else "false"
        url = f"{API_BASE}/resources?path={quote(path, safe='/:')}&permanently={permanently_flag}"
        self._request("DELETE", url)

    def upload(self, local_path: Path, remote_path: str, progress_callback=None) -> None:
        """Загружает локальный файл на Диск (с перезаписью, если файл уже есть).

        progress_callback(bytes_sent, total_bytes), если передан, вызывается
        по мере отправки данных чанками — для отображения процента загрузки.
        """
        url = f"{API_BASE}/resources/upload?path={quote(remote_path, safe='/:')}&overwrite=true"
        upload_info = self._request("GET", url)
        href = upload_info.get("href")
        if not href:
            raise YandexDiskError("Яндекс.Диск не вернул ссылку для загрузки")

        total_size = local_path.stat().st_size
        try:
            self._put_file_streaming(href, local_path, total_size, progress_callback)
        except (HTTPError, URLError, OSError) as exc:
            raise YandexDiskError(f"Не удалось загрузить файл на Яндекс.Диск: {exc}") from exc

    def _put_file_streaming(self, href: str, local_path: Path, total_size: int, progress_callback=None) -> None:
        import http.client
        from urllib.parse import urlsplit

        parts = urlsplit(href)
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parts.netloc, timeout=self.timeout)
        try:
            selector = parts.path + (f"?{parts.query}" if parts.query else "")
            conn.putrequest("PUT", selector)
            conn.putheader("Content-Length", str(total_size))
            conn.endheaders()

            sent = 0
            with open(local_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    conn.send(chunk)
                    sent += len(chunk)
                    if progress_callback:
                        progress_callback(sent, total_size)

            response = conn.getresponse()
            response.read()
            if response.status >= 400:
                raise YandexDiskError(
                    f"Яндекс.Диск вернул ошибку {response.status} при загрузке файла",
                    status_code=response.status,
                )
        finally:
            conn.close()

    def upload_bytes(self, data: bytes, remote_path: str, *, overwrite: bool = True) -> None:
        """Загружает содержимое (bytes) на Диск без промежуточного локального
        файла — для небольших сгенерированных данных (JSON-конфиги и т.п.),
        где заводить временный файл ради upload() было бы лишним.
        """
        overwrite_flag = "true" if overwrite else "false"
        url = (
            f"{API_BASE}/resources/upload?path={quote(remote_path, safe='/:')}"
            f"&overwrite={overwrite_flag}"
        )
        upload_info = self._request("GET", url)
        href = upload_info.get("href")
        if not href:
            raise YandexDiskError("Яндекс.Диск не вернул ссылку для загрузки")

        request = Request(href, data=data, method="PUT")
        try:
            with urlopen(request, timeout=self.timeout):
                pass
        except HTTPError as exc:
            raise YandexDiskError(
                f"Не удалось загрузить данные на Яндекс.Диск: {exc}",
                status_code=exc.code,
            ) from exc
        except URLError as exc:
            raise YandexDiskError(f"Не удалось загрузить данные на Яндекс.Диск: {exc}") from exc

    def download_bytes(self, remote_path: str) -> bytes:
        """Скачивает содержимое файла с Диска целиком в память."""
        href = self._get_download_href(remote_path)
        request = Request(href, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except (HTTPError, URLError) as exc:
            raise YandexDiskError(f"Не удалось скачать файл с Яндекс.Диска: {exc}") from exc

    def download_to_file(self, remote_path: str, local_path: Path, progress_callback=None) -> None:
        """Скачивает файл с Диска потоково прямо в local_path.

        progress_callback(bytes_received, total_bytes_or_none), если передан,
        вызывается по мере получения данных — для отображения процента.
        total_bytes может быть None, если сервер не прислал Content-Length.
        """
        href = self._get_download_href(remote_path)
        request = Request(href, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                total_size = response.headers.get("Content-Length")
                total_size = int(total_size) if total_size is not None else None
                received = 0
                with open(local_path, "wb") as f:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        if progress_callback:
                            progress_callback(received, total_size)
        except (HTTPError, URLError, OSError) as exc:
            raise YandexDiskError(f"Не удалось скачать файл с Яндекс.Диска: {exc}") from exc

    def _get_download_href(self, remote_path: str) -> str:
        url = f"{API_BASE}/resources/download?path={quote(remote_path, safe='/:')}"
        download_info = self._request("GET", url)
        href = download_info.get("href")
        if not href:
            raise YandexDiskError("Яндекс.Диск не вернул ссылку для скачивания")
        return href
