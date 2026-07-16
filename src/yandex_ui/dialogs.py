"""Диалоги UI-слоя интеграции с Яндекс.Диском."""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from src.icons import make_icon
from src.yandex_ui.edit_sync import YandexEditSyncController
from src.yandex_ui.helpers import (
    _format_disk_file_size, _format_disk_modified_date,
    _quick_look_preview, _stop_thread,
)
from src.yandex_ui.threads import (
    CURRENT_DRAFT, _DeleteThread, _ListFolderThread, _MkdirThread, _RenameThread,
    YandexDiskDownloadThread,
)


class YandexUploadDiffDialog(QDialog):
    """Краткая сводка сравнения (маркеры/блокеры/новые маркеры/параметры)

    с выбранной предыдущей версией того же эпизода. В режиме upload_mode=True
    внизу кнопки «Отправить»/«Отмена» (используется перед загрузкой на Диск),
    иначе — одна кнопка «Закрыть» (просто просмотр сравнения), плюс, если
    allow_pick_another=True, кнопка «Выбрать другую версию» (возвращает
    result_code == PICK_ANOTHER из exec_()).
    """

    PICK_ANOTHER = 2

    def __init__(
        self, comparison, parent=None, upload_mode: bool = True,
        old_label: str = None, new_label: str = None, allow_pick_another: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Сравнение с предыдущим отчётом")
        self.setModal(True)
        self.resize(420, 420)
        self.setMinimumWidth(380)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        title = QLabel("Сравнение версий")
        title.setFont(QFont(".AppleSystemUIFont", 13, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        title.setWordWrap(True)
        layout.addWidget(title)

        if old_label and new_label:
            layout.addWidget(self._comparison_subtitle(old_label, new_label))

        card = QWidget()
        card.setStyleSheet("""
            QWidget#metricsCard {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 10px;
            }
        """)
        card.setObjectName("metricsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)
        card_layout.addWidget(self._metric_row("Маркеров", comparison.marker_count_old, comparison.marker_count_new))
        card_layout.addWidget(self._metric_row(
            "из них новых", comparison.new_marker_count_old, comparison.new_marker_count_new, indent=True
        ))
        card_layout.addWidget(self._metric_row(
            "Блокеров", comparison.blocker_count_old, comparison.blocker_count_new
        ))
        layout.addWidget(card)

        # Маркеры (diff по таймкодам) и параметры — в одном скролле,
        # чтобы длинные списки не спорили друг с другом за высоту диалога.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        # TODO: Включить вывод marker_diff когда функция сравнения будет полностью готова.
        # marker_diff = getattr(comparison, "marker_diff", None) or {}
        # added = marker_diff.get("added", [])
        # removed = marker_diff.get("removed", [])
        # changed = marker_diff.get("changed", [])
        # if added or removed or changed:
        #     content_layout.addWidget(self._section_title("Маркеры"))
        #     if added:
        #         content_layout.addWidget(self._marker_group_card(
        #             f"Добавлены ({len(added)})", added, bg="#EDFAF0", border="#C9EED3"))
        #     if removed:
        #         content_layout.addWidget(self._marker_group_card(
        #             f"Удалены ({len(removed)})", removed, bg="#FFF1F0", border="#FFD4D1", strike=True))
        #     if changed:
        #         content_layout.addWidget(self._marker_changed_card(changed))

        content_layout.addWidget(self._section_title("Параметры"))
        if comparison.parameter_changes:
            for change in comparison.parameter_changes:
                content_layout.addWidget(self._parameter_card(change))
        else:
            no_changes = QLabel("Без изменений.")
            no_changes.setFont(QFont(".AppleSystemUIFont", 12))
            no_changes.setStyleSheet("color: #86868B;")
            content_layout.addWidget(no_changes)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)

        if allow_pick_another:
            pick_another_btn = QPushButton("Выбрать другую версию")
            pick_another_btn.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF;
                    color: #007AFF;
                    border: 1px solid #D2D2D7;
                    border-radius: 8px;
                    padding: 6px 12px;
                    font-family: ".AppleSystemUIFont";
                    font-size: 12px;
                }
                QPushButton:hover { background: #F5F5F7; }
            """)
            pick_another_btn.clicked.connect(lambda: self.done(self.PICK_ANOTHER))
            buttons_row.addWidget(pick_another_btn)

        buttons_row.addStretch()

        if upload_mode:
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Отправить")
            buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Ok)
            buttons.button(QDialogButtonBox.Ok).setText("Закрыть")
            buttons.accepted.connect(self.accept)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

    @staticmethod
    def _elided_label(text: str, prefix: str) -> QLabel:
        label = QLabel()
        label.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
        label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        label.setToolTip(text)
        metrics = label.fontMetrics()
        # Ширину под многоточие берём с запасом на длину диалога (см. resize(420, ...)),
        # реальный перенос всё равно не нужен — это однострочная подпись.
        available_width = 420 - 18 * 2 - 10 * 2 - metrics.horizontalAdvance(prefix)
        elided = metrics.elidedText(text, Qt.ElideRight, max(available_width, 80))
        label.setText(prefix + elided)
        return label

    @classmethod
    def _comparison_subtitle(cls, old_label: str, new_label: str) -> QWidget:
        """Бейдж «что с чем сравниваем» — чтобы не приходилось гадать,

        к какой именно версии относятся цифры ниже. Наведение показывает
        полный текст, если он не поместился и был обрезан многоточием.
        """
        badge = QWidget()
        badge.setStyleSheet("""
            QWidget {
                background: #FFF6E5;
                border: 1px solid #FFE2A8;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(badge)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        layout.addWidget(cls._elided_label(old_label, "Было: "))
        layout.addWidget(cls._elided_label(new_label, "Стало: "))

        return badge

    @staticmethod
    def _metric_row(label: str, old: int, new: int, indent: bool = False) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(18 if indent else 0, 0, 0, 0)
        row_layout.setSpacing(8)

        label_widget = QLabel(label)
        label_widget.setFont(QFont(".AppleSystemUIFont", 11 if indent else 12, QFont.Normal if indent else QFont.DemiBold))
        label_widget.setStyleSheet(f"color: {'#86868B' if indent else '#1D1D1F'};")
        row_layout.addWidget(label_widget)
        row_layout.addStretch()

        value_widget = QLabel(f"{old} → {new}")
        value_widget.setFont(QFont(".AppleSystemUIFont", 11 if indent else 12))
        value_widget.setStyleSheet("color: #1D1D1F;")
        row_layout.addWidget(value_widget)

        diff = new - old
        if diff != 0:
            delta_color = "#FF9500" if diff > 0 else "#34C759"
            delta_widget = QLabel(f"{'+' if diff > 0 else ''}{diff}")
            delta_widget.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
            delta_widget.setStyleSheet(f"color: {delta_color}; min-width: 28px;")
            delta_widget.setAlignment(Qt.AlignRight)
            row_layout.addWidget(delta_widget)

        return row

    @staticmethod
    def _section_title(text: str) -> QLabel:
        title = QLabel(text)
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        return title

    @staticmethod
    def _marker_group_card(title: str, markers: list, *, bg: str, border: str, strike: bool = False) -> QWidget:
        """Карточка «Добавлены (N)»/«Удалены (N)»: строка на маркер —

        таймкод + описание (+ пометка «блокер»). strike=True перечёркивает
        текст (удалённые маркеры).
        """
        card = QWidget()
        card.setStyleSheet(f"""
            QWidget {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QLabel(title)
        header.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
        header.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        layout.addWidget(header)

        for marker in markers:
            description = marker.get("description") or "—"
            blocker_suffix = "  ⛔ блокер" if marker.get("blocker") else ""
            text = f'{marker.get("tc_in", "")}  {description}{blocker_suffix}'
            row = QLabel(f"<s>{html.escape(text)}</s>" if strike else html.escape(text))
            row.setTextFormat(Qt.RichText)
            row.setFont(QFont(".AppleSystemUIFont", 11))
            row.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
            row.setWordWrap(True)
            layout.addWidget(row)

        return card

    @classmethod
    def _marker_changed_card(cls, changed: list) -> QWidget:
        """Карточка «Изменены (N)»: таймкод + построчно «поле: было → стало»."""
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background: #F0F6FF;
                border: 1px solid #D6E6FF;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QLabel(f"Изменены ({len(changed)})")
        header.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
        header.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        layout.addWidget(header)

        for entry in changed:
            tc_label = QLabel(entry.get("tc_in", ""))
            tc_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
            tc_label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
            layout.addWidget(tc_label)
            for item in entry.get("changes", []):
                row = QLabel(
                    f'<span style="color:#86868B;">{html.escape(item["field"])}:</span> '
                    f'<span style="color:#B3B3BA;">{html.escape(item["old"])}</span>'
                    f'&nbsp;→&nbsp;'
                    f'<b style="color:#007AFF;">{html.escape(item["new"])}</b>'
                )
                row.setTextFormat(Qt.RichText)
                row.setFont(QFont(".AppleSystemUIFont", 11))
                row.setStyleSheet("background: transparent; border: none; margin-left: 10px;")
                row.setWordWrap(True)
                layout.addWidget(row)

        return card

    @staticmethod
    def _parameter_card(change: dict) -> QWidget:
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background: #F0F6FF;
                border: 1px solid #D6E6FF;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        label = QLabel(change["label"])
        label.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
        label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        layout.addWidget(label)

        for item in change["changes"]:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            field_label = QLabel(f'{item["field"]}:')
            field_label.setFont(QFont(".AppleSystemUIFont", 11))
            field_label.setStyleSheet("color: #86868B; background: transparent; border: none;")
            row_layout.addWidget(field_label)

            # Старое значение приглушено, новое — жирным; если в самом
            # отчёте новое значение помечено как несоответствие норме
            # (та же заливка ячейки, что в MARKER LIST/тех. таблице),
            # красим его красным (bad) или оранжевым (warn) вместо синего.
            new_value_color = {"bad": "#FF3B30", "warn": "#FF9500"}.get(item.get("status"), "#007AFF")
            value_label = QLabel()
            value_label.setTextFormat(Qt.RichText)
            value_label.setText(
                f'<span style="color:#B3B3BA;">{item["old"]}</span>'
                f'&nbsp;→&nbsp;'
                f'<b style="color:{new_value_color};">{item["new"]}</b>'
            )
            value_label.setFont(QFont(".AppleSystemUIFont", 11))
            value_label.setStyleSheet("background: transparent; border: none;")
            value_label.setWordWrap(True)
            row_layout.addWidget(value_label, 1)

            layout.addWidget(row)

        return card


class YandexVersionPickerDialog(QDialog):
    """Выбор двух версий отчёта для сравнения между собой (например, первой

    с четвёртой) — не только с текущим черновиком. По умолчанию: «Было» —
    самая свежая версия на Диске, «Стало» — текущий черновик. Оба поля можно
    поменять на любую другую версию; при большом количестве версий можно
    начать печатать дату или часть названия, чтобы отфильтровать список.
    """

    CURRENT_DRAFT = CURRENT_DRAFT  # см. src/yandex_ui/threads.py — единый источник значения

    def __init__(self, versions: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор версий для сравнения")
        self.setModal(True)
        self.resize(460, 220)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")
        self.selection_old = None
        self.selection_new = None
        self.selection_old_label = None
        self.selection_new_label = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Какие версии сравнить?")
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(title)

        combo_style = """
            QComboBox {
                background: #F5F5F7;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 6px 10px;
                color: #1D1D1F;
            }
            QComboBox:focus { border: 1px solid #007AFF; }
        """

        # «Было» — версии на Диске (без текущего черновика: с ним нечего
        # сравнивать в этой роли, он и так всегда «новее» любой залитой версии).
        entries_old = []
        for version in reversed(versions):  # новые сверху
            date_text = version["date"].strftime("%d.%m.%Y") if version["date"] else ""
            label = f"{date_text}  {version['label']}" if date_text else version["label"]
            entries_old.append((label, version["path"]))

        # «Стало» — текущий черновик (по умолчанию) плюс те же версии на
        # Диске, чтобы можно было сравнить и две старые версии между собой.
        entries_new = [("Текущий черновик (ещё не отправлен)", self.CURRENT_DRAFT)] + entries_old

        old_label = QLabel("Было")
        old_label.setFont(QFont(".AppleSystemUIFont", 11))
        old_label.setStyleSheet("color: #86868B;")
        layout.addWidget(old_label)
        self.combo_old = self._make_searchable_combo(entries_old, combo_style)
        layout.addWidget(self.combo_old)

        new_label = QLabel("Стало")
        new_label.setFont(QFont(".AppleSystemUIFont", 11))
        new_label.setStyleSheet("color: #86868B;")
        layout.addWidget(new_label)
        self.combo_new = self._make_searchable_combo(entries_new, combo_style)
        layout.addWidget(self.combo_new)

        if self.combo_old.count() > 0:
            self.combo_old.setCurrentIndex(0)  # самая свежая версия на Диске
        self.combo_new.setCurrentIndex(0)  # текущий черновик

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Сравнить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_searchable_combo(entries: list, style: str) -> QComboBox:
        combo = QComboBox()
        combo.setStyleSheet(style)
        combo.setFont(QFont(".AppleSystemUIFont", 12))
        for label, data in entries:
            combo.addItem(label, data)
        if len(entries) > 6:
            # При большом списке версий даём печатать для фильтрации.
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.NoInsert)
            completer = QCompleter([label for label, _ in entries], combo)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCompletionMode(QCompleter.PopupCompletion)
            combo.setCompleter(completer)
        return combo

    def _on_accept(self):
        if self.combo_old.count() == 0:
            self.reject()
            return
        old_path = self.combo_old.currentData()
        new_path = self.combo_new.currentData()
        if old_path is None or new_path is None:
            # Пользователь стёр текст в редактируемом combo, не выбрав пункт.
            QMessageBox.warning(self, "Не выбрано", "Выберите обе версии из списка.")
            return
        if old_path == new_path:
            QMessageBox.warning(self, "Одинаковые версии", "Выберите две разные версии для сравнения.")
            return
        self.selection_old = old_path
        self.selection_new = new_path
        self.selection_old_label = self.combo_old.currentText()
        self.selection_new_label = self.combo_new.currentText()
        self.accept()


class YandexFolderPickerDialog(QDialog):
    """Дерево папок Диска для ручного выбора/создания папки серии.

    Используется, когда имя файла отчёта не удалось разобрать автоматически
    (нет distinguishable season/episode-меток) или папка сериала не нашлась
    по имени — пользователь сам находит нужную папку по названию серии
    среди уже загруженных, либо создаёт новую. Если имя файла распознано,
    выбранная папка трактуется как папка *серии* — папка эпизода (eNN)
    находится или создаётся внутри неё автоматически вызывающим кодом
    (см. resolve_manual_pick_target в src/report_uploader.py); либо можно
    сразу выбрать уже существующую папку конкретного эпизода — это тоже
    распознаётся корректно, без задваивания eNN/eNN.
    """

    def __init__(
        self, client, roots: list = None, parent=None, *,
        window_title: str = "Выбор папки на Яндекс.Диске",
        prompt_text: str = "Выберите папку сериала/фильма или создайте новую",
        hint_text: str = (
            "Для сериала: внутри выбранной папки эпизод (например, e02) будет\n"
            "найден или создан автоматически — можно также сразу выбрать готовую\n"
            "папку эпизода. Для фильма выбранная папка используется как есть."
        ),
    ):
        super().__init__(parent)
        self.client = client
        self.roots = list(roots) if roots else ["/отчеты"]
        self.selected_path = None
        self._root_items = []
        self._expand_threads = {}  # path -> _ListFolderThread
        self._new_folder_thread = None

        self.setWindowTitle(window_title)
        self.setModal(True)
        self.resize(420, 480)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(prompt_text)
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(title)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet("""
            QTreeWidget {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
                color: #1D1D1F;
            }
        """)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        layout.addWidget(self.tree, 1)

        self.new_folder_btn = QPushButton("+ Новая папка")
        self.new_folder_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 5px 12px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """)
        self.new_folder_btn.clicked.connect(self._create_new_folder)
        layout.addWidget(self.new_folder_btn)

        hint = QLabel(hint_text)
        hint.setFont(QFont(".AppleSystemUIFont", 10))
        hint.setStyleSheet("color: #86868B;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Выбрать")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # Создание ПЕРВОЙ (основной) корневой папки — асинхронно, чтобы не
        # блокировать GUI-поток на медленной сети; дерево/кнопки недоступны
        # до готовности. Остальные корни (если есть) не создаются
        # автоматически — это, как правило, уже существующие папки
        # пользователя, добавленные вручную в настройках.
        self.tree.setEnabled(False)
        self.new_folder_btn.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._root_mkdir_thread = _MkdirThread(self.client, self.roots[0])
        self._root_mkdir_thread.finished_mkdir.connect(self._on_root_ready)
        self._root_mkdir_thread.start()

    def _on_root_ready(self, success: bool, message: str) -> None:
        self.tree.setEnabled(True)
        self.new_folder_btn.setEnabled(True)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        self._populate_root()

    def _make_item(self, name: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name])
        item.setData(0, Qt.UserRole, path)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return item

    def _populate_root(self):
        # Каждый настроенный корень — отдельная ветка верхнего уровня,
        # лениво разворачиваемая тем же _on_item_expanded, что и обычная
        # папка (никакого отдельного сетевого запроса здесь не требуется).
        for root in self.roots:
            item = self._make_item(root, root)
            self.tree.addTopLevelItem(item)
            self._root_items.append(item)

    def _make_loading_placeholder(self) -> QTreeWidgetItem:
        placeholder = QTreeWidgetItem(["Загрузка…"])
        placeholder.setFlags(Qt.NoItemFlags)  # не выбирается — _on_accept не примет
        return placeholder

    def _on_item_expanded(self, item: QTreeWidgetItem):
        # Листинг — в фоне (см. _ListFolderThread): раньше сетевой запрос шёл
        # синхронно прямо здесь и на медленной сети замораживал GUI-поток.
        if item.childCount() > 0:
            return
        path = item.data(0, Qt.UserRole)
        if path in self._expand_threads:
            return
        item.addChild(self._make_loading_placeholder())
        thread = _ListFolderThread(self.client, path)
        thread.resolved.connect(
            lambda rpath, children, it=item: self._on_children_loaded(it, rpath, children)
        )
        thread.failed.connect(
            lambda rpath, message, it=item: self._on_children_load_failed(it, rpath, message)
        )
        self._expand_threads[path] = thread
        thread.start()

    def _on_children_loaded(self, item: QTreeWidgetItem, path: str, children: list):
        self._expand_threads.pop(path, None)
        try:
            item.takeChildren()  # убираем placeholder «Загрузка…»
            for entry in children:
                if entry.get("type") != "dir":
                    continue
                name = entry.get("name", "")
                child_path = entry.get("path") or f"{path}/{name}"
                item.addChild(self._make_item(name, child_path))
        except RuntimeError:
            # Qt-объект элемента уже уничтожен (диалог закрывается) —
            # результат просто некому показывать.
            pass

    def _on_children_load_failed(self, item: QTreeWidgetItem, path: str, message: str):
        self._expand_threads.pop(path, None)
        try:
            item.takeChildren()
            item.setExpanded(False)  # свернуть, чтобы повторная попытка перезапустила листинг
        except RuntimeError:
            return
        QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _create_new_folder(self):
        selected = self.tree.currentItem() or self._root_items[0]
        parent_path = selected.data(0, Qt.UserRole)

        name, ok = QInputDialog.getText(self, "Новая папка", "Название папки:")
        name = name.strip()
        if not ok or not name:
            return
        if "/" in name:
            QMessageBox.warning(self, "Неверное имя", "Имя не может содержать «/».")
            return

        # mkdir — в фоне: раньше выполнялся синхронно и замораживал GUI-поток.
        new_path = f"{parent_path}/{name}"
        self.new_folder_btn.setEnabled(False)
        self._new_folder_thread = _MkdirThread(self.client, new_path)
        self._new_folder_thread.finished_mkdir.connect(
            lambda success, message, parent_item=selected, folder_name=name:
                self._on_new_folder_created(parent_item, folder_name, success, message)
        )
        self._new_folder_thread.start()

    def _on_new_folder_created(self, parent_item: QTreeWidgetItem, name: str, success: bool, message: str):
        self.new_folder_btn.setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        try:
            new_item = self._make_item(name, message)
            parent_item.addChild(new_item)
            parent_item.setExpanded(True)
            self.tree.setCurrentItem(new_item)
        except RuntimeError:
            pass

    def _on_accept(self):
        selected = self.tree.currentItem()
        if not selected or selected in self._root_items:
            QMessageBox.warning(self, "Не выбрано", "Выберите папку или создайте новую.")
            return
        self.selected_path = selected.data(0, Qt.UserRole)
        self.accept()

    def done(self, r):
        # accept()/reject() (кнопки/крестик — все пути закрытия идут через
        # done()) — останавливаем фоновые потоки (mkdir корня/новой папки,
        # листинги разворачиваемых папок), иначе при закрытии диалога до их
        # завершения PyQt валит процесс
        # "QThread: Destroyed while thread is still running".
        _stop_thread(getattr(self, "_root_mkdir_thread", None))
        _stop_thread(getattr(self, "_new_folder_thread", None))
        for thread in list(getattr(self, "_expand_threads", {}).values()):
            _stop_thread(thread)
        super().done(r)


class YandexDiskBrowserDialog(QDialog):
    """Просмотр содержимого папки «отчеты» на Яндекс.Диске: папки и файлы,

    размер и дата изменения, скачивание/открытие файла, переименование и
    удаление (в Корзину) файлов и папок — это не пикер, выбор ничего не
    возвращает вызывающей стороне.
    """

    def __init__(self, token: str, roots: list = None, parent=None):
        super().__init__(parent)
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError

        self.token = token
        self.roots = list(roots) if roots else ["/отчеты"]
        self._root_items = []
        self._cache = {}  # remote_path -> (local_path: Path, modified: str)
        self._inflight = {}  # remote_path -> YandexDiskDownloadThread
        self._pending = {}  # remote_path -> [(callback, silent), ...]
        self._rename_thread = None
        self._delete_thread = None
        self._mkdir_thread = None
        self._expand_threads = {}  # path -> _ListFolderThread
        self._closing = False
        self._editing_remote_by_local = {}  # local_path (str) -> remote_path, для «Открыть» + автосинк правок
        self._edit_sync = YandexEditSyncController(
            get_token=lambda: self.token,
            resolve_remote_path=lambda path: self._editing_remote_by_local.get(path),
            parent=self,
        )
        self._edit_sync.status_changed.connect(self._on_edit_sync_status_changed)
        self._edit_sync.conflict.connect(self._on_edit_sync_conflict)

        self.setWindowTitle("Файлы на Яндекс.Диске")
        self.setModal(True)
        self.resize(520, 480)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Файлы на Яндекс.Диске")
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(title)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по названию…")
        self.search_edit.setFont(QFont(".AppleSystemUIFont", 12))
        self.search_edit.setStyleSheet("""
            QLineEdit {
                background: #F5F5F7;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 6px 10px;
                color: #1D1D1F;
            }
            QLineEdit:focus { border: 1px solid #007AFF; }
        """)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_edit)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Имя", "Размер", "Дата изменения"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 70)
        self.tree.setStyleSheet("""
            QTreeWidget {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
                color: #1D1D1F;
            }
        """)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.currentItemChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.installEventFilter(self)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)

        self.edit_sync_status_label = QLabel("")
        self.edit_sync_status_label.setVisible(False)
        self.edit_sync_status_label.setFont(QFont(".AppleSystemUIFont", 10))
        self.edit_sync_status_label.setStyleSheet("color: #86868B; background: transparent;")
        self.edit_sync_status_label.setWordWrap(True)
        layout.addWidget(self.edit_sync_status_label)

        footer_row = QHBoxLayout()
        footer_row.setSpacing(8)
        aliases_btn = QPushButton("Алиасы серий")
        aliases_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """)
        aliases_btn.clicked.connect(self._open_series_aliases_dialog)
        footer_row.addWidget(aliases_btn)
        footer_row.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        footer_row.addWidget(buttons)
        layout.addLayout(footer_row)

        try:
            self.client = YandexDiskClient(token)
        except YandexDiskError as exc:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", str(exc))
            self.client = None
            return

        self._populate_root()

    def _icon_for(self, name: str, is_dir: bool) -> QIcon:
        if is_dir:
            return make_icon("folder", "#86868B", 13)
        suffix = Path(name).suffix.lower()
        icon_name = {".pdf": "pdf", ".csv": "csv", ".docx": "doc", ".doc": "doc",
                     ".mp4": "video", ".mov": "video", ".wav": "music", ".mp3": "music"}.get(suffix, "doc")
        return make_icon(icon_name, "#86868B", 13)

    def _make_item(self, entry: dict, fallback_path: str) -> QTreeWidgetItem:
        name = entry.get("name", "")
        is_dir = entry.get("type") == "dir"
        path = entry.get("path") or fallback_path
        item = QTreeWidgetItem([
            name,
            "" if is_dir else _format_disk_file_size(entry.get("size")),
            _format_disk_modified_date(entry.get("modified", "")),
        ])
        item.setIcon(0, self._icon_for(name, is_dir))
        item.setData(0, Qt.UserRole, path)
        item.setData(0, Qt.UserRole + 1, "dir" if is_dir else "file")
        item.setData(0, Qt.UserRole + 2, entry.get("modified", ""))
        if is_dir:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return item

    def _populate_root(self):
        # Каждый настроенный корень — отдельная ветка верхнего уровня,
        # лениво разворачиваемая тем же _on_item_expanded, что и обычная
        # папка (никакого отдельного сетевого запроса здесь не требуется).
        for root in self.roots:
            item = self._make_item({"name": root, "type": "dir"}, root)
            self.tree.addTopLevelItem(item)
            self._root_items.append(item)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        # Листинг — в фоне (см. _ListFolderThread): раньше сетевой запрос шёл
        # синхронно прямо здесь и на медленной сети замораживал GUI-поток.
        if item.childCount() > 0 or item.data(0, Qt.UserRole + 1) != "dir":
            return
        path = item.data(0, Qt.UserRole)
        if path in self._expand_threads:
            return
        placeholder = QTreeWidgetItem(["Загрузка…", "", ""])
        placeholder.setFlags(Qt.NoItemFlags)  # не выбирается; UserRole+1 пуст —
        # обработчики файлов/папок (двойной клик, контекстное меню) его игнорируют
        item.addChild(placeholder)
        thread = _ListFolderThread(self.client, path)
        thread.resolved.connect(
            lambda rpath, children, it=item: self._on_children_loaded(it, rpath, children)
        )
        thread.failed.connect(
            lambda rpath, message, it=item: self._on_children_load_failed(it, rpath, message)
        )
        self._expand_threads[path] = thread
        thread.start()

    def _on_children_loaded(self, item: QTreeWidgetItem, path: str, children: list):
        self._expand_threads.pop(path, None)
        try:
            item.takeChildren()  # убираем placeholder «Загрузка…»
            for entry in children:
                name = entry.get("name", "")
                item.addChild(self._make_item(entry, f"{path}/{name}"))
        except RuntimeError:
            # Qt-объект элемента уже уничтожен (папку удалили/переименовали,
            # пока шёл листинг, или диалог закрывается) — некому показывать.
            return
        query = self.search_edit.text().strip().lower()
        if query:
            # Догруженные лениво потомки должны сразу попасть под текущий фильтр.
            self._filter_item(item, query)

    def _on_children_load_failed(self, item: QTreeWidgetItem, path: str, message: str):
        self._expand_threads.pop(path, None)
        try:
            item.takeChildren()
            item.setExpanded(False)  # свернуть, чтобы повторная попытка перезапустила листинг
        except RuntimeError:
            return
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _on_search_text_changed(self, text: str):
        query = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), query)

    def _filter_item(self, item: QTreeWidgetItem, query: str) -> bool:
        """Скрывает элементы, не подходящие под запрос; папку с подходящим

        потомком оставляет видимой и разворачивает (потомки, которые ещё
        не подгружены лениво, поиском не охватываются).
        """
        self_matches = not query or query in item.text(0).lower()
        child_matches = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), query):
                child_matches = True
        visible = self_matches or child_matches
        item.setHidden(not visible)
        if query and child_matches:
            item.setExpanded(True)
        return visible

    def _on_selection_changed(self, current: QTreeWidgetItem, _previous: QTreeWidgetItem):
        is_file = bool(current) and current.data(0, Qt.UserRole + 1) == "file"
        if is_file:
            # Предзагрузка в фоне сразу по выделению — к моменту, когда
            # пользователь нажмёт пробел/«Открыть», файл уже часто готов.
            remote_path, modified, name = self._selected_file_info(current)
            self._get_local_copy(remote_path, name, modified, callback=None, silent=True)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int):
        # Для папок двойной клик и так разворачивает/сворачивает узел
        # (стандартное поведение QTreeWidget) — здесь обрабатываем только файлы.
        if item.data(0, Qt.UserRole + 1) == "file":
            self._open_selected()

    def _selected_file(self):
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "file":
            return None, None
        return item.data(0, Qt.UserRole), item.text(0)

    def _selected_file_info(self, item: QTreeWidgetItem):
        return item.data(0, Qt.UserRole), item.data(0, Qt.UserRole + 2), item.text(0)

    def _cached_path(self, remote_path: str, modified: str) -> Path | None:
        cached = self._cache.get(remote_path)
        if cached is None:
            return None
        local_path, cached_modified = cached
        if cached_modified != modified or not local_path.exists():
            return None
        return local_path

    def _get_local_copy(self, remote_path: str, name: str, modified: str, callback=None, silent: bool = False):
        """Даёт локальную копию файла через callback(local_path) — из кэша,

        из уже идущей предзагрузки, либо запускает новую закачку. silent=True
        (тихая предзагрузка по выделению) не показывает ошибку, если сеть
        подвела — просто ничего не закэшируется, а явное действие (пробел/
        «Открыть»/«Сохранить») попробует скачать заново.
        """
        if self._closing:
            # Диалог закрывается — предзагрузка по выделению могла остаться
            # в очереди событий; новый поток скачивания уже никто не остановит.
            return
        cached = self._cached_path(remote_path, modified)
        if cached is not None:
            if callback:
                callback(cached)
            return

        self._pending.setdefault(remote_path, []).append((callback, silent))
        if remote_path in self._inflight:
            return  # уже качается — просто подождём вместе с остальными

        local_path = Path(tempfile.gettempdir()) / name
        thread = YandexDiskDownloadThread(self.token, remote_path, local_path)
        thread.finished_download.connect(
            lambda success, message: self._on_copy_ready(remote_path, modified, success, message)
        )
        thread.progress.connect(
            lambda received, total: self._on_download_progress(remote_path, received, total)
        )
        self._inflight[remote_path] = thread
        thread.start()

    def _on_download_progress(self, remote_path: str, received: int, total):
        waiters = self._pending.get(remote_path, [])
        if not any(not is_silent for _, is_silent in waiters):
            return  # только тихие предзагрузки — не отвлекаем пользователя процентами
        if total:
            percent = int(received * 100 / total)
            self.edit_sync_status_label.setText(f"Загрузка: {percent}%")
        else:
            self.edit_sync_status_label.setText(f"Загрузка: {_format_disk_file_size(received)}")
        self.edit_sync_status_label.setStyleSheet("color: #86868B; background: transparent;")
        self.edit_sync_status_label.setVisible(True)

    def _on_copy_ready(self, remote_path: str, modified: str, success: bool, message: str):
        self._inflight.pop(remote_path, None)
        waiters = self._pending.pop(remote_path, [])
        if not success:
            if any(not is_silent for _, is_silent in waiters):
                QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        local_path = Path(message)
        self._cache[remote_path] = (local_path, modified)
        for callback, _is_silent in waiters:
            if callback:
                callback(local_path)

    def _save_selected_as(self):
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "file":
            return
        remote_path, modified, name = self._selected_file_info(item)
        local_path_str, _ = QFileDialog.getSaveFileName(self, "Сохранить как", name)
        if not local_path_str:
            return

        def _save_copy(cached_path: Path):
            shutil.copy(cached_path, local_path_str)
            QMessageBox.information(self, "Готово", f"Файл сохранён:\n{local_path_str}")

        self._get_local_copy(remote_path, name, modified, callback=_save_copy)

    def _open_selected(self):
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "file":
            return
        remote_path, modified, name = self._selected_file_info(item)
        self._get_local_copy(
            remote_path, name, modified,
            callback=lambda p: self._open_and_watch(p, remote_path, modified),
        )

    def _open_and_watch(self, local_path: Path, remote_path: str, modified: str):
        """Открывает файл во внешнем редакторе и следит за ним — после

        сохранения правки автоматически уедут обратно на тот же remote_path.
        """
        subprocess.Popen(["open", str(local_path)])
        self._editing_remote_by_local[str(local_path)] = remote_path
        self._edit_sync.note_known_modified(remote_path, modified)
        self._edit_sync.watch(str(local_path))
        self.edit_sync_status_label.setText("Отслеживаем изменения — сохраните файл, чтобы обновить его на Диске")
        self.edit_sync_status_label.setStyleSheet("color: #86868B; background: transparent;")
        self.edit_sync_status_label.setVisible(True)

    def _on_edit_sync_status_changed(self, status: str):
        is_error = status.startswith("Не удалось") or status.startswith("Правки НЕ")
        is_warning = "повтор через" in status or status.startswith("Конфликт")
        color = "#FF3B30" if is_error else ("#FF9500" if is_warning else (
            "#34C759" if status.startswith("Обновлено") else "#86868B"
        ))
        self.edit_sync_status_label.setText(status)
        self.edit_sync_status_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.edit_sync_status_label.setVisible(True)

    def _on_edit_sync_conflict(self, path: str, actual_modified: str):
        choice = QMessageBox.question(
            self, "Конфликт версий",
            f"Файл на Яндекс.Диске был изменён после того, как вы его открыли "
            f"({_format_disk_modified_date(actual_modified)}).\n\n"
            "Перезаписать его вашей версией?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        self._edit_sync.resolve_conflict(path, overwrite=(choice == QMessageBox.Yes))

    def _quicklook_selected(self):
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "file":
            return
        remote_path, modified, name = self._selected_file_info(item)
        self._get_local_copy(remote_path, name, modified, callback=_quick_look_preview)

    def _rename_selected(self):
        item = self.tree.currentItem()
        if not item or item in self._root_items:
            # Переименование корневой ветки означало бы переименовать саму
            # настроенную папку на Диске — слишком рискованное действие для
            # случайного Enter, делаем это только через настройки.
            return
        remote_path = item.data(0, Qt.UserRole)
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if "/" in new_name:
            QMessageBox.warning(self, "Неверное имя", "Имя не может содержать «/».")
            return
        parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
        new_path = f"{parent}/{new_name}" if parent else new_name

        self.tree.setEnabled(False)
        self._rename_thread = _RenameThread(self.token, remote_path, new_path)
        self._rename_thread.finished_rename.connect(
            lambda success, message: self._on_rename_finished(item, new_name, success, message)
        )
        self._rename_thread.start()

    def _on_rename_finished(self, item: QTreeWidgetItem, new_name: str, success: bool, message: str):
        self.tree.setEnabled(True)
        if success:
            old_path = item.data(0, Qt.UserRole)
            item.setText(0, new_name)
            item.setData(0, Qt.UserRole, message)
            if item.data(0, Qt.UserRole + 1) == "dir" and item.childCount() > 0:
                # Пути уже загруженных дочерних элементов вычислены от старого
                # пути папки — проще перезагрузить их заново под новым путём,
                # чем пересчитывать префикс у каждого рекурсивно.
                item.takeChildren()
                if item.isExpanded():
                    self._on_item_expanded(item)
            self._cache.pop(old_path, None)
        else:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
        self._on_selection_changed(self.tree.currentItem(), None)

    def _delete_selected(self):
        item = self.tree.currentItem()
        if not item or item in self._root_items:
            # Удаление корневой ветки удалило бы саму настроенную папку на
            # Диске целиком — слишком рискованно для случайного Cmd+Delete.
            return
        remote_path = item.data(0, Qt.UserRole)
        name = item.text(0)
        is_dir = item.data(0, Qt.UserRole + 1) == "dir"
        kind = "папку" if is_dir else "файл"
        choice = QMessageBox.question(
            self, "Удалить",
            f"Удалить {kind} «{name}»?\nОн будет перемещён в Корзину на Яндекс.Диске.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        self.tree.setEnabled(False)
        self._delete_thread = _DeleteThread(self.token, remote_path)
        self._delete_thread.finished_delete.connect(
            lambda success, message: self._on_delete_finished(item, remote_path, success, message)
        )
        self._delete_thread.start()

    def _on_delete_finished(self, item: QTreeWidgetItem, remote_path: str, success: bool, message: str):
        self.tree.setEnabled(True)
        if success:
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
            else:
                index = self.tree.indexOfTopLevelItem(item)
                if index >= 0:
                    self.tree.takeTopLevelItem(index)
            self._cache.pop(remote_path, None)
        else:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
        self._on_selection_changed(self.tree.currentItem(), None)

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            self.tree.setCurrentItem(item)
            is_file = item.data(0, Qt.UserRole + 1) == "file"
            is_root = item in self._root_items
            if is_file:
                menu.addAction("Сохранить как…", self._save_selected_as)
                menu.addAction("Открыть", self._open_selected)
                menu.addSeparator()
            if not is_root:
                menu.addAction("Переименовать", self._rename_selected)
                menu.addAction("Удалить", self._delete_selected)
                menu.addSeparator()
        menu.addAction("Новая папка", self._create_new_folder)
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _create_new_folder(self):
        selected = self.tree.currentItem()
        if selected is not None and selected.data(0, Qt.UserRole + 1) == "file":
            parent_item = selected.parent()
        elif selected is not None:
            parent_item = selected
        else:
            parent_item = self._root_items[0]
        parent_path = parent_item.data(0, Qt.UserRole)

        name, ok = QInputDialog.getText(self, "Новая папка", "Название папки:", text="Новая папка")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if "/" in name:
            QMessageBox.warning(self, "Неверное имя", "Имя не может содержать «/».")
            return

        new_path = f"{parent_path}/{name}"
        self.tree.setEnabled(False)
        self._mkdir_thread = _MkdirThread(self.client, new_path)
        self._mkdir_thread.finished_mkdir.connect(
            lambda success, message: self._on_new_folder_created(parent_item, name, success, message)
        )
        self._mkdir_thread.start()

    def _on_new_folder_created(self, parent_item, name: str, success: bool, message: str):
        self.tree.setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        siblings = [parent_item.child(i) for i in range(parent_item.childCount())]
        existing = next((item for item in siblings if item.text(0) == name), None)
        if existing is not None:
            # mkdir идемпотентен — если папка с таким именем уже была на
            # Диске (и уже показана в дереве), просто выделяем её, а не
            # добавляем задвоенную запись.
            self.tree.setCurrentItem(existing)
            return
        new_item = self._make_item({"name": name, "type": "dir", "path": message, "modified": ""}, message)
        parent_item.addChild(new_item)
        parent_item.setExpanded(True)
        self.tree.setCurrentItem(new_item)

    def eventFilter(self, obj, event):
        if obj is self.tree and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                remote_path, _ = self._selected_file()
                if remote_path is not None:
                    self._quicklook_selected()
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self.tree.currentItem() is not None:
                    self._rename_selected()
                    return True
            if event.key() in (Qt.Key_Backspace, Qt.Key_Delete) and event.modifiers() & Qt.ControlModifier:
                # На macOS Qt меняет местами Cmd/Ctrl — ControlModifier здесь
                # соответствует физической клавише Cmd, как и «cmd+delete»
                # в Finder. Backspace — потому что на клавиатурах Mac обычно
                # нет отдельной клавиши Delete (она же Backspace).
                if self.tree.currentItem() is not None:
                    self._delete_selected()
                    return True
            if (event.key() == Qt.Key_N and event.modifiers() & Qt.ControlModifier
                    and event.modifiers() & Qt.ShiftModifier):
                self._create_new_folder()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        self._closing = True
        for thread in list(self._inflight.values()):
            _stop_thread(thread)
        for thread in list(self._expand_threads.values()):
            _stop_thread(thread)
        _stop_thread(self._rename_thread)
        _stop_thread(self._delete_thread)
        _stop_thread(self._mkdir_thread)
        self._edit_sync.stop_all()
        super().closeEvent(event)

    def _open_series_aliases_dialog(self):
        dialog = SeriesAliasesDialog(parent=self)
        dialog.exec_()


class YandexUploadQueueDialog(QDialog):
    """Список отчётов в очереди автозагрузки на Яндекс.Диск со статусами

    и ручными действиями для тех, что требуют внимания (папка не найдена
    или исчерпаны попытки повтора).
    """

    _STATUS_LABELS = {
        "queued": ("В очереди", "#86868B"),
        "uploading": ("Загружается…", "#007AFF"),
        "needs_folder": ("Нужна папка", "#FF9500"),
        "failed": ("Ошибка", "#FF3B30"),
        "done": ("Отправлено", "#34C759"),
    }

    def __init__(self, manager: "YandexUploadQueueManager", parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Очередь загрузок на Яндекс.Диск")
        self.setModal(True)
        self.resize(440, 360)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 16, 18, 16)
        self._layout.setSpacing(10)

        title = QLabel("Очередь загрузок")
        title.setFont(QFont(".AppleSystemUIFont", 13, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        self._layout.addWidget(title)

        self._list_container = QVBoxLayout()
        self._list_container.setSpacing(8)
        self._layout.addLayout(self._list_container)
        self._layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self._layout.addWidget(buttons)

        manager.queue_changed.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        while self._list_container.count():
            item = self._list_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        jobs = self.manager.queue.jobs
        if not jobs:
            empty_label = QLabel("Очередь пуста.")
            empty_label.setFont(QFont(".AppleSystemUIFont", 12))
            empty_label.setStyleSheet("color: #86868B;")
            self._list_container.addWidget(empty_label)
            return

        for job in jobs:
            self._list_container.addWidget(self._job_row(job))

    def _job_row(self, job) -> QWidget:
        row = QWidget()
        row.setStyleSheet("""
            QWidget {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
            }
        """)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        name_label = QLabel(job.local_folder.name)
        name_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
        name_label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        name_label.setWordWrap(True)
        text_col.addWidget(name_label)

        status_text, status_color = self._STATUS_LABELS.get(job.status, (job.status, "#86868B"))
        if job.status in ("failed", "needs_folder") and job.error:
            status_text = f"{status_text}: {job.error}"
        status_label = QLabel(status_text)
        status_label.setFont(QFont(".AppleSystemUIFont", 10))
        status_label.setStyleSheet(f"color: {status_color}; background: transparent; border: none;")
        status_label.setWordWrap(True)
        text_col.addWidget(status_label)

        layout.addLayout(text_col, 1)

        secondary_btn_style = """
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 5px 10px;
                font-family: ".AppleSystemUIFont";
                font-size: 11px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """

        if job.status == "needs_folder":
            # «Повторить» здесь почти всегда бесполезен — если имя файла не
            # распознано, автоопределение папки так и останется невозможным.
            # Даём выбрать папку вручную, это и решает проблему.
            pick_folder_btn = QPushButton("Выбрать папку")
            pick_folder_btn.setStyleSheet(secondary_btn_style)
            pick_folder_btn.clicked.connect(lambda: self._pick_folder_for_job(job))
            layout.addWidget(pick_folder_btn)

        if job.status in ("needs_folder", "failed"):
            retry_btn = QPushButton("Повторить")
            retry_btn.setStyleSheet(secondary_btn_style)
            retry_btn.clicked.connect(lambda: self.manager.retry(job))
            layout.addWidget(retry_btn)

        return row

    def _pick_folder_for_job(self, job) -> None:
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError

        token = self.manager._get_token()
        if not token:
            QMessageBox.warning(
                self, "Нет токена",
                "Укажите OAuth-токен Яндекс.Диска в настройках (кнопка «Настройки»)."
            )
            return
        try:
            client = YandexDiskClient(token)
            dialog = YandexFolderPickerDialog(client, roots=self.manager._get_roots(), parent=self)
        except YandexDiskError as exc:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", str(exc))
            return

        if dialog.exec_() != QDialog.Accepted or not dialog.selected_path:
            return

        self.manager.upload_to_folder(job, dialog.selected_path)


class SeriesAliasesDialog(QDialog):
    """Просмотр/удаление подтверждённых сопоставлений сериал -> папка на

    Диске (series_aliases.json) — раньше правился только вручную текстом.
    """

    def __init__(self, parent=None, aliases_path: Path = None):
        super().__init__(parent)
        from src.report_uploader import SERIES_ALIASES_FILE

        self._aliases_path = aliases_path or SERIES_ALIASES_FILE
        self.setWindowTitle("Алиасы серий")
        self.setModal(True)
        self.resize(440, 360)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Запомненные папки серий")
        title.setFont(QFont(".AppleSystemUIFont", 13, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(title)

        hint = QLabel(
            "Один раз подтверждённая папка сериала запоминается — следующие\n"
            "отчёты того же сериала находят её сразу, без повторного поиска."
        )
        hint.setFont(QFont(".AppleSystemUIFont", 10))
        hint.setStyleSheet("color: #86868B;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._list_container = QVBoxLayout(content)
        self._list_container.setSpacing(8)
        self._list_container.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._refresh()

    def _refresh(self) -> None:
        from src.report_uploader import list_series_aliases

        while self._list_container.count():
            item = self._list_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        aliases = list_series_aliases(self._aliases_path)
        if not aliases:
            empty_label = QLabel("Алиасов пока нет.")
            empty_label.setFont(QFont(".AppleSystemUIFont", 12))
            empty_label.setStyleSheet("color: #86868B;")
            self._list_container.addWidget(empty_label)
            return

        for key, folder_path in aliases:
            self._list_container.addWidget(self._alias_row(key, folder_path))

    def _alias_row(self, key: str, folder_path: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("""
            QWidget {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
            }
        """)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        key_label = QLabel(key)
        key_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold))
        key_label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        key_label.setWordWrap(True)
        text_col.addWidget(key_label)

        path_label = QLabel(folder_path)
        path_label.setFont(QFont(".AppleSystemUIFont", 10))
        path_label.setStyleSheet("color: #86868B; background: transparent; border: none;")
        path_label.setWordWrap(True)
        text_col.addWidget(path_label)

        layout.addLayout(text_col, 1)

        remove_btn = QPushButton("Удалить")
        remove_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #FF3B30;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 5px 10px;
                font-family: ".AppleSystemUIFont";
                font-size: 11px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """)
        remove_btn.clicked.connect(lambda: self._remove_alias(key))
        layout.addWidget(remove_btn)

        return row

    def _remove_alias(self, key: str) -> None:
        from src.report_uploader import forget_series_alias

        forget_series_alias(key, self._aliases_path)
        self._refresh()


