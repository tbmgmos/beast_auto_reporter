import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QCloseEvent, QKeyEvent, QKeySequence
from PyQt5.QtWidgets import QLabel, QMainWindow, QMessageBox, QPushButton, QRadioButton


@pytest.fixture(scope="module")
def app_module():
    app_path = Path(__file__).resolve().parents[1] / "beast_auto_reporter (v2 beta).py"
    spec = importlib.util.spec_from_file_location("beast_app_shortcut_tests", app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def main_window(app_module):
    window = app_module.BeastApp.__new__(app_module.BeastApp)
    QMainWindow.__init__(window)
    yield window
    window.deleteLater()


def test_application_shortcuts_minimize_close_window_and_quit(app_module, main_window):
    main_window._install_application_shortcuts()

    assert main_window._minimize_action.shortcut() == QKeySequence("Ctrl+M")
    assert main_window._close_window_action.shortcut() == QKeySequence.Close
    assert main_window._quit_action.shortcut() == QKeySequence.Quit

    main_window.show()
    main_window._minimize_action.trigger()
    assert main_window.isMinimized()

    main_window.showNormal()
    main_window._close_window_action.trigger()
    assert not main_window.isVisible()
    assert not getattr(main_window, "_closing", False)


def test_deferred_startup_initializes_services_once(app_module, main_window, monkeypatch):
    created = []

    class _Signal:
        def connect(self, callback):
            pass

    class _QueueManager:
        def __init__(self, **kwargs):
            created.append("queue")
            self.queue_changed = _Signal()
            self.queue_paused_offline = _Signal()
            self.auth_expired = _Signal()
            self.job_uploaded = _Signal()
            self.queue = SimpleNamespace(jobs=[])

    class _EditSync:
        def __init__(self, **kwargs):
            created.append("edit")
            self.status_changed = _Signal()
            self.conflict = _Signal()

    class _Service:
        def __init__(self, *args, **kwargs):
            created.append(type(self).__name__)

    monkeypatch.setattr(app_module, "ExactReportGenerator", _Service)
    monkeypatch.setattr(app_module, "PDFExtractor", _Service)
    monkeypatch.setattr(app_module, "CSVImporter", _Service)
    monkeypatch.setattr(app_module, "TechnicalInfoExtractor", _Service)
    monkeypatch.setattr(app_module, "YandexUploadQueueManager", _QueueManager)
    monkeypatch.setattr(app_module, "YandexEditSyncController", _EditSync)

    main_window.config = {}
    main_window.conclusion_gen = SimpleNamespace(_ollama_generate=lambda *args: None)
    main_window._startup_settings = {"llm_spellcheck_enabled": True}
    main_window._startup_services_ready = False
    main_window._startup_services_initializing = False
    main_window._closing = False
    main_window.yandex_queue_btn = None

    main_window._finish_deferred_startup()
    main_window._finish_deferred_startup()

    assert main_window._startup_services_ready is True
    assert created.count("queue") == 1
    assert created.count("edit") == 1
    assert len(created) == 6


def test_sync_shortcut_accepts_s_from_english_russian_and_physical_key(app_module):
    english = QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier, "s")
    russian = QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, "ы")
    physical_s = QKeyEvent(
        QEvent.KeyPress, 0, Qt.NoModifier,
        0, 1, 0, "ж", False, 1,
    )

    assert app_module.BeastApp._is_sync_shortcut_event(english)
    assert app_module.BeastApp._is_sync_shortcut_event(russian)
    assert app_module.BeastApp._is_sync_shortcut_event(physical_s)


def test_sync_shortcut_starts_config_sync(app_module, main_window, monkeypatch):
    calls = []
    main_window._config_sync = SimpleNamespace(sync_now=lambda: calls.append("sync"))
    monkeypatch.setattr(app_module.QApplication, "activeWindow", lambda: main_window)
    monkeypatch.setattr(app_module.QApplication, "focusWidget", lambda: None)
    event = QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, "ы")

    handled = main_window.eventFilter(main_window, event)

    assert handled is True
    assert calls == ["sync"]


def test_yandex_upload_folder_snapshot_survives_auto_reset(app_module, tmp_path):
    report_folder = tmp_path / "отчет_show"
    report_folder.mkdir()
    report_path = report_folder / "отчет_show.docx"
    report_path.touch()
    state = SimpleNamespace(
        last_output_folder=report_folder,
        last_report_docx_path=report_path,
    )

    captured = app_module.BeastApp._capture_yandex_report_folder(state)
    state.last_output_folder = None
    state.last_report_docx_path = None

    assert captured == report_folder
    assert app_module.BeastApp._get_yandex_report_folder_snapshot(state) == report_folder


def test_close_is_cancelled_by_default(app_module, main_window, monkeypatch):
    monkeypatch.setattr(
        app_module.SettingsDialog,
        "load_settings",
        classmethod(lambda cls: {}),
    )
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No
    )
    event = QCloseEvent()

    main_window.closeEvent(event)

    assert not event.isAccepted()
    assert not getattr(main_window, "_closing", False)


def test_confirmed_close_starts_shutdown(app_module, main_window, monkeypatch):
    monkeypatch.setattr(
        app_module.SettingsDialog,
        "load_settings",
        classmethod(lambda cls: {}),
    )
    monkeypatch.setattr(
        app_module.QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )
    event = QCloseEvent()

    main_window.closeEvent(event)

    assert event.isAccepted()
    assert main_window._closing is True


def test_close_skips_confirmation_when_disabled(app_module, main_window, monkeypatch):
    monkeypatch.setattr(
        app_module.SettingsDialog,
        "load_settings",
        classmethod(lambda cls: {"confirm_exit": False}),
    )
    question_calls = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *args, **kwargs: question_calls.append((args, kwargs)),
    )
    event = QCloseEvent()

    main_window.closeEvent(event)

    assert event.isAccepted()
    assert main_window._closing is True
    assert question_calls == []


def test_model_picker_is_compact_and_grouped(app_module):
    dialog = app_module.LLMModelPickerDialog(
        app_module.AVAILABLE_LLM_MODELS,
        "ollama",
        "gemma4:e2b-it-qat",
        True,
    )

    radios = dialog.findChildren(QRadioButton)
    sections = [
        label.text()
        for label in dialog.findChildren(QLabel)
        if label.objectName() == "section"
    ]
    access_buttons = [
        button.text()
        for button in dialog.findChildren(QPushButton)
        if button.objectName() == "accessButton"
    ]

    assert dialog.width() == 440
    assert sections == ["ЛОКАЛЬНО НА ЭТОМ MAC", "ОБЛАЧНЫЕ МОДЕЛИ"]
    assert len(radios) == len(app_module.AVAILABLE_LLM_MODELS)
    assert [radio.text() for radio in radios if radio.isChecked()] == ["Gemma 4 · Быстрая"]
    assert access_buttons == ["Groq", "YandexGPT", "GigaChat"]
    dialog.deleteLater()


def test_settings_normalizes_shared_and_tiflo_roots(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.SettingsDialog,
        "load_settings",
        classmethod(lambda cls: {
            "yandex_shared_root": "Team Shared/",
            "yandex_tiflo_root": "/Tiflo Projects/",
        }),
    )

    assert app_module.SettingsDialog.get_yandex_shared_root() == "/Team Shared"
    assert app_module.SettingsDialog.get_yandex_tiflo_root() == "/Tiflo Projects"


def test_settings_exposes_compact_browser_folder_fields(app_module, monkeypatch):
    settings = {
        "yandex_disk_roots": ["/отчеты"],
        "yandex_npr_root": "/ПРОКТЫ NUENDO",
        "yandex_shared_root": "/SHARED",
        "yandex_tiflo_root": "/ПРОКТЫ TIFLO",
    }
    monkeypatch.setattr(
        app_module.SettingsDialog,
        "load_settings",
        classmethod(lambda cls: dict(settings)),
    )
    monkeypatch.setattr(
        app_module.SettingsDialog,
        "get_yandex_token",
        classmethod(lambda cls: "test-token"),
    )
    monkeypatch.setattr(app_module.yandex_oauth, "is_configured", lambda: False)

    dialog = app_module.SettingsDialog()

    assert dialog.yandex_roots_list.height() == 52
    assert dialog.yandex_npr_root_edit.text() == "/ПРОКТЫ NUENDO"
    assert dialog.yandex_shared_root_edit.text() == "/SHARED"
    assert dialog.yandex_tiflo_root_edit.text() == "/ПРОКТЫ TIFLO"
    dialog.deleteLater()
