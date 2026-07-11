from unittest.mock import patch

from src.ollama_service import OllamaService


class DummyResponse:
    def __init__(self, payload: str):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_ollama_service_check_status_falls_back_to_http_when_sdk_missing():
    service = OllamaService({"llm": {"ollama_host": "http://127.0.0.1:11434"}})

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", return_value=DummyResponse('{"models": []}')):
        assert service.check_status() is True


def test_ollama_service_generate_falls_back_to_http_when_sdk_missing():
    service = OllamaService({"llm": {"model": "gemma3:12b"}})

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", return_value=DummyResponse('{"response": "готово"}')):
        assert service.generate("test prompt") == "готово"


def test_get_status_reports_unreachable_when_ollama_not_running():
    service = OllamaService({"llm": {"model": "gemma3:12b"}})

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", side_effect=OSError("connection refused")):
        status = service.get_status()

    assert status["reachable"] is False
    assert status["model_installed"] is False
    assert status["installed_models"] == []
    assert status["error"]


def test_get_status_reports_reachable_but_model_missing():
    service = OllamaService({"llm": {"model": "gemma3:12b"}})
    payload = '{"models": [{"name": "llama3:8b"}, {"name": "mistral:7b"}]}'

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", return_value=DummyResponse(payload)):
        status = service.get_status()

    assert status["reachable"] is True
    assert status["model_installed"] is False
    assert status["installed_models"] == ["llama3:8b", "mistral:7b"]


def test_get_status_does_not_confuse_different_model_variants():
    service = OllamaService({"llm": {"model": "gemma3:12b"}})
    payload = '{"models": [{"name": "gemma3:12b-instruct-q4"}]}'

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", return_value=DummyResponse(payload)):
        status = service.get_status()

    assert status["reachable"] is True
    assert status["model_installed"] is False  # разные теги одной базовой модели — не взаимозаменяемы


def test_get_status_matches_missing_tag_as_latest():
    service = OllamaService({"llm": {"model": "gemma3"}})
    payload = '{"models": [{"name": "gemma3:latest"}]}'

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", return_value=DummyResponse(payload)):
        status = service.get_status()

    assert status["model_installed"] is True


def test_get_status_reports_model_installed_exact_match():
    service = OllamaService({"llm": {"model": "gemma3:12b"}})
    payload = '{"models": [{"name": "gemma3:12b"}]}'

    with patch.object(service, "_import_ollama", side_effect=ModuleNotFoundError), \
         patch("src.ollama_service.urlopen", return_value=DummyResponse(payload)):
        status = service.get_status()

    assert status["reachable"] is True
    assert status["model_installed"] is True
