from types import SimpleNamespace

from src.llm_integration import LLMIntegration


def test_llm_integration_converts_defects_to_issues():
    llm = LLMIntegration(config={})
    defects = [
        SimpleNamespace(
            timecode_in="01:00:10:00",
            timecode_out="01:00:10:12",
            description="Отсутствует звук",
            severity="blocker",
            channels=["2.0", "5.1"],
        )
    ]

    issues = llm._convert_defects_to_issues(defects)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.timecode_in == "01:00:10:00"
    assert issue.timecode_out == "01:00:10:12"
    assert issue.description == "Отсутствует звук"
    assert issue.audio_20_c is True
    assert issue.audio_51_c is True
    assert issue.blocker is True
    assert issue.fix_required is False
    assert issue.comment_required is False


def test_llm_integration_generate_conclusion_uses_conclusion_generator_path():
    llm = LLMIntegration(config={})
    defects = [
        SimpleNamespace(
            timecode_in="01:00:10:00",
            timecode_out="",
            description="Отсутствует звук",
            severity="blocker",
            channels=["2.0"],
        )
    ]
    audio_analysis = {
        "compliance": {
            "lufs_compliant": True,
            "true_peak_compliant": True,
            "lra_compliant": True,
        },
        "measurements": {
            "lufs": -23.0,
            "true_peak": -3.0,
            "lra": 8.0,
        },
    }
    file_info = {"file_name": "test.wav"}

    conclusion = llm.generate_conclusion(audio_analysis, defects, file_info)

    assert "Файл: test.wav" in conclusion
    assert "По техническим характеристикам нареканий не обнаружено." in conclusion
    assert "По субъективной оценке выявлены следующие недочёты:" in conclusion
    assert "На таймкоде 01:00:10:00 отсутствует звук" in conclusion
