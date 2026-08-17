"""OAuth Device Flow для входа в Яндекс без ручной вставки токена.

Обычный "authorization code"/"implicit" флоу рассчитан на веб-браузер,
которому OAuth-сервер может сделать редирект — для десктопного приложения
это означало бы либо встроенный веб-вьюер (тяжёлая зависимость, QtWebEngine),
либо локальный HTTP-сервер, слушающий редирект (риски с занятыми портами).

Device Flow (RFC 8628, эндпоинты подтверждены по рабочей реализации
https://github.com/dmfed/yauth) сделан именно для таких случаев: приложение
получает короткий user_code и ссылку, пользователь вводит код на странице
Яндекса в ЛЮБОМ браузере (может быть на другом устройстве), а приложение
тем временем поллит эндпоинт токена, пока пользователь не подтвердит.

CLIENT_ID/CLIENT_SECRET — публичные идентификаторы приложения на
oauth.yandex.ru (не пользовательский секрет!). Для десктопных ("installed")
приложений такой client_secret не считается тайной в общепринятой практике
OAuth (см. https://developers.google.com/identity/protocols/oauth2/native-app) —
он используется только для идентификации приложения перед Яндексом, а не
для защиты доступа к чужим данным: получить токен всё равно может только тот,
кто ввёл user_code на странице Яндекса под своим аккаунтом.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.parse
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

logger = logging.getLogger(__name__)

# Зарегистрировано на https://oauth.yandex.ru/client/new для Beast Auto Reporter
# (платформа "Приложение для устройств без браузера" / Device Flow).
CLIENT_ID = "6affbeffcfe8495dbcbb49679ec308fa"
CLIENT_SECRET = "1b5e47ea9f934fffbf6a9a5e1e36bbaf"

DEVICE_CODE_URL = "https://oauth.yandex.ru/device/code"
TOKEN_URL = "https://oauth.yandex.ru/token"
REQUEST_TIMEOUT = 15

# Ровно те права, что нужны отправке отчётов: чтение/запись в произвольном
# месте на Диске (папки /отчеты — не App Folder) + минимальная информация
# о Диске для проверки токена (get_disk_info). Без cloud_api:disk.app_folder —
# он не нужен и запрашивал бы лишнее разрешение у пользователя.
SCOPE = "cloud_api:disk.read cloud_api:disk.write cloud_api:disk.info"


class YandexOAuthError(Exception):
    """Ошибка при обращении к OAuth-серверу Яндекса."""


@dataclass
class DeviceCodeInfo:
    device_code: str
    user_code: str
    verification_url: str
    interval: int
    expires_in: int


def is_configured() -> bool:
    """CLIENT_ID/CLIENT_SECRET заполнены — без них кнопка входа не имеет смысла."""
    return bool(CLIENT_ID and CLIENT_SECRET)


def _post_form(url: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = Request(url, data=data, method="POST",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        # Homebrew Python on ARM points its default trust store at a path
        # under /opt/homebrew. That path does not exist on a Mac receiving
        # only the packaged .app, so use the CA bundle shipped with the app.
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        with urlopen(request, timeout=REQUEST_TIMEOUT, context=ssl_context) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise YandexOAuthError(f"Яндекс OAuth вернул ошибку {exc.code}: {body}") from exc
    except URLError as exc:
        raise YandexOAuthError(f"Не удалось подключиться к Яндекс OAuth: {exc}") from exc


def request_device_code() -> DeviceCodeInfo:
    """Запрашивает device_code/user_code для начала Device Flow."""
    payload = _post_form(DEVICE_CODE_URL, {
        "client_id": CLIENT_ID,
        "scope": SCOPE,
    })
    if "device_code" not in payload:
        message = payload.get("error_description") or payload.get("error") or "неизвестная ошибка"
        raise YandexOAuthError(f"Не удалось получить код авторизации: {message}")
    return DeviceCodeInfo(
        device_code=payload["device_code"],
        user_code=payload["user_code"],
        verification_url=payload.get("verification_url", "https://ya.ru/device"),
        interval=int(payload.get("interval", 5)),
        expires_in=int(payload.get("expires_in", 600)),
    )


# error-коды поллинга по RFC 8628 (Device Authorization Grant), которых
# придерживается Яндекс: "pending" — продолжаем поллить как обычно,
# "slow_down" — тоже продолжаем, но увеличив паузу, остальное — терминальная
# ошибка (пользователь отклонил/код истёк/что-то пошло не так).
_PENDING_ERRORS = {"authorization_pending"}
_SLOW_DOWN_ERRORS = {"slow_down"}


@dataclass
class PollResult:
    status: str  # "success" | "pending" | "slow_down" | "error"
    access_token: str = None
    error_message: str = None


def poll_for_token(device_code: str) -> PollResult:
    """Один запрос обмена device_code на токен — вызывающий код (фоновый

    поток) сам управляет циклом и паузами между попытками (interval из
    DeviceCodeInfo, увеличенный при status == "slow_down").
    """
    payload = _post_form(TOKEN_URL, {
        "grant_type": "device_code",
        "code": device_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })

    if "access_token" in payload:
        return PollResult(status="success", access_token=payload["access_token"])

    error = payload.get("error", "")
    if error in _PENDING_ERRORS:
        return PollResult(status="pending")
    if error in _SLOW_DOWN_ERRORS:
        return PollResult(status="slow_down")

    message = payload.get("error_description") or error or "неизвестная ошибка авторизации"
    return PollResult(status="error", error_message=message)
