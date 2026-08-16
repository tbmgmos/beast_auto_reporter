"""Groq Cloud Service Module

Единая обёртка над Groq Cloud API (OpenAI-совместимый chat/completions).
Интерфейс (generate/check_status/get_status) намеренно зеркалит
src/ollama_service.py::OllamaService — ConclusionGenerator использует оба
сервиса взаимозаменяемо через один и тот же вызов _ollama_generate.

Groq выбран как облачный вариант для больших маркер-листов: хостит чужие
открытые веса (Llama 3.3 70B и т.п.) на быстром железе, бесплатный тариф,
и по договору не использует входы/выходы API для обучения моделей ни на
каком тарифе (см. https://console.groq.com/docs/your-data).
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src import secret_store

logger = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"


class GroqError(Exception):
    """Ошибка обращения к Groq API (сеть, авторизация, формат ответа)."""


class GroqService:
    """Единый слой доступа к Groq Cloud API."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})

        self.model = llm_cfg.get("groq_model", "llama-3.3-70b-versatile")
        self.temperature = llm_cfg.get("temperature", 0.15)
        self.max_tokens = llm_cfg.get("max_tokens", 2000)
        self.timeout = llm_cfg.get("timeout", 60)

    def get_api_key(self) -> str:
        """Ключ из Связки ключей macOS; если недоступна — из конфига

        (settings.json, если пользователь сохранил его туда явно как
        временный фолбэк). Никогда не хардкодится и не запрашивается сама
        по себе — пользователь вводит его один раз в настройках.
        """
        key = secret_store.load_groq_key()
        if key:
            return key
        return self.config.get("llm", {}).get("groq_api_key", "") or ""

    def _request(self, url: str, payload: Optional[dict] = None) -> dict:
        api_key = self.get_api_key()
        if not api_key:
            raise GroqError("Groq API-ключ не задан (см. настройки)")

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Дефолтный User-Agent urllib ("Python-urllib/3.x") — классическая
            # сигнатура бота, которую Cloudflare Browser Integrity Check
            # блокирует до того, как запрос вообще доходит до API Groq
            # (see: ошибка "error code: 1010" вместо ответа от самого Groq).
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
        }
        request = Request(url, data=data, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GroqError(f"Groq API вернул {exc.code}: {body[:300]}") from exc
        except URLError as exc:
            raise GroqError(f"Groq API недоступен: {exc.reason}") from exc

    def check_status(self) -> bool:
        """Проверка доступности Groq (валидный ключ + сеть)."""
        try:
            self._request(MODELS_URL)
            return True
        except GroqError as exc:
            logger.debug(f"Groq недоступен: {exc}")
            return False

    def get_status(self) -> dict:
        """Статус подключения — для UI, по аналогии с OllamaService.get_status()."""
        status = {
            "reachable": False,
            "model": self.model,
            "has_key": bool(self.get_api_key()),
            "error": None,
        }
        if not status["has_key"]:
            status["error"] = "Groq API-ключ не задан"
            return status
        try:
            self._request(MODELS_URL)
            status["reachable"] = True
        except GroqError as exc:
            status["error"] = str(exc)
        return status

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> str:
        """Генерирует текст через Groq chat/completions и возвращает текст ответа.

        options принимает те же ключи, что и OllamaService.generate, для
        совместимости вызова из ConclusionGenerator._ollama_generate —
        num_predict/num_ctx у Groq не нужны (нет локального контекстного
        окна, которым надо явно управлять) и просто игнорируются.
        """
        options = options or {}
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": options.get("temperature", self.temperature),
        }
        max_tokens = options.get("num_predict") or self.max_tokens
        if max_tokens:
            payload["max_tokens"] = max_tokens

        response = self._request(API_URL, payload)
        try:
            return response["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise GroqError(f"Неожиданный формат ответа Groq: {response}") from exc
