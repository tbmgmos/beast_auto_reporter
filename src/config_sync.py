"""Синхронизация локальных "выученных" конфигов между машинами через

Яндекс.Диск: алиасы серий/NPR-проектов, ручные назначения варианта отчёта,
пользовательский словарь спелчекера и реестр отправленных отчётов. Цель —
чтобы то, что один человек один раз подтвердил на своей машине (например,
"эта папка на Диске — сериал X"), не пришлось заново вводить на остальных
5-6 компьютерах команды.

Чистая логика без Qt — оркестрация (потоки/таймеры/UI) в
src/yandex_ui/config_sync_controller.py и src/yandex_ui/threads.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from src.app_paths import CONFIG_DIR, atomic_write_text
from src.report_uploader import (
    NPR_ALIASES_FILE,
    load_series_aliases,
    load_uploaded_reports,
    load_variant_overrides,
    save_series_aliases,
    save_uploaded_reports,
    save_variant_overrides,
)
from src.spellcheck_service import load_custom_corrections, save_custom_corrections
from src.yandex_disk_client import YandexDiskClient, YandexDiskError

logger = logging.getLogger(__name__)

# Отдельная папка на Диске под конфиги приложения — не пересекается с
# папками отчётов (REPORTS_ROOT/npr root в report_uploader.py настраиваются
# пользователем и трактуются как "содержимое = папки сериалов").
SYNC_REMOTE_PARENT = "/Beast Auto Reporter"
SYNC_REMOTE_ROOT = f"{SYNC_REMOTE_PARENT}/sync"

_UPLOADED_REPORTS_REMOTE_FILENAME = "uploaded_reports.json"
_SHARED_UPLOADED_REPORTS_MAX = 100


def merge_dicts(base: dict, local: dict, remote: dict) -> tuple[dict, list[str]]:
    """3-way merge словарей вида key -> str (алиасы/варианты/исправления).

    base — последнее известное общее состояние (снэпшот с прошлой удачной
    синхронизации; пустой словарь на новой машине, где синка ещё не было).
    Логика на ключ:
      - не менялся ни там, ни там относительно base -> как есть;
      - менялся только с одной стороны (в т.ч. до отсутствия — удаление) ->
        берём изменённую сторону;
      - менялся с обеих сторон одинаково -> как есть, не конфликт;
      - менялся с обеих сторон по-разному (включая "удалили с одной
        стороны, изменили с другой") -> побеждает local (правка этой
        машины считается более свежей/приоритетной), ключ дополнительно
        попадает в список conflicts для статуса/уведомления. Без временных
        меток на ключ — конфликты в данных такого рода редки и к тому же
        самовосстанавливаются существующим механизмом (например,
        forget_series_alias при обнаружении 404 у алиаса).
    """
    merged: dict = {}
    conflicts: list[str] = []
    for key in set(base) | set(local) | set(remote):
        base_value = base.get(key)
        local_value = local.get(key)
        remote_value = remote.get(key)
        local_changed = local_value != base_value
        remote_changed = remote_value != base_value

        if not local_changed and not remote_changed:
            winner = local_value
        elif local_changed and not remote_changed:
            winner = local_value
        elif remote_changed and not local_changed:
            winner = remote_value
        elif local_value == remote_value:
            winner = local_value
        else:
            conflicts.append(key)
            winner = local_value

        if winner is not None:
            merged[key] = winner

    return merged, sorted(conflicts)


def _base_snapshot_path(name: str):
    return CONFIG_DIR / ".sync_base" / f"{name}.json"


def load_base_snapshot(name: str) -> dict:
    """Снэпшот словаря на момент прошлой удачной синхронизации — служебные

    данные для merge_dicts, не показываются пользователю. Отсутствующий/
    битый файл — не ошибка, просто пустой словарь (тогда merge_dicts
    трактует всё содержимое remote как новое — так и подтягиваются алиасы
    команды на свежую машину при первом запуске синхронизации).
    """
    path = _base_snapshot_path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Не удалось прочитать sync-снэпшот {name}: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_base_snapshot(name: str, data: dict) -> None:
    try:
        atomic_write_text(
            _base_snapshot_path(name),
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        )
    except OSError as exc:
        logger.warning(f"Не удалось сохранить sync-снэпшот {name}: {exc}")


def _download_json(client: YandexDiskClient, remote_path: str, default):
    """Скачивает и разбирает JSON с Диска; отсутствующий файл (404) или

    битое содержимое — не ошибка синхронизации, просто default (пустой
    словарь/список — на первую синхронизацию с этой машины remote-файла
    ещё может не существовать вовсе).
    """
    try:
        raw = client.download_bytes(remote_path)
    except YandexDiskError as exc:
        if exc.status_code == 404:
            return default
        raise
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning(f"Не удалось разобрать {remote_path} с Диска: {exc}")
        return default


def _upload_json(client: YandexDiskClient, remote_path: str, data) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=isinstance(data, dict))
    client.upload_bytes(payload.encode("utf-8"), remote_path)


def _ensure_remote_root(client: YandexDiskClient) -> None:
    client.mkdir(SYNC_REMOTE_PARENT)
    client.mkdir(SYNC_REMOTE_ROOT)


@dataclass(frozen=True)
class SyncableDictConfig:
    name: str
    load_fn: Callable[[], dict]
    save_fn: Callable[[dict], None]
    remote_filename: str


# npr_aliases переиспользует функции алиасов серий с другим путём к файлу
# (та же схема key -> путь папки, см. report_uploader.py) — оборачиваем
# лямбдами, чтобы у всех элементов реестра была одна и та же сигнатура
# () -> dict / (dict) -> None.
SYNCABLE_DICT_CONFIGS: list[SyncableDictConfig] = [
    SyncableDictConfig("series_aliases", load_series_aliases, save_series_aliases, "series_aliases.json"),
    SyncableDictConfig(
        "npr_aliases",
        lambda: load_series_aliases(NPR_ALIASES_FILE),
        lambda d: save_series_aliases(d, NPR_ALIASES_FILE),
        "npr_aliases.json",
    ),
    SyncableDictConfig("variant_overrides", load_variant_overrides, save_variant_overrides, "variant_overrides.json"),
    SyncableDictConfig(
        "spellcheck_custom_corrections",
        load_custom_corrections,
        save_custom_corrections,
        "spellcheck_custom_corrections.json",
    ),
]


@dataclass
class DictSyncResult:
    name: str
    changed: bool
    conflicts: list[str] = field(default_factory=list)


def sync_dict_config(client: YandexDiskClient, config: SyncableDictConfig) -> DictSyncResult:
    """Синхронизирует один словарный конфиг (см. SYNCABLE_DICT_CONFIGS):

    скачивает remote, мёрджит с local через base-снэпшот, пишет результат
    и локально, и на Диск (если реально отличается от того, что уже там),
    обновляет base-снэпшот на смёрженное состояние.
    """
    remote_path = f"{SYNC_REMOTE_ROOT}/{config.remote_filename}"
    remote = _download_json(client, remote_path, {})
    if not isinstance(remote, dict):
        remote = {}
    local = config.load_fn()
    base = load_base_snapshot(config.name)

    merged, conflicts = merge_dicts(base, local, remote)
    changed = merged != local or merged != remote

    if merged != local:
        config.save_fn(merged)
    if merged != remote:
        _upload_json(client, remote_path, merged)
    save_base_snapshot(config.name, merged)

    return DictSyncResult(config.name, changed, conflicts)


def sync_uploaded_reports(client: YandexDiskClient) -> bool:
    """Синхронизирует общий реестр отправленных отчётов (remote_path,

    uploaded_at) — без local_folder у чужих записей (бессмысленный
    локальный путь другой машины). Не 3-way merge: список идемпотентно
    дедуплицируется по remote_path (новее uploaded_at побеждает), реальных
    конфликтов тут не бывает — записи только добавляются. Используется для
    "мягкого" предупреждения о повторной отправке того же отчёта (полноценная
    распределённая блокировка через REST API Диска не строится надёжно —
    осознанно не делаем, см. план).
    """
    remote_path = f"{SYNC_REMOTE_ROOT}/{_UPLOADED_REPORTS_REMOTE_FILENAME}"
    remote_entries = _download_json(client, remote_path, [])
    if not isinstance(remote_entries, list):
        remote_entries = []
    local_entries = load_uploaded_reports()

    merged_by_path: dict[str, dict] = {}
    for entry in remote_entries:
        if not isinstance(entry, dict):
            continue
        rp = entry.get("remote_path")
        if not rp:
            continue
        merged_by_path[rp] = {"remote_path": rp, "uploaded_at": entry.get("uploaded_at")}

    for entry in local_entries:
        rp = entry.get("remote_path")
        if not rp:
            continue
        existing = merged_by_path.get(rp)
        local_uploaded_at = entry.get("uploaded_at") or ""
        if existing is None or local_uploaded_at >= (existing.get("uploaded_at") or ""):
            merged_by_path[rp] = dict(entry)
        else:
            kept = dict(existing)
            kept["local_folder"] = entry.get("local_folder")
            merged_by_path[rp] = kept

    merged = sorted(
        merged_by_path.values(),
        key=lambda e: e.get("uploaded_at") or "",
        reverse=True,
    )[:_SHARED_UPLOADED_REPORTS_MAX]

    changed = merged != local_entries
    if changed:
        save_uploaded_reports(merged)

    upload_payload = [
        {"remote_path": e["remote_path"], "uploaded_at": e.get("uploaded_at")}
        for e in merged
    ]
    if upload_payload != remote_entries:
        _upload_json(client, remote_path, upload_payload)

    return changed


@dataclass
class SyncSummary:
    dict_results: list[DictSyncResult]
    uploaded_reports_changed: bool
    conflicts: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.uploaded_reports_changed or any(r.changed for r in self.dict_results)


def sync_all(client: YandexDiskClient) -> SyncSummary:
    """Синхронизирует все конфиги разом — один проход, вызывается из

    ConfigSyncThread. Каждый файл — небольшой JSON, последовательные
    запросы вместо параллельных не создают заметной задержки.
    """
    _ensure_remote_root(client)
    dict_results = [sync_dict_config(client, config) for config in SYNCABLE_DICT_CONFIGS]
    uploaded_reports_changed = sync_uploaded_reports(client)
    conflicts = [f"{result.name}:{key}" for result in dict_results for key in result.conflicts]
    return SyncSummary(
        dict_results=dict_results,
        uploaded_reports_changed=uploaded_reports_changed,
        conflicts=conflicts,
    )
