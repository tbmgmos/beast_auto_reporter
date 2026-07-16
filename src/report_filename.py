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
    for ext in (".docx", ".pages", ".pdf", ".csv"):
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
