"""
Tests for spellcheck_service (RU/EN spellcheck + autocorrect in marker lists).
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spellcheck_service import SpellcheckService
from src.csv_importer import CSVImporter


def test_correct_text_fixes_known_english_typos():
    text = "The frequrncy is too hiqh on this clik sound."
    fixed, corrections = SpellcheckService.correct_text(text)

    assert fixed == "The frequency is too high on this click sound."
    assert ("frequrncy", "frequency") in corrections
    assert ("hiqh", "high") in corrections
    assert ("clik", "click") in corrections


def test_correct_text_fixes_known_russian_typo():
    text = "На реплике слышен дефкт."
    fixed, corrections = SpellcheckService.correct_text(text)

    assert "дефект" in fixed
    assert corrections == [("дефкт", "дефект")]


def test_correct_text_leaves_domain_terms_untouched():
    text = "Присутствует дефект синхронизации на таймкоде. Слышен фоновый шум."
    fixed, corrections = SpellcheckService.correct_text(text)

    assert fixed == text
    assert corrections == []


def test_correct_text_keeps_capitalized_word_mid_sentence():
    # Слово с Заглавной буквы НЕ в начале предложения — вероятное имя
    # собственное (персонаж, название), его нельзя молча «исправлять»,
    # даже если словарь его не знает ("Дефкт" ниже без защиты
    # исправлялся бы в "Дефект", как в тесте с строчным "дефкт").
    text = "На реплике слышен Дефкт."
    fixed, corrections = SpellcheckService.correct_text(text)

    assert fixed == text
    assert corrections == []


def test_correct_text_still_fixes_capitalized_typo_at_sentence_start():
    # В начале текста/предложения заглавная буква — обычная капитализация,
    # проверка и исправление работают как всегда.
    fixed, corrections = SpellcheckService.correct_text("Дефкт на реплике.")
    assert fixed == "Дефект на реплике."
    assert corrections == [("Дефкт", "Дефект")]

    fixed, corrections = SpellcheckService.correct_text("Шум. Дефкт на реплике.")
    assert "Дефект" in fixed


def test_correct_text_treats_new_line_as_sentence_start():
    fixed, corrections = SpellcheckService.correct_text("фоновый шум\nДефкт на реплике")
    assert "Дефект" in fixed
    assert corrections == [("Дефкт", "Дефект")]


def test_check_text_does_not_flag_capitalized_word_mid_sentence():
    assert SpellcheckService.check_text("слышен голос Дефкт") == []
    assert "дефкт" in SpellcheckService.check_text("слышен дефкт")


def test_correct_text_handles_empty_text():
    fixed, corrections = SpellcheckService.correct_text("")
    assert fixed == ""
    assert corrections == []


def test_check_text_flags_typos_without_changing_them():
    misspelled = SpellcheckService.check_text("This has a clik and a hiqh sound.")
    assert "clik" in misspelled
    assert "hiqh" in misspelled


def test_csv_importer_autocorrects_description_and_comments():
    csv_content = (
        "Timecode In\tTimecode Out\tDescription\t2.0 C\t2.0 UC\t5.1 C\t5.1 UC\t"
        "БЛОКЕР\tТРЕБУЕТ ИСПРАВЛЕНИЯ\tТРЕБУЕТ КОММЕНТАРИЯ\tКОММЕНТАРИИ\n"
        "01:00:00:00\t01:00:05:00\tThe frequrncy is too hiqh\t*\t\t\t\t\t*\t\tслышен дефкт\n"
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
        f.write(csv_content)
        csv_path = f.name

    try:
        importer = CSVImporter()
        issues = importer.import_issues(csv_path)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.description == "The frequency is too high"
        assert issue.description_original == "The frequrncy is too hiqh"
        assert "дефект" in issue.comments
    finally:
        Path(csv_path).unlink(missing_ok=True)
