import io
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch
from urllib.error import HTTPError

import pytest

from src.yandex_disk_client import YandexDiskClient, YandexDiskError


class DummyResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_requires_token():
    with pytest.raises(YandexDiskError):
        YandexDiskClient("")


def test_get_disk_info_returns_parsed_payload():
    client = YandexDiskClient("test-token")
    payload = '{"total_space": 1000, "used_space": 400, "user": {"login": "vlad"}}'.encode("utf-8")

    with patch("src.yandex_disk_client.urlopen", return_value=DummyResponse(payload)):
        info = client.get_disk_info()

    assert info == {"total_space": 1000, "used_space": 400, "user": {"login": "vlad"}}


def test_get_disk_info_raises_on_bad_token():
    client = YandexDiskClient("bad-token")
    error = HTTPError(url="x", code=401, msg="Unauthorized", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        with pytest.raises(YandexDiskError):
            client.get_disk_info()


def test_get_meta_returns_parsed_payload():
    client = YandexDiskClient("test-token")
    payload = '{"name": "report.docx", "modified": "2025-06-23T10:00:00+00:00", "size": 123}'.encode("utf-8")

    with patch("src.yandex_disk_client.urlopen", return_value=DummyResponse(payload)):
        meta = client.get_meta("отчеты/Show/e01/report.docx")

    assert meta["modified"] == "2025-06-23T10:00:00+00:00"


def test_get_meta_raises_on_missing_resource():
    client = YandexDiskClient("test-token")
    error = HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        with pytest.raises(YandexDiskError):
            client.get_meta("отчеты/Show/e01/report.docx")


def test_list_folder_returns_items():
    client = YandexDiskClient("test-token")
    payload = '{"_embedded": {"items": [{"name": "e01", "type": "dir", "path": "disk:/отчеты/Show/e01"}], "total": 1}}'.encode("utf-8")

    with patch("src.yandex_disk_client.urlopen", return_value=DummyResponse(payload)):
        items = client.list_folder("отчеты/Show")

    assert items == [{"name": "e01", "type": "dir", "path": "disk:/отчеты/Show/e01"}]


def test_list_folder_paginates_beyond_first_page():
    # Папка с числом элементов больше limit одного запроса: раньше
    # результат молча обрезался, и элементы со второй страницы «не
    # находились» (например, папка сериала при поиске по корню).
    client = YandexDiskClient("test-token")
    page1 = ('{"_embedded": {"items": ['
             + ",".join(f'{{"name": "s{i}", "type": "dir"}}' for i in range(1000))
             + '], "total": 1002}}').encode("utf-8")
    page2 = ('{"_embedded": {"items": ['
             '{"name": "хвост_1", "type": "dir"}, {"name": "хвост_2", "type": "dir"}'
             '], "total": 1002}}').encode("utf-8")
    captured_urls = []

    def fake_urlopen(request, timeout=None):
        captured_urls.append(request.full_url)
        return DummyResponse(page1 if len(captured_urls) == 1 else page2)

    with patch("src.yandex_disk_client.urlopen", side_effect=fake_urlopen):
        items = client.list_folder("отчеты")

    assert len(items) == 1002
    assert items[-1]["name"] == "хвост_2"
    assert "offset=0" in captured_urls[0]
    assert "offset=1000" in captured_urls[1]
    assert len(captured_urls) == 2


def test_list_folder_stops_on_empty_page_even_if_total_lies():
    # Защита от зацикливания, если API вернёт total больше фактического
    # числа элементов (или страница окажется пустой по любой причине).
    client = YandexDiskClient("test-token")
    payload = b'{"_embedded": {"items": [], "total": 10}}'

    with patch("src.yandex_disk_client.urlopen", return_value=DummyResponse(payload)):
        assert client.list_folder("отчеты") == []


def test_list_folder_does_not_percent_encode_colon_in_disk_path():
    # disk:/... пути, которые возвращает сам API, не должны ломаться при
    # повторной передаче в другие вызовы — quote() по умолчанию экранирует
    # ':' в '%3A', из-за чего Диск отвечал 404 DiskNotFoundError.
    client = YandexDiskClient("test-token")
    payload = b'{"_embedded": {"items": []}}'
    captured_urls = []

    def fake_urlopen(request, timeout=None):
        captured_urls.append(request.full_url)
        return DummyResponse(payload)

    with patch("src.yandex_disk_client.urlopen", side_effect=fake_urlopen):
        client.list_folder("disk:/отчеты/Show/e01")

    assert "%3A" not in captured_urls[0]
    assert "disk:/" in captured_urls[0]


def test_mkdir_ignores_existing_folder_conflict():
    client = YandexDiskClient("test-token")
    error = HTTPError(url="x", code=409, msg="Conflict", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        client.mkdir("отчеты/Show/e01")  # не должно поднимать исключение


def test_mkdir_ignores_409_when_folder_already_exists():
    client = YandexDiskClient("test-token")
    body = io.BytesIO(b'{"error": "DiskPathPointsToExistentDirectoryError"}')
    error = HTTPError(url="x", code=409, msg="Conflict", hdrs=None, fp=body)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        client.mkdir("отчеты/Show/e01")  # папка уже есть — mkdir идемпотентен


def test_mkdir_raises_409_when_parent_path_missing():
    # У API Диска 409 означает и «папка уже есть», и «родительский путь
    # не существует» — второе глотать нельзя, иначе ошибка всплывёт позже
    # в непонятном месте (при загрузке файлов в несозданную папку).
    client = YandexDiskClient("test-token")
    body = io.BytesIO(b'{"error": "DiskPathDoesntExistsError"}')
    error = HTTPError(url="x", code=409, msg="Conflict", hdrs=None, fp=body)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        with pytest.raises(YandexDiskError) as exc_info:
            client.mkdir("отчеты/Нет_такого_родителя/e01")
    assert exc_info.value.error_code == "DiskPathDoesntExistsError"


def test_mkdir_raises_on_other_errors():
    client = YandexDiskClient("test-token")
    error = HTTPError(url="x", code=500, msg="Server Error", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        with pytest.raises(YandexDiskError):
            client.mkdir("отчеты/Show/e01")


def test_upload_gets_href_then_puts_file_streaming(tmp_path):
    client = YandexDiskClient("test-token")
    local_file = tmp_path / "report.docx"
    local_file.write_bytes(b"content")

    href_response = DummyResponse(b'{"href": "https://upload.example/put"}')
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MagicMock(status=201, read=lambda: b"")

    with patch("src.yandex_disk_client.urlopen", return_value=href_response), \
         patch("http.client.HTTPSConnection", return_value=mock_conn):
        client.upload(local_file, "отчеты/Show/e01/report.docx")

    mock_conn.putrequest.assert_called_once_with("PUT", "/put")
    mock_conn.putheader.assert_called_once_with("Content-Length", "7")
    mock_conn.send.assert_called_once_with(b"content")
    mock_conn.close.assert_called_once()


def test_upload_reports_progress_via_callback(tmp_path):
    client = YandexDiskClient("test-token")
    local_file = tmp_path / "report.docx"
    local_file.write_bytes(b"content")

    href_response = DummyResponse(b'{"href": "https://upload.example/put"}')
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MagicMock(status=201, read=lambda: b"")
    progress_calls = []

    with patch("src.yandex_disk_client.urlopen", return_value=href_response), \
         patch("http.client.HTTPSConnection", return_value=mock_conn):
        client.upload(local_file, "отчеты/Show/e01/report.docx",
                       progress_callback=lambda sent, total: progress_calls.append((sent, total)))

    assert progress_calls == [(7, 7)]


def test_upload_raises_on_server_error_status(tmp_path):
    client = YandexDiskClient("test-token")
    local_file = tmp_path / "report.docx"
    local_file.write_bytes(b"content")

    href_response = DummyResponse(b'{"href": "https://upload.example/put"}')
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = MagicMock(status=500, read=lambda: b"")

    with patch("src.yandex_disk_client.urlopen", return_value=href_response), \
         patch("http.client.HTTPSConnection", return_value=mock_conn):
        with pytest.raises(YandexDiskError):
            client.upload(local_file, "отчеты/Show/e01/report.docx")


def test_upload_bytes_gets_href_then_puts_data_directly():
    client = YandexDiskClient("test-token")
    captured = []
    responses = iter([DummyResponse(b'{"href": "https://upload.example/put"}'), DummyResponse(b"")])

    def fake_urlopen(request, timeout=None):
        captured.append((request.get_method(), request.full_url, request.data))
        return next(responses)

    with patch("src.yandex_disk_client.urlopen", side_effect=fake_urlopen):
        client.upload_bytes(b'{"a": 1}', "/Beast Auto Reporter/sync/series_aliases.json")

    href_call, put_call = captured
    assert href_call[0] == "GET" and "overwrite=true" in href_call[1]
    assert put_call[0] == "PUT" and put_call[1] == "https://upload.example/put"
    assert put_call[2] == b'{"a": 1}'


def test_upload_bytes_raises_when_no_href_returned():
    client = YandexDiskClient("test-token")

    with patch("src.yandex_disk_client.urlopen", return_value=DummyResponse(b"{}")):
        with pytest.raises(YandexDiskError):
            client.upload_bytes(b"data", "/Beast Auto Reporter/sync/series_aliases.json")


def test_upload_bytes_raises_on_network_error_during_put():
    client = YandexDiskClient("test-token")
    href_response = DummyResponse(b'{"href": "https://upload.example/put"}')
    error = HTTPError(url="x", code=500, msg="Server Error", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=[href_response, error]):
        with pytest.raises(YandexDiskError):
            client.upload_bytes(b"data", "/Beast Auto Reporter/sync/series_aliases.json")


def test_move_sends_post_with_from_and_path():
    client = YandexDiskClient("test-token")
    captured_urls = []

    def fake_urlopen(request, timeout=None):
        captured_urls.append((request.get_method(), request.full_url))
        return DummyResponse(b"{}")

    with patch("src.yandex_disk_client.urlopen", side_effect=fake_urlopen):
        client.move("отчеты/Show/e01", "отчеты/Show/e02")

    method, url = captured_urls[0]
    assert method == "POST"
    assert "from=" in url and "path=" in url
    assert "overwrite=false" in url


def test_move_raises_on_conflict():
    client = YandexDiskClient("test-token")
    error = HTTPError(url="x", code=409, msg="Conflict", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        with pytest.raises(YandexDiskError):
            client.move("отчеты/Show/e01", "отчеты/Show/e02")


def test_delete_sends_delete_with_permanently_false_by_default():
    client = YandexDiskClient("test-token")
    captured_urls = []

    def fake_urlopen(request, timeout=None):
        captured_urls.append((request.get_method(), request.full_url))
        return DummyResponse(b"{}")

    with patch("src.yandex_disk_client.urlopen", side_effect=fake_urlopen):
        client.delete("отчеты/Show/e01")

    method, url = captured_urls[0]
    assert method == "DELETE"
    assert "permanently=false" in url


def test_delete_permanently_true_sets_flag():
    client = YandexDiskClient("test-token")
    captured_urls = []

    def fake_urlopen(request, timeout=None):
        captured_urls.append(request.full_url)
        return DummyResponse(b"{}")

    with patch("src.yandex_disk_client.urlopen", side_effect=fake_urlopen):
        client.delete("отчеты/Show/e01", permanently=True)

    assert "permanently=true" in captured_urls[0]


def test_delete_raises_on_missing_resource():
    client = YandexDiskClient("test-token")
    error = HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)

    with patch("src.yandex_disk_client.urlopen", side_effect=error):
        with pytest.raises(YandexDiskError):
            client.delete("отчеты/Show/e01")


def test_download_bytes_gets_href_then_gets_content():
    client = YandexDiskClient("test-token")
    responses = [
        DummyResponse(b'{"href": "https://download.example/get"}'),
        DummyResponse(b"file content"),
    ]

    with patch("src.yandex_disk_client.urlopen", side_effect=responses):
        data = client.download_bytes("отчеты/Show/e01/report.docx")

    assert data == b"file content"


def test_download_to_file_writes_content_and_reports_progress(tmp_path):
    client = YandexDiskClient("test-token")
    local_path = tmp_path / "downloaded.docx"

    class DummyDownloadResponse(DummyResponse):
        headers = {"Content-Length": "12"}

        def read(self, size=None):
            if not hasattr(self, "_consumed"):
                self._consumed = True
                return self.payload
            return b""

    responses = [
        DummyResponse(b'{"href": "https://download.example/get"}'),
        DummyDownloadResponse(b"file content"),
    ]

    progress_calls = []
    with patch("src.yandex_disk_client.urlopen", side_effect=responses):
        client.download_to_file(
            "отчеты/Show/e01/report.docx", local_path,
            progress_callback=lambda received, total: progress_calls.append((received, total)),
        )

    assert local_path.read_bytes() == b"file content"
    assert progress_calls == [(12, 12)]
