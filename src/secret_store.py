"""Хранение секретов приложения в Связке ключей macOS.

Раньше OAuth-токен Яндекс.Диска лежал в открытом виде в
~/.beast_auto_reporter/settings.json (права 644): его читал любой процесс
пользователя, и он попадал в бэкапы/синхронизацию домашней папки. Связка
ключей шифрует секрет и привязывает его к учётной записи macOS.

Реализовано через системную утилиту /usr/bin/security — без сторонних
зависимостей (не усложняет сборку PyInstaller). Запись идёт через
интерактивный режим `security -i` (команда передаётся на stdin), чтобы
токен не светился в списке процессов (argv виден через `ps` любому
процессу пользователя).

Если Связка ключей недоступна (не macOS, ошибка утилиты) — функции
возвращают None/False, и вызывающий код продолжает использовать
settings.json как раньше (см. SettingsDialog.get_yandex_token).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "Beast Auto Reporter"
ACCOUNT_NAME = "yandex_disk_token"
GROQ_ACCOUNT_NAME = "groq_api_key"
YANDEXGPT_KEY_ACCOUNT_NAME = "yandexgpt_api_key"
YANDEXGPT_FOLDER_ACCOUNT_NAME = "yandexgpt_folder_id"
GIGACHAT_KEY_ACCOUNT_NAME = "gigachat_auth_key"

# Код возврата security(1) для «записи нет в Связке ключей» (errSecItemNotFound).
_ERR_ITEM_NOT_FOUND = 44

# Кэш на время жизни процесса, отдельно на каждый account (yandex-токен,
# groq-ключ и т.п.) — дёргается часто (каждый job очереди, проверки при
# старте), а каждый вызов security — отдельный субпроцесс. Инвалидируется
# при save/delete.
_caches: dict = {}


def _cache_for(account: str) -> dict:
    return _caches.setdefault(account, {"valid": False, "value": ""})


def _keychain_available() -> bool:
    return sys.platform == "darwin"


def _quote_for_security(value: str) -> str:
    """Экранирует значение для интерактивной команды security -i."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _save_secret(account: str, value: str) -> bool:
    """Сохраняет значение в Связку ключей под указанным account. False —

    не удалось (нет macOS/ошибка), вызывающий код должен оставить прежнее
    хранение в settings.json.
    """
    if not _keychain_available():
        return False
    if not value:
        return _delete_secret(account)
    command = (
        f"add-generic-password -U -a {_quote_for_security(account)} "
        f"-s {_quote_for_security(SERVICE_NAME)} -w {_quote_for_security(value)}\n"
    )
    try:
        result = subprocess.run(
            ["security", "-i"], input=command,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Не удалось сохранить секрет «{account}» в Связку ключей: {exc}")
        return False
    if result.returncode != 0:
        logger.warning(
            f"Не удалось сохранить секрет «{account}» в Связку ключей (security -> {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        return False
    cache = _cache_for(account)
    cache["valid"] = True
    cache["value"] = value
    return True


def _load_secret(account: str) -> Optional[str]:
    """Значение из Связки ключей: "" — записи нет, None — Связка недоступна

    (не macOS/ошибка утилиты; вызывающий код откатывается на settings.json).
    """
    if not _keychain_available():
        return None
    cache = _cache_for(account)
    if cache["valid"]:
        return cache["value"]
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", SERVICE_NAME, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Не удалось прочитать секрет «{account}» из Связки ключей: {exc}")
        return None
    if result.returncode == _ERR_ITEM_NOT_FOUND:
        cache["valid"] = True
        cache["value"] = ""
        return ""
    if result.returncode != 0:
        logger.warning(
            f"Не удалось прочитать секрет «{account}» из Связки ключей (security -> {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        return None
    value = result.stdout.rstrip("\n")
    cache["valid"] = True
    cache["value"] = value
    return value


def _delete_secret(account: str) -> bool:
    """Убирает значение из Связки ключей. Отсутствие записи — тоже успех."""
    if not _keychain_available():
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", SERVICE_NAME],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Не удалось удалить секрет «{account}» из Связки ключей: {exc}")
        return False
    if result.returncode not in (0, _ERR_ITEM_NOT_FOUND):
        logger.warning(
            f"Не удалось удалить секрет «{account}» из Связки ключей (security -> {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        return False
    cache = _cache_for(account)
    cache["valid"] = True
    cache["value"] = ""
    return True


def save_token(token: str) -> bool:
    """Сохраняет OAuth-токен Яндекс.Диска в Связку ключей."""
    return _save_secret(ACCOUNT_NAME, token)


def load_token() -> Optional[str]:
    """Токен Яндекс.Диска из Связки ключей ("" — нет записи, None — Связка недоступна)."""
    return _load_secret(ACCOUNT_NAME)


def delete_token() -> bool:
    """Убирает токен Яндекс.Диска из Связки ключей."""
    return _delete_secret(ACCOUNT_NAME)


def save_groq_key(key: str) -> bool:
    """Сохраняет API-ключ Groq в Связку ключей."""
    return _save_secret(GROQ_ACCOUNT_NAME, key)


def load_groq_key() -> Optional[str]:
    """Ключ Groq из Связки ключей ("" — нет записи, None — Связка недоступна)."""
    return _load_secret(GROQ_ACCOUNT_NAME)


def delete_groq_key() -> bool:
    """Убирает API-ключ Groq из Связки ключей."""
    return _delete_secret(GROQ_ACCOUNT_NAME)


def save_yandexgpt_key(key: str) -> bool:
    """Сохраняет API-ключ YandexGPT (Yandex Cloud Foundation Models) в Связку ключей."""
    return _save_secret(YANDEXGPT_KEY_ACCOUNT_NAME, key)


def load_yandexgpt_key() -> Optional[str]:
    """Ключ YandexGPT из Связки ключей ("" — нет записи, None — Связка недоступна)."""
    return _load_secret(YANDEXGPT_KEY_ACCOUNT_NAME)


def delete_yandexgpt_key() -> bool:
    """Убирает API-ключ YandexGPT из Связки ключей."""
    return _delete_secret(YANDEXGPT_KEY_ACCOUNT_NAME)


def save_yandexgpt_folder_id(folder_id: str) -> bool:
    """Сохраняет folder_id каталога Yandex Cloud в Связку ключей.

    Сам по себе не секрет (просто идентификатор), но хранится там же, где
    ключ — так оба живут в одном месте и одинаково переживают переустановку
    приложения без миграции настроек.
    """
    return _save_secret(YANDEXGPT_FOLDER_ACCOUNT_NAME, folder_id)


def load_yandexgpt_folder_id() -> Optional[str]:
    """folder_id из Связки ключей ("" — нет записи, None — Связка недоступна)."""
    return _load_secret(YANDEXGPT_FOLDER_ACCOUNT_NAME)


def delete_yandexgpt_folder_id() -> bool:
    """Убирает folder_id из Связки ключей."""
    return _delete_secret(YANDEXGPT_FOLDER_ACCOUNT_NAME)


def save_gigachat_key(key: str) -> bool:
    """Сохраняет "Authorization key" GigaChat (base64 client_id:client_secret) в Связку ключей."""
    return _save_secret(GIGACHAT_KEY_ACCOUNT_NAME, key)


def load_gigachat_key() -> Optional[str]:
    """Ключ GigaChat из Связки ключей ("" — нет записи, None — Связка недоступна)."""
    return _load_secret(GIGACHAT_KEY_ACCOUNT_NAME)


def delete_gigachat_key() -> bool:
    """Убирает Authorization key GigaChat из Связки ключей."""
    return _delete_secret(GIGACHAT_KEY_ACCOUNT_NAME)


def invalidate_cache() -> None:
    """Сбрасывает кэш всех секретов (для тестов и на случай внешнего изменения Связки)."""
    _caches.clear()
