import json
import time

import pytest
from unittest.mock import patch

from src.gigachat_service import GigaChatError, GigaChatService


class DummyResponse:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


OAUTH_PAYLOAD = json.dumps({"access_token": "token-abc", "expires_at": int((time.time() + 1800) * 1000)})
CHAT_PAYLOAD = json.dumps({"choices": [{"message": {"content": "готовый ответ"}}]})


def _fake_urlopen_factory(oauth_payload=OAUTH_PAYLOAD, chat_payload=CHAT_PAYLOAD, captured=None):
    captured = captured if captured is not None else {}
    calls = []

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(request)
        if "oauth" in request.full_url:
            captured["oauth_request"] = request
            return DummyResponse(oauth_payload)
        captured["chat_request"] = request
        return DummyResponse(chat_payload)

    return fake_urlopen, calls


def _with_key(key="Y2xpZW50OnNlY3JldA=="):
    return patch("src.gigachat_service.secret_store.load_gigachat_key", return_value=key)


def test_generate_raises_without_auth_key():
    service = GigaChatService({"llm": {}})
    with patch("src.gigachat_service.secret_store.load_gigachat_key", return_value=""):
        with pytest.raises(GigaChatError, match="не задан"):
            service.generate("текст")


def test_generate_returns_message_content():
    service = GigaChatService({"llm": {}})
    fake_urlopen, _ = _fake_urlopen_factory()
    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        assert service.generate("текст") == "готовый ответ"


def test_generate_sends_oauth_then_bearer_chat_request():
    service = GigaChatService({"llm": {"gigachat_model": "GigaChat"}})
    captured = {}
    fake_urlopen, _ = _fake_urlopen_factory(captured=captured)

    with _with_key(key="Y2xpZW50OnNlY3JldA=="), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        service.generate("привет")

    oauth_request = captured["oauth_request"]
    assert oauth_request.get_header("Authorization") == "Basic Y2xpZW50OnNlY3JldA=="
    assert oauth_request.get_header("Rquid")
    assert oauth_request.data == b"scope=GIGACHAT_API_PERS"

    chat_request = captured["chat_request"]
    assert chat_request.get_header("Authorization") == "Bearer token-abc"
    body = json.loads(chat_request.data.decode("utf-8"))
    assert body["model"] == "GigaChat"
    assert body["messages"] == [{"role": "user", "content": "привет"}]


def test_generate_reuses_cached_token_within_expiry():
    service = GigaChatService({"llm": {}})
    fake_urlopen, calls = _fake_urlopen_factory()

    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        service.generate("первый")
        service.generate("второй")

    oauth_calls = [c for c in calls if "oauth" in c.full_url]
    assert len(oauth_calls) == 1


def test_generate_refetches_token_after_expiry():
    service = GigaChatService({"llm": {}})
    expired_oauth_payload = json.dumps({"access_token": "token-old", "expires_at": int((time.time() - 1) * 1000)})
    fake_urlopen, calls = _fake_urlopen_factory(oauth_payload=expired_oauth_payload)

    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        service.generate("первый")
        service.generate("второй")

    oauth_calls = [c for c in calls if "oauth" in c.full_url]
    assert len(oauth_calls) == 2


def test_generate_retries_once_on_401_with_fresh_token():
    from urllib.error import HTTPError
    import io

    service = GigaChatService({"llm": {}})
    state = {"chat_attempts": 0}

    def fake_urlopen(request, timeout=None, context=None):
        if "oauth" in request.full_url:
            return DummyResponse(OAUTH_PAYLOAD)
        state["chat_attempts"] += 1
        if state["chat_attempts"] == 1:
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error": "expired"}'))
        return DummyResponse(CHAT_PAYLOAD)

    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        assert service.generate("текст") == "готовый ответ"
    assert state["chat_attempts"] == 2


def test_generate_raises_gigachat_error_on_persistent_http_error():
    from urllib.error import HTTPError
    import io

    service = GigaChatService({"llm": {}})

    def fake_urlopen(request, timeout=None, context=None):
        if "oauth" in request.full_url:
            return DummyResponse(OAUTH_PAYLOAD)
        raise HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO(b"internal error"))

    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        with pytest.raises(GigaChatError, match="500"):
            service.generate("текст")


def test_generate_raises_gigachat_error_on_unexpected_payload():
    service = GigaChatService({"llm": {}})
    fake_urlopen, _ = _fake_urlopen_factory(chat_payload='{"unexpected": true}')

    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        with pytest.raises(GigaChatError, match="Неожиданный формат"):
            service.generate("текст")


def test_get_status_reports_missing_key_without_network_call():
    service = GigaChatService({"llm": {}})
    with patch("src.gigachat_service.secret_store.load_gigachat_key", return_value=""), \
         patch("src.gigachat_service.urlopen") as mock_urlopen:
        status = service.get_status()

    mock_urlopen.assert_not_called()
    assert status["reachable"] is False
    assert status["has_key"] is False
    assert "не задан" in status["error"]


def test_get_status_reachable_when_oauth_succeeds():
    service = GigaChatService({"llm": {}})
    fake_urlopen, _ = _fake_urlopen_factory()

    with _with_key(), patch("src.gigachat_service.urlopen", side_effect=fake_urlopen):
        status = service.get_status()

    assert status["reachable"] is True
    assert status["has_key"] is True
    assert status["error"] is None


def test_get_auth_key_falls_back_to_config_when_keychain_empty():
    service = GigaChatService({"llm": {"gigachat_auth_key": "config-key"}})
    with patch("src.gigachat_service.secret_store.load_gigachat_key", return_value=""):
        assert service.get_auth_key() == "config-key"
