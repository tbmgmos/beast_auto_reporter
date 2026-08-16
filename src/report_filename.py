"""Разбор имени файла отчёта на сериал/сезон/серию/дату.

Ожидаемый формат (по реальным примерам из отчёты_learn):
    отчет_Nepreklonniy_vozrast_s01_e02_2025_05_19_rus.docx
    отчет_Nepreklonniy_vozrast_s01_e02_MnE_2025_06_23_rus.docx

Парсер рассчитан на ЧИСТЫЕ имена отчётов: sanitize_base_name в главном
файле вырезает маркеры каналов/цензуры из имени исходника до генерации
отчёта. Имена исходников с такими маркерами между эпизодом и датой
(например, "..._s01_e02_51_uncens_2025_05_19_rus.pdf") намеренно НЕ
распознаются — variant допускает только буквы (см. тест
test_returns_none_for_source_filename_with_channel_markers).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


REPORT_PATTERN = re.compile(
    r"^(?:отчет_)?(?P<series>.+?)_s(?P<season>\d{2})_e(?P<episode>\d{2})"
    r"(?:_(?P<variant>[A-Za-z]+))?_(?P<date>\d{4}_\d{2}_\d{2})_(?P<lang>[a-z]+)"
)

# Более старая, встречающаяся нечасто схема имени — без season/episode
# вообще, с версией через явный суффикс "_v1"/"_V2" (например
# "отчет_KP_Orlov_2026_03_27_v1"). REPORT_PATTERN такие имена не
# распознаёт (там обязателен "_sNN_eNN_"), поэтому разбирается отдельно
# от ReportMeta — сюда не заводим season/episode/lang, которых в таком
# имени просто нет.
LEGACY_VERSIONED_PATTERN = re.compile(
    r"^(?:отчет_)?(?P<series>.+?)_(?P<date>\d{4}_\d{2}_\d{2})_[vV]\d+$"
)


@dataclass(frozen=True)
class ReportMeta:
    series: str
    season: int
    episode: int
    date: date
    lang: str
    variant: str | None = None


def parse_report_filename(name: str) -> ReportMeta | None:
    """Извлекает метаданные из имени файла отчёта/исходника.

    Возвращает None, если имя не соответствует ожидаемому формату
    (в этом случае вызывающий код должен предложить ручной выбор папки).
    """
    stem = name
    for ext in (".docx", ".pdf", ".csv"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break

    match = REPORT_PATTERN.match(stem)
    if not match:
        return None

    try:
        report_date = date(*(int(part) for part in match.group("date").split("_")))
    except ValueError:
        return None

    return ReportMeta(
        series=match.group("series"),
        season=int(match.group("season")),
        episode=int(match.group("episode")),
        date=report_date,
        lang=match.group("lang"),
        variant=match.group("variant"),
    )


def parse_legacy_versioned_filename(name: str) -> tuple[str, date] | None:
    """Разбирает старое имя вида «отчет_<сериал>_<дата>_v1» (без season/

    episode) — возвращает (series, date) для группировки версий по
    сериалу+дате, либо None, если имя не подходит под эту схему. Сам номер
    версии из суффикса не используется: порядок версий пересчитывается по
    дате, как и для обычных имён (см. YandexDiskBrowserDialog.
    _label_report_version_siblings).
    """
    stem = name
    for ext in (".docx", ".pdf", ".csv"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break

    match = LEGACY_VERSIONED_PATTERN.match(stem)
    if not match:
        return None

    try:
        report_date = date(*(int(part) for part in match.group("date").split("_")))
    except ValueError:
        return None

    return match.group("series"), report_date


_LENIENT_DATE_PATTERN = re.compile(r"(\d{4})_(\d{2})_(\d{2})")


def extract_date_loosely(name: str) -> date | None:
    """Дата из имени, даже если оно не подходит под REPORT_PATTERN/

    LEGACY_VERSIONED_PATTERN целиком (нестандартная схема имени — другой
    формат сезона/эпизода, составной тег между эпизодом и датой и т.п.,
    см. report_uploader.list_report_versions) — просто первое найденное
    в самом имени вхождение «YYYY_MM_DD». Нужна как запасной вариант при
    сортировке версий отчёта по дате написания (а не по дате загрузки на
    Диск) — иначе только что загруженный, но написанный давно отчёт с
    нестандартным именем сортировался бы по времени загрузки и выглядел
    бы «последним», хотя по содержанию он самый старый.
    """
    match = _LENIENT_DATE_PATTERN.search(name)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


# Реальные папки/файлы на Диске часто названы не по строгой схеме имени
# отчёта (нет "_sNN_eNN_", дата в другом формате и т.п. — например
# «GMS_EP1_M&E_01.08» или «igry_EP1_AD_12.09»), поэтому REPORT_PATTERN их
# не распознаёт вообще. Для варианта этого достаточно — ищем сам маркер
# как отдельное "слово" (по границам не-буквы), чтобы не подхватить «AD»
# внутри случайного имени вроде «Vlad». DUBBED проверяется раньше DUB —
# не из-за порядка альтернатив (граница по не-букве и так не даёт «DUB»
# откусить кусок «DUBBED»), а для ясности.
#
# Помимо основных обозначений (ME/AD/VO/DCP) добавлены индустриальные
# синонимы, встречающиеся в реальных именах папок студий: DME/MDE/DM&E/M+E
# для M&E-стемов (разные студии по-разному сокращают «Music & Effects» —
# см. https://spotlightfx.com/blog/what-is-a-dme-track), DVS (Descriptive
# Video Service — устаревшее североамериканское название Audio Description,
# см. https://www.3playmedia.com/blog/described-video-vs-audio-description-whats-the-difference/),
# VOICEOVER/VOICE-OVER как слитное написание VO, DCDM (Digital Cinema
# Distribution Master — исходник до упаковки в DCP, синоним для наших
# целей). «VI»/«NAD» сознательно не добавлены — слишком короткие/частые
# случайные совпадения (римские цифры, обычные слова).
_VARIANT_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё])"
    r"(M&E|M\+E|DM&E|MNE|DME|MDE|ME"
    r"|AD|DVS"
    r"|VOICE-OVER|VOICEOVER|VO"
    r"|DUBBED|DUB"
    r"|DCDM|DCP)"
    r"(?![A-Za-zА-Яа-яЁё])",
    re.IGNORECASE,
)

# Редкое, но встречающееся исключение: «..._no_rus_VO_...» означает «этой
# дорожки НЕТ» (например, нет русской VO), а не то, что сам отчёт —
# вариант VO. Токен, которому непосредственно предшествует «no_»
# (опционально с языком между, «no_rus_») — отрицание, не маркер варианта.
_VARIANT_ABSENCE_PREFIX = re.compile(r"no_(?:[a-z]+_)?$", re.IGNORECASE)


def extract_variant_loosely(name: str) -> str | None:
    """Маркер варианта (ME/AD/VO/DUB и т.п.) из имени, даже если оно не

    подходит под REPORT_PATTERN целиком — сперва пробует строгий разбор
    (parse_report_filename), затем ищет маркер как отдельное слово прямо
    в имени. Используется и для иконок/сортировки/фильтра в просмотрщике
    Диска (см. YandexDiskBrowserDialog), и для группировки версий при
    сравнении (см. report_uploader.list_report_versions) — единая логика,
    чтобы одно и то же имя не классифицировалось по-разному в двух местах.
    """
    meta = parse_report_filename(name)
    if meta and meta.variant:
        return meta.variant
    for match in _VARIANT_TOKEN_PATTERN.finditer(name):
        if _VARIANT_ABSENCE_PREFIX.search(name[:match.start()]):
            continue
        return match.group(1)
    return None


def categorize_variant(variant: str | None) -> str:
    """Нормализует сырой маркер варианта в одну из категорий: "main"

    (нет маркера, а также CENS/UNCENS — это признак цензурирования самого
    основного отчёта, а не отдельный параллельный тип поставки вроде
    ME/VO/AD/DUB/DCP, поэтому не выделяется в свою категорию), "me"
    (ME/MnE/M&E/M+E/DME/MDE/DM&E), "vo" (VO/VOICEOVER/VOICE-OVER), "dub"
    (DUB/DUBBED), "ad" (AD/DVS), "dcp" (DCP/DCDM) или "other" (всё
    остальное неопознанное).
    """
    if not variant:
        return "main"
    variant_upper = variant.upper()
    if variant_upper in ("CENS", "UNCENS"):
        return "main"
    if variant_upper in ("ME", "MNE", "M&E", "M+E", "DME", "MDE", "DM&E"):
        return "me"
    if variant_upper in ("VO", "VOICEOVER", "VOICE-OVER"):
        return "vo"
    if variant_upper in ("DUB", "DUBBED"):
        return "dub"
    if variant_upper in ("AD", "DVS"):
        return "ad"
    if variant_upper in ("DCP", "DCDM"):
        return "dcp"
    return "other"
