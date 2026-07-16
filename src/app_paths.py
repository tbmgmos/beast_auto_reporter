"""Единая директория конфигурации приложения.

Раньше настройки/очередь/алиасы хранились в трёх независимых дотфайлах
прямо в домашней папке пользователя — теперь все конфигурационные файлы
приложения живут под одной директорией, что упрощает бэкап/перенос и обзор
того, что вообще хранит приложение.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".beast_auto_reporter"


def ensure_parent_dir(path: Path) -> None:
    """Создаёт директорию под конфигурационный файл, если её ещё нет.

    На чистой установке (без legacy-дотфайлов, из которых migrate_legacy_config_file
    создала бы директорию попутно) CONFIG_DIR не существует — без этого вызова
    каждая save_*-функция молча падала бы с FileNotFoundError, и настройки
    (включая токен Яндекс.Диска) никогда бы не сохранялись.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


def migrate_legacy_config_file(old_path: Path, new_path: Path) -> None:
    """Переносит конфигурацию со старого места на новое (один раз).

    Если новый файл уже существует, либо старого никогда не было —
    ничего не делает. Старый файл не удаляется (на случай отката на
    предыдущую версию приложения).
    """
    if new_path.exists() or not old_path.exists():
        return
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, new_path)
        logger.info(f"Перенесена конфигурация: {old_path} -> {new_path}")
    except OSError as exc:
        logger.warning(f"Не удалось перенести конфигурацию {old_path} -> {new_path}: {exc}")
