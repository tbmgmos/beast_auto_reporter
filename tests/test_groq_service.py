import json

import pytest
from unittest.mock import patch

from src.groq_service import GroqError, GroqService


class DummyResponse:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_generate_raises_without_api_key():
    service = GroqService({"llm": {}})
    with patch("src.groq_service.secret_store.load_groq_key", return_value=""):
        with pytest.raises(GroqError, match="ключ не задан"):
            service.generate("тестовый промпт")


def test_generate_returns_message_content():
    service = GroqService({"llm": {"model": "llama-3.3-70b-versatile"}})
    payload = '{"choices": [{"message": {"content": "готовый ответ"}}]}'

    with patch("src.groq_service.secret_store.load_groq_key", return_value="gsk_test"), \
         patch("src.groq_service.urlopen", return_value=DummyResponse(payload)):
        assert service.generate("тестовый промпт") == "готовый ответ"


def test_generate_sends_bearer_auth_and_model():
    service = GroqService({"llm": {"model": "llama-3.3-70b-versatile"}})
    payload = '{"choices": [{"message": {"content": "ok"}}]}'
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return DummyResponse(payload)

    with patch("src.groq_service.secret_store.load_groq_key", return_value="gsk_abc123"), \
         patch("src.groq_service.urlopen", side_effect=fake_urlopen):
        service.generate("привет")

    request = captured["request"]
    assert request.get_header("Authorization") == "Bearer gsk_abc123"
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["messages"] == [{"role": "user", "content": "привет"}]


def test_generate_raises_groq_error_on_http_error():
    from urllib.error import HTTPError
    import io

    service = GroqService({"llm": {}})

    def fake_urlopen(request, timeout=None):
        raise HTTPError(
            "https://api.groq.com/openai/v1/chat/completions", 401,
            "Unauthorized", {}, io.BytesIO(b'{"error": "invalid api key"}'),
        )

    with patch("src.groq_service.secret_store.load_groq_key", return_value="bad-key"), \
         patch("src.groq_service.urlopen", side_effect=fake_urlopen):
        with pytest.raises(GroqError, match="401"):
            service.generate("текст")


def test_generate_raises_groq_error_on_unexpected_payload():
    service = GroqService({"llm": {}})

    with patch("src.groq_service.secret_store.load_groq_key", return_value="gsk_x"), \
         patch("src.groq_service.urlopen", return_value=DummyResponse('{"unexpected": true}')):
        with pytest.raises(GroqError, match="Неожиданный формат"):
            service.generate("текст")


def test_get_status_reports_missing_key_without_network_call():
    service = GroqService({"llm": {}})

    with patch("src.groq_service.secret_store.load_groq_key", return_value=""), \
         patch("src.groq_service.urlopen") as mock_urlopen:
        status = service.get_status()

    mock_urlopen.assert_not_called()
    assert status["reachable"] is False
    assert status["has_key"] is False
    assert "ключ не задан" in status["error"]


def test_get_status_reachable_with_valid_key():
    service = GroqService({"llm": {"model": "llama-3.3-70b-versatile"}})

    with patch("src.groq_service.secret_store.load_groq_key", return_value="gsk_test"), \
         patch("src.groq_service.urlopen", return_value=DummyResponse('{"data": []}')):
        status = service.get_status()

    assert status["reachable"] is True
    assert status["has_key"] is True
    assert status["error"] is None


def test_get_api_key_falls_back_to_config_when_keychain_empty():
    service = GroqService({"llm": {"groq_api_key": "config-fallback-key"}})

    with patch("src.groq_service.secret_store.load_groq_key", return_value=""):
        assert service.get_api_key() == "config-fallback-key"


def test_get_api_key_prefers_keychain_over_config():
    service = GroqService({"llm": {"groq_api_key": "config-key"}})

    with patch("src.groq_service.secret_store.load_groq_key", return_value="keychain-key"):
        assert service.get_api_key() == "keychain-key"
