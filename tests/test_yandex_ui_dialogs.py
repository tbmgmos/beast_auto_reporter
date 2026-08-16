"""Headless-тесты диалогов сравнения версий отчёта на Яндекс.Диске."""

import os
import sys
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import Qt, QPoint, QUrl
from PyQt5.QtWidgets import (
    QApplication, QDialog, QInputDialog, QMenu, QMessageBox, QPushButton, QTreeWidgetItem,
)

import src.yandex_ui.dialogs as dialogs_module
from src.report_uploader import ReportComparison
from src.yandex_ui.dialogs import (
    CombinedFolderPickerDialog, TagEditDialog, VersionChainDialog, YandexDiskBrowserDialog,
    YandexFolderPickerDialog, YandexUploadDiffDialog, YandexVersionPickerDialog,
)
from src.yandex_ui.threads import CURRENT_DRAFT, YandexCombinedFindThread


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def _versions():
    return [
        {"path": "/отчеты/Show/e01/v1", "label": "v1", "date": date(2026, 6, 1)},
        {"path": "/отчеты/Show/e01/v2", "label": "v2", "date": date(2026, 6, 10)},
        {"path": "/отчеты/Show/e01/v3", "label": "v3", "date": date(2026, 6, 15)},
    ]


def _run_until(condition, timeout_ms=5000):
    elapsed = 0
    while not condition() and elapsed < timeout_ms:
        QApplication.processEvents()
        time.sleep(0.01)
        elapsed += 10
    assert condition(), "не дождались условия в отведённое время"


def _comparison():
    return ReportComparison(
        marker_count_old=5, marker_count_new=7,
        blocker_count_old=1, blocker_count_new=0,
        new_marker_count_old=0, new_marker_count_new=2,
        parameter_changes=[],
    )


# ---------------------------------------------------------------------------
# YandexVersionPickerDialog


def test_version_picker_includes_draft_by_default(app):
    dlg = YandexVersionPickerDialog(_versions(), include_current_draft=True)
    assert dlg.combo_new.itemData(0) == CURRENT_DRAFT
    assert dlg.combo_old.currentData() == "/отчеты/Show/e01/v3"  # самая свежая версия на Диске
    assert dlg.combo_new.currentData() == CURRENT_DRAFT


def test_version_picker_excludes_draft_when_disabled(app):
    dlg = YandexVersionPickerDialog(_versions(), include_current_draft=False)
    data_items = [dlg.combo_new.itemData(i) for i in range(dlg.combo_new.count())]
    assert CURRENT_DRAFT not in data_items
    # Нет черновика по умолчанию -> вторая свежая версия против самой свежей.
    assert dlg.combo_old.currentData() == "/отчеты/Show/e01/v2"
    assert dlg.combo_new.currentData() == "/отчеты/Show/e01/v3"


def test_version_picker_marks_latest_entry(app):
    dlg = YandexVersionPickerDialog(_versions(), include_current_draft=False)
    assert "(последняя)" in dlg.combo_old.itemText(0)
    assert "(последняя)" not in dlg.combo_old.itemText(1)


# ---------------------------------------------------------------------------
# VersionChainDialog


def test_version_chain_dialog_builds_pair_buttons(app):
    dlg = VersionChainDialog(_versions())
    compare_buttons = [b for b in dlg.findChildren(QPushButton) if b.text().startswith("⇄")]
    assert len(compare_buttons) == 2  # 3 версии -> 2 соседние пары


def test_version_chain_dialog_emits_compare_requested_for_correct_pair(app):
    dlg = VersionChainDialog(_versions())
    compare_buttons = [b for b in dlg.findChildren(QPushButton) if b.text().startswith("⇄")]

    received = []
    dlg.compare_requested.connect(lambda *args: received.append(args))
    compare_buttons[0].click()

    assert received == [("/отчеты/Show/e01/v1", "/отчеты/Show/e01/v2", "v1", "v2")]


# ---------------------------------------------------------------------------
# YandexDiskBrowserDialog — оркестрация сравнения версий


def _make_browser_dialog(app, local_draft_docx_path=None):
    # Два корня — чтобы получить старое поведение с узлом верхнего уровня
    # на каждый корень (см. _root_items) как стабильный контейнер для
    # тестовых фикстур; сценарий с одним корнем без узла-обёртки
    # (_load_root_contents) покрыт отдельными тестами ниже.
    with patch("src.report_uploader.load_uploaded_reports", return_value=[]):
        dlg = YandexDiskBrowserDialog(
            "test-token", report_roots=["/отчеты", "/архив"], parent=None,
            local_draft_docx_path=local_draft_docx_path,
            view_mode="list",
        )
    return dlg


def test_browser_dialog_exposes_shared_and_tiflo_groups(app):
    with patch("src.report_uploader.load_uploaded_reports", return_value=[]):
        dlg = YandexDiskBrowserDialog(
            "test-token", report_roots=["/отчеты", "/архив"],
            npr_root="/ПРОЕКТЫ NUENDO", shared_root="/SHARED",
            tiflo_root="/ПРОКТЫ TIFLO", parent=None,
        )

    assert dlg._groups["shared"] == ["/SHARED"]
    assert dlg._groups["tiflo"] == ["/ПРОКТЫ TIFLO"]
    assert dlg._group_buttons["shared"].text() == "Shared"
    assert dlg._group_buttons["tiflo"].text() == "Tiflo"
    dlg.close()


def test_browser_dialog_switches_finder_view_modes(app):
    dlg = _make_browser_dialog(app)
    available = app.primaryScreen().availableGeometry()
    assert dlg.width() == min(1040, max(680, available.width() - 96))
    assert dlg.height() == min(640, max(500, available.height() - 120))
    source = dlg._make_item(
        {"name": "Папка", "type": "dir", "path": "/отчеты/Папка"},
        "/отчеты/Папка",
    )
    dlg.tree.addTopLevelItem(source)

    dlg._set_view_mode("icons", persist=False)
    assert dlg.browser_stack.currentWidget() is dlg.icon_view
    assert dlg.icon_view.count() == 3  # два корня + добавленная папка
    assert dlg._view_buttons["icons"].isChecked()

    dlg._set_view_mode("columns", persist=False)
    assert dlg.browser_stack.currentWidget() is dlg.column_view
    assert dlg.column_view.count() == 1
    assert dlg._column_lists[0].count() == 3
    assert dlg._view_buttons["columns"].isChecked()

    dlg._set_view_mode("list", persist=False)
    assert not dlg.tree.isColumnHidden(1)
    assert not dlg.tree.header().isHidden()
    dlg.close()


def test_browser_column_view_keeps_each_selected_folder_as_a_column(app):
    dlg = _make_browser_dialog(app)
    dlg._folder_listing_cache["/отчеты"] = [
        {"name": "Сериал", "type": "dir", "path": "/отчеты/Сериал"},
        {"name": "readme.pdf", "type": "file", "path": "/отчеты/readme.pdf"},
    ]
    dlg._folder_listing_cache["/отчеты/Сериал"] = [
        {"name": "Серия 01", "type": "dir", "path": "/отчеты/Сериал/Серия 01"},
    ]

    dlg._set_view_mode("columns", persist=False)
    root_column = dlg._column_lists[0]
    reports_item = next(
        root_column.item(index)
        for index in range(root_column.count())
        if root_column.item(index).data(Qt.UserRole) == "/отчеты"
    )
    root_column.setCurrentItem(reports_item)

    assert len(dlg._column_lists) == 2
    assert root_column.currentItem().data(Qt.UserRole) == "/отчеты"
    series_column = dlg._column_lists[1]
    assert [series_column.item(i).toolTip() for i in range(series_column.count())] == [
        "readme.pdf", "Сериал",
    ]

    series_item = next(
        series_column.item(index)
        for index in range(series_column.count())
        if series_column.item(index).data(Qt.UserRole) == "/отчеты/Сериал"
    )
    series_column.setCurrentItem(series_item)
    assert len(dlg._column_lists) == 3
    assert dlg._column_lists[0] is root_column
    assert dlg._column_lists[1] is series_column
    assert dlg._column_lists[2].item(0).toolTip() == "Серия 01"
    dlg.close()


def test_browser_column_view_applies_type_filter_and_preserves_open_path(app):
    dlg = _make_browser_dialog(app)
    series_path = "/отчеты/Show/e05"
    main_path = f"{series_path}/main"
    me_path = f"{series_path}/me"
    dlg._folder_listing_cache["/отчеты"] = [
        {"name": "Show e05", "type": "dir", "path": series_path},
    ]
    dlg._folder_listing_cache[series_path] = [
        {
            "name": "отчеты_GAMES_EP01_MIX_3007",
            "type": "dir",
            "path": main_path,
        },
        {
            "name": "отчеты_GAMES_EP01_M&E_3007",
            "type": "dir",
            "path": me_path,
        },
        {
            "name": "отчеты_GAMES_EP01_MIX_5.1_FOR_DCP",
            "type": "dir",
            "path": f"{series_path}/dcp",
        },
    ]

    dlg._set_view_mode("columns", persist=False)
    root_column = dlg._column_lists[0]
    reports_item = next(
        root_column.item(index)
        for index in range(root_column.count())
        if root_column.item(index).data(Qt.UserRole) == "/отчеты"
    )
    root_column.setCurrentItem(reports_item)
    dlg._column_lists[1].setCurrentRow(0)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))

    assert len(dlg._column_lists) == 3
    assert dlg._column_lists[0].currentItem().data(Qt.UserRole) == "/отчеты"
    assert dlg._column_lists[1].currentItem().data(Qt.UserRole) == series_path
    visible_report_paths = [
        dlg._column_lists[2].item(index).data(Qt.UserRole)
        for index in range(dlg._column_lists[2].count())
    ]
    assert visible_report_paths == [me_path]
    dlg.close()


def test_browser_column_view_uses_variant_order_and_marks_latest_versions(app):
    dlg = _make_browser_dialog(app)
    entries = [
        {
            "name": "отчет_Show_s01_e02_DCP_2026_06_12_rus",
            "type": "dir", "path": "/x/dcp",
        },
        {
            "name": "отчет_Show_s01_e02_ME_2026_06_15_rus",
            "type": "dir", "path": "/x/me-new",
        },
        {
            "name": "отчет_Show_s01_e02_2026_06_10_rus",
            "type": "dir", "path": "/x/main-new",
        },
        {
            "name": "отчет_Show_s01_e02_DUB_2026_06_11_rus",
            "type": "dir", "path": "/x/dub",
        },
        {
            "name": "отчет_Show_s01_e02_ME_2026_06_02_rus",
            "type": "dir", "path": "/x/me-old",
        },
        {
            "name": "отчет_Show_s01_e02_2026_06_01_rus",
            "type": "dir", "path": "/x/main-old",
        },
    ]

    column = dlg._append_column(entries, parent_path="/x")

    paths = [column.item(index).data(Qt.UserRole) for index in range(column.count())]
    assert paths == [
        "/x/main-old", "/x/main-new", "/x/me-old", "/x/me-new", "/x/dub", "/x/dcp",
    ]
    by_path = {
        column.item(index).data(Qt.UserRole): column.item(index)
        for index in range(column.count())
    }
    assert by_path["/x/main-old"].text().startswith("v1  —")
    assert by_path["/x/main-new"].text().startswith("v2 · последняя  —")
    assert by_path["/x/main-new"].data(Qt.UserRole + 13) is True
    assert type(column.itemDelegate()).__name__ == "_ColumnVersionDelegate"
    assert by_path["/x/me-old"].text().startswith("ME · v1  —")
    assert by_path["/x/me-new"].text().startswith("ME · v2 · последняя  —")
    assert "Версия:" in by_path["/x/me-new"].toolTip()
    dlg.close()


def test_browser_file_icons_are_distinct_by_format(app):
    dlg = _make_browser_dialog(app)

    pdf = dlg._icon_for("mix.pdf", False)
    csv_icon = dlg._icon_for("markers.csv", False)
    docx = dlg._icon_for("отчет.docx", False)
    audio = dlg._icon_for("mix.wav", False)
    folder = dlg._icon_for("Сезон 01", True)

    icons = [pdf, csv_icon, docx, audio, folder]
    assert all(not icon.isNull() for icon in icons)
    assert len({icon.cacheKey() for icon in icons}) == len(icons)
    assert dlg._FILE_BADGE_STYLES[".pdf"] != dlg._FILE_BADGE_STYLES[".csv"]
    dlg.close()


def test_browser_dialog_guards_insufficient_versions_without_draft(app):
    dlg = _make_browser_dialog(app)
    dlg._open_version_picker_dialog = MagicMock()
    dlg._open_version_chain_dialog = MagicMock()
    dlg._pending_versions_action = "picker"

    with patch.object(QMessageBox, "information") as info_mock:
        dlg._on_folder_versions_resolved([_versions()[0]])  # только 1 версия, черновика нет

    info_mock.assert_called_once()
    dlg._open_version_picker_dialog.assert_not_called()
    dlg._open_version_chain_dialog.assert_not_called()
    dlg.close()


def test_browser_dialog_allows_single_version_compare_with_draft(app, tmp_path):
    draft = tmp_path / "отчет_draft.docx"
    draft.write_text("stub")
    dlg = _make_browser_dialog(app, local_draft_docx_path=draft)
    dlg._open_version_picker_dialog = MagicMock()
    dlg._open_version_chain_dialog = MagicMock()
    dlg._pending_versions_action = "picker"

    with patch.object(QMessageBox, "information") as info_mock:
        dlg._on_folder_versions_resolved([_versions()[0]])

    info_mock.assert_not_called()
    dlg._open_version_picker_dialog.assert_called_once()
    dlg.close()


def test_browser_dialog_chain_action_needs_two_versions_even_with_draft(app, tmp_path):
    draft = tmp_path / "отчет_draft.docx"
    draft.write_text("stub")
    dlg = _make_browser_dialog(app, local_draft_docx_path=draft)
    dlg._open_version_picker_dialog = MagicMock()
    dlg._open_version_chain_dialog = MagicMock()
    dlg._pending_versions_action = "chain"

    with patch.object(QMessageBox, "information") as info_mock:
        dlg._on_folder_versions_resolved([_versions()[0]])

    info_mock.assert_called_once()
    dlg._open_version_chain_dialog.assert_not_called()
    dlg.close()


def test_browser_dialog_dispatches_to_chain_dialog(app):
    dlg = _make_browser_dialog(app)
    dlg._open_version_picker_dialog = MagicMock()
    dlg._open_version_chain_dialog = MagicMock()
    dlg._pending_versions_action = "chain"

    dlg._on_folder_versions_resolved(_versions())

    dlg._open_version_chain_dialog.assert_called_once()
    dlg._open_version_picker_dialog.assert_not_called()
    dlg.close()


def _fake_versions_thread_capturing(captured):
    def fake_thread(token, path, meta=None):
        captured["path"] = path
        thread = MagicMock()
        thread.resolved = MagicMock()
        thread.resolved.connect = MagicMock()
        thread.failed = MagicMock()
        thread.failed.connect = MagicMock()
        thread.start = MagicMock()
        return thread
    return fake_thread


def test_browser_dialog_fetch_versions_uses_parent_folder_for_selected_report_version(app):
    # Выбрана сама версия отчёта (имя начинается с «отчет_», в т.ч.
    # нестандартное «отчет_20251018_MVD_MIX») — цепочку версий надо искать
    # в РОДИТЕЛЬСКОЙ папке (соседи), а не среди её собственных файлов.
    dlg = _make_browser_dialog(app)
    dlg._nav_current = ("/отчеты/МАЖОР В ДУБАЕ", "МАЖОР В ДУБАЕ")
    item = dlg._make_item(
        {"name": "отчет_20251018_MVD_MIX", "type": "dir", "path": "/отчеты/МАЖОР В ДУБАЕ/отчет_20251018_MVD_MIX"},
        "/отчеты/МАЖОР В ДУБАЕ/отчет_20251018_MVD_MIX",
    )
    dlg.tree.addTopLevelItem(item)
    dlg.tree.setCurrentItem(item)

    captured = {}
    with patch.object(dialogs_module, "YandexDiskFolderVersionsThread",
                      side_effect=_fake_versions_thread_capturing(captured)):
        dlg._compare_versions_on_selected()

    assert captured["path"] == "/отчеты/МАЖОР В ДУБАЕ"
    assert dlg._pending_versions_wanted_category == "main"  # нет маркера варианта
    dlg.close()


def test_browser_dialog_fetch_versions_uses_expanded_childs_own_parent_not_navigated_root(app):
    # Реальный регресс: вошли (плоская навигация) в папку СЕРИИ, раскрыли
    # эпизод e01 внутри плоского вида, выбрали версию внутри e01. Путь
    # цепочки должен браться от РОДИТЕЛЯ выбранной версии (e01), а не от
    # корневой просматриваемой папки серии (там «версии» — это e01…e08).
    dlg = _make_browser_dialog(app)
    dlg._nav_current = ("/отчеты/БПЦ В ПИТЕРЕ", "БПЦ В ПИТЕРЕ")
    episode_item = dlg._make_item(
        {"name": "e01", "type": "dir", "path": "/отчеты/БПЦ В ПИТЕРЕ/e01"}, "/отчеты/БПЦ В ПИТЕРЕ/e01",
    )
    dlg.tree.addTopLevelItem(episode_item)
    version_item = dlg._make_item(
        {"name": "отчет_besprintsipnye_v_pitere_s01_e01_cens_AD_2025_04_20_rus", "type": "dir",
         "path": "/отчеты/БПЦ В ПИТЕРЕ/e01/отчет_besprintsipnye_v_pitere_s01_e01_cens_AD_2025_04_20_rus"},
        "/отчеты/БПЦ В ПИТЕРЕ/e01/отчет_besprintsipnye_v_pitere_s01_e01_cens_AD_2025_04_20_rus",
    )
    episode_item.addChild(version_item)
    dlg.tree.setCurrentItem(version_item)

    captured = {}
    with patch.object(dialogs_module, "YandexDiskFolderVersionsThread",
                      side_effect=_fake_versions_thread_capturing(captured)):
        dlg._compare_versions_on_selected()

    assert captured["path"] == "/отчеты/БПЦ В ПИТЕРЕ/e01"  # родитель версии, не серия
    assert dlg._pending_versions_wanted_category == "ad"  # AD найден лёгким сканированием в "cens_AD"
    dlg.close()


def test_browser_dialog_fetch_versions_uses_own_path_for_episode_container(app):
    # Выбрана папка-контейнер эпизода (имя не начинается с «отчет_») —
    # версии это её прямые дети, путь = сама папка.
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e01", "type": "dir", "path": "/отчеты/Show/e01"}, "/отчеты/Show/e01")
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    captured = {}
    with patch.object(dialogs_module, "YandexDiskFolderVersionsThread",
                      side_effect=_fake_versions_thread_capturing(captured)):
        dlg._compare_versions_on_selected()

    assert captured["path"] == "/отчеты/Show/e01"
    assert dlg._pending_versions_wanted_category is None  # контейнер — вариант заранее не известен
    dlg.close()


def test_browser_dialog_fetch_versions_falls_back_to_parent_for_legacy_unprefixed_folder(app):
    # Реальный регресс: старая версия отчёта, загруженная ДО введения
    # соглашения об имени с префиксом «отчет_», лежит РЯДОМ со своей
    # современной копией «отчет_...» — обе представляют один и тот же
    # эпизод. Поиск внутри самой такой папки (как если бы это был
    # контейнер) ничего не находит; должен сработать один retry на
    # родителя, где обнаружится хотя бы современная копия-сосед.
    dlg = _make_browser_dialog(app)
    item = dlg._make_item(
        {"name": "one_last_sin_s01_e01_Master_uncens_2025_05_14", "type": "dir",
         "path": "/отчеты/one last sin/e01/one_last_sin_s01_e01_Master_uncens_2025_05_14"},
        "/отчеты/one last sin/e01/one_last_sin_s01_e01_Master_uncens_2025_05_14",
    )
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    captured_paths = []

    def fake_thread(token, path, meta=None):
        captured_paths.append(path)
        thread = MagicMock()
        thread.resolved = MagicMock()
        thread.resolved.connect = MagicMock()
        thread.failed = MagicMock()
        thread.failed.connect = MagicMock()
        thread.start = MagicMock()
        return thread

    with patch.object(dialogs_module, "YandexDiskFolderVersionsThread", side_effect=fake_thread):
        dlg._compare_versions_on_selected()
        assert captured_paths == ["/отчеты/one last sin/e01/one_last_sin_s01_e01_Master_uncens_2025_05_14"]

        # Контейнерный поиск внутри самой папки не находит ничего (это не
        # контейнер, а версия отчёта без префикса) — должен запуститься
        # ровно один fallback-запрос к родителю.
        dlg._on_folder_versions_resolved([])
        assert captured_paths == [
            "/отчеты/one last sin/e01/one_last_sin_s01_e01_Master_uncens_2025_05_14",
            "/отчеты/one last sin/e01",
        ]
        assert dlg._pending_versions_wanted_category == "main"  # свой вариант, без маркера

    with patch.object(QMessageBox, "information") as info_mock:
        dlg._open_version_picker_dialog = MagicMock()
        # Родитель нашёл современную копию-соседа — теперь версий достаточно.
        dlg._on_folder_versions_resolved([
            {"path": "/отчеты/one last sin/e01/one_last_sin_s01_e01_Master_uncens_2025_05_14",
             "label": "one_last_sin_s01_e01_Master_uncens_2025_05_14", "date": date(2025, 5, 14)},
            {"path": "/отчеты/one last sin/e01/отчет_one_last_sin_s01_e01_Master_uncens_2025_05_14",
             "label": "отчет_one_last_sin_s01_e01_Master_uncens_2025_05_14", "date": date(2025, 5, 14)},
        ])

    info_mock.assert_not_called()  # больше не "недостаточно версий"
    dlg._open_version_picker_dialog.assert_called_once()
    dlg.close()


def test_browser_dialog_fetch_versions_does_not_fallback_twice(app):
    # Fallback — ровно один retry, не бесконечный цикл: если и родитель не
    # даёт достаточно версий, должно показаться обычное сообщение об ошибке.
    dlg = _make_browser_dialog(app)
    item = dlg._make_item(
        {"name": "lonely_legacy_2025_01_01", "type": "dir", "path": "/отчеты/Show/e01/lonely_legacy_2025_01_01"},
        "/отчеты/Show/e01/lonely_legacy_2025_01_01",
    )
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    captured_paths = []

    def fake_thread(token, path, meta=None):
        captured_paths.append(path)
        thread = MagicMock()
        thread.resolved = MagicMock()
        thread.resolved.connect = MagicMock()
        thread.failed = MagicMock()
        thread.failed.connect = MagicMock()
        thread.start = MagicMock()
        return thread

    with patch.object(dialogs_module, "YandexDiskFolderVersionsThread", side_effect=fake_thread):
        dlg._compare_versions_on_selected()
        dlg._on_folder_versions_resolved([])  # контейнер пуст — триггерит fallback на родителя

        with patch.object(QMessageBox, "information") as info_mock:
            dlg._on_folder_versions_resolved([])  # родитель тоже пуст — второго fallback быть не должно

    assert captured_paths == [
        "/отчеты/Show/e01/lonely_legacy_2025_01_01",
        "/отчеты/Show/e01",
    ]
    info_mock.assert_called_once()
    dlg.close()


def test_browser_dialog_fetch_versions_starts_thread_and_resolves(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e01", "type": "dir", "path": "/отчеты/Show/e01"}, "/отчеты/Show/e01")
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    dlg._on_folder_versions_resolved = MagicMock()
    with patch("src.report_uploader.list_report_versions", return_value=_versions()):
        dlg._compare_versions_on_selected()
        _run_until(lambda: dlg._on_folder_versions_resolved.called)

    dlg._on_folder_versions_resolved.assert_called_once_with(_versions())
    dlg.close()


def test_browser_dialog_fetch_versions_derives_wanted_category_from_selected_version_item(app):
    # Right-click прямо на конкретной версии (не на контейнере эпизода) —
    # цепочка ищется в родительской папке, а нужная категория варианта
    # (для авто-выбора без вопроса) берётся из имени выбранной версии.
    dlg = _make_browser_dialog(app)
    episode_item = dlg._make_item({"name": "e02", "type": "dir", "path": "/отчеты/Show/e02"}, "/отчеты/Show/e02")
    dlg._root_items[0].addChild(episode_item)
    version_item = dlg._make_item(
        {"name": "отчет_Show_s01_e02_MnE_2026_06_10_rus", "type": "dir",
         "path": "/отчеты/Show/e02/отчет_Show_s01_e02_MnE_2026_06_10_rus"},
        "/отчеты/Show/e02/отчет_Show_s01_e02_MnE_2026_06_10_rus",
    )
    episode_item.addChild(version_item)
    dlg.tree.setCurrentItem(version_item)

    captured = {}
    with patch.object(dialogs_module, "YandexDiskFolderVersionsThread",
                      side_effect=_fake_versions_thread_capturing(captured)):
        dlg._compare_versions_on_selected()

    assert captured["path"] == "/отчеты/Show/e02"
    assert dlg._pending_versions_wanted_category == "me"
    dlg.close()


def test_browser_dialog_compare_shows_warning_when_comparison_is_none(app):
    dlg = _make_browser_dialog(app)

    with patch("src.report_uploader.compare_two_versions", return_value=None), \
         patch.object(QMessageBox, "warning") as warning_mock:
        dlg._run_version_compare("/a/v1", "/a/v2", "v1", "v2")
        _run_until(lambda: warning_mock.called)

    assert dlg._compare_thread is None
    dlg.close()


def test_browser_dialog_pick_another_reopens_picker(app):
    dlg = _make_browser_dialog(app)
    dlg._open_version_picker_dialog = MagicMock()

    with patch("src.report_uploader.compare_two_versions", return_value=_comparison()), \
         patch.object(YandexUploadDiffDialog, "exec_", return_value=YandexUploadDiffDialog.PICK_ANOTHER):
        dlg._run_version_compare("/a/v1", "/a/v2", "v1", "v2")
        _run_until(lambda: dlg._open_version_picker_dialog.called)

    dlg.close()


def _mixed_variant_versions():
    return [
        {"path": "/a/v1", "label": "отчет_Show_s01_e02_2026_06_01_rus", "date": date(2026, 6, 1)},
        {"path": "/a/v2", "label": "отчет_Show_s01_e02_2026_06_10_rus", "date": date(2026, 6, 10)},
        {"path": "/a/mne1", "label": "отчет_Show_s01_e02_MnE_2026_06_02_rus", "date": date(2026, 6, 2)},
        {"path": "/a/mne2", "label": "отчет_Show_s01_e02_MnE_2026_06_12_rus", "date": date(2026, 6, 12)},
    ]


def test_resolve_variant_chain_passes_through_single_variant(app):
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem") as get_item_mock:
        result = dlg._resolve_variant_chain(_versions())
    get_item_mock.assert_not_called()
    assert result == _versions()
    dlg.close()


def test_resolve_variant_chain_asks_and_filters_to_chosen_variant(app):
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem", return_value=("ME", True)):
        result = dlg._resolve_variant_chain(_mixed_variant_versions())

    assert [v["path"] for v in result] == ["/a/mne1", "/a/mne2"]
    dlg.close()


def test_resolve_variant_chain_returns_none_when_cancelled(app):
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem", return_value=("ME", False)):
        result = dlg._resolve_variant_chain(_mixed_variant_versions())

    assert result is None
    dlg.close()


def _versions_with_compound_tag_and_different_series():
    # Реальный регресс: "cens_AD" — составной тег (censorship-маркер +
    # вариант), строгий REPORT_PATTERN его не разбирает вообще (variant
    # допускает только один "словный" сегмент между эпизодом и датой) — но
    # лёгкое сканирование (то же, что у иконок/фильтра в дереве) всё равно
    # находит "AD" по границам слова, поэтому эти версии больше не
    # смешиваются молча с распознанной MnE-группой другого сериала.
    return [
        {"path": "/a/ad1", "label": "отчет_besprintsipnye_v_pitere_s01_e08_cens_AD_2025_06_11_rus", "date": None},
        {"path": "/a/ad2", "label": "отчет_besprintsipnye_v_pitere_s01_e08_cens_AD_2025_06_23_rus", "date": None},
        {"path": "/a/mne1", "label": "отчет_Nepreklonniy_vozrast_s01_e08_MnE_2025_08_11_rus", "date": date(2025, 8, 11)},
        {"path": "/a/mne2", "label": "отчет_Nepreklonniy_vozrast_s01_e08_MnE_2025_07_17_rus", "date": date(2025, 7, 17)},
    ]


def test_resolve_variant_chain_finds_ad_inside_compound_tag_via_lenient_scan(app):
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem", return_value=("ME", True)) as get_item_mock:
        result = dlg._resolve_variant_chain(_versions_with_compound_tag_and_different_series())

    # Диалог должен был спросить (2 реальные группы: ME и AD, найденный
    # лёгким сканированием внутри "cens_AD").
    get_item_mock.assert_called_once()
    options = get_item_mock.call_args[0][3]
    assert set(options) == {"ME", "AD"}
    # Выбор "ME" не должен подмешивать AD-версии из "cens_AD".
    assert [v["path"] for v in result] == ["/a/mne1", "/a/mne2"]
    dlg.close()


def test_resolve_variant_chain_auto_selects_wanted_category_without_prompting(app):
    # Правый клик пришёлся на конкретную версию с уже известным вариантом
    # (см. _fetch_versions_for_selected) — пользователь уже неявно выбрал
    # вариант, кликнув по конкретному файлу, спрашивать ещё раз не нужно.
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem") as get_item_mock:
        result = dlg._resolve_variant_chain(_versions_with_compound_tag_and_different_series(), wanted_category="me")

    get_item_mock.assert_not_called()
    assert [v["path"] for v in result] == ["/a/mne1", "/a/mne2"]
    dlg.close()


def test_resolve_variant_chain_falls_back_to_prompt_when_wanted_category_absent(app):
    # wanted_category, которой нет среди найденных групп (например, override
    # или устаревшая информация) — не должно молча схлопывать список в
    # пустой, спрашиваем пользователя как обычно.
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem", return_value=("ME", True)) as get_item_mock:
        result = dlg._resolve_variant_chain(_versions_with_compound_tag_and_different_series(), wanted_category="vo")

    get_item_mock.assert_called_once()
    assert [v["path"] for v in result] == ["/a/mne1", "/a/mne2"]
    dlg.close()


def test_resolve_variant_chain_can_select_other_group_explicitly(app):
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem", return_value=("AD", True)):
        result = dlg._resolve_variant_chain(_versions_with_compound_tag_and_different_series())

    assert [v["path"] for v in result] == ["/a/ad1", "/a/ad2"]
    dlg.close()


def test_resolve_variant_chain_keeps_non_standard_names_as_one_main_group(app):
    # Реальный регресс: многие серии не совпадают со строгой схемой имени
    # вообще (другой формат даты, нет "_sNN_eNN_" и т.п.), но по факту это
    # обычные основные отчёты. Раньше такие версии считались "не
    # распознанными" и уходили в отдельную группу-обломок по каждому
    # непохожему имени — папка с 5 разными по формату, но одинаково
    # "основными" версиями внезапно требовала выбора варианта, и после
    # выбора показывала "Недостаточно версий", хотя версий было достаточно.
    versions = [
        {"path": "/a/1", "label": "отчет_20251015_MVD_PLATFORM", "date": None},
        {"path": "/a/2", "label": "отчет_20251018_MVD_MIX", "date": None},
        {"path": "/a/3", "label": "отчет_MAZHOR_DUBAI_2025_11_28_rus", "date": None},
    ]
    dlg = _make_browser_dialog(app)
    with patch.object(QInputDialog, "getItem") as get_item_mock:
        result = dlg._resolve_variant_chain(versions)

    get_item_mock.assert_not_called()  # одна группа ("main") — спрашивать нечего
    assert result == versions
    dlg.close()


def test_on_folder_versions_resolved_aborts_when_variant_choice_cancelled(app):
    dlg = _make_browser_dialog(app)
    dlg._open_version_picker_dialog = MagicMock()
    dlg._open_version_chain_dialog = MagicMock()
    dlg._pending_versions_action = "picker"

    with patch.object(QInputDialog, "getItem", return_value=("MnE", False)):
        dlg._on_folder_versions_resolved(_mixed_variant_versions())

    dlg._open_version_picker_dialog.assert_not_called()
    dlg._open_version_chain_dialog.assert_not_called()
    dlg.close()


# ---------------------------------------------------------------------------
# Инлайн-метки версий прямо в дереве при разворачивании папки


def test_browser_dialog_labels_versions_oldest_to_newest(app):
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e02_2026_06_10_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_2026_06_01_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_2026_06_15_rus", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    assert by_name["отчет_Show_s01_e02_2026_06_01_rus"].text(2).startswith("v1")
    assert by_name["отчет_Show_s01_e02_2026_06_10_rus"].text(2).startswith("v2")
    latest = by_name["отчет_Show_s01_e02_2026_06_15_rus"]
    assert latest.text(2).startswith("v3")
    assert "последняя" in latest.text(2)
    dlg.close()


def test_browser_dialog_skips_label_for_lone_version_and_unrelated_entries(app):
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e03_2026_06_01_rus", "type": "dir", "modified": "2026-06-01T00:00:00+00:00"},
        {"name": "Прочее", "type": "dir", "modified": "2026-06-01T00:00:00+00:00"},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    assert not by_name["отчет_Show_s01_e03_2026_06_01_rus"].text(2).startswith("v")
    assert by_name["Прочее"].text(2) == "01.06.2026 03:00"  # UTC 00:00 -> MSK 03:00
    dlg.close()


def test_browser_dialog_keeps_mne_variant_as_separate_chain(app):
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e02_2026_06_01_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_2026_06_10_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_MnE_2026_06_02_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_MnE_2026_06_12_rus", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}

    # Основная цепочка (без варианта) — «чистые» v1/v2, без упоминания MnE.
    assert by_name["отчет_Show_s01_e02_2026_06_01_rus"].text(2).startswith("v1")
    assert "MnE" not in by_name["отчет_Show_s01_e02_2026_06_01_rus"].text(2)
    assert "MnE" not in by_name["отчет_Show_s01_e02_2026_06_10_rus"].text(2)

    # MnE-цепочка размечена отдельно и помечена как таковая в подписи.
    mne_old = by_name["отчет_Show_s01_e02_MnE_2026_06_02_rus"]
    mne_new = by_name["отчет_Show_s01_e02_MnE_2026_06_12_rus"]
    assert mne_old.text(2).startswith("MnE · v1")
    assert mne_new.text(2).startswith("MnE · v2")
    assert "последняя" in mne_new.text(2)
    # Основная цепочка не должна ошибочно получить «последнюю» из MnE-серии.
    assert "последняя" in by_name["отчет_Show_s01_e02_2026_06_10_rus"].text(2)
    dlg.close()


def test_browser_dialog_skips_label_for_lone_mne_entry(app):
    # ME может иметь столько же версий, сколько и основной отчёт (не
    # обязательно единственную запись) — но пока в папке лежит только одна
    # ME-запись, сравнивать/нумеровать нечего, как и для одиночной основной
    # версии: метка не ставится ни для той, ни для другой.
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e02_2026_06_01_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_2026_06_10_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_MnE_2026_06_05_rus", "type": "dir", "modified": "2026-06-05T00:00:00+00:00"},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    mne_item = by_name["отчет_Show_s01_e02_MnE_2026_06_05_rus"]
    assert "MnE" not in mne_item.text(2)
    assert "последняя" not in mne_item.text(2)
    dlg.close()


def test_browser_dialog_labels_multi_version_mne_chain_independently(app):
    # ME с несколькими версиями — своя цепочка v1/v2/…/последняя, со своей
    # собственной «последней», независимой от основной цепочки рядом.
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e02_2026_06_01_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_MnE_2026_06_02_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_MnE_2026_06_08_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e02_MnE_2026_06_15_rus", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    assert by_name["отчет_Show_s01_e02_MnE_2026_06_02_rus"].text(2).startswith("MnE · v1")
    assert by_name["отчет_Show_s01_e02_MnE_2026_06_08_rus"].text(2).startswith("MnE · v2")
    latest = by_name["отчет_Show_s01_e02_MnE_2026_06_15_rus"]
    assert latest.text(2).startswith("MnE · v3")
    assert "последняя" in latest.text(2)
    assert latest.foreground(2).color().name() == "#34c759"
    # Одиночная основная версия рядом по-прежнему без метки — не цепочка.
    assert "последняя" not in by_name["отчет_Show_s01_e02_2026_06_01_rus"].text(2)
    dlg.close()


def test_browser_dialog_merges_uncens_version_into_main_chain(app):
    # CENS/UNCENS — признак цензурирования самого основного отчёта, а не
    # отдельный параллельный тип поставки вроде ME/VO/AD/DUB/DCP (см.
    # categorize_variant). Раньше "uncens" получал свою собственную группу
    # и, будучи там единственной записью, оставался вообще без метки
    # версии — реальный регресс, воспроизведённый на «БПЦ В ПИТЕРЕ/e08».
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e08_2025_06_10_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e08_2025_06_11_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e08_uncens_2025_06_23_rus", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    v1 = by_name["отчет_Show_s01_e08_2025_06_10_rus"]
    v2 = by_name["отчет_Show_s01_e08_2025_06_11_rus"]
    v3_uncens = by_name["отчет_Show_s01_e08_uncens_2025_06_23_rus"]

    assert v1.text(2).startswith("v1")
    assert v2.text(2).startswith("v2")
    # Метка варианта — ПОСЛЕ номера версии (это подвариант основной
    # цепочки, а не отдельная параллельная цепочка вроде "MnE · v1").
    assert v3_uncens.text(2).startswith("v3 · uncens")
    assert "последняя" in v3_uncens.text(2)
    assert "последняя" not in v1.text(2)
    assert "последняя" not in v2.text(2)
    dlg.close()


def test_browser_dialog_merges_cens_version_into_main_chain(app):
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e08_cens_2025_06_10_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e08_2025_06_23_rus", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    v1_cens = by_name["отчет_Show_s01_e08_cens_2025_06_10_rus"]
    v2 = by_name["отчет_Show_s01_e08_2025_06_23_rus"]

    assert v1_cens.text(2).startswith("v1 · cens")
    assert "последняя" not in v1_cens.text(2)
    assert v2.text(2).startswith("v2")
    assert "последняя" in v2.text(2)
    dlg.close()


def test_browser_dialog_skips_label_for_lone_main_version(app):
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_Show_s01_e02_2026_06_01_rus", "type": "dir", "modified": "2026-06-01T00:00:00+00:00"},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    item = parent_item.child(0)
    assert not item.text(2).startswith("v")
    assert "последняя" not in item.text(2)
    dlg.close()


def test_browser_dialog_labels_legacy_versioned_filenames_without_season_episode(app):
    # Старая схема имени без season/episode («отчет_<сериал>_<дата>_v1») —
    # REPORT_PATTERN её не разбирает, но версии всё равно должны
    # группироваться по сериалу и нумероваться по дате, как обычно.
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_KP_Orlov_2026_03_27_v1", "type": "dir", "modified": ""},
        {"name": "отчет_KP_Orlov_2026_03_20_v1", "type": "dir", "modified": ""},
        {"name": "отчет_KP_Orlov_2026_04_01_v1", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    by_name = {parent_item.child(i).text(0): parent_item.child(i) for i in range(parent_item.childCount())}
    assert by_name["отчет_KP_Orlov_2026_03_20_v1"].text(2).startswith("v1")
    assert by_name["отчет_KP_Orlov_2026_03_27_v1"].text(2).startswith("v2")
    latest = by_name["отчет_KP_Orlov_2026_04_01_v1"]
    assert latest.text(2).startswith("v3")
    assert "последняя" in latest.text(2)
    dlg.close()


def test_browser_dialog_skips_label_for_lone_legacy_versioned_filename(app):
    dlg = _make_browser_dialog(app)
    parent_item = dlg._root_items[0]

    children = [
        {"name": "отчет_KP_Orlov_2026_03_27_v1", "type": "dir", "modified": "2026-03-27T00:00:00+00:00"},
    ]
    dlg._on_children_loaded(parent_item, parent_item.data(0, Qt.UserRole), children)

    item = parent_item.child(0)
    assert not item.text(2).startswith("v")
    dlg.close()


def test_browser_dialog_does_not_label_files_inside_a_report_submission_folder(app):
    # Регрессия: реальная структура — одна папка на отправку, внутри неё
    # docx отчёта плюс сопутствующие файлы (исходники и т.п.), которые
    # нередко наследуют то же имя/дату исходника и потому сами по себе
    # тоже парсятся как «отчёт». Раньше это ошибочно превращалось в
    # фальшивую цепочку версий внутри ОДНОЙ отправки — метка оказывалась
    # на файле внутри, а не на самой папке отправки.
    dlg = _make_browser_dialog(app)
    submission_item = dlg._make_item(
        {"name": "отчет_KP_Orlov_Perehodniy_2026_03_27_v1", "type": "dir",
         "path": "/отчеты/preroll/отчет_KP_Orlov_Perehodniy_2026_03_27_v1"},
        "/отчеты/preroll/отчет_KP_Orlov_Perehodniy_2026_03_27_v1",
    )
    dlg._root_items[0].addChild(submission_item)

    children = [
        {"name": "KP_Orlov_Perehodniy_2026_03_27_v1", "type": "file", "modified": ""},
        {"name": "отчет_KP_Orlov_Perehodniy_2026_03_27_v2", "type": "file", "modified": ""},
    ]
    dlg._on_children_loaded(submission_item, submission_item.data(0, Qt.UserRole), children)

    for i in range(submission_item.childCount()):
        assert not submission_item.child(i).text(2).startswith("v")
    dlg.close()


def test_browser_dialog_still_labels_version_folders_under_a_generic_container(app):
    # Убедиться, что фикс выше не задел нормальный случай: контейнер
    # (эпизод/сериал), имя которого само НЕ похоже на отчёт, по-прежнему
    # размечает свои дочерние папки-отправки как цепочку версий.
    dlg = _make_browser_dialog(app)
    container_item = dlg._make_item(
        {"name": "preroll", "type": "dir", "path": "/отчеты/preroll"}, "/отчеты/preroll",
    )
    dlg._root_items[0].addChild(container_item)

    children = [
        {"name": "отчет_KP_Orlov_Perehodniy_2026_03_20_v1", "type": "dir", "modified": ""},
        {"name": "отчет_KP_Orlov_Perehodniy_2026_03_27_v2", "type": "dir", "modified": ""},
    ]
    dlg._on_children_loaded(container_item, container_item.data(0, Qt.UserRole), children)

    by_name = {container_item.child(i).text(0): container_item.child(i) for i in range(container_item.childCount())}
    latest = by_name["отчет_KP_Orlov_Perehodniy_2026_03_27_v2"]
    assert latest.text(2).startswith("v2")
    assert "последняя" in latest.text(2)
    dlg.close()


# ---------------------------------------------------------------------------
# Отдельные иконки для папок ME/AD/основного отчёта


def test_report_variant_extracts_me_and_ad_and_none_for_main(app):
    dlg = _make_browser_dialog(app)
    assert dlg._report_variant("отчет_ulichnaya_eda_s01_e05_ME_2026_04_05_rus") == "ME"
    assert dlg._report_variant("отчет_ulichnaya_eda_s01_e05_AD_2026_04_05_rus") == "AD"
    assert dlg._report_variant("отчет_ulichnaya_eda_s01_e05_2026_04_05_rus") is None
    dlg.close()


def test_icon_for_uses_distinct_icon_per_variant(app):
    dlg = _make_browser_dialog(app)
    me_pixmap = dlg._icon_for("отчет_Show_s01_e05_ME_2026_04_05_rus", True).pixmap(13, 13).toImage()
    ad_pixmap = dlg._icon_for("отчет_Show_s01_e05_AD_2026_04_05_rus", True).pixmap(13, 13).toImage()
    main_pixmap = dlg._icon_for("отчет_Show_s01_e05_2026_04_05_rus", True).pixmap(13, 13).toImage()

    assert me_pixmap != ad_pixmap
    assert me_pixmap != main_pixmap
    assert ad_pixmap != main_pixmap
    dlg.close()


def test_report_variant_recognizes_vo_via_strict_pattern(app):
    dlg = _make_browser_dialog(app)
    assert dlg._report_variant("отчеты_igry_s01_e01_VO_2024_08_01_uzb") == "VO"
    dlg.close()


def test_report_variant_falls_back_to_lenient_token_scan_for_loosely_named_folders(app):
    # Реальные папки на Диске часто не соответствуют строгой схеме имени
    # отчёта (нет "_sNN_eNN_", своя дата вроде "01.08") — строгий парсер
    # их не распознаёт вообще, но маркер варианта в имени всё равно есть.
    dlg = _make_browser_dialog(app)
    assert dlg._report_variant("отчеты_GMS_EP1_M&E_01.08") == "M&E"
    assert dlg._report_variant("отчеты_GMS_EP1_M&E_12.09+") == "M&E"
    assert dlg._report_variant("igry_EP1_AD_12.09") == "AD"
    dlg.close()


def test_report_variant_lenient_scan_does_not_false_positive_on_substrings(app):
    # "AD"/"VO" внутри обычного слова (без границы не-буквы по краям) не
    # должны считаться маркером варианта — иначе "Vlad"/"Advent"/"Volvo"
    # получили бы бейдж ME/AD/VO на ровном месте.
    dlg = _make_browser_dialog(app)
    assert dlg._report_variant("Vlad_random_folder") is None
    assert dlg._report_variant("Advent_calendar") is None
    assert dlg._report_variant("Volvo_project") is None
    dlg.close()


def test_report_variant_ignores_no_lang_negation_prefix(app):
    # «..._no_rus_VO_...» означает «этой дорожки нет» (нет русской VO),
    # а не то, что папка — вариант VO: это обычный основной отчёт.
    dlg = _make_browser_dialog(app)
    assert dlg._report_variant("отчет_mazhor_v_dubae_mix_2025_12_26_no_rus_VO_test") is None
    assert dlg._report_variant("отчет_mazhor_v_dubae_mix_2026_01_15_no_rus_VO_something") is None
    assert dlg._report_variant("отчет_mazhor_v_dubae_mix_2026_01_15_no_VO_something") is None
    dlg.close()


def test_icon_for_recognizes_vo_and_loosely_named_me(app):
    dlg = _make_browser_dialog(app)
    vo_pixmap = dlg._icon_for("отчеты_igry_s01_e01_VO_2024_08_01_uzb", True).pixmap(13, 13).toImage()
    loose_me_pixmap = dlg._icon_for("отчеты_GMS_EP1_M&E_01.08", True).pixmap(13, 13).toImage()
    strict_me_pixmap = dlg._icon_for("отчет_Show_s01_e05_ME_2026_04_05_rus", True).pixmap(13, 13).toImage()
    plain_pixmap = dlg._icon_for("Vlad_random_folder", True).pixmap(13, 13).toImage()

    assert vo_pixmap != plain_pixmap
    assert loose_me_pixmap == strict_me_pixmap  # оба ME — одинаковый бейдж
    assert loose_me_pixmap != plain_pixmap
    dlg.close()


def test_report_variant_recognizes_dub_and_dubbed(app):
    dlg = _make_browser_dialog(app)
    assert dlg._report_variant("отчет_Show_s01_e05_DUB_2026_04_05_rus") == "DUB"
    assert dlg._report_variant("отчет_Show_dubbed_2026_04_05") == "dubbed"
    assert dlg._report_variant("отчет_mazhor_v_dubae_mix_2026_01_15") is None  # "dubae" — не токен "dub"
    dlg.close()


def test_icon_for_recognizes_dub(app):
    dlg = _make_browser_dialog(app)
    dub_pixmap = dlg._icon_for("отчет_Show_s01_e05_DUB_2026_04_05_rus", True).pixmap(13, 13).toImage()
    dubbed_pixmap = dlg._icon_for("отчет_Show_dubbed_2026_04_05", True).pixmap(13, 13).toImage()
    plain_pixmap = dlg._icon_for("Vlad_random_folder", True).pixmap(13, 13).toImage()

    assert dub_pixmap == dubbed_pixmap  # DUB и DUBBED — один и тот же бейдж
    assert dub_pixmap != plain_pixmap
    dlg.close()


def test_variant_category_main_is_default_for_names_without_any_marker(app):
    # "Основной" определяется от противного — нет известного маркера
    # варианта — а не строгим совпадением со схемой имени отчёта. Реальные
    # папки на Диске часто не совпадают со строгой схемой вообще (другой
    # формат даты, нет "_sNN_eNN_"), но по факту являются обычными
    # основными отчётами.
    dlg = _make_browser_dialog(app)
    assert dlg._variant_category("отчет_20251018_MVD_MIX", "/x/mvd") == "main"
    assert dlg._variant_category("отчет_MAZHOR_DUBAI_2025_11_28_rus", "/x/mazhor") == "main"
    dlg.close()


def test_variant_category_recognizes_dcp(app):
    dlg = _make_browser_dialog(app)
    assert dlg._variant_category("DCP +18", "/x/dcp18") == "dcp"
    assert dlg._variant_category("DCP 16+", "/x/dcp16") == "dcp"
    dlg.close()


def test_variant_category_recognizes_industry_synonyms(app):
    # Разные студии по-разному сокращают одни и те же типы отчётов —
    # см. report_filename._VARIANT_TOKEN_PATTERN.
    dlg = _make_browser_dialog(app)
    assert dlg._variant_category("GMS_EP1_DME_01.08", "/x/dme") == "me"
    assert dlg._variant_category("igry_EP1_DVS_12.09", "/x/dvs") == "ad"
    assert dlg._variant_category("igry_EP1_VOICEOVER_12.09", "/x/vo2") == "vo"
    assert dlg._variant_category("some_DCDM_master", "/x/dcdm") == "dcp"
    dlg.close()


def test_icon_for_recognizes_industry_synonym_badges(app):
    dlg = _make_browser_dialog(app)
    dme_icon = dlg._icon_for("GMS_EP1_DME_01.08", True, "/x/dme")
    plain_icon = dlg._icon_for("plain_folder", True, "/x/plain")
    dme_pixmap = dme_icon.pixmap(13, 13).toImage()
    plain_pixmap = plain_icon.pixmap(13, 13).toImage()
    assert dme_pixmap != plain_pixmap
    dlg.close()


def test_variant_category_recognizes_dub(app):
    dlg = _make_browser_dialog(app)
    assert dlg._variant_category("отчет_mazhor_v_dubae_me_2025_12_26_no_rus_VO", "/x/me") == "me"
    assert dlg._variant_category("отчет_Show_s01_e05_DUB_2026_04_05_rus", "/x/dub") == "dub"
    dlg.close()


def test_type_filter_dub_option_shows_only_dub(app):
    dlg = _make_browser_dialog(app)
    dub_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_DUB_2026_04_05_rus", "type": "dir", "path": "/x/dub"}, "/x/dub",
    )
    me_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_ME_2026_04_05_rus", "type": "dir", "path": "/x/me"}, "/x/me",
    )
    dlg.tree.addTopLevelItem(dub_item)
    dlg.tree.addTopLevelItem(me_item)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("dub"))

    assert not dub_item.isHidden()
    assert me_item.isHidden()
    dlg.close()


# ---------------------------------------------------------------------------
# Массовое удаление: папка + вложенный элемент в одном выделении


def test_delete_selected_excludes_descendants_of_other_selected_items(app):
    # Удаление папки уже уничтожает C++-объекты всех её потомков (Qt отдаёт
    # им владение родителю) — если бы потомок остался в очереди на отдельное
    # удаление, .parent() на нём упал бы с "wrapped C/C++ object ... has
    # been deleted" (реальный краш, воспроизведённый в проде).
    dlg = _make_browser_dialog(app)
    folder_item = dlg._make_item({"name": "e02", "type": "dir", "path": "/отчеты/Show/e02"}, "/отчеты/Show/e02")
    dlg._root_items[0].addChild(folder_item)
    child_item = dlg._make_item({"name": "readme.txt", "type": "file", "path": "/отчеты/Show/e02/readme.txt"}, "/отчеты/Show/e02/readme.txt")
    folder_item.addChild(child_item)

    dlg.tree.setCurrentItem(folder_item)
    folder_item.setSelected(True)
    child_item.setSelected(True)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
         patch.object(dialogs_module, "_DeleteThread") as delete_thread_cls:
        fake_thread = MagicMock()
        delete_thread_cls.return_value = fake_thread
        dlg._delete_selected()

    assert delete_thread_cls.call_count == 1
    assert delete_thread_cls.call_args[0][1] == "/отчеты/Show/e02"
    dlg.close()


def test_on_delete_finished_survives_item_whose_cpp_object_was_already_deleted(app):
    from PyQt5 import sip

    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e03", "type": "dir", "path": "/отчеты/Show/e03"}, "/отчеты/Show/e03")
    dlg._root_items[0].addChild(item)
    sip.delete(item)  # эмулируем уничтожение C++-объекта (см. краш выше)

    dlg._delete_queue = []
    dlg._on_delete_finished(item, "/отчеты/Show/e03", True, "")  # не должно поднимать исключение

    dlg.close()


def test_delete_next_queued_skips_item_whose_cpp_object_was_already_deleted(app):
    # Реальный краш из прода: пока открыт модальный QMessageBox.critical()
    # после неудачного удаления предыдущего элемента очереди, его вложенный
    # event loop продолжает доставлять сигналы фоновых потоков — рефреш
    # ветки успевает пересобрать дерево и уничтожить C++-объект следующего
    # элемента в self._delete_queue. Следующий _delete_next_queued() должен
    # пропустить протухший элемент, а не падать на item.data(...).
    from PyQt5 import sip

    dlg = _make_browser_dialog(app)
    stale_item = dlg._make_item({"name": "e04", "type": "dir", "path": "/отчеты/Show/e04"}, "/отчеты/Show/e04")
    dlg._root_items[0].addChild(stale_item)
    sip.delete(stale_item)  # эмулируем уничтожение C++-объекта фоновым рефрешем

    live_item = dlg._make_item({"name": "e05", "type": "dir", "path": "/отчеты/Show/e05"}, "/отчеты/Show/e05")
    dlg._root_items[0].addChild(live_item)

    dlg._delete_queue = [stale_item, live_item]

    with patch.object(dialogs_module, "_DeleteThread") as delete_thread_cls:
        fake_thread = MagicMock()
        delete_thread_cls.return_value = fake_thread
        dlg._delete_next_queued()  # не должно поднимать RuntimeError

    assert delete_thread_cls.call_count == 1
    assert delete_thread_cls.call_args[0][1] == "/отчеты/Show/e05"
    assert dlg._delete_queue == []
    dlg.close()


# ---------------------------------------------------------------------------
# Единственный корень группы показывается без узла-обёртки


def test_browser_dialog_single_root_shows_contents_without_wrapper_node(app):
    # Кнопка-переключатель наверху ("Отчёты"/"Nuendo") уже говорит, какой
    # корень открыт — при единственном настроенном корне не должно быть
    # лишнего узла "/отчеты" в дереве, который приходится разворачивать
    # вручную; сразу показывается содержимое корня.
    entries = [
        {"name": "Show_A", "type": "dir", "path": "/отчеты/Show_A", "modified": ""},
        {"name": "Show_B", "type": "dir", "path": "/отчеты/Show_B", "modified": ""},
    ]
    with patch("src.report_uploader.load_uploaded_reports", return_value=[]), \
         patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=entries):
        dlg = YandexDiskBrowserDialog("test-token", report_roots=["/отчеты"], parent=None)
        _run_until(lambda: dlg.tree.topLevelItemCount() == 2)

    assert dlg._root_items == []
    top_level_names = {dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())}
    assert top_level_names == {"Show_A", "Show_B"}
    assert "/отчеты" not in top_level_names
    dlg.close()


def test_browser_dialog_multi_root_keeps_wrapper_nodes(app):
    dlg = _make_browser_dialog(app)  # два корня — см. _make_browser_dialog
    assert len(dlg._root_items) == 2
    top_level_names = {dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())}
    assert top_level_names == {"/отчеты", "/архив"}
    dlg.close()


def test_browser_dialog_switch_group_reuses_cached_tree_without_refetch(app):
    # Переключение "Отчёты" -> "Nuendo" -> "Отчёты" не должно заново
    # запрашивать листинг уже загруженного корня — см. _stash_group_tree/
    # _restore_group_tree в _switch_group.
    report_entries = [{"name": "Show_A", "type": "dir", "path": "/отчеты/Show_A", "modified": ""}]
    npr_entries = [{"name": "Project_A", "type": "dir", "path": "/nuendo/Project_A", "modified": ""}]

    def fake_list_folder(path):
        return report_entries if path == "/отчеты" else npr_entries

    with patch("src.report_uploader.load_uploaded_reports", return_value=[]), \
         patch("src.yandex_disk_client.YandexDiskClient.list_folder",
               side_effect=fake_list_folder) as mock_list_folder:
        dlg = YandexDiskBrowserDialog(
            "test-token", report_roots=["/отчеты"], npr_root="/nuendo", parent=None,
        )
        _run_until(lambda: dlg.tree.topLevelItemCount() == 1
                   and dlg.tree.topLevelItem(0).text(0) == "Show_A")
        assert mock_list_folder.call_count == 1

        dlg._switch_group("nuendo")
        _run_until(lambda: dlg.tree.topLevelItemCount() == 1
                   and dlg.tree.topLevelItem(0).text(0) == "Project_A")
        assert mock_list_folder.call_count == 2

        dlg._switch_group("reports")
        # Восстановлено из кэша сразу, без нового сетевого запроса.
        assert dlg.tree.topLevelItemCount() == 1
        assert dlg.tree.topLevelItem(0).text(0) == "Show_A"
        assert mock_list_folder.call_count == 2

        dlg._switch_group("nuendo")
        assert dlg.tree.topLevelItemCount() == 1
        assert dlg.tree.topLevelItem(0).text(0) == "Project_A"
        assert mock_list_folder.call_count == 2

    dlg.close()


def test_browser_dialog_close_event_stops_version_threads(app):
    dlg = _make_browser_dialog(app)
    fake_versions_thread = MagicMock()
    fake_versions_thread.isRunning.return_value = False
    fake_compare_thread = MagicMock()
    fake_compare_thread.isRunning.return_value = False
    dlg._folder_versions_thread = fake_versions_thread
    dlg._compare_thread = fake_compare_thread

    dlg.close()

    fake_versions_thread.isRunning.assert_called()
    fake_compare_thread.isRunning.assert_called()


# ---------------------------------------------------------------------------
# Комбинированный выбор папки для отчёта + npr при отправке


def _select_first_leaf(tree, root_item):
    """Разворачивает первый корневой узел дерева пикера и возвращает его

    первого настоящего потомка (не placeholder «Загрузка…»).
    """
    root_item.setExpanded(True)
    _run_until(lambda: root_item.childCount() > 0 and root_item.child(0).data(0, Qt.UserRole) is not None)
    leaf = root_item.child(0)
    tree.setCurrentItem(leaf)
    return leaf


def test_folder_picker_dialog_regression_contract(app):
    # Рефакторинг вынес внутренности в YandexFolderTreeWidget — контракт
    # диалога (конструктор, .selected_path, exec_()) не должен измениться.
    entries = [{"name": "Show_A", "type": "dir", "path": "/отчеты/Show_A", "modified": ""}]
    with patch("src.yandex_disk_client.YandexDiskClient.mkdir", return_value=None), \
         patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=entries):
        from src.yandex_disk_client import YandexDiskClient
        client = YandexDiskClient("test-token")
        dialog = YandexFolderPickerDialog(client, roots=["/отчеты"], parent=None)
        _run_until(lambda: dialog.buttons.button(dialog.buttons.Ok).isEnabled())

        with patch.object(QMessageBox, "warning") as warning_mock:
            dialog._on_accept()  # ничего не выбрано
        warning_mock.assert_called_once()
        assert dialog.selected_path is None

        leaf = _select_first_leaf(dialog.panel.tree, dialog.panel._root_items[0])
        dialog._on_accept()

    assert dialog.selected_path == leaf.data(0, Qt.UserRole)
    assert dialog.result() == QDialog.Accepted
    dialog.close()


def test_combined_folder_picker_requires_both_selections(app):
    entries = [{"name": "Show_A", "type": "dir", "path": "/x/Show_A", "modified": ""}]
    with patch("src.yandex_disk_client.YandexDiskClient.mkdir", return_value=None), \
         patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=entries):
        from src.yandex_disk_client import YandexDiskClient
        client = YandexDiskClient("test-token")
        dialog = CombinedFolderPickerDialog(
            client, report_roots=["/отчеты"], npr_root="/npr", parent=None,
        )
        _run_until(lambda: dialog.buttons.button(dialog.buttons.Ok).isEnabled())

        # Выбрана только цепочка отчёта — npr не выбран, accept должен отказать.
        _select_first_leaf(dialog.report_panel.tree, dialog.report_panel._root_items[0])
        with patch.object(QMessageBox, "warning") as warning_mock:
            dialog._on_accept()
        warning_mock.assert_called_once()
        assert dialog.result() != QDialog.Accepted

        npr_leaf = _select_first_leaf(dialog.npr_panel.tree, dialog.npr_panel._root_items[0])
        dialog._on_accept()

    assert dialog.report_selected_path is not None
    assert dialog.npr_selected_path == npr_leaf.data(0, Qt.UserRole)
    assert dialog.result() == QDialog.Accepted
    dialog.close()


def test_combined_folder_picker_reject_leaves_paths_none(app):
    with patch("src.yandex_disk_client.YandexDiskClient.mkdir", return_value=None), \
         patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=[]):
        from src.yandex_disk_client import YandexDiskClient
        client = YandexDiskClient("test-token")
        dialog = CombinedFolderPickerDialog(
            client, report_roots=["/отчеты"], npr_root="/npr", parent=None,
        )
        dialog.reject()

    assert dialog.report_selected_path is None
    assert dialog.npr_selected_path is None


def test_combined_find_thread_resolves_both():
    thread = YandexCombinedFindThread("token", "Show", ["/отчеты"], None, "npr-key", "/npr")
    with patch("src.report_uploader.find_series_folder", side_effect=["/отчеты/Show", "/npr/Show"]):
        result = thread._work()
    assert result == {"series_path": "/отчеты/Show", "episode_path": "/отчеты/Show", "npr_folder": "/npr/Show"}


def test_combined_find_thread_computes_episode_path_from_meta():
    meta = MagicMock(episode=2)
    thread = YandexCombinedFindThread("token", "Show", ["/отчеты"], meta, "npr-key", "/npr")
    with patch("src.report_uploader.find_series_folder", side_effect=["/отчеты/Show", "/npr/Show"]):
        result = thread._work()
    assert result["episode_path"] == "/отчеты/Show/e02"


def test_combined_find_thread_report_only_resolved():
    thread = YandexCombinedFindThread("token", "Show", ["/отчеты"], None, "npr-key", "/npr")
    with patch("src.report_uploader.find_series_folder", side_effect=["/отчеты/Show", None]):
        result = thread._work()
    assert result["episode_path"] == "/отчеты/Show"
    assert result["npr_folder"] is None


def test_combined_find_thread_npr_only_resolved():
    thread = YandexCombinedFindThread("token", "Show", ["/отчеты"], None, "npr-key", "/npr")
    with patch("src.report_uploader.find_series_folder", side_effect=[None, "/npr/Show"]):
        result = thread._work()
    assert result["episode_path"] is None
    assert result["npr_folder"] == "/npr/Show"


def test_combined_find_thread_neither_resolved():
    thread = YandexCombinedFindThread("token", "Show", ["/отчеты"], None, "npr-key", "/npr")
    with patch("src.report_uploader.find_series_folder", side_effect=[None, None]):
        result = thread._work()
    assert result == {"series_path": None, "episode_path": None, "npr_folder": None}


def test_npr_upload_thread_emits_progress_per_file():
    from src.yandex_ui.threads import NprUploadThread

    thread = NprUploadThread(
        "token", "/npr", "npr-key", ["/local/a.npr", "/local/b.npr", "/local/c.npr"],
        target_folder_path="/npr/Show",
    )
    progress_events = []
    thread.progress.connect(lambda done, total: progress_events.append((done, total)))

    with patch("src.yandex_disk_client.YandexDiskClient.upload", return_value=None), \
         patch("src.report_uploader.remember_series_alias"):
        thread.run()  # напрямую, без .start() — синхронно в потоке теста

    assert progress_events == [(1, 3), (2, 3), (3, 3)]


# ---------------------------------------------------------------------------
# 404 при разворачивании — папка пропала с Диска мимо приложения


def test_list_folder_thread_emits_not_found_for_404():
    from src.yandex_disk_client import YandexDiskError
    from src.yandex_ui.threads import _ListFolderThread

    thread = _ListFolderThread(MagicMock(), "/отчеты/Show/e05/отчет_x")
    thread.client.list_folder.side_effect = YandexDiskError("не найдено", status_code=404)

    not_found_events = []
    failed_events = []
    thread.not_found.connect(lambda path: not_found_events.append(path))
    thread.failed.connect(lambda path, message: failed_events.append((path, message)))

    thread.run()

    assert not_found_events == ["/отчеты/Show/e05/отчет_x"]
    assert failed_events == []


def test_list_folder_thread_still_emits_failed_for_non_404_errors():
    from src.yandex_disk_client import YandexDiskError
    from src.yandex_ui.threads import _ListFolderThread

    thread = _ListFolderThread(MagicMock(), "/отчеты/Show/e05")
    thread.client.list_folder.side_effect = YandexDiskError("сбой сервера", status_code=500)

    not_found_events = []
    failed_events = []
    thread.not_found.connect(lambda path: not_found_events.append(path))
    thread.failed.connect(lambda path, message: failed_events.append((path, message)))

    thread.run()

    assert not_found_events == []
    assert failed_events == [("/отчеты/Show/e05", "сбой сервера")]


def test_browser_dialog_removes_recent_entry_and_forgets_it_on_404(app):
    entry = {
        "local_folder": "/local/x", "remote_path": "/отчеты/Show/e05/отчет_x", "uploaded_at": "",
    }
    with patch("src.report_uploader.load_uploaded_reports", return_value=[entry]):
        dlg = YandexDiskBrowserDialog("test-token", report_roots=["/отчеты", "/архив"], parent=None)
        dlg._switch_group("recent")
    assert dlg.tree.topLevelItemCount() == 1
    item = dlg.tree.topLevelItem(0)

    with patch("src.report_uploader.forget_uploaded_reports") as forget_mock:
        dlg._on_children_load_not_found(item, entry["remote_path"])

    forget_mock.assert_called_once_with([{"remote_path": entry["remote_path"]}])
    assert dlg.tree.topLevelItemCount() == 0
    dlg.close()


def test_browser_dialog_root_branch_404_shows_error_without_forgetting(app):
    dlg = _make_browser_dialog(app)
    root_item = dlg._root_items[0]

    with patch("src.report_uploader.forget_uploaded_reports") as forget_mock, \
         patch.object(QMessageBox, "critical") as critical_mock:
        dlg._on_children_load_not_found(root_item, root_item.data(0, Qt.UserRole))

    forget_mock.assert_not_called()
    critical_mock.assert_called_once()
    dlg.close()


# ---------------------------------------------------------------------------
# Настоящая навигация вперёд (двойной клик на папке — как в Finder)


def test_double_click_on_dir_triggers_navigate_into(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "Show_A", "type": "dir", "path": "/отчеты/Show_A"}, "/отчеты/Show_A")
    dlg._root_items[0].addChild(item)

    with patch.object(dlg, "_navigate_into") as navigate_mock:
        dlg._on_item_double_clicked(item, 0)

    navigate_mock.assert_called_once_with("/отчеты/Show_A", "Show_A")
    dlg.close()


def test_double_click_on_file_still_opens_it_not_navigates(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item(
        {"name": "report.docx", "type": "file", "path": "/отчеты/report.docx", "modified": ""},
        "/отчеты/report.docx",
    )
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    with patch.object(dlg, "_navigate_into") as navigate_mock, \
         patch.object(dlg, "_open_selected") as open_mock:
        dlg._on_item_double_clicked(item, 0)

    navigate_mock.assert_not_called()
    open_mock.assert_called_once()
    dlg.close()


def test_navigate_into_replaces_tree_with_folder_contents_and_shows_breadcrumb(app):
    entries = [{"name": "Show_A", "type": "dir", "path": "/отчеты/Show_A", "modified": ""}]
    dlg = _make_browser_dialog(app)

    with patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=entries):
        dlg._navigate_into("/отчеты/some_folder", "some_folder")
        _run_until(lambda: dlg.tree.topLevelItemCount() == 1 and dlg.tree.topLevelItem(0).text(0) == "Show_A")

    assert not dlg.nav_bar_widget.isHidden()
    assert dlg.nav_breadcrumb_label.text() == "/отчеты/some_folder"  # полный путь, не только имя папки
    dlg.close()


def test_navigate_back_restores_root_view(app):
    dlg = _make_browser_dialog(app)
    original_names = {dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())}

    with patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=[]):
        dlg._navigate_into("/отчеты/some_folder", "some_folder")
        _run_until(lambda: dlg._nav_current is not None and not dlg._expand_threads)

    dlg._navigate_back()

    assert dlg._nav_current is None
    assert dlg.nav_bar_widget.isHidden()
    restored_names = {dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())}
    assert restored_names == original_names
    dlg.close()


def test_switch_group_resets_navigation_and_skips_stashing_navigated_view(app):
    with patch("src.report_uploader.load_uploaded_reports", return_value=[]):
        dlg = YandexDiskBrowserDialog(
            "test-token", report_roots=["/отчеты", "/архив"], npr_root="/npr", parent=None,
        )

    with patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=[]):
        dlg._navigate_into("/отчеты/some_folder", "some_folder")
        _run_until(lambda: dlg._nav_current is not None and not dlg._expand_threads)

    dlg._switch_group("nuendo")

    assert dlg._nav_current is None
    assert dlg.nav_bar_widget.isHidden()
    assert "reports" not in dlg._group_snapshots
    dlg.close()


def test_navigate_into_not_found_forgets_entry_and_goes_back_automatically(app):
    dlg = _make_browser_dialog(app)

    with patch("src.yandex_disk_client.YandexDiskClient.list_folder", return_value=[]):
        dlg._navigate_into("/отчеты/gone", "gone")
        _run_until(lambda: dlg._nav_current is not None and not dlg._expand_threads)

    with patch("src.report_uploader.forget_uploaded_reports") as forget_mock, \
         patch.object(QMessageBox, "critical") as critical_mock:
        dlg._on_nav_folder_not_found("/отчеты/gone")

    forget_mock.assert_called_once_with([{"remote_path": "/отчеты/gone"}])
    critical_mock.assert_called_once()
    assert dlg._nav_current is None
    dlg.close()


# ---------------------------------------------------------------------------
# Сортировка (основные -> ME -> остальное) и фильтр по типу


def test_variant_sort_rank_orders_main_me_vo_dub_other(app):
    dlg = _make_browser_dialog(app)
    assert dlg._variant_sort_rank("отчет_Show_s01_e05_2026_04_05_rus", "/x/main") == 0
    assert dlg._variant_sort_rank("отчет_Show_s01_e05_ME_2026_04_05_rus", "/x/me") == 1
    assert dlg._variant_sort_rank("отчет_igry_s01_e05_VO_2026_04_05_rus", "/x/vo") == 2
    assert dlg._variant_sort_rank("отчет_Show_s01_e05_DUB_2026_04_05_rus", "/x/dub") == 3
    assert dlg._variant_sort_rank("отчет_Show_s01_e05_AD_2026_04_05_rus", "/x/ad") == 4
    assert dlg._variant_sort_rank("e05", "/x/e05") == 0  # контейнер — тоже «основной» (нет варианта)
    dlg.close()


def test_tree_sorts_main_before_me_before_vo_before_other_alphabetically(app):
    dlg = _make_browser_dialog(app)
    parent = dlg._root_items[0]
    for entry in [
        {"name": "отчет_Show_s01_e05_AD_2026_04_05_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e05_VO_2026_04_05_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e05_ME_2026_04_05_rus", "type": "dir", "modified": ""},
        {"name": "отчет_Show_s01_e05_2026_04_05_rus", "type": "dir", "modified": ""},
    ]:
        parent.addChild(dlg._make_item(entry, f"/x/{entry['name']}"))

    names_in_order = [parent.child(i).text(0) for i in range(parent.childCount())]
    assert names_in_order == [
        "отчет_Show_s01_e05_2026_04_05_rus",
        "отчет_Show_s01_e05_ME_2026_04_05_rus",
        "отчет_Show_s01_e05_VO_2026_04_05_rus",
        "отчет_Show_s01_e05_AD_2026_04_05_rus",
    ]
    dlg.close()


def test_type_filter_shows_only_matching_category_and_keeps_container_visible(app):
    dlg = _make_browser_dialog(app)
    container = dlg._make_item({"name": "e05", "type": "dir", "path": "/x/e05"}, "/x/e05")
    dlg._root_items[0].addChild(container)
    main_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_2026_04_05_rus", "type": "dir", "path": "/x/e05/main"}, "/x/e05/main",
    )
    me_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_ME_2026_04_05_rus", "type": "dir", "path": "/x/e05/me"}, "/x/e05/me",
    )
    container.addChild(main_item)
    container.addChild(me_item)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))

    assert not container.isHidden()  # контейнер виден — внутри есть подходящий потомок
    assert main_item.isHidden()
    assert not me_item.isHidden()
    dlg.close()


def test_type_filter_vo_option_shows_only_vo(app):
    dlg = _make_browser_dialog(app)
    container = dlg._make_item({"name": "e05", "type": "dir", "path": "/x/e05"}, "/x/e05")
    dlg._root_items[0].addChild(container)
    me_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_ME_2026_04_05_rus", "type": "dir", "path": "/x/e05/me"}, "/x/e05/me",
    )
    vo_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_VO_2026_04_05_rus", "type": "dir", "path": "/x/e05/vo"}, "/x/e05/vo",
    )
    container.addChild(me_item)
    container.addChild(vo_item)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("vo"))

    assert me_item.isHidden()
    assert not vo_item.isHidden()
    dlg.close()


def test_variant_category_recognizes_loosely_named_me_and_ad_without_override(app):
    # Раньше папки вроде «GMS_EP1_M&E_01.08» получали иконку-бейдж (лёгкое
    # сканирование имени), но категория оставалась None — не участвовали
    # ни в сортировке, ни в фильтре. Сам факт найденного маркера варианта
    # должен работать как раньше строгий _is_report_submission_name.
    dlg = _make_browser_dialog(app)
    assert dlg._variant_category("отчеты_GMS_EP1_M&E_01.08", "/x/me_loose") == "me"
    assert dlg._variant_category("igry_EP1_AD_12.09", "/x/ad_loose") == "ad"
    dlg.close()


def test_type_filter_hides_container_without_matching_descendant(app):
    dlg = _make_browser_dialog(app)
    container = dlg._make_item({"name": "e07", "type": "dir", "path": "/x/e07"}, "/x/e07")
    dlg._root_items[0].addChild(container)
    main_item = dlg._make_item(
        {"name": "отчет_Show_s01_e07_2026_04_05_rus", "type": "dir", "path": "/x/e07/main"}, "/x/e07/main",
    )
    container.addChild(main_item)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))

    assert container.isHidden()  # нет ни одного ME-потомка
    dlg.close()


def test_type_filter_persists_across_group_switch(app):
    with patch("src.report_uploader.load_uploaded_reports", return_value=[]):
        dlg = YandexDiskBrowserDialog(
            "test-token", report_roots=["/отчеты", "/архив"], npr_root="/npr", parent=None,
        )

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))
    assert dlg._type_filter == "me"

    dlg._switch_group("nuendo")

    assert dlg._type_filter == "me"
    dlg.close()


def test_type_filter_reset_to_all_shows_everything_again(app):
    dlg = _make_browser_dialog(app)
    container = dlg._root_items[0]
    main_item = dlg._make_item(
        {"name": "отчет_Show_s01_e05_2026_04_05_rus", "type": "dir", "path": "/x/main"}, "/x/main",
    )
    container.addChild(main_item)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))
    assert main_item.isHidden()

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("all"))
    assert not main_item.isHidden()
    dlg.close()


def test_type_filter_cascades_to_files_inside_a_matching_folder(app):
    # Файл сам по себе не отчёт (нет variant) — если бы фильтр судил его
    # независимо от родителя, он скрывался бы даже внутри ПОДХОДЯЩЕЙ папки.
    dlg = _make_browser_dialog(app)
    me_folder = dlg._make_item(
        {"name": "отчет_Show_s01_e05_ME_2026_04_05_rus", "type": "dir", "path": "/x/me"}, "/x/me",
    )
    dlg._root_items[0].addChild(me_folder)
    inner_file = dlg._make_item(
        {"name": "report.docx", "type": "file", "path": "/x/me/report.docx", "modified": ""}, "/x/me/report.docx",
    )
    me_folder.addChild(inner_file)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))

    assert not me_folder.isHidden()
    assert not inner_file.isHidden()
    dlg.close()


def test_type_filter_hides_files_inside_a_non_matching_folder(app):
    dlg = _make_browser_dialog(app)
    main_folder = dlg._make_item(
        {"name": "отчет_Show_s01_e05_2026_04_05_rus", "type": "dir", "path": "/x/main"}, "/x/main",
    )
    dlg._root_items[0].addChild(main_folder)
    inner_file = dlg._make_item(
        {"name": "report.docx", "type": "file", "path": "/x/main/report.docx", "modified": ""}, "/x/main/report.docx",
    )
    main_folder.addChild(inner_file)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("me"))

    assert main_folder.isHidden()
    assert inner_file.isHidden()
    dlg.close()


def test_type_filter_matches_flat_top_level_files_directly(app):
    # Регресс: некоторые серии хранят отчёты как отдельные файлы прямо в
    # папке серии, без оборачивающей папки на версию — это ровно вид,
    # который показывает "плоская" навигация (двойной клик на папке), где
    # эти файлы — настоящие элементы ВЕРХНЕГО уровня дерева, без общего
    # родителя (иначе категория родителя-контейнера каскадом решала бы
    # видимость обоих файлов одинаково, и тест не проверял бы то, что нужно).
    dlg = _make_browser_dialog(app)
    main_file = dlg._make_item(
        {"name": "отчет_Show_s01_e05_2026_04_05_rus.docx", "type": "file", "path": "/x/main.docx"}, "/x/main.docx",
    )
    me_file = dlg._make_item(
        {"name": "отчет_Show_s01_e05_ME_2026_04_05_rus.docx", "type": "file", "path": "/x/me.docx"}, "/x/me.docx",
    )
    dlg.tree.addTopLevelItem(main_file)
    dlg.tree.addTopLevelItem(me_file)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("main"))

    assert not main_file.isHidden()
    assert me_file.isHidden()
    dlg.close()


def test_type_filter_matches_files_that_do_not_match_strict_naming_at_all(app):
    # Реальный регресс: серия хранит отчёты как файлы, чьё имя не
    # соответствует ни строгому REPORT_PATTERN (нет "_sNN_eNN_"), ни
    # найденному варианту (лёгкое сканирование корректно возвращает None —
    # "no_rus_VO" это отрицание, а не сама VO-версия). Раньше такие файлы
    # считались "контейнером" (категория None) и не проходили ни один
    # конкретный фильтр, даже "Только основные" — список становился пустым.
    dlg = _make_browser_dialog(app)
    item = dlg._make_item(
        {"name": "отчет_mazhor_v_dubae_mix_2025_12_26_no_rus_VO_test.docx", "type": "file", "path": "/x/mazhor.docx"},
        "/x/mazhor.docx",
    )
    dlg._root_items[0].addChild(item)

    dlg.type_filter_combo.setCurrentIndex(dlg.type_filter_combo.findData("main"))

    assert not item.isHidden()
    dlg.close()


# ---------------------------------------------------------------------------
# Ручное назначение типа отчёта через ПКМ (override автоопределения)


def test_effective_variant_uses_override_over_auto_detection(app):
    dlg = _make_browser_dialog(app)
    path = "/x/weird_folder_name"
    assert dlg._effective_variant("weird_folder_name", path) is None  # авто: не распознано

    dlg._variant_overrides[path] = "ME"
    assert dlg._effective_variant("weird_folder_name", path) == "ME"

    dlg._variant_overrides[path] = "MAIN"  # явное «без варианта», отличается от отсутствия override
    assert dlg._effective_variant("weird_folder_name", path) is None
    dlg.close()


def test_variant_category_override_takes_priority_over_auto_detection(app):
    # Автоопределение для "weird_folder_name" (без единого известного
    # маркера варианта) считает его "main" — категория определяется от
    # противного, а не строгим распознаванием имени. Ручной override всё
    # равно должен иметь приоритет — это единственный способ поправить
    # автоопределение, если оно ошиблось (например, реальный AD-отчёт с
    # нестандартным именем, где токен AD не встречается в самом имени).
    dlg = _make_browser_dialog(app)
    path = "/x/weird_folder_name"
    assert dlg._variant_category("weird_folder_name", path) == "main"

    dlg._variant_overrides[path] = "AD"
    assert dlg._variant_category("weird_folder_name", path) == "ad"

    dlg._variant_overrides[path] = "MAIN"
    assert dlg._variant_category("weird_folder_name", path) == "main"
    dlg.close()


def test_set_variant_override_on_item_updates_icon_sort_rank_and_persists(app):
    dlg = _make_browser_dialog(app)
    path = "/x/weird_folder_name"
    item = dlg._make_item({"name": "weird_folder_name", "type": "dir", "path": path}, path)
    dlg._root_items[0].addChild(item)
    assert item.data(0, Qt.UserRole + 3) == 0

    with patch("src.report_uploader.set_variant_override") as set_override_mock:
        dlg._set_variant_override_on_item(item, "ME")

    set_override_mock.assert_called_once_with(path, "ME")
    assert dlg._variant_overrides[path] == "ME"
    assert item.data(0, Qt.UserRole + 3) == 1  # ME-ранг сортировки
    dlg.close()


def test_set_variant_override_on_item_auto_clears_override(app):
    dlg = _make_browser_dialog(app)
    path = "/x/weird_folder_name"
    item = dlg._make_item({"name": "weird_folder_name", "type": "dir", "path": path}, path)
    dlg._root_items[0].addChild(item)
    dlg._variant_overrides[path] = "AD"

    with patch("src.report_uploader.set_variant_override") as set_override_mock:
        dlg._set_variant_override_on_item(item, None)

    set_override_mock.assert_called_once_with(path, None)
    assert path not in dlg._variant_overrides
    dlg.close()


def test_build_variant_override_menu_checks_current_override(app):
    dlg = _make_browser_dialog(app)
    path = "/x/weird_folder_name"
    item = dlg._make_item({"name": "weird_folder_name", "type": "dir", "path": path}, path)
    dlg._variant_overrides[path] = "AD"

    menu = QMenu()
    dlg._build_variant_override_menu(menu, item)
    submenu = menu.actions()[0].menu()
    labels_checked = {a.text(): a.isChecked() for a in submenu.actions()}

    assert labels_checked == {
        "Авто": False, "Основной": False, "ME": False, "AD": True, "VO": False, "DUB": False,
        "DCP": False,
    }
    dlg.close()


# ---------------------------------------------------------------------------
# Drag-and-drop между деревом и Finder


def _make_kind_item(kind: str) -> QTreeWidgetItem:
    item = QTreeWidgetItem(["item"])
    item.setData(0, Qt.UserRole + 1, kind)
    return item


def test_start_drag_uses_finder_urls_for_pure_file_selection(app):
    on_drag_out = MagicMock(return_value=[QUrl.fromLocalFile("/tmp/x.docx")])
    tree = dialogs_module._DragMoveTreeWidget(MagicMock(), MagicMock(), on_drag_out)
    item = _make_kind_item("file")
    tree.addTopLevelItem(item)
    item.setSelected(True)

    with patch.object(dialogs_module, "QDrag") as drag_cls:
        fake_drag = MagicMock()
        drag_cls.return_value = fake_drag
        tree.startDrag(Qt.CopyAction)

    on_drag_out.assert_called_once_with([item])
    fake_drag.setMimeData.assert_called_once()
    fake_drag.exec_.assert_called_once()


def test_start_drag_falls_back_to_super_for_folder_selection(app):
    # Перетаскивание папок должно продолжать работать как раньше (штатный
    # внутренний drag Qt) — новая ветка не должна его перехватывать.
    on_drag_out = MagicMock()
    tree = dialogs_module._DragMoveTreeWidget(MagicMock(), MagicMock(), on_drag_out)
    item = _make_kind_item("dir")
    tree.addTopLevelItem(item)
    item.setSelected(True)

    with patch("PyQt5.QtWidgets.QTreeWidget.startDrag") as super_start_drag:
        tree.startDrag(Qt.CopyAction)

    on_drag_out.assert_not_called()
    super_start_drag.assert_called_once()


def test_drop_event_dispatches_external_urls_to_callback(app):
    on_external_drop = MagicMock()
    tree = dialogs_module._DragMoveTreeWidget(MagicMock(), on_external_drop, MagicMock())
    target_item = _make_kind_item("dir")
    tree.addTopLevelItem(target_item)

    mime = MagicMock()
    mime.hasUrls.return_value = True
    mime.urls.return_value = [QUrl.fromLocalFile("/tmp/dropped.txt")]
    event = MagicMock()
    event.mimeData.return_value = mime

    with patch.object(tree, "itemAt", return_value=target_item):
        tree.dropEvent(event)

    event.acceptProposedAction.assert_called_once()
    on_external_drop.assert_called_once_with([Path("/tmp/dropped.txt")], target_item)


def test_drop_event_ignores_external_urls_without_a_target(app):
    on_external_drop = MagicMock()
    tree = dialogs_module._DragMoveTreeWidget(MagicMock(), on_external_drop, MagicMock())

    mime = MagicMock()
    mime.hasUrls.return_value = True
    mime.urls.return_value = [QUrl.fromLocalFile("/tmp/dropped.txt")]
    event = MagicMock()
    event.mimeData.return_value = mime

    with patch.object(tree, "itemAt", return_value=None):
        tree.dropEvent(event)

    on_external_drop.assert_not_called()


def test_drop_event_still_handles_internal_move_when_no_urls(app):
    on_items_dropped = MagicMock()
    tree = dialogs_module._DragMoveTreeWidget(on_items_dropped, MagicMock(), MagicMock())
    target_item = _make_kind_item("dir")
    tree.addTopLevelItem(target_item)
    dragged_item = _make_kind_item("file")
    tree.addTopLevelItem(dragged_item)
    dragged_item.setSelected(True)

    mime = MagicMock()
    mime.hasUrls.return_value = False
    event = MagicMock()
    event.mimeData.return_value = mime

    with patch.object(tree, "itemAt", return_value=target_item):
        tree.dropEvent(event)

    event.ignore.assert_called_once()
    on_items_dropped.assert_called_once_with([dragged_item], target_item)


def test_on_drag_out_uses_cache_without_downloading(app, tmp_path):
    dlg = _make_browser_dialog(app)
    cached_file = tmp_path / "report.docx"
    cached_file.write_text("x")
    remote_path = "/отчеты/Show/e01/report.docx"
    item = dlg._make_item(
        {"name": "report.docx", "type": "file", "path": remote_path, "modified": "2026-01-01"}, remote_path,
    )
    dlg._cache[remote_path] = (cached_file, "2026-01-01")

    with patch.object(dlg.client, "download_to_file") as download_mock:
        urls = dlg._on_drag_out([item])

    download_mock.assert_not_called()
    assert urls == [QUrl.fromLocalFile(str(cached_file))]
    dlg.close()


def test_on_drag_out_downloads_when_not_cached(app):
    dlg = _make_browser_dialog(app)
    remote_path = "/отчеты/Show/e01/report.docx"
    item = dlg._make_item(
        {"name": "report.docx", "type": "file", "path": remote_path, "modified": "2026-01-01"}, remote_path,
    )

    with patch.object(dlg.client, "download_to_file") as download_mock:
        urls = dlg._on_drag_out([item])

    download_mock.assert_called_once()
    assert len(urls) == 1
    assert remote_path in dlg._cache
    dlg.close()


def test_on_drag_out_skips_file_on_download_error(app):
    dlg = _make_browser_dialog(app)
    remote_path = "/отчеты/Show/e01/broken.docx"
    item = dlg._make_item(
        {"name": "broken.docx", "type": "file", "path": remote_path, "modified": "2026-01-01"}, remote_path,
    )

    with patch.object(dlg.client, "download_to_file", side_effect=RuntimeError("boom")):
        urls = dlg._on_drag_out([item])

    assert urls == []
    dlg.close()


def test_on_external_drop_ignores_non_dir_target(app, tmp_path):
    dlg = _make_browser_dialog(app)
    file_item = dlg._make_item(
        {"name": "report.docx", "type": "file", "path": "/отчеты/Show/report.docx"}, "/отчеты/Show/report.docx",
    )

    with patch.object(dialogs_module, "FinderDropUploadThread") as thread_cls:
        dlg._on_external_drop([tmp_path / "x.txt"], file_item)

    thread_cls.assert_not_called()
    dlg.close()


def test_on_external_drop_starts_upload_thread_on_confirmation(app, tmp_path):
    dlg = _make_browser_dialog(app)
    dir_item = dlg._make_item({"name": "e01", "type": "dir", "path": "/отчеты/Show/e01"}, "/отчеты/Show/e01")
    dropped_file = tmp_path / "extra.wav"
    dropped_file.write_text("x")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
         patch.object(dialogs_module, "FinderDropUploadThread") as thread_cls:
        fake_thread = MagicMock()
        thread_cls.return_value = fake_thread
        dlg._on_external_drop([dropped_file], dir_item)

    thread_cls.assert_called_once_with(dlg.token, [dropped_file], "/отчеты/Show/e01")
    fake_thread.start.assert_called_once()
    assert dlg.tree.isEnabled() is False
    dlg.close()


def test_on_external_drop_cancelled_starts_nothing(app, tmp_path):
    dlg = _make_browser_dialog(app)
    dir_item = dlg._make_item({"name": "e01", "type": "dir", "path": "/отчеты/Show/e01"}, "/отчеты/Show/e01")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
         patch.object(dialogs_module, "FinderDropUploadThread") as thread_cls:
        dlg._on_external_drop([tmp_path / "x.txt"], dir_item)

    thread_cls.assert_not_called()
    dlg.close()


def test_on_external_upload_finished_refreshes_already_expanded_target(app):
    dlg = _make_browser_dialog(app)
    dir_item = dlg._make_item({"name": "e01", "type": "dir", "path": "/отчеты/Show/e01"}, "/отчеты/Show/e01")
    dlg._root_items[0].addChild(dir_item)
    # Симулируем "уже загруженную" папку добавлением реального ребёнка —
    # без setExpanded(True), которое иначе синхронно триггерит настоящий
    # itemExpanded → _on_item_expanded → реальный _ListFolderThread ещё
    # до вызова проверяемого метода (childCount() > 0 этого и так достаточно).
    existing_child = dlg._make_item(
        {"name": "old.docx", "type": "file", "path": "/отчеты/Show/e01/old.docx"}, "/отчеты/Show/e01/old.docx",
    )
    dir_item.addChild(existing_child)
    dlg.tree.setEnabled(False)

    with patch.object(dialogs_module, "_ListFolderThread") as list_thread_cls:
        fake_thread = MagicMock()
        list_thread_cls.return_value = fake_thread
        dlg._on_external_upload_finished(True, "", dir_item)

    assert dlg.tree.isEnabled() is True
    list_thread_cls.assert_called_once()
    dlg.close()


def test_on_external_upload_finished_leaves_unloaded_target_alone(app):
    dlg = _make_browser_dialog(app)
    dir_item = dlg._make_item({"name": "e02", "type": "dir", "path": "/отчеты/Show/e02"}, "/отчеты/Show/e02")
    dlg._root_items[0].addChild(dir_item)
    dlg.tree.setEnabled(False)

    with patch.object(dialogs_module, "_ListFolderThread") as list_thread_cls:
        dlg._on_external_upload_finished(True, "", dir_item)

    list_thread_cls.assert_not_called()
    dlg.close()


def test_on_external_upload_finished_shows_error_on_failure(app):
    dlg = _make_browser_dialog(app)
    dir_item = dlg._make_item({"name": "e03", "type": "dir", "path": "/отчеты/Show/e03"}, "/отчеты/Show/e03")
    dlg.tree.setEnabled(False)

    with patch.object(QMessageBox, "critical") as critical_mock:
        dlg._on_external_upload_finished(False, "boom", dir_item)

    critical_mock.assert_called_once()
    assert dlg.tree.isEnabled() is True
    dlg.close()


# ---------------------------------------------------------------------------
# Теги (цвет + комментарий, хранятся в custom_properties на самом ресурсе)


def test_tag_edit_dialog_starts_with_no_color_selected_by_default(app):
    dialog = TagEditDialog(None)
    assert dialog.selected_color is None
    assert dialog.comment_edit.text() == ""


def test_tag_edit_dialog_preselects_current_tag(app):
    dialog = TagEditDialog(None, current_color="#FF9500", current_comment="срочно")
    assert dialog.selected_color == "#FF9500"
    assert dialog.comment_edit.text() == "срочно"
    assert dialog._swatch_buttons["#FF9500"].isChecked()
    assert not dialog._swatch_buttons["#34C759"].isChecked()


def test_tag_edit_dialog_select_color_updates_selection(app):
    dialog = TagEditDialog(None)
    dialog._select_color("#5856D6")
    assert dialog.selected_color == "#5856D6"
    assert dialog._swatch_buttons["#5856D6"].isChecked()


def test_tag_edit_dialog_remove_clears_selection_and_accepts(app):
    dialog = TagEditDialog(None, current_color="#FF3B30", current_comment="старый тег")
    dialog._on_remove()
    assert dialog.selected_color is None
    assert dialog.comment_edit.text() == ""
    assert dialog.result() == QDialog.Accepted


def test_make_item_reads_tag_from_custom_properties(app):
    dlg = _make_browser_dialog(app)
    entry = {
        "name": "e01", "type": "dir", "path": "/x/e01",
        "custom_properties": {"beast_tag_color": "#FF9500", "beast_tag_comment": "проверить звук"},
    }
    item = dlg._make_item(entry, "/x/e01")

    assert item.data(0, Qt.UserRole + 4) == "#FF9500"
    assert item.data(0, Qt.UserRole + 5) == "проверить звук"
    assert "Тег: проверить звук" in item.toolTip(0)


def test_make_item_without_tag_leaves_tag_roles_empty(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e02", "type": "dir", "path": "/x/e02"}, "/x/e02")

    assert item.data(0, Qt.UserRole + 4) is None
    assert "Тег:" not in item.toolTip(0)


def test_edit_tag_for_selected_starts_thread_with_new_tag(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e03", "type": "dir", "path": "/x/e03"}, "/x/e03")
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    fake_dialog = MagicMock()
    fake_dialog.exec_.return_value = QDialog.Accepted
    fake_dialog.selected_color = "#007AFF"
    fake_dialog.comment_edit.text.return_value = "  готово к отправке  "

    with patch.object(dialogs_module, "TagEditDialog", return_value=fake_dialog), \
         patch.object(dialogs_module, "_SetTagThread") as thread_cls:
        fake_thread = MagicMock()
        thread_cls.return_value = fake_thread
        dlg._edit_tag_for_selected()

    thread_cls.assert_called_once_with(
        dlg.client, "/x/e03",
        {"beast_tag_color": "#007AFF", "beast_tag_comment": "готово к отправке"},
    )
    fake_thread.start.assert_called_once()
    dlg.close()


def test_edit_tag_for_selected_skips_network_call_when_nothing_changed(app):
    dlg = _make_browser_dialog(app)
    entry = {
        "name": "e04", "type": "dir", "path": "/x/e04",
        "custom_properties": {"beast_tag_color": "#FF3B30", "beast_tag_comment": "было"},
    }
    item = dlg._make_item(entry, "/x/e04")
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    fake_dialog = MagicMock()
    fake_dialog.exec_.return_value = QDialog.Accepted
    fake_dialog.selected_color = "#FF3B30"
    fake_dialog.comment_edit.text.return_value = "было"

    with patch.object(dialogs_module, "TagEditDialog", return_value=fake_dialog), \
         patch.object(dialogs_module, "_SetTagThread") as thread_cls:
        dlg._edit_tag_for_selected()

    thread_cls.assert_not_called()
    dlg.close()


def test_edit_tag_for_selected_cancelled_starts_nothing(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e05", "type": "dir", "path": "/x/e05"}, "/x/e05")
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    fake_dialog = MagicMock()
    fake_dialog.exec_.return_value = QDialog.Rejected

    with patch.object(dialogs_module, "TagEditDialog", return_value=fake_dialog), \
         patch.object(dialogs_module, "_SetTagThread") as thread_cls:
        dlg._edit_tag_for_selected()

    thread_cls.assert_not_called()
    dlg.close()


def test_on_tag_set_finished_updates_item_on_success(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e06", "type": "dir", "path": "/x/e06"}, "/x/e06")

    dlg._on_tag_set_finished(item, True, "", "#34C759", "готово")

    assert item.data(0, Qt.UserRole + 4) == "#34C759"
    assert item.data(0, Qt.UserRole + 5) == "готово"
    assert "Тег: готово" in item.toolTip(0)
    dlg.close()


def test_on_tag_set_finished_shows_error_on_failure(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e07", "type": "dir", "path": "/x/e07"}, "/x/e07")

    with patch.object(QMessageBox, "critical") as critical_mock:
        dlg._on_tag_set_finished(item, False, "boom", None, "")

    critical_mock.assert_called_once()
    dlg.close()


def test_context_menu_offers_tag_action_for_non_root_item(app):
    dlg = _make_browser_dialog(app)
    item = dlg._make_item({"name": "e08", "type": "dir", "path": "/x/e08"}, "/x/e08")
    dlg._root_items[0].addChild(item)
    dlg.tree.setCurrentItem(item)

    menu_holder = {}

    def _capture_menu(self_menu, *_a, **_kw):
        menu_holder["menu"] = self_menu

    with patch.object(dlg.tree, "itemAt", return_value=item), \
         patch.object(QMenu, "exec_", _capture_menu):
        dlg._show_context_menu(QPoint(0, 0))

    action_texts = [a.text() for a in menu_holder["menu"].actions()]
    assert "Тег…" in action_texts
    dlg.close()
