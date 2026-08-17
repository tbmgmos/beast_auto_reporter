import importlib.util
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QDialog


@pytest.fixture(scope="module")
def app_module():
    app_path = Path(__file__).resolve().parents[1] / "beast_auto_reporter (v2 beta).py"
    spec = importlib.util.spec_from_file_location("beast_app_me_assignment_tests", app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _AudioExtractor:
    def extract_audio_info(self, path):
        return {
            "file_name": Path(path).name,
            "channels": 2,
            "duration": 60.0,
            "sample_rate": 48000,
            "bit_depth": "PCM_24",
            "channel_order": "L R",
        }


class _PdfExtractor:
    def extract_technical_info(self, path):
        return {
            "channels": "2.0",
            "true_peak": -2.1,
            "sample_rate": 48000,
            "bit_depth": 24,
        }


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("episode_L.wav", "l"),
        ("episode_R.wav", "r"),
        ("episode_C.wav", "c"),
        ("episode_LFE.wav", "lfe"),
        ("episode_Ls.wav", "ls"),
        ("episode_Rs.wav", "rs"),
    ],
)
def test_dcp_split_detection_accepts_unknown_mono_metadata(app_module, file_name, expected):
    assert app_module.detect_dcp_split_channel(file_name, channels=0) == expected


def test_dcp_split_detection_rejects_confirmed_multichannel_file(app_module):
    assert app_module.detect_dcp_split_channel("episode_L.wav", channels=6) is None


def test_only_generic_wav_requires_manual_assignment_not_pdf(app_module, monkeypatch, tmp_path):
    audio = tmp_path / "A003_017_0825XZ.wav"
    pdf = tmp_path / "meter_export_001.pdf"
    audio.touch()
    pdf.touch()
    monkeypatch.setattr(app_module, "TechnicalInfoExtractor", _AudioExtractor)
    monkeypatch.setattr(app_module, "PDFExtractor", _PdfExtractor)

    candidates = app_module.collect_me_assignment_candidates({
        "audio": [str(audio)],
        "pdf": [str(pdf)],
    })

    assert len(candidates) == 1
    assert candidates[0]["media"] == "audio"
    assert all(item["channel"] == "20" for item in candidates)
    assert all(item["needs_review"] for item in candidates)
    assert all(item["suggested_key"] is None for item in candidates)


def test_explicit_industry_aliases_are_assigned_without_review(app_module, monkeypatch, tmp_path):
    audio = tmp_path / "show_20_Dialogue_Guide.wav"
    audio.touch()
    monkeypatch.setattr(app_module, "TechnicalInfoExtractor", _AudioExtractor)
    monkeypatch.setattr(app_module, "PDFExtractor", _PdfExtractor)

    candidate = app_module.collect_me_assignment_candidates({"audio": [str(audio)]})[0]

    assert candidate["suggested_key"] == "audio_me_dx_20"
    assert candidate["needs_review"] is False


def test_assignment_dialog_accepts_custom_opt_cell(app_module):
    candidate = {
        "path": "/tmp/internal.wav",
        "name": "internal.wav",
        "media": "audio",
        "channel": "20",
        "confidence": "unknown",
        "suggested_key": None,
        "existing_key": None,
    }
    dialog = app_module.METrackAssignmentDialog([candidate])
    dialog.combos[0].setCurrentText("2.0 OPT 12")

    dialog._accept_assignments()

    assert dialog.result() == QDialog.Accepted
    assert dialog.result_assignments == {"/tmp/internal.wav": "audio_me_opt_12_20"}
    dialog.deleteLater()


def test_preview_respects_manual_dx_assignment(app_module, monkeypatch, tmp_path):
    audio = tmp_path / "internal.wav"
    audio.touch()
    monkeypatch.setattr(app_module, "TechnicalInfoExtractor", _AudioExtractor)
    monkeypatch.setattr(app_module, "PDFExtractor", _PdfExtractor)

    preview = app_module.analyze_files_for_preview({
        "audio": [str(audio)],
        "video": [],
        "csv": [],
        "pdf": [],
        "params": [],
        "me_track_assignments": {str(audio): "audio_me_dx_20"},
    }, report_type="me")

    audio_record = next(item for item in preview["recognized"] if item["kind"] == "audio")
    assert audio_record["slot"] == "audio_me_dx_20"


def test_pdf_with_opt_text_still_belongs_to_main_me_mix(app_module, monkeypatch, tmp_path):
    pdf = tmp_path / "show_20_OPT_A.pdf"
    pdf.touch()
    monkeypatch.setattr(app_module, "TechnicalInfoExtractor", _AudioExtractor)
    monkeypatch.setattr(app_module, "PDFExtractor", _PdfExtractor)

    preview = app_module.analyze_files_for_preview({
        "audio": [],
        "video": [],
        "csv": [],
        "pdf": [str(pdf)],
        "params": [],
    }, report_type="me")

    pdf_record = next(item for item in preview["recognized"] if item["kind"] == "pdf")
    assert pdf_record["slot"] == "pdf_20"


def test_dynamic_dx_true_peak_can_be_measured_without_pdf(app_module, tmp_path):
    audio = tmp_path / "internal.wav"
    audio.touch()
    audio_key = "audio_me_dx_20"
    pdf_key = "pdf_me_dx_20"
    tech_info = {
        audio_key: {"file_name": audio.name, "file_path": str(audio)},
    }
    thread = app_module.ProcessingThread(None, {}, "me", str(tmp_path))

    items = thread._find_tp_verify_items(tech_info, {audio_key: str(audio)})

    assert len(items) == 1
    assert items[0]["key"] == pdf_key
    assert items[0]["current_value"] is None
    assert tech_info[pdf_key]["true_peak_source"] == "pending_precise_measurement"


def test_automatic_dynamic_tp_skips_main_me_mixes(app_module, tmp_path):
    main_audio = tmp_path / "main_20.wav"
    dx_audio = tmp_path / "dx_20.wav"
    main_audio.touch()
    dx_audio.touch()
    tech_info = {
        "audio_20_c": {"file_name": main_audio.name, "file_path": str(main_audio)},
        "pdf_20_c": {"source_pdf": "main_20.pdf", "true_peak": -2.0},
        "audio_me_dx_20": {"file_name": dx_audio.name, "file_path": str(dx_audio)},
    }
    audio_paths = {
        "audio_20_c": str(main_audio),
        "audio_me_dx_20": str(dx_audio),
    }
    thread = app_module.ProcessingThread(
        None,
        {},
        "me",
        str(tmp_path),
        tp_verify_enabled=True,
        tp_verify_main_mix_enabled=False,
    )

    items = thread._find_tp_verify_items(tech_info, audio_paths)

    assert [item["key"] for item in items] == ["pdf_me_dx_20"]


def test_manual_tp_verify_includes_main_me_mixes(app_module, tmp_path):
    main_audio = tmp_path / "main_20.wav"
    main_audio.touch()
    tech_info = {
        "audio_20_c": {"file_name": main_audio.name, "file_path": str(main_audio)},
        "pdf_20_c": {"source_pdf": "main_20.pdf", "true_peak": -2.0},
    }
    thread = app_module.ProcessingThread(
        None,
        {},
        "me",
        str(tmp_path),
        tp_verify_enabled=True,
        tp_verify_main_mix_enabled=True,
    )

    items = thread._find_tp_verify_items(
        tech_info, {"audio_20_c": str(main_audio)}
    )

    assert [item["key"] for item in items] == ["pdf_20_c"]


@pytest.mark.parametrize(
    "file_name",
    [
        "show_s01e01_ME_AD_20.wav",
        "show_s01e01_ME20.wav",
        "show_s01e01_20ME.wav",
    ],
)
def test_report_type_detection_prioritizes_explicit_me_marker(app_module, file_name):
    report_type, analysis = app_module.detect_report_type_from_files([file_name])

    assert report_type == "me"
    assert analysis["reason"] == "scored_match"


def test_me_report_name_comes_from_main_mix_not_dx_or_opt(app_module):
    files_data = {
        "audio": [
            "/source/show_s01e02_DX_20.wav",
            "/source/show_s01e02_OPT_A_20.wav",
            "/source/show_s01e02_ME_20.wav",
        ],
    }

    assert app_module.select_report_base_name(files_data, "me") == "show_s01e02_ME"


def test_me_report_name_respects_manual_main_mix_assignment(app_module):
    dx = "/source/internal_01.wav"
    main_me = "/source/show_s01e02_MnE_master_20.wav"
    files_data = {
        "audio": [dx, main_me],
        "me_track_assignments": {
            dx: "audio_me_dx_20",
            main_me: "audio_20_c",
        },
    }

    assert app_module.select_report_base_name(files_data, "me") == "show_s01e02_MnE_master"
