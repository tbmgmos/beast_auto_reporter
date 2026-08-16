"""Тесты LLM-сводки различий между версиями отчёта.

Покрывает всю цепочку фичи «AI-сводка изменений»: бриф для промпта
(format_comparison_brief), метод генератора (summarize_version_changes,
в т.ч. диспатч на Groq), фоновый поток (VersionSummaryThread) и блок
сводки в диалоге сравнения (YandexUploadDiffDialog).
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QTimer
from PyQt5.QtWidgets import QApplication

from src.conclusion_generator import ConclusionGenerator
from src.report_uploader import (
    DocumentSummary, ReportComparison, _build_comparison, format_comparison_brief,
)
from src.yandex_ui.dialogs import YandexUploadDiffDialog
from src.yandex_ui.threads import VersionSummaryThread


def _get_app():
    return QApplication.instance() or QCoreApplication.instance() or QApplication(sys.argv)


def _run_until(condition, timeout_ms=5000):
    elapsed = 0
    while not condition() and elapsed < timeout_ms:
        QApplication.processEvents()
        time.sleep(0.01)
        elapsed += 10
    assert condition(), "не дождались условия в отведённое время"


def _comparison(with_diff=True, with_params=True):
    marker_diff = None
    if with_diff:
        marker_diff = {
            "added": [
                {"tc_in": "01:00:10:00", "tc_out": "01:00:12:00", "description": "Шипение на реплике",
                 "blocker": True, "comments": ""},
                {"tc_in": "01:05:00:00", "tc_out": "01:05:01:00", "description": "Щелчок",
                 "blocker": False, "comments": ""},
            ],
            "removed": [
                {"tc_in": "01:02:00:00", "tc_out": "01:02:03:00", "description": "Посторонний шум",
                 "blocker": True, "comments": ""},
            ],
            "changed": [
                {"tc_in": "01:03:00:00", "changes": [
                    {"field": "Описание", "old": "гул", "new": "низкий гул"},
                ]},
            ],
        }
    parameter_changes = []
    if with_params:
        parameter_changes = [
            {"label": "2.0 cens", "changes": [
                {"field": "Громкость", "old": "-23.0 LUFS", "new": "-24.5 LUFS", "status": "bad"},
                {"field": "True Peak", "old": "-1.0 dBTP", "new": "-2.0 dBTP", "status": None},
            ]},
        ]
    return ReportComparison(
        marker_count_old=5, marker_count_new=7,
        blocker_count_old=1, blocker_count_new=1,
        new_marker_count_old=0, new_marker_count_new=2,
        parameter_changes=parameter_changes,
        marker_diff=marker_diff,
        new_parameters={
            "2.0 cens": {"LOUDNESS": "-24.5 LUFS", "TRUE PEAK": "-2.0 dBTP",
                          "LRA": "5.1 LU", "ХРОНОМЕТРАЖ": "00:42:15", "ФОРМАТ ФАЙЛА": "ProRes 422"},
            "5.1": {"LOUDNESS": "-23.0 LUFS", "TRUE PEAK": "-3.1 dBTP"},
        },
        new_parameter_status={"2.0 cens": {"LOUDNESS": "bad"}},
    )


# ---------------------------------------------------------------------------
# format_comparison_brief


def test_build_comparison_keeps_new_version_parameter_snapshot():
    old = DocumentSummary(marker_count=1, parameters={"2.0": {"LOUDNESS": "-24.0 LUFS"}})
    new = DocumentSummary(
        marker_count=2,
        parameters={"2.0": {"LOUDNESS": "-23.0 LUFS", "ХРОНОМЕТРАЖ": "00:42:15"}},
        parameter_status={"2.0": {"LOUDNESS": "warn"}},
    )
    comparison = _build_comparison(old, new)
    assert comparison.new_parameters == {"2.0": {"LOUDNESS": "-23.0 LUFS", "ХРОНОМЕТРАЖ": "00:42:15"}}
    assert comparison.new_parameter_status == {"2.0": {"LOUDNESS": "warn"}}


def test_brief_contains_counts_and_all_groups():
    brief = format_comparison_brief(
        _comparison(), old_label="v1 от 01.06", new_label="v2 от 10.06",
    )
    assert "Старая версия: v1 от 01.06" in brief
    assert "Новая версия: v2 от 10.06" in brief
    assert "Маркеров: 5 → 7" in brief
    assert "блокеров: 1 → 1" in brief
    # Текущие параметры новой версии — независимо от того, менялись ли они.
    assert "Текущие технические параметры новой версии:" in brief
    assert "- 2.0 cens: LOUDNESS: -24.5 LUFS (НЕ В НОРМЕ); TRUE PEAK: -2.0 dBTP; " \
           "LRA: 5.1 LU; ХРОНОМЕТРАЖ: 00:42:15; ФОРМАТ ФАЙЛА: ProRes 422" in brief
    assert "- 5.1: LOUDNESS: -23.0 LUFS; TRUE PEAK: -3.1 dBTP" in brief
    # Различия.
    assert "Различия между версиями:" in brief
    assert "Добавленные маркеры (2):" in brief
    assert "- 01:00:10:00 — Шипение на реплике (блокер)" in brief
    assert "- 01:05:00:00 — Щелчок" in brief
    assert "Удалённые маркеры (1):" in brief
    assert "- 01:02:00:00 — Посторонний шум (блокер)" in brief
    assert "Изменённые маркеры (1):" in brief
    assert "- 01:03:00:00: Описание: «гул» → «низкий гул»" in brief
    assert "Изменения технических параметров:" in brief
    assert "- 2.0 cens, Громкость: -23.0 LUFS → -24.5 LUFS (НЕ В НОРМЕ)" in brief
    assert "- 2.0 cens, True Peak: -1.0 dBTP → -2.0 dBTP" in brief


def test_brief_marks_warn_status():
    comparison = ReportComparison(
        marker_count_old=1, marker_count_new=1,
        blocker_count_old=0, blocker_count_new=0,
        new_marker_count_old=0, new_marker_count_new=0,
        parameter_changes=[
            {"label": "5.1", "changes": [
                {"field": "Громкость", "old": "-23.0", "new": "-23.4", "status": "warn"},
            ]},
        ],
    )
    brief = format_comparison_brief(comparison)
    assert "(на грани нормы)" in brief


def test_brief_caps_long_marker_lists():
    added = [
        {"tc_in": f"01:{i:02d}:00:00", "tc_out": "", "description": f"маркер {i}",
         "blocker": False, "comments": ""}
        for i in range(3)
    ]
    comparison = ReportComparison(
        marker_count_old=0, marker_count_new=3,
        blocker_count_old=0, blocker_count_new=0,
        new_marker_count_old=0, new_marker_count_new=0,
        parameter_changes=[],
        marker_diff={"added": added, "removed": [], "changed": []},
    )
    brief = format_comparison_brief(comparison, max_markers_per_group=2)
    assert "Добавленные маркеры (3):" in brief  # счётчик полный
    assert "маркер 0" in brief and "маркер 1" in brief
    assert "маркер 2" not in brief
    assert "…и ещё 1." in brief


def test_brief_without_details_says_so():
    comparison = ReportComparison(
        marker_count_old=5, marker_count_new=5,
        blocker_count_old=0, blocker_count_new=0,
        new_marker_count_old=0, new_marker_count_new=0,
        parameter_changes=[],
        marker_diff=None,
    )
    brief = format_comparison_brief(comparison)
    assert "Изменений в маркерах и технических параметрах между версиями не обнаружено" in brief
    assert "Добавленные маркеры" not in brief
    assert "Различия между версиями:" not in brief


def test_brief_shows_current_params_even_without_changes():
    comparison = ReportComparison(
        marker_count_old=3, marker_count_new=3,
        blocker_count_old=0, blocker_count_new=0,
        new_marker_count_old=0, new_marker_count_new=0,
        parameter_changes=[],
        marker_diff=None,
        new_parameters={"2.0": {"LOUDNESS": "-23.0 LUFS", "ХРОНОМЕТРАЖ": "00:42:15"}},
    )
    brief = format_comparison_brief(comparison)
    # Версии совпадают, но текущее состояние (хронометраж и прочее) всё равно видно.
    assert "- 2.0: LOUDNESS: -23.0 LUFS; ХРОНОМЕТРАЖ: 00:42:15" in brief
    assert "Изменений в маркерах и технических параметрах между версиями не обнаружено" in brief


# ---------------------------------------------------------------------------
# ConclusionGenerator.summarize_version_changes


def test_summarize_uses_groq_and_builds_prompt_from_brief():
    generator = ConclusionGenerator()
    generator.set_llm_provider("groq")

    captured = {}

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["options"] = kwargs.get("options")
        return "  Исправлен блокер, добавлено два маркера. \n"

    generator.groq_service.generate = fake_generate

    result = generator.summarize_version_changes(
        _comparison(), old_label="v1", new_label="v2",
    )

    assert result == "Исправлен блокер, добавлено два маркера."  # strip()
    prompt = captured["prompt"]
    # В промпте — и инструкции, и данные брифа (включая текущие параметры).
    assert "5–10 предложений" in prompt
    assert "хронометраж" in prompt
    assert "Старая версия: v1" in prompt
    assert "Текущие технические параметры новой версии:" in prompt
    assert "ХРОНОМЕТРАЖ: 00:42:15" in prompt
    assert "- 01:00:10:00 — Шипение на реплике (блокер)" in prompt
    assert "(НЕ В НОРМЕ)" in prompt
    # Низкая температура и ограничение длины ответа для пересказа фактов.
    assert captured["options"]["temperature"] < 0.5
    assert captured["options"]["num_predict"] <= 1000


def test_summarize_dispatches_to_current_provider():
    generator = ConclusionGenerator()  # по умолчанию provider == "ollama"
    generator.ollama_service.generate = lambda prompt, **kw: "из ollama"
    generator.groq_service.generate = lambda prompt, **kw: "из groq"

    assert generator.summarize_version_changes(_comparison()) == "из ollama"

    generator.set_llm_provider("groq")
    assert generator.summarize_version_changes(_comparison()) == "из groq"


# ---------------------------------------------------------------------------
# VersionSummaryThread


class _FakeGenerator:
    def __init__(self, result="готовая сводка", error=None):
        self.result = result
        self.error = error
        self.calls = []

    def summarize_version_changes(self, comparison, old_label=None, new_label=None):
        self.calls.append((comparison, old_label, new_label))
        if self.error is not None:
            raise self.error
        return self.result


def test_version_summary_thread_emits_resolved():
    _get_app()
    generator = _FakeGenerator()
    results = []
    comparison = _comparison()

    thread = VersionSummaryThread(generator, comparison, old_label="v1", new_label="v2")
    thread.resolved.connect(lambda text: results.append(text))
    thread.failed.connect(lambda message: results.append(f"FAIL: {message}"))
    thread.start()

    _run_until(lambda: bool(results))

    assert results == ["готовая сводка"]
    assert generator.calls == [(comparison, "v1", "v2")]


def test_version_summary_thread_emits_failed_on_error():
    _get_app()
    generator = _FakeGenerator(error=RuntimeError("LLM недоступен"))
    results = []

    thread = VersionSummaryThread(generator, _comparison())
    thread.resolved.connect(lambda text: results.append(f"OK: {text}"))
    thread.failed.connect(lambda message: results.append(message))
    thread.start()

    _run_until(lambda: bool(results))

    assert results == ["LLM недоступен"]


# ---------------------------------------------------------------------------
# Блок сводки в YandexUploadDiffDialog


def test_diff_dialog_has_summary_card_only_with_generator():
    app = _get_app()

    # isHidden() (явный флаг setVisible), а не isVisible(): сам диалог в
    # тестах не показывается, и isVisible() у детей всегда False.
    with_generator = YandexUploadDiffDialog(
        _comparison(), upload_mode=False, summary_generator=_FakeGenerator(),
    )
    assert not with_generator.summary_btn.isHidden()
    assert with_generator.summary_label.isHidden()  # до запуска пусто
    with_generator.done(0)

    without_generator = YandexUploadDiffDialog(_comparison(), upload_mode=False)
    assert not hasattr(without_generator, "summary_btn")
    without_generator.done(0)


def test_diff_dialog_fills_summary_after_click():
    app = _get_app()
    dialog = YandexUploadDiffDialog(
        _comparison(), upload_mode=False, summary_generator=_FakeGenerator(result="Короткая сводка."),
    )

    dialog.summary_btn.click()
    _run_until(lambda: dialog.summary_label.text() == "Короткая сводка.")

    assert not dialog.summary_label.isHidden()
    assert dialog.summary_btn.isHidden()  # после успеха кнопка прячется
    dialog.done(0)


def test_diff_dialog_shows_error_and_keeps_button_on_failure():
    app = _get_app()
    dialog = YandexUploadDiffDialog(
        _comparison(), upload_mode=False,
        summary_generator=_FakeGenerator(error=RuntimeError("Groq API-ключ не задан")),
    )

    dialog.summary_btn.click()
    _run_until(lambda: "Groq API-ключ не задан" in dialog.summary_label.text())

    assert not dialog.summary_btn.isHidden()
    assert dialog.summary_btn.isEnabled()  # можно повторить
    dialog.done(0)
