import json

import pytest
from unittest.mock import patch

from src.yandexgpt_service import YandexGPTError, YandexGPTService


class DummyResponse:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patched(key="AQVN_test", folder="folder123"):
    return (
        patch("src.yandexgpt_service.secret_store.load_yandexgpt_key", return_value=key),
        patch("src.yandexgpt_service.secret_store.load_yandexgpt_folder_id", return_value=folder),
    )


def test_generate_raises_without_api_key():
    service = YandexGPTService({"llm": {}})
    with patch("src.yandexgpt_service.secret_store.load_yandexgpt_key", return_value=""), \
         patch("src.yandexgpt_service.secret_store.load_yandexgpt_folder_id", return_value="folder123"):
        with pytest.raises(YandexGPTError, match="не заданы"):
            service.generate("текст")


def test_generate_raises_without_folder_id():
    service = YandexGPTService({"llm": {}})
    with patch("src.yandexgpt_service.secret_store.load_yandexgpt_key", return_value="AQVN_test"), \
         patch("src.yandexgpt_service.secret_store.load_yandexgpt_folder_id", return_value=""):
        with pytest.raises(YandexGPTError, match="не заданы"):
            service.generate("текст")


def test_generate_returns_message_text():
    service = YandexGPTService({"llm": {"yandexgpt_model": "yandexgpt/latest"}})
    payload = '{"result": {"alternatives": [{"message": {"text": "готовый ответ"}}]}}'

    p1, p2 = _patched()
    with p1, p2, patch("src.yandexgpt_service.urlopen", return_value=DummyResponse(payload)):
        assert service.generate("текст") == "готовый ответ"


def test_generate_sends_api_key_folder_header_and_model_uri():
    service = YandexGPTService({"llm": {"yandexgpt_model": "yandexgpt/latest"}})
    payload = '{"result": {"alternatives": [{"message": {"text": "ok"}}]}}'
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return DummyResponse(payload)

    p1, p2 = _patched(key="AQVN_abc", folder="b1gfolder")
    with p1, p2, patch("src.yandexgpt_service.urlopen", side_effect=fake_urlopen):
        service.generate("привет")

    request = captured["request"]
    assert request.get_header("Authorization") == "Api-Key AQVN_abc"
    assert request.get_header("X-folder-id") == "b1gfolder"
    body = json.loads(request.data.decode("utf-8"))
    assert body["modelUri"] == "gpt://b1gfolder/yandexgpt/latest"
    assert body["messages"] == [{"role": "user", "text": "привет"}]


def test_generate_raises_yandexgpt_error_on_http_error():
    from urllib.error import HTTPError
    import io

    service = YandexGPTService({"llm": {}})

    def fake_urlopen(request, timeout=None):
        raise HTTPError(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion", 401,
            "Unauthorized", {}, io.BytesIO(b'{"error": "invalid api key"}'),
        )

    p1, p2 = _patched()
    with p1, p2, patch("src.yandexgpt_service.urlopen", side_effect=fake_urlopen):
        with pytest.raises(YandexGPTError, match="401"):
            service.generate("текст")


def test_generate_raises_yandexgpt_error_on_unexpected_payload():
    service = YandexGPTService({"llm": {}})

    p1, p2 = _patched()
    with p1, p2, patch("src.yandexgpt_service.urlopen", return_value=DummyResponse('{"unexpected": true}')):
        with pytest.raises(YandexGPTError, match="Неожиданный формат"):
            service.generate("текст")


def test_get_status_reports_missing_credentials_without_network_call():
    service = YandexGPTService({"llm": {}})

    with patch("src.yandexgpt_service.secret_store.load_yandexgpt_key", return_value=""), \
         patch("src.yandexgpt_service.secret_store.load_yandexgpt_folder_id", return_value=""), \
         patch("src.yandexgpt_service.urlopen") as mock_urlopen:
        status = service.get_status()

    mock_urlopen.assert_not_called()
    assert status["reachable"] is False
    assert status["has_key"] is False
    assert "не заданы" in status["error"]


def test_get_status_reports_configured_without_network_call():
    """get_status не должен дёргать платный API — статус только по наличию

    ключа+folder_id (см. докстринг YandexGPTService.get_status)."""
    service = YandexGPTService({"llm": {}})

    p1, p2 = _patched()
    with p1, p2, patch("src.yandexgpt_service.urlopen") as mock_urlopen:
        status = service.get_status()

    mock_urlopen.assert_not_called()
    assert status["reachable"] is True
    assert status["has_key"] is True
    assert status["error"] is None


def test_get_api_key_falls_back_to_config_when_keychain_empty():
    service = YandexGPTService({"llm": {"yandexgpt_api_key": "config-key"}})

    with patch("src.yandexgpt_service.secret_store.load_yandexgpt_key", return_value=""):
        assert service.get_api_key() == "config-key"


def test_get_folder_id_falls_back_to_config_when_keychain_empty():
    service = YandexGPTService({"llm": {"yandexgpt_folder_id": "config-folder"}})

    with patch("src.yandexgpt_service.secret_store.load_yandexgpt_folder_id", return_value=""):
        assert service.get_folder_id() == "config-folder"
