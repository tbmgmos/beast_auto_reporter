from datetime import date

from src.report_filename import (
    categorize_variant, extract_date_loosely, extract_variant_loosely,
    parse_legacy_versioned_filename, parse_report_filename,
)


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


def test_parse_legacy_versioned_filename_extracts_series_and_date():
    result = parse_legacy_versioned_filename("отчет_KP_Orlov_2026_03_27_v1")
    assert result == ("KP_Orlov", date(2026, 3, 27))


def test_parse_legacy_versioned_filename_accepts_uppercase_v_and_extension():
    result = parse_legacy_versioned_filename("отчет_KP_Orlov_2026_03_20_V2.docx")
    assert result == ("KP_Orlov", date(2026, 3, 20))


def test_parse_legacy_versioned_filename_returns_none_for_season_episode_names():
    assert parse_legacy_versioned_filename("отчет_Show_s01_e02_2026_06_10_rus") is None


def test_parse_legacy_versioned_filename_returns_none_without_version_suffix():
    assert parse_legacy_versioned_filename("отчет_KP_Orlov_2026_03_27") is None


def test_parse_legacy_versioned_filename_returns_none_for_invalid_date():
    assert parse_legacy_versioned_filename("отчет_KP_Orlov_2026_13_40_v1") is None


def test_extract_date_loosely_finds_date_in_non_standard_name():
    # Реальный регресс: "отчет_MAZHOR_DUBAI_2025_11_28_rus" не совпадает с
    # REPORT_PATTERN целиком (нет "_sNN_eNN_"), но дата написания отчёта
    # в имени всё равно есть — нужна для сортировки версий по дате
    # написания, а не по дате загрузки на Диск.
    assert extract_date_loosely("отчет_MAZHOR_DUBAI_2025_11_28_rus") == date(2025, 11, 28)


def test_extract_date_loosely_finds_date_inside_compound_tag():
    assert extract_date_loosely(
        "отчет_besprintsipnye_v_pitere_s01_e08_cens_AD_2025_06_11_rus"
    ) == date(2025, 6, 11)


def test_extract_date_loosely_returns_none_without_any_date():
    assert extract_date_loosely("DCP +18") is None
    assert extract_date_loosely("отчет_20251018_MVD_MIX") is None  # "20251018" без подчёркиваний — не совпадает


def test_extract_date_loosely_returns_none_for_invalid_date():
    assert extract_date_loosely("отчет_Show_2026_13_40_rus") is None


def test_extract_variant_loosely_prefers_strict_parse():
    assert extract_variant_loosely("отчет_Show_s01_e05_ME_2026_04_05_rus") == "ME"


def test_extract_variant_loosely_finds_marker_in_non_standard_name():
    assert extract_variant_loosely("отчеты_GMS_EP1_M&E_01.08") == "M&E"
    assert extract_variant_loosely("igry_EP1_AD_12.09") == "AD"


def test_extract_variant_loosely_finds_marker_inside_compound_tag():
    assert extract_variant_loosely(
        "отчет_besprintsipnye_v_pitere_s01_e08_cens_AD_2025_06_11_rus"
    ) == "AD"


def test_extract_variant_loosely_ignores_negation_prefix():
    assert extract_variant_loosely("отчет_mazhor_v_dubae_mix_2025_12_26_no_rus_VO_test") is None


def test_extract_variant_loosely_does_not_false_positive_on_substrings():
    assert extract_variant_loosely("Vlad_random_folder") is None
    assert extract_variant_loosely("Advent_calendar") is None


def test_categorize_variant_maps_known_markers():
    assert categorize_variant(None) == "main"
    assert categorize_variant("ME") == "me"
    assert categorize_variant("MnE") == "me"
    assert categorize_variant("M&E") == "me"
    assert categorize_variant("VO") == "vo"
    assert categorize_variant("DUB") == "dub"
    assert categorize_variant("DUBBED") == "dub"
    assert categorize_variant("AD") == "ad"
    assert categorize_variant("DCP") == "dcp"
    assert categorize_variant("dcp") == "dcp"
    assert categorize_variant("something_unknown") == "other"


def test_categorize_variant_treats_cens_uncens_as_main():
    # CENS/UNCENS — признак цензурирования самого основного отчёта, а не
    # отдельный параллельный тип поставки вроде ME/VO/AD/DUB/DCP.
    assert categorize_variant("cens") == "main"
    assert categorize_variant("uncens") == "main"
    assert categorize_variant("CENS") == "main"
    assert categorize_variant("UNCENS") == "main"


def test_extract_variant_loosely_finds_dcp():
    assert extract_variant_loosely("DCP +18") == "DCP"
    assert extract_variant_loosely("DCP 16+") == "DCP"


def test_extract_variant_loosely_finds_industry_synonyms():
    # Разные студии по-разному сокращают одни и те же типы отчётов —
    # эти варианты должны распознаваться как та же категория, что и
    # канонический маркер (ME/AD/VO/DCP).
    assert extract_variant_loosely("GMS_EP1_DME_01.08") == "DME"
    assert extract_variant_loosely("GMS_EP1_MDE_01.08") == "MDE"
    assert extract_variant_loosely("GMS_EP1_DM&E_01.08") == "DM&E"
    assert extract_variant_loosely("GMS_EP1_M+E_01.08") == "M+E"
    assert extract_variant_loosely("igry_EP1_DVS_12.09") == "DVS"
    assert extract_variant_loosely("igry_EP1_VOICEOVER_12.09") == "VOICEOVER"
    assert extract_variant_loosely("igry_EP1_VOICE-OVER_12.09") == "VOICE-OVER"
    assert extract_variant_loosely("some_DCDM_master") == "DCDM"


def test_categorize_variant_maps_industry_synonyms():
    assert categorize_variant("DME") == "me"
    assert categorize_variant("MDE") == "me"
    assert categorize_variant("DM&E") == "me"
    assert categorize_variant("M+E") == "me"
    assert categorize_variant("DVS") == "ad"
    assert categorize_variant("VOICEOVER") == "vo"
    assert categorize_variant("VOICE-OVER") == "vo"
    assert categorize_variant("DCDM") == "dcp"
