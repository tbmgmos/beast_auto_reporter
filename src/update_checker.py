"""Проверка обновлений Beast Auto Reporter через GitHub Releases.

Сами .dmg/.zip не обязательно прикладывать к GitHub Release (там есть лимит
на размер ассета) — вместо этого в тексте релиза (release notes) можно
указать прямые публичные ссылки (например, на Яндекс.Диск), по одной на
архитектуру:

    arm64: https://disk.yandex.ru/d/XXXXXXXXXXXXXX
    intel: https://disk.yandex.ru/d/YYYYYYYYYYYYYY

GitHub Release в этом случае используется только как источник номера версии
и текста изменений; сам файл обновления скачивается напрямую с Яндекс.Диска
через его публичный API (авторизация не требуется для публичных ссылок).
"""

import json
import logging
import platform
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# owner/repo, где публикуются релизы приложения
GITHUB_REPO = "tbmgmos/beast_auto_reporter"
REQUEST_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 300

# Подстроки, по которым определяем архитектуру ассета/ссылки.
# Совпадает с соглашением scripts/build_dmg.sh (Beast_Auto_Reporter_2.0_arm64.dmg).
ARM_TOKENS = ("arm64", "apple_silicon", "applesilicon", "aarch64", "apple silicon")
INTEL_TOKENS = ("x86_64", "intel", "x86-64", "amd64")

YANDEX_DISK_DOMAINS = ("disk.yandex.", "yadi.sk")
YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"


@dataclass
class UpdateInfo:
    version: str
    notes: str
    download_url: str
    html_url: str
    is_direct_download: bool = True


def _parse_version(tag: str) -> tuple:
    parts = re.findall(r"\d+", tag or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def is_newer(remote_tag: str, current_version: str) -> bool:
    return _parse_version(remote_tag) > _parse_version(current_version)


def _current_arch_key() -> str:
    """'arm' для arm64-сборки, 'intel' для x86_64-сборки (в т.ч. под Rosetta)."""
    machine = platform.machine().lower()
    return "arm" if machine in ("arm64", "aarch64") else "intel"


def _pick_by_arch(candidates: dict) -> str:
    """Из {"arm": url, "intel": url, "universal": url} выбирает подходящий текущей сборке.

    Если под свою архитектуру ссылки нет, но есть под ДРУГУЮ — не подсовываем
    её (лучше отправить пользователя на страницу релиза, чем скачать
    несовместимый бинарник). Если архитектура нигде не указана — берём
    универсальную ссылку.
    """
    own = _current_arch_key()
    other = "intel" if own == "arm" else "arm"
    if candidates.get(own):
        return candidates[own]
    if candidates.get(other):
        return ""
    return candidates.get("universal", "")


def _assets_by_arch(assets: list) -> dict:
    """Ассеты GitHub Release (.dmg/.zip), сгруппированные по архитектуре."""
    result = {"arm": "", "intel": "", "universal": ""}
    for asset in assets:
        name = (asset.get("name") or "").lower()
        if not name.endswith((".dmg", ".zip")):
            continue
        url = asset.get("browser_download_url", "")
        if any(tok in name for tok in ARM_TOKENS):
            result["arm"] = result["arm"] or url
        elif any(tok in name for tok in INTEL_TOKENS):
            result["intel"] = result["intel"] or url
        else:
            result["universal"] = result["universal"] or url
    return result


def _links_from_release_notes(body: str) -> dict:
    """Парсит из текста релиза строки вида 'arm64: <url>' / 'intel: <url>'.

    Так релиз может указывать на файл, физически лежащий не в GitHub Release
    (например, публичная ссылка на Яндекс.Диск), обходя ограничение GitHub
    на размер прикладываемого к релизу файла.
    """
    result = {"arm": "", "intel": "", "universal": ""}
    for line in (body or "").splitlines():
        # Только https: скачанный файл открывается автоматически, и ссылка
        # по http позволила бы подменить его по пути (MITM).
        m = re.match(r"\s*[-*]?\s*([\w .]{2,30}?)\s*:\s*(https://\S+)", line, re.UNICODE)
        if not m:
            continue
        label, url = m.group(1).strip().lower(), m.group(2).strip()
        if any(tok in label for tok in ARM_TOKENS):
            result["arm"] = result["arm"] or url
        elif any(tok in label for tok in INTEL_TOKENS):
            result["intel"] = result["intel"] or url
        elif label in ("download", "скачать", "ссылка", "link", "universal"):
            result["universal"] = result["universal"] or url
    return result


def _is_yandex_disk_link(url: str) -> bool:
    return any(domain in url for domain in YANDEX_DISK_DOMAINS)


def _resolve_yandex_disk_url(public_url: str) -> str:
    """Превращает публичную ссылку на Яндекс.Диск в прямую ссылку на файл."""
    api_url = f"{YANDEX_DISK_API}?public_key={urllib.parse.quote(public_url, safe='')}"
    req = urllib.request.Request(api_url, headers={"User-Agent": "BeastAutoReporter"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("href", "")


def check_for_update(current_version: str, repo: str = GITHUB_REPO) -> Optional[UpdateInfo]:
    """Возвращает UpdateInfo, если на GitHub есть релиз новее current_version, иначе None.

    Ошибки сети/API намеренно проглатываются — отсутствие интернета или ещё
    не созданный репозиторий не должны мешать обычному запуску приложения.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "BeastAutoReporter"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.info("Проверка обновлений не выполнена: %s", exc)
        return None

    tag = data.get("tag_name") or ""
    if not tag or not is_newer(tag, current_version):
        return None

    body = data.get("body") or ""

    # Сначала — обычные ассеты GitHub Release, если они есть.
    download_url = _pick_by_arch(_assets_by_arch(data.get("assets", [])))
    # Иначе — ссылки, указанные в тексте релиза (например, на Яндекс.Диск).
    if not download_url:
        download_url = _pick_by_arch(_links_from_release_notes(body))

    return UpdateInfo(
        version=tag.lstrip("vV"),
        notes=body.strip(),
        download_url=download_url or data.get("html_url", ""),
        html_url=data.get("html_url", ""),
        is_direct_download=bool(download_url),
    )


def _filename_from_download(resp, fallback_url: str) -> str:
    content_disposition = resp.headers.get("Content-Disposition", "") if resp.headers else ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", content_disposition)
    if m:
        # Только базовое имя: заголовок приходит с сервера, и без этого
        # "../../x" или абсолютный путь в filename записали бы файл за
        # пределами dest_dir (у pathlib абсолютная правая часть при
        # соединении вообще заменяет левую).
        name = Path(urllib.parse.unquote(m.group(1))).name
        if name and name not in (".", ".."):
            return name

    name = Path(urllib.parse.urlparse(fallback_url).path.rstrip("/")).name
    if name and "." in name:
        return name
    return "Beast_Auto_Reporter_update.dmg"


def download_update_asset(url: str, dest_dir: Optional[Path] = None) -> str:
    """Скачивает файл обновления в ~/Downloads (или dest_dir) и возвращает путь к нему.

    Если url — публичная ссылка на Яндекс.Диск, сначала резолвит её в прямую
    ссылку на файл через публичный API Яндекс.Диска (не требует авторизации).
    """
    dest_dir = dest_dir or (Path.home() / "Downloads")
    dest_dir.mkdir(parents=True, exist_ok=True)

    actual_url = url
    if _is_yandex_disk_link(url):
        actual_url = _resolve_yandex_disk_url(url)
        if not actual_url:
            raise RuntimeError("Не удалось получить прямую ссылку для скачивания с Яндекс.Диска")

    req = urllib.request.Request(actual_url, headers={"User-Agent": "BeastAutoReporter"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        filename = _filename_from_download(resp, url)
        dest_path = dest_dir / filename
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(resp, out)
    return str(dest_path)


def _prefs_path() -> Path:
    base = Path.home() / "Library" / "Application Support" / "Beast Auto Reporter"
    base.mkdir(parents=True, exist_ok=True)
    return base / "update_prefs.json"


def load_skipped_version() -> str:
    try:
        with _prefs_path().open("r", encoding="utf-8") as fh:
            return json.load(fh).get("skip_version", "")
    except Exception:
        return ""


def save_skipped_version(version: str) -> None:
    try:
        with _prefs_path().open("w", encoding="utf-8") as fh:
            json.dump({"skip_version": version}, fh)
    except Exception as exc:
        logger.warning("Не удалось сохранить пропущенную версию обновления: %s", exc)
