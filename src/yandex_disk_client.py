"""
Yandex.Disk Client Module

Тонкая обёртка над REST API Яндекс.Диска (cloud-api.yandex.net)
для листинга папок, создания папок и загрузки/скачивания файлов.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote

logger = logging.getLogger(__name__)

API_BASE = "https://cloud-api.yandex.net/v1/disk"


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
        request = Request(url, data=data, headers=req_headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning(f"Yandex.Disk API {method} {url} -> HTTP {exc.code}: {body}")
            try:
                error_code = json.loads(body).get("error")
            except (ValueError, AttributeError):
                error_code = None
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
