"""Диалог ревью орфографических исправлений маркер-листа.

Раньше уверенные исправления опечаток применялись к Description/Комментарии
молча при импорте CSV (только строчка в логе) — пользователь узнавал о
замене уже из готового отчёта. Теперь перед генерацией CSV сканируется в
фоне (SpellcheckScanThread), найденные замены показываются таблицей
«было → стало» с чекбоксами (SpellcheckReviewDialog), и в отчёт попадают
только одобренные (см. approved_corrections в CSVImporter.import_issues).
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


def group_proposals(proposals: list[dict]) -> list[dict]:
    """Сворачивает предложения по уникальной замене (было, стало):

    [{"old", "new", "count", "timecodes": [str, ...]}, ...] — одна строка
    диалога на замену, сколько бы раз она ни встречалась в маркер-листе.
    Порядок — по первому появлению в файле.
    """
    grouped: dict[tuple, dict] = {}
    for proposal in proposals:
        key = (proposal["old"], proposal["new"])
        entry = grouped.setdefault(key, {
            "old": proposal["old"],
            "new": proposal["new"],
            "count": 0,
            "timecodes": [],
        })
        entry["count"] += 1
        timecode = proposal.get("timecode", "")
        if timecode and timecode not in entry["timecodes"]:
            entry["timecodes"].append(timecode)
    return list(grouped.values())


def _format_occurrences(entry: dict, max_timecodes: int = 3) -> str:
    shown = entry["timecodes"][:max_timecodes]
    text = ", ".join(shown)
    if entry["count"] > len(shown):
        text = f"{text} (+{entry['count'] - len(shown)})" if text else f"{entry['count']} вхождений"
    return text


class SpellcheckScanThread(QThread):
    """Фоновый скан CSV на опечатки перед генерацией отчёта.

    Ошибки скана (включая недоступные словари) не блокируют генерацию —
    просто эмитится пустой список, и диалог не показывается.
    """

    finished_scan = pyqtSignal(list)  # список предложений (может быть пуст)

    def __init__(self, csv_path: str):
        super().__init__()
        self.csv_path = csv_path

    def run(self):
        from src.csv_importer import CSVImporter

        try:
            proposals = CSVImporter().scan_spelling(self.csv_path)
        except Exception as exc:
            logger.warning(f"Скан орфографии перед генерацией не удался: {exc}")
            proposals = []
        self.finished_scan.emit(proposals)


class SpellcheckReviewDialog(QDialog):
    """Таблица найденных замен «было → стало» с чекбоксами.

    После exec_() читать approved_corrections — множество одобренных пар
    (было, стало). Кнопка «Без исправлений» (и закрытие крестиком/Esc)
    оставляет множество пустым: текст маркер-листа попадёт в отчёт как есть.
    """

    def __init__(self, proposals: list[dict], parent=None):
        super().__init__(parent)
        self.approved_corrections: set = set()
        self._entries = group_proposals(proposals)

        self.setWindowTitle("Проверка орфографии")
        self.setModal(True)
        self.resize(480, 380)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Найдены возможные опечатки в маркер-листе")
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(title)

        hint = QLabel("Отмеченные замены будут применены в отчёте. "
                      "Имена и термины можно снять с отметки.")
        hint.setFont(QFont(".AppleSystemUIFont", 10))
        hint.setStyleSheet("color: #86868B;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.select_all_cb = QCheckBox("Выбрать все")
        self.select_all_cb.setChecked(True)
        self.select_all_cb.setFont(QFont(".AppleSystemUIFont", 11))
        self.select_all_cb.setStyleSheet("color: #1D1D1F;")
        self.select_all_cb.toggled.connect(self._on_select_all_toggled)
        layout.addWidget(self.select_all_cb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Было → Стало", "Где (таймкоды)", ""])
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(1, 170)
        self.tree.setRootIsDecorated(False)
        self.tree.setStyleSheet("""
            QTreeWidget {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
                color: #1D1D1F;
            }
        """)
        for entry in self._entries:
            item = QTreeWidgetItem([
                f"{entry['old']} → {entry['new']}",
                _format_occurrences(entry),
                "",
            ])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setData(0, Qt.UserRole, (entry["old"], entry["new"]))
            self.tree.addTopLevelItem(item)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Применить выбранные")
        buttons.button(QDialogButtonBox.Cancel).setText("Без исправлений")
        buttons.accepted.connect(self._on_apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _checked_pairs(self) -> set:
        pairs = set()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                pairs.add(item.data(0, Qt.UserRole))
        return pairs

    def _on_select_all_toggled(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)
        self.tree.blockSignals(False)

    def _on_item_changed(self, _item, _column):
        # «Выбрать все» отражает фактическое состояние списка, не triggering
        # повторную рассылку setCheckState по всем строкам.
        all_checked = len(self._checked_pairs()) == self.tree.topLevelItemCount()
        self.select_all_cb.blockSignals(True)
        self.select_all_cb.setChecked(all_checked)
        self.select_all_cb.blockSignals(False)

    def _on_apply(self):
        self.approved_corrections = self._checked_pairs()
        self.accept()
