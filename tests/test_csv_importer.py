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
