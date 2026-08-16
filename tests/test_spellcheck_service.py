"""
Tests for spellcheck_service (RU/EN spellcheck + autocorrect in marker lists).
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.spellcheck_service as spellcheck_service
from src.spellcheck_service import (
    SpellcheckService,
    load_custom_corrections,
    remember_custom_correction,
    save_custom_corrections,
)
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


def test_remember_custom_correction_persists_to_disk(tmp_path):
    path = tmp_path / "corrections.json"

    remember_custom_correction("шурашине", "шуршание", path=path)

    assert load_custom_corrections(path=path) == {"шурашине": "шуршание"}


def test_remember_custom_correction_normalizes_key_to_lowercase(tmp_path):
    path = tmp_path / "corrections.json"

    remember_custom_correction("Шурашине", "шуршание", path=path)

    assert load_custom_corrections(path=path) == {"шурашине": "шуршание"}


def test_load_custom_corrections_missing_file_returns_empty_dict(tmp_path):
    assert load_custom_corrections(path=tmp_path / "нет_такого.json") == {}


def test_load_custom_corrections_ignores_corrupted_file(tmp_path):
    path = tmp_path / "corrections.json"
    path.write_text("не json", encoding="utf-8")

    assert load_custom_corrections(path=path) == {}


def test_save_and_reload_custom_corrections_round_trip(tmp_path):
    path = tmp_path / "corrections.json"

    save_custom_corrections({"звуков": "звуковой"}, path=path)

    assert load_custom_corrections(path=path) == {"звуков": "звуковой"}


def test_correct_text_prefers_remembered_custom_correction_over_algorithm(monkeypatch):
    # "звуков" — настоящее слово, алгоритм его не тронул бы вообще; ручное
    # исправление, запомненное пользователем, обязано сработать в приоритете.
    monkeypatch.setattr(
        spellcheck_service, "load_custom_corrections", lambda *a, **kw: {"звуков": "звуковой"}
    )

    fixed, corrections = SpellcheckService.correct_text("Проблема со звуков дорожки")

    assert "звуковой" in fixed
    assert ("звуков", "звуковой") in corrections


def test_correct_text_applies_custom_correction_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        spellcheck_service, "load_custom_corrections", lambda *a, **kw: {"звуков": "звуковой"}
    )

    fixed, corrections = SpellcheckService.correct_text("Звуков не хватает.")

    assert "Звуковой" in fixed
    assert ("Звуков", "Звуковой") in corrections


def test_correct_texts_batch_uses_llm_when_generate_fn_given():
    calls = []

    def fake_generate(prompt, *, model=None, options=None):
        calls.append(prompt)
        return (
            '[{"index": 0, "corrections": [{"old": "дефкт", "new": "дефект"}]}, '
            '{"index": 1, "corrections": []}]'
        )

    service = SpellcheckService(generate_fn=fake_generate)
    result = service.correct_texts_batch(["слышен дефкт", "всё чисто"])

    assert result == {"слышен дефкт": [("дефкт", "дефект")]}
    assert len(calls) == 1  # один batch-запрос на все тексты сразу


def test_correct_texts_batch_falls_back_to_algorithm_on_llm_error():
    def broken_generate(prompt, *, model=None, options=None):
        raise RuntimeError("сеть недоступна")

    service = SpellcheckService(generate_fn=broken_generate)
    result = service.correct_texts_batch(["На реплике слышен дефкт."])

    assert result == {"На реплике слышен дефкт.": [("дефкт", "дефект")]}


def test_correct_texts_batch_falls_back_on_invalid_json():
    service = SpellcheckService(generate_fn=lambda prompt, **kw: "не json вообще")
    result = service.correct_texts_batch(["слышен дефкт"])

    assert result == {"слышен дефкт": [("дефкт", "дефект")]}


def test_correct_texts_batch_drops_hallucinated_corrections_not_in_text():
    def fake_generate(prompt, *, model=None, options=None):
        return '[{"index": 0, "corrections": [{"old": "нетвтексте", "new": "х"}]}]'

    service = SpellcheckService(generate_fn=fake_generate)
    result = service.correct_texts_batch(["всё чисто"])

    assert result == {}


def test_correct_texts_batch_without_generate_fn_uses_algorithm():
    service = SpellcheckService()
    result = service.correct_texts_batch(["слышен дефкт", "всё чисто"])

    assert result == {"слышен дефкт": [("дефкт", "дефект")]}


def test_apply_pairs_replaces_by_word_boundary():
    text, applied = SpellcheckService.apply_pairs("снова дефкт на реплике", [("дефкт", "дефект")])
    assert text == "снова дефект на реплике"
    assert applied == [("дефкт", "дефект")]


def test_apply_pairs_replaces_every_repeated_occurrence():
    text, applied = SpellcheckService.apply_pairs(
        "дефкт и ещё дефкт", [("дефкт", "дефект")]
    )
    assert text == "дефект и ещё дефект"
    assert applied == [("дефкт", "дефект"), ("дефкт", "дефект")]


def test_apply_pairs_ignores_pairs_not_present_in_text():
    text, applied = SpellcheckService.apply_pairs("всё чисто", [("дефкт", "дефект")])
    assert text == "всё чисто"
    assert applied == []


def test_csv_importer_scan_spelling_uses_llm_generate_fn():
    calls = []

    def fake_generate(prompt, *, model=None, options=None):
        calls.append(prompt)
        return '[{"index": 0, "corrections": [{"old": "дефкт", "new": "дефект"}]}]'

    csv_content = (
        "Timecode In,Timecode Out,Description,КОММЕНТАРИИ\n"
        "01:00:05:00,01:00:07:00,слышен дефкт,\n"
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
        f.write(csv_content)
        csv_path = f.name

    try:
        importer = CSVImporter(generate_fn=fake_generate)
        proposals = importer.scan_spelling(csv_path)

        assert [(p["old"], p["new"]) for p in proposals] == [("дефкт", "дефект")]
        assert len(calls) == 1  # один batch-запрос на весь CSV
    finally:
        Path(csv_path).unlink(missing_ok=True)


def test_csv_importer_import_issues_with_approved_corrections_skips_llm_call():
    calls = []

    def fake_generate(prompt, *, model=None, options=None):
        calls.append(prompt)
        return '[{"index": 0, "corrections": [{"old": "дефкт", "new": "дефект"}]}]'

    csv_content = (
        "Timecode In,Timecode Out,Description,КОММЕНТАРИИ\n"
        "01:00:05:00,01:00:07:00,слышен дефкт,\n"
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
        f.write(csv_content)
        csv_path = f.name

    try:
        importer = CSVImporter(generate_fn=fake_generate)
        issues = importer.import_issues(csv_path, approved_corrections={("дефкт", "дефект")})

        assert issues[0].description == "слышен дефект"
        # approved_corrections уже известны из ревью — повторный вызов LLM не нужен
        assert calls == []
    finally:
        Path(csv_path).unlink(missing_ok=True)


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
