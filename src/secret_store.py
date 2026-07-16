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

# Код возврата security(1) для «записи нет в Связке ключей» (errSecItemNotFound).
_ERR_ITEM_NOT_FOUND = 44

# Кэш на время жизни процесса: get_yandex_token дёргается часто (каждый
# job очереди, проверки при старте), а каждый вызов security — отдельный
# субпроцесс. Инвалидируется при save/delete.
_cache: dict = {"valid": False, "token": ""}


def _keychain_available() -> bool:
    return sys.platform == "darwin"


def _quote_for_security(value: str) -> str:
    """Экранирует значение для интерактивной команды security -i."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_token(token: str) -> bool:
    """Сохраняет токен в Связку ключей. False — не удалось (нет macOS/ошибка),

    вызывающий код должен оставить прежнее хранение в settings.json.
    """
    if not _keychain_available():
        return False
    if not token:
        return delete_token()
    command = (
        f"add-generic-password -U -a {_quote_for_security(ACCOUNT_NAME)} "
        f"-s {_quote_for_security(SERVICE_NAME)} -w {_quote_for_security(token)}\n"
    )
    try:
        result = subprocess.run(
            ["security", "-i"], input=command,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Не удалось сохранить токен в Связку ключей: {exc}")
        return False
    if result.returncode != 0:
        logger.warning(
            f"Не удалось сохранить токен в Связку ключей (security -> {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        return False
    _cache["valid"] = True
    _cache["token"] = token
    return True


def load_token() -> Optional[str]:
    """Токен из Связки ключей: "" — записи нет, None — Связка недоступна

    (не macOS/ошибка утилиты; вызывающий код откатывается на settings.json).
    """
    if not _keychain_available():
        return None
    if _cache["valid"]:
        return _cache["token"]
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", ACCOUNT_NAME, "-s", SERVICE_NAME, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Не удалось прочитать токен из Связки ключей: {exc}")
        return None
    if result.returncode == _ERR_ITEM_NOT_FOUND:
        _cache["valid"] = True
        _cache["token"] = ""
        return ""
    if result.returncode != 0:
        logger.warning(
            f"Не удалось прочитать токен из Связки ключей (security -> {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        return None
    token = result.stdout.rstrip("\n")
    _cache["valid"] = True
    _cache["token"] = token
    return token


def delete_token() -> bool:
    """Убирает токен из Связки ключей. Отсутствие записи — тоже успех."""
    if not _keychain_available():
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-a", ACCOUNT_NAME, "-s", SERVICE_NAME],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Не удалось удалить токен из Связки ключей: {exc}")
        return False
    if result.returncode not in (0, _ERR_ITEM_NOT_FOUND):
        logger.warning(
            f"Не удалось удалить токен из Связки ключей (security -> {result.returncode}): "
            f"{result.stderr.strip()}"
        )
        return False
    _cache["valid"] = True
    _cache["token"] = ""
    return True


def invalidate_cache() -> None:
    """Сбрасывает кэш (для тестов и на случай внешнего изменения Связки)."""
    _cache["valid"] = False
    _cache["token"] = ""
