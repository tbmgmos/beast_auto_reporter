"""
Ollama Service Module

Единая обёртка над локальным Ollama SDK.
Используется всеми AI-ветками проекта, чтобы не дублировать
инициализацию клиента, host/timeout и обработку совместимости SDK.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class OllamaService:
    """Единый слой доступа к Ollama."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})

        self.model = llm_cfg.get("model", "gemma3:12b")
        self.temperature = llm_cfg.get("temperature", 0.15)
        self.max_tokens = llm_cfg.get("max_tokens", 2000)
        self.timeout = llm_cfg.get("timeout", 60)
        self.host = llm_cfg.get("ollama_host", "http://localhost:11434")

    def _import_ollama(self):
        import ollama

        return ollama

    def _api_url(self, path: str) -> str:
        """Собирает URL для Ollama HTTP API."""
        return f"{self.host.rstrip('/')}{path}"

    def _http_request(self, path: str, payload: Optional[dict] = None) -> dict:
        """
        Делает прямой HTTP-запрос к Ollama API.
        Используется как fallback, если Python SDK не установлен.
        """
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(self._api_url(path), data=data, headers=headers)
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def get_client(self):
        """
        Возвращает клиент Ollama.
        Если SDK поддерживает Client(host=...), используем его.
        Иначе откатываемся к модульному API.
        """
        try:
            ollama = self._import_ollama()
        except ModuleNotFoundError:
            logger.info("Python-пакет ollama не установлен, используем HTTP API fallback")
            return None

        client_cls = getattr(ollama, "Client", None)
        if client_cls is None:
            if self.host != "http://localhost:11434":
                logger.warning(
                    "Текущий ollama SDK не поддерживает host override, используется host по умолчанию"
                )
            return ollama

        return client_cls(host=self.host, timeout=self.timeout)

    def check_status(self) -> bool:
        """Проверка доступности Ollama через настроенный host."""
        try:
            client = self.get_client()
            if client is None:
                self._http_request("/api/tags")
            else:
                client.list()
            return True
        except Exception as exc:
            logger.debug(f"Ollama недоступна ({self.host}): {exc}")
            return False

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> str:
        """Генерирует текст через Ollama и возвращает поле response."""
        client = self.get_client()
        if client is None:
            response = self._http_request(
                "/api/generate",
                {
                    "model": model or self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": options or {},
                },
            )
        else:
            response = client.generate(
                model=model or self.model,
                prompt=prompt,
                options=options or {},
            )
        return response["response"].strip()
