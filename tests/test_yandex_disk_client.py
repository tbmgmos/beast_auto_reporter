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
    payload = '{"_embedded": {"items": [{"name": "e01", "type": "dir", "path": "disk:/отчеты/Show/e01"}]}}'.encode("utf-8")

    with patch("src.yandex_disk_client.urlopen", return_value=DummyResponse(payload)):
        items = client.list_folder("отчеты/Show")

    assert items == [{"name": "e01", "type": "dir", "path": "disk:/отчеты/Show/e01"}]


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
