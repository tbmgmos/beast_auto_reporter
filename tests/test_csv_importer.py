"""Тесты импорта CSV: кодировка с BOM (Excel «CSV UTF-8») и разделители."""

from src.csv_importer import CSVImporter


HEADER = "Timecode In,Timecode Out,Description,БЛОКЕР"
ROW = "01:00:05:00,01:00:07:00,провал громкости,*"


def _write_and_import(tmp_path, content: str, encoding: str = "utf-8"):
    csv_file = tmp_path / "markers.csv"
    csv_file.write_text(content, encoding=encoding)
    return CSVImporter().import_issues(str(csv_file))


def test_imports_plain_utf8_csv(tmp_path):
    issues = _write_and_import(tmp_path, f"{HEADER}\n{ROW}\n")
    assert len(issues) == 1
    assert issues[0].timecode_in == "01:00:05:00"
    assert issues[0].blocker is True


def test_imports_csv_with_bom(tmp_path):
    # Excel при экспорте «CSV UTF-8» ставит BOM в начало файла. С обычным
    # utf-8 BOM прилипал к первому заголовку ('﻿Timecode In'), колонка
    # не находилась, и ВСЕ строки пропускались как «нет таймкода».
    issues = _write_and_import(tmp_path, f"{HEADER}\n{ROW}\n", encoding="utf-8-sig")
    assert len(issues) == 1
    assert issues[0].timecode_in == "01:00:05:00"


def test_imports_semicolon_delimited_csv(tmp_path):
    # Русская локаль Excel экспортирует CSV с ';' в качестве разделителя.
    content = HEADER.replace(",", ";") + "\n" + ROW.replace(",", ";") + "\n"
    issues = _write_and_import(tmp_path, content)
    assert len(issues) == 1
    assert issues[0].timecode_in == "01:00:05:00"
    assert issues[0].blocker is True


def test_imports_tab_delimited_csv(tmp_path):
    content = HEADER.replace(",", "\t") + "\n" + ROW.replace(",", "\t") + "\n"
    issues = _write_and_import(tmp_path, content)
    assert len(issues) == 1
    assert issues[0].timecode_in == "01:00:05:00"


CSV_WITH_TYPOS = (
    "Timecode In,Timecode Out,Description,КОММЕНТАРИИ\n"
    "01:00:05:00,01:00:07:00,слышен дефкт,\n"
    "01:02:10:00,01:02:12:00,снова дефкт на реплике,провал громкости\n"
)


def test_scan_spelling_returns_proposals_without_modifying_file(tmp_path):
    csv_file = tmp_path / "markers.csv"
    csv_file.write_text(CSV_WITH_TYPOS, encoding="utf-8")

    proposals = CSVImporter().scan_spelling(str(csv_file))

    assert [(p["old"], p["new"]) for p in proposals] == [("дефкт", "дефект"), ("дефкт", "дефект")]
    assert proposals[0]["timecode"] == "01:00:05:00"
    assert proposals[0]["field"] == "Описание"
    assert proposals[1]["timecode"] == "01:02:10:00"
    assert csv_file.read_text(encoding="utf-8") == CSV_WITH_TYPOS  # файл не тронут


def test_import_applies_only_approved_corrections(tmp_path):
    csv_file = tmp_path / "markers.csv"
    csv_file.write_text(CSV_WITH_TYPOS, encoding="utf-8")

    issues = CSVImporter().import_issues(
        str(csv_file), approved_corrections={("дефкт", "дефект")}
    )

    assert issues[0].description == "слышен дефект"
    assert issues[1].description == "снова дефект на реплике"


def test_import_with_empty_approved_set_keeps_text_as_is(tmp_path):
    # Пользователь нажал «Без исправлений» — текст маркер-листа попадает
    # в отчёт как есть, даже если сервис уверен в исправлении.
    csv_file = tmp_path / "markers.csv"
    csv_file.write_text(CSV_WITH_TYPOS, encoding="utf-8")

    issues = CSVImporter().import_issues(str(csv_file), approved_corrections=set())

    assert issues[0].description == "слышен дефкт"
    assert issues[1].description == "снова дефкт на реплике"


def test_scan_spelling_on_missing_file_returns_empty_list(tmp_path):
    assert CSVImporter().scan_spelling(str(tmp_path / "нет_такого.csv")) == []
