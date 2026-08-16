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

        self.model = llm_cfg.get("model", "gemma4:latest")
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

    @staticmethod
    def _extract_model_names(raw_list) -> list:
        """
        Нормализует ответ ollama.list()/api/tags к списку имён моделей.
        SDK и HTTP API отдают разные формы (dict с ключом "models",
        объекты с атрибутом .model вместо .name в новых версиях SDK и т.п.).
        """
        entries = raw_list
        if isinstance(raw_list, dict):
            entries = raw_list.get("models", [])
        elif hasattr(raw_list, "models"):
            entries = raw_list.models

        names = []
        for entry in entries or []:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("model")
            else:
                name = getattr(entry, "name", None) or getattr(entry, "model", None)
            if name:
                names.append(name)
        return names

    @staticmethod
    def _model_matches(configured: str, installed: str) -> bool:
        """
        Сравнивает имена моделей терпимо только к отсутствующему тегу
        (эквивалент неявного ":latest"). НЕ считает совпадением разные
        варианты одной базовой модели (например, "gemma4:12b" и
        "gemma4:12b-instruct-q4" — это разные веса, не взаимозаменяемые).
        """
        if configured == installed:
            return True

        def split_tag(name: str):
            base, _, tag = name.partition(":")
            return base, (tag or "latest")

        c_base, c_tag = split_tag(configured)
        i_base, i_tag = split_tag(installed)
        if c_base != i_base:
            return False
        return c_tag == i_tag or c_tag == "latest" or i_tag == "latest"

    def get_status(self) -> dict:
        """
        Полный статус подключения: доступен ли сервис Ollama, установлена ли
        сконфигурированная модель, и список того, что реально установлено.
        Используется UI, чтобы показать пользователю не просто "есть/нет
        соединение", а конкретную причину, если генерация не заработает.
        """
        status = {
            "reachable": False,
            "model": self.model,
            "model_installed": False,
            "installed_models": [],
            "host": self.host,
            "error": None,
        }

        try:
            client = self.get_client()
            raw_list = self._http_request("/api/tags") if client is None else client.list()
        except Exception as exc:
            status["error"] = str(exc)
            logger.debug(f"Ollama недоступна ({self.host}): {exc}")
            return status

        status["reachable"] = True
        installed = self._extract_model_names(raw_list)
        status["installed_models"] = installed
        status["model_installed"] = any(self._model_matches(self.model, name) for name in installed)
        return status

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> str:
        """Генерирует текст через Ollama и возвращает поле response.

        think=False отключает у гибридных reasoning-моделей (gemma4 и т.п.)
        фазу внутренних "размышлений" перед ответом — для наших задач
        (форматирование по уже заданным в промпте правилам, а не открытые
        рассуждения) она не нужна, а по времени генерации на неё уходит
        основная часть eval-токенов (см. `ollama run --verbose`).
        """
        client = self.get_client()
        if client is None:
            response = self._http_request(
                "/api/generate",
                {
                    "model": model or self.model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": options or {},
                },
            )
        else:
            try:
                response = client.generate(
                    model=model or self.model,
                    prompt=prompt,
                    think=False,
                    options=options or {},
                )
            except TypeError:
                # Установленный SDK старее и не знает параметр think —
                # просто теряем ускорение, не падаем.
                response = client.generate(
                    model=model or self.model,
                    prompt=prompt,
                    options=options or {},
                )
        return response["response"].strip()
