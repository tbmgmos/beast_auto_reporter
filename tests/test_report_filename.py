from datetime import date

from src.report_filename import parse_report_filename


def test_returns_none_for_source_filename_with_channel_markers():
    # Исходники (аудио/pdf/csv) содержат маркеры каналов/цензуры между эпизодом
    # и датой ("_51_uncens_") — парсер рассчитан на чистое имя файла отчёта
    # ("отчет_...docx"), поэтому для таких исходников ожидаемо возвращает None.
    assert parse_report_filename("Nepreklonniy_vozrast_s01_e02_51_uncens_2025_05_19_rus.pdf") is None


def test_parses_report_filename_without_variant():
    meta = parse_report_filename("отчет_Nepreklonniy_vozrast_s01_e02_2025_05_19_rus.docx")
    assert meta is not None
    assert meta.series == "Nepreklonniy_vozrast"
    assert meta.season == 1
    assert meta.episode == 2
    assert meta.variant is None
    assert meta.date == date(2025, 5, 19)
    assert meta.lang == "rus"


def test_parses_report_filename_with_variant():
    meta = parse_report_filename("отчет_Nepreklonniy_vozrast_s01_e02_MnE_2025_06_23_rus.docx")
    assert meta is not None
    assert meta.series == "Nepreklonniy_vozrast"
    assert meta.season == 1
    assert meta.episode == 2
    assert meta.variant == "MnE"
    assert meta.date == date(2025, 6, 23)
    assert meta.lang == "rus"


def test_parses_report_filename_with_later_variant():
    meta = parse_report_filename("отчет_Nepreklonniy_vozrast_s01_e02_MnE_2025_07_14_rus.docx")
    assert meta is not None
    assert meta.date == date(2025, 7, 14)


def test_returns_none_for_unrecognized_filename():
    assert parse_report_filename("случайный_файл_без_маркеров.docx") is None


def test_returns_none_for_invalid_date():
    assert parse_report_filename("отчет_Show_s01_e02_2025_13_40_rus.docx") is None


def test_returns_none_for_film_filename_without_season_episode():
    # Без _sNN_eNN_ (например, полнометражный фильм) — имя не распознаётся,
    # вызывающий код предлагает ручной выбор папки.
    assert parse_report_filename("отчет_Название_фильма_2025_05_19_rus.docx") is None
