"""YandexGPT (Yandex Cloud Foundation Models) Service Module

Единая обёртка над Foundation Models API Yandex Cloud. Интерфейс
(generate/check_status/get_status) намеренно зеркалит
src/ollama_service.py::OllamaService и src/groq_service.py::GroqService —
ConclusionGenerator использует все три сервиса взаимозаменяемо через один
и тот же вызов _ollama_generate.

YandexGPT выбран как облачный вариант для регионов, где Groq/OpenAI/
Anthropic недоступны без VPN: российский сервис, работает напрямую,
данные хранятся на серверах в России (152-ФЗ). На тарифе Pro и выше
(в отличие от Lite) данные клиента по умолчанию не используются для
обучения моделей — используйте groq_model="yandexgpt/latest" (Pro), а не
"yandexgpt-lite/latest", если это важно.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src import secret_store

logger = logging.getLogger(__name__)

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


class YandexGPTError(Exception):
    """Ошибка обращения к Foundation Models API (сеть, авторизация, формат ответа)."""


class YandexGPTService:
    """Единый слой доступа к YandexGPT (Yandex Cloud Foundation Models API)."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})

        # "yandexgpt/latest" — тариф Pro (данные не используются для обучения
        # по умолчанию); "yandexgpt-lite/latest" — дешевле, но приватность
        # только по отдельному запросу в поддержку.
        self.model = llm_cfg.get("yandexgpt_model", "yandexgpt/latest")
        self.temperature = llm_cfg.get("temperature", 0.15)
        self.max_tokens = llm_cfg.get("max_tokens", 2000)
        self.timeout = llm_cfg.get("timeout", 60)

    def get_api_key(self) -> str:
        """Ключ из Связки ключей macOS; фолбэк на config, если недоступна."""
        key = secret_store.load_yandexgpt_key()
        if key:
            return key
        return self.config.get("llm", {}).get("yandexgpt_api_key", "") or ""

    def get_folder_id(self) -> str:
        """folder_id из Связки ключей macOS; фолбэк на config, если недоступна."""
        folder_id = secret_store.load_yandexgpt_folder_id()
        if folder_id:
            return folder_id
        return self.config.get("llm", {}).get("yandexgpt_folder_id", "") or ""

    def _model_uri(self, model: Optional[str] = None) -> str:
        folder_id = self.get_folder_id()
        return f"gpt://{folder_id}/{model or self.model}"

    def _request(self, payload: dict) -> dict:
        api_key = self.get_api_key()
        folder_id = self.get_folder_id()
        if not api_key or not folder_id:
            raise YandexGPTError("API-ключ или folder_id YandexGPT не заданы (см. настройки)")

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Api-Key {api_key}",
            "x-folder-id": folder_id,
            "Content-Type": "application/json",
        }
        request = Request(API_URL, data=data, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise YandexGPTError(f"YandexGPT API вернул {exc.code}: {body[:300]}") from exc
        except URLError as exc:
            raise YandexGPTError(f"YandexGPT API недоступен: {exc.reason}") from exc

    def check_status(self) -> bool:
        """Проверка доступности YandexGPT (заданы ли ключ и folder_id)."""
        return self.get_status()["reachable"]

    def get_status(self) -> dict:
        """Статус подключения — для UI, по аналогии с GroqService.get_status().

        В отличие от Ollama/Groq, здесь НЕ делается реальный сетевой запрос:
        у Foundation Models API нет бесплатного health-check эндпоинта вроде
        /v1/models, единственный способ проверить реальную доступность —
        платный completion-запрос. Индикатор в UI опрашивается раз в 5 сек,
        пока включена генерация, — дёргать платный API с такой частотой не
        нужно. "reachable" здесь означает "ключ и folder_id заданы"; реальные
        сетевые ошибки всплывут при настоящей генерации и обрабатываются
        поштучным откатом на python-текст (см. _summarize_item_with_llm).
        """
        has_key = bool(self.get_api_key() and self.get_folder_id())
        return {
            "reachable": has_key,
            "model": self.model,
            "has_key": has_key,
            "error": None if has_key else "API-ключ или folder_id YandexGPT не заданы",
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> str:
        """Генерирует текст через Foundation Models completion и возвращает текст ответа."""
        options = options or {}
        max_tokens = options.get("num_predict") or self.max_tokens
        payload = {
            "modelUri": self._model_uri(model),
            "completionOptions": {
                "stream": False,
                "temperature": options.get("temperature", self.temperature),
                "maxTokens": str(max_tokens),
            },
            "messages": [{"role": "user", "text": prompt}],
        }

        response = self._request(payload)
        try:
            return response["result"]["alternatives"][0]["message"]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise YandexGPTError(f"Неожиданный формат ответа YandexGPT: {response}") from exc
