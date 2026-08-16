"""GigaChat (Сбер) Service Module

Единая обёртка над GigaChat API. Интерфейс (generate/check_status/get_status)
намеренно зеркалит src/ollama_service.py::OllamaService, src/groq_service.py
::GroqService и src/yandexgpt_service.py::YandexGPTService — ConclusionGenerator
использует все четыре сервиса взаимозаменяемо через один и тот же вызов
_ollama_generate.

GigaChat выбран как единственный из рассмотренных вариантов, который одновременно
бесплатен (1 млн токенов/мес на тарифе для физлиц, GIGACHAT_API_PERS) и работает
без VPN в РФ — в отличие от Groq (нужен VPN) и YandexGPT (без VPN, но платный
с первого токена).

Два отличия от остальных облачных провайдеров:

1. Авторизация — OAuth2, а не статичный API-ключ. Пользователь вводит один раз
   "Authorization key" (base64 client_id:client_secret, выдаётся в личном
   кабинете developers.sber.ru) — из него на каждый запрос обменивается
   короткоживущий access_token (30 минут). Токен кэшируется в памяти процесса
   и обновляется автоматически по истечении, без участия пользователя.

2. TLS — сертификаты обоих эндпоинтов (oauth и chat) выпущены удостоверяющим
   центром Минцифры России, которому macOS не доверяет по умолчанию (санкции
   отрезали Сбер от обычных зарубежных CA). Вместо того чтобы просить
   пользователя вручную устанавливать сертификат в системный Keychain —
   что меняет доверие для ВСЕХ приложений на машине — используем собранный
   в комплекте bundle (src/certs/russian_trusted_ca_bundle.pem, содержит
   Russian Trusted Root CA + Sub CA) и подключаемся с явным ssl.SSLContext,
   ограниченным только этим сервисом.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src import secret_store

logger = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

CA_BUNDLE_PATH = Path(__file__).resolve().with_name("certs") / "russian_trusted_ca_bundle.pem"

# access_token живёт 30 минут — обновляем немного заранее, чтобы не словить
# «токен истёк между проверкой и отправкой запроса» на медленной сети.
_TOKEN_REFRESH_MARGIN_SEC = 60


class GigaChatError(Exception):
    """Ошибка обращения к GigaChat API (сеть, авторизация, формат ответа, сертификат)."""


class GigaChatService:
    """Единый слой доступа к GigaChat API (OAuth2 + chat/completions)."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})

        self.model = llm_cfg.get("gigachat_model", "GigaChat")
        self.scope = llm_cfg.get("gigachat_scope", "GIGACHAT_API_PERS")
        self.temperature = llm_cfg.get("temperature", 0.15)
        self.max_tokens = llm_cfg.get("max_tokens", 2000)
        self.timeout = llm_cfg.get("timeout", 60)

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._ssl_context: Optional[ssl.SSLContext] = None

    def get_auth_key(self) -> str:
        """"Authorization key" (base64 client_id:client_secret) из Связки


        ключей macOS; фолбэк на config, если недоступна.
        """
        key = secret_store.load_gigachat_key()
        if key:
            return key
        return self.config.get("llm", {}).get("gigachat_auth_key", "") or ""

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is not None:
            return self._ssl_context
        if not CA_BUNDLE_PATH.exists():
            raise GigaChatError(
                f"Не найден сертификат НУЦ Минцифры (ожидался в {CA_BUNDLE_PATH}) — "
                "переустановите приложение."
            )
        self._ssl_context = ssl.create_default_context(cafile=str(CA_BUNDLE_PATH))
        return self._ssl_context

    def _fetch_access_token(self) -> str:
        auth_key = self.get_auth_key()
        if not auth_key:
            raise GigaChatError("GigaChat Authorization key не задан (см. настройки)")

        data = urlencode({"scope": self.scope}).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {auth_key}",
        }
        request = Request(OAUTH_URL, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout, context=self._get_ssl_context()) as response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw) if raw else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GigaChatError(f"GigaChat OAuth вернул {exc.code}: {body[:300]}") from exc
        except URLError as exc:
            raise GigaChatError(f"GigaChat OAuth недоступен: {exc.reason}") from exc

        token = payload.get("access_token")
        if not token:
            raise GigaChatError(f"Неожиданный формат ответа GigaChat OAuth: {payload}")

        # expires_at из ответа — unix-время в миллисекундах; на всякий случай
        # не полагаемся только на него и держим свой запас (_TOKEN_REFRESH_MARGIN_SEC).
        expires_at_ms = payload.get("expires_at")
        if expires_at_ms:
            self._token_expires_at = expires_at_ms / 1000.0 - _TOKEN_REFRESH_MARGIN_SEC
        else:
            self._token_expires_at = time.time() + 30 * 60 - _TOKEN_REFRESH_MARGIN_SEC

        self._access_token = token
        return token

    def _get_access_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        return self._fetch_access_token()

    def _request(self, payload: dict, *, _retried: bool = False) -> dict:
        access_token = self._get_access_token()
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        request = Request(API_URL, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout, context=self._get_ssl_context()) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code == 401 and not _retried:
                # access_token мог протухнуть раньше расчётного времени
                # (например, был отозван) — один раз принудительно обновляем
                # и повторяем, прежде чем сдаваться.
                self._get_access_token(force_refresh=True)
                return self._request(payload, _retried=True)
            body = exc.read().decode("utf-8", errors="replace")
            raise GigaChatError(f"GigaChat API вернул {exc.code}: {body[:300]}") from exc
        except URLError as exc:
            raise GigaChatError(f"GigaChat API недоступен: {exc.reason}") from exc

    def check_status(self) -> bool:
        """Проверка доступности GigaChat (валидный auth key + сеть)."""
        try:
            self._get_access_token()
            return True
        except GigaChatError as exc:
            logger.debug(f"GigaChat недоступен: {exc}")
            return False

    def get_status(self) -> dict:
        """Статус подключения — для UI, по аналогии с GroqService.get_status().

        Реально дёргает OAuth (бесплатный, не тратит лимит токенов на
        completion), поэтому, в отличие от YandexGPTService.get_status(),
        может честно проверить сеть/ключ, а не только их наличие.
        """
        status = {
            "reachable": False,
            "model": self.model,
            "has_key": bool(self.get_auth_key()),
            "error": None,
        }
        if not status["has_key"]:
            status["error"] = "GigaChat Authorization key не задан"
            return status
        try:
            self._get_access_token()
            status["reachable"] = True
        except GigaChatError as exc:
            status["error"] = str(exc)
        return status

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> str:
        """Генерирует текст через GigaChat chat/completions и возвращает текст ответа."""
        options = options or {}
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": options.get("temperature", self.temperature),
        }
        max_tokens = options.get("num_predict") or self.max_tokens
        if max_tokens:
            payload["max_tokens"] = max_tokens

        response = self._request(payload)
        try:
            return response["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise GigaChatError(f"Неожиданный формат ответа GigaChat: {response}") from exc
