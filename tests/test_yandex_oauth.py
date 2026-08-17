import threading
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from src.yandex_oauth import (
    YandexOAuthError, is_configured, poll_for_token, request_device_code,
)
from src.yandex_ui.oauth_dialog import YandexOAuthDialog


class DummyResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_is_configured_false_without_credentials(monkeypatch):
    monkeypatch.setattr("src.yandex_oauth.CLIENT_ID", "")
    monkeypatch.setattr("src.yandex_oauth.CLIENT_SECRET", "")
    assert is_configured() is False


def test_is_configured_true_with_both_credentials(monkeypatch):
    monkeypatch.setattr("src.yandex_oauth.CLIENT_ID", "abc")
    monkeypatch.setattr("src.yandex_oauth.CLIENT_SECRET", "def")
    assert is_configured() is True


def test_request_device_code_parses_response():
    payload = (
        b'{"device_code": "dc123", "user_code": "AB12CD", '
        b'"verification_url": "https://ya.ru/device", "interval": 5, "expires_in": 600}'
    )
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        info = request_device_code()

    assert info.device_code == "dc123"
    assert info.user_code == "AB12CD"
    assert info.verification_url == "https://ya.ru/device"
    assert info.interval == 5
    assert info.expires_in == 600


def test_request_device_code_sends_scope_and_client_id(monkeypatch):
    monkeypatch.setattr("src.yandex_oauth.CLIENT_ID", "my-client-id")
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["data"] = request.data.decode("utf-8")
        return DummyResponse(b'{"device_code": "d", "user_code": "u", '
                              b'"verification_url": "https://ya.ru/device", '
                              b'"interval": 5, "expires_in": 600}')

    with patch("src.yandex_oauth.urlopen", side_effect=fake_urlopen):
        request_device_code()

    assert "client_id=my-client-id" in captured["data"]
    assert "cloud_api" in captured["data"]


def test_oauth_uses_bundled_ca_store():
    payload = (
        b'{"device_code": "dc123", "user_code": "AB12CD", '
        b'"verification_url": "https://ya.ru/device", "interval": 5, "expires_in": 600}'
    )
    context = object()

    with patch("src.yandex_oauth.certifi.where", return_value="/bundle/cacert.pem") as where, \
         patch("src.yandex_oauth.ssl.create_default_context", return_value=context) as create_context, \
         patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)) as open_url:
        request_device_code()

    where.assert_called_once_with()
    create_context.assert_called_once_with(cafile="/bundle/cacert.pem")
    assert open_url.call_args.kwargs["context"] is context


def test_request_device_code_raises_on_missing_device_code():
    payload = b'{"error": "invalid_client", "error_description": "no such client"}'
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        with pytest.raises(YandexOAuthError, match="no such client"):
            request_device_code()


def test_poll_for_token_returns_success():
    payload = b'{"access_token": "y0_secret", "token_type": "bearer", "expires_in": 31536000}'
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        result = poll_for_token("dc123")

    assert result.status == "success"
    assert result.access_token == "y0_secret"


def test_poll_for_token_returns_pending():
    payload = b'{"error": "authorization_pending"}'
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        result = poll_for_token("dc123")
    assert result.status == "pending"


def test_poll_for_token_returns_slow_down():
    payload = b'{"error": "slow_down"}'
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        result = poll_for_token("dc123")
    assert result.status == "slow_down"


def test_poll_for_token_returns_error_on_denied():
    payload = b'{"error": "access_denied", "error_description": "\xd0\x9e\xd1\x82\xd0\xba\xd0\xb0\xd0\xb7\xd0\xb0\xd0\xbd\xd0\xbe"}'
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        result = poll_for_token("dc123")
    assert result.status == "error"
    assert result.error_message


def test_poll_for_token_returns_error_on_expired_code():
    payload = b'{"error": "expired_token"}'
    with patch("src.yandex_oauth.urlopen", return_value=DummyResponse(payload)):
        result = poll_for_token("dc123")
    assert result.status == "error"


def test_post_form_wraps_http_error_body_as_json():
    # Яндекс возвращает информативные JSON-ошибки даже при не-200 статусе
    # (например 400 Bad Request с error/error_description в теле).
    error_body = b'{"error": "invalid_grant", "error_description": "code expired"}'

    class FakeFP:
        def read(self):
            return error_body

        def close(self):
            pass

    error = HTTPError(url="x", code=400, msg="Bad Request", hdrs=None, fp=FakeFP())
    with patch("src.yandex_oauth.urlopen", side_effect=error):
        result = poll_for_token("dc123")
    assert result.status == "error"
    assert "code expired" in result.error_message


def test_post_form_raises_on_network_error():
    from urllib.error import URLError

    with patch("src.yandex_oauth.urlopen", side_effect=URLError("no network")):
        with pytest.raises(YandexOAuthError):
            request_device_code()


def test_open_browser_does_not_block_gui_thread():
    release = threading.Event()
    entered = threading.Event()

    def slow_open(_url):
        entered.set()
        release.wait(timeout=2)

    dialog = SimpleNamespace(
        _verification_url="https://oauth.yandex.test/device",
        _open_browser_url=slow_open,
    )
    YandexOAuthDialog._open_browser(dialog)
    assert entered.wait(timeout=1)
    # Метод уже вернулся, хотя работа фонового потока ещё заблокирована.
    assert not release.is_set()
    release.set()
