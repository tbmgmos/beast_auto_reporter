"""Тесты группировки предложений для диалога ревью орфографии."""

from src.spellcheck_review import _format_occurrences, group_proposals


def test_group_proposals_merges_same_correction():
    proposals = [
        {"timecode": "01:00:05:00", "field": "Описание", "old": "дефкт", "new": "дефект"},
        {"timecode": "01:02:10:00", "field": "Описание", "old": "дефкт", "new": "дефект"},
        {"timecode": "01:03:00:00", "field": "Комментарии", "old": "hiqh", "new": "high"},
    ]

    grouped = group_proposals(proposals)

    assert len(grouped) == 2
    assert grouped[0]["old"] == "дефкт"
    assert grouped[0]["count"] == 2
    assert grouped[0]["timecodes"] == ["01:00:05:00", "01:02:10:00"]
    assert grouped[1] == {"old": "hiqh", "new": "high", "count": 1, "timecodes": ["01:03:00:00"]}


def test_group_proposals_keeps_case_variants_separate():
    # «Дефкт» и «дефкт» — разные замены (регистр сохраняется при применении),
    # пользователь должен видеть и решать по каждой отдельно.
    proposals = [
        {"timecode": "01:00:05:00", "field": "Описание", "old": "дефкт", "new": "дефект"},
        {"timecode": "01:01:00:00", "field": "Описание", "old": "Дефкт", "new": "Дефект"},
    ]
    assert len(group_proposals(proposals)) == 2


def test_group_proposals_deduplicates_timecodes_within_group():
    proposals = [
        {"timecode": "01:00:05:00", "field": "Описание", "old": "дефкт", "new": "дефект"},
        {"timecode": "01:00:05:00", "field": "Комментарии", "old": "дефкт", "new": "дефект"},
    ]
    grouped = group_proposals(proposals)
    assert grouped[0]["count"] == 2
    assert grouped[0]["timecodes"] == ["01:00:05:00"]


def test_format_occurrences_truncates_long_lists():
    entry = {
        "old": "x", "new": "y", "count": 5,
        "timecodes": ["01:00", "02:00", "03:00", "04:00", "05:00"],
    }
    assert _format_occurrences(entry) == "01:00, 02:00, 03:00 (+2)"


def test_format_occurrences_short_list_has_no_suffix():
    entry = {"old": "x", "new": "y", "count": 2, "timecodes": ["01:00", "02:00"]}
    assert _format_occurrences(entry) == "01:00, 02:00"
