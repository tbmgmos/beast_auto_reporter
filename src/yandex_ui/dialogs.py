"""Диалоги UI-слоя интеграции с Яндекс.Диском."""

from __future__ import annotations

import html
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtCore import QEvent, QMimeData, QRect, QSettings, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDrag, QFont, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from src.icons import make_icon, make_icon_pixmap, make_tagged_icon, make_text_badge_icon
from src.yandex_ui.edit_sync import YandexEditSyncController
from src.yandex_ui.helpers import (
    _format_disk_file_size, _format_disk_modified_date, _relative_date_label,
    _quick_look_preview, _stop_thread,
)
from src.yandex_ui.threads import (
    CURRENT_DRAFT, _DeleteThread, _FolderSizeThread, _ListFolderThread, _MkdirThread, _PublishThread,
    _RenameThread, _SetTagThread, FinderDropUploadThread, VersionSummaryThread, YandexDiskCompareThread,
    YandexDiskDownloadThread, YandexDiskFolderVersionsThread,
)

logger = logging.getLogger(__name__)


class MarkerIdentityResolutionDialog(QDialog):
    """Resolve only uncertain marker-history matches before generation."""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.choices: dict[int, str] = {}
        self._combos: dict[int, QComboBox] = {}
        self.setWindowTitle("Проверка ID маркеров")
        self.setModal(True)
        self.resize(760, 420)
        self.setMinimumSize(650, 340)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel("Найдены неоднозначные маркеры")
        title.setFont(QFont(".AppleSystemUIFont", 14, QFont.DemiBold))
        layout.addWidget(title)
        hint = QLabel(
            "Выберите прежний ID, если это восстановленный маркер. "
            "Вариант «Новый» оставит предложенный новый номер."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6E6E73;")
        layout.addWidget(hint)

        table = QTableWidget(len(plan.ambiguities), 4)
        table.setHorizontalHeaderLabels(["Текущий маркер", "Решение", "Лучшее совпадение", "Причина"])
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        for row, ambiguity in enumerate(plan.ambiguities):
            current = f"{ambiguity.tc_in or '—'}  {ambiguity.description or 'Без описания'}"
            table.setItem(row, 0, QTableWidgetItem(current))
            combo = QComboBox()
            combo.addItem(f"Новый — {ambiguity.proposed_id}", ambiguity.proposed_id)
            for candidate in ambiguity.candidates:
                status = "удалён" if candidate.status == "deleted" else "активен"
                combo.addItem(f"{candidate.marker_id} · {status}", candidate.marker_id)
            self._combos[ambiguity.row_index] = combo
            table.setCellWidget(row, 1, combo)
            if ambiguity.candidates:
                best = ambiguity.candidates[0]
                old = f"{best.tc_in or '—'}  {best.description or 'Без описания'}"
                table.setItem(row, 2, QTableWidgetItem(old))
                table.setItem(row, 3, QTableWidgetItem(f"{round(best.score * 100)}%"))
            else:
                table.setItem(row, 2, QTableWidgetItem("—"))
                table.setItem(row, 3, QTableWidgetItem("—"))
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Применить и продолжить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept_choices)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_choices(self):
        self.choices = {row: combo.currentData() for row, combo in self._combos.items()}
        self.accept()


class MarkerRegistryConflictDialog(QDialog):
    """Resolve the rare case where two confirmed histories own one M-ID."""

    def __init__(self, conflict, parent=None):
        super().__init__(parent)
        self.conflict = conflict
        self.choices: dict[str, str] = {}
        self._combos = {}
        self.setWindowTitle("Конфликт истории ID")
        self.setModal(True)
        self.resize(780, 390)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("Один ID связан с разными маркерами")
        title.setFont(QFont(".AppleSystemUIFont", 14, QFont.DemiBold))
        layout.addWidget(title)
        hint = QLabel(
            "Если это один и тот же маркер — объедините историю. Если маркеры разные, "
            "локальная история получит новый свободный номер."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6E6E73;")
        layout.addWidget(hint)
        table = QTableWidget(len(conflict.details), 4)
        table.setHorizontalHeaderLabels(["ID", "Локальная история", "История на Диске", "Решение"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for row, detail in enumerate(conflict.details):
            marker_id = detail["marker_id"]
            local = detail.get("local", {}).get("current", {})
            remote = detail.get("remote", {}).get("current", {})
            table.setItem(row, 0, QTableWidgetItem(marker_id))
            table.setItem(row, 1, QTableWidgetItem(f"{local.get('tc_in', '—')}  {local.get('description', '—')}"))
            table.setItem(row, 2, QTableWidgetItem(f"{remote.get('tc_in', '—')}  {remote.get('description', '—')}"))
            combo = QComboBox()
            combo.addItem("Это один маркер — объединить", "same")
            combo.addItem("Разные — локальному новый ID", "renumber_local")
            table.setCellWidget(row, 3, combo)
            self._combos[marker_id] = combo
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Применить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept_choices)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_choices(self):
        self.choices = {marker_id: combo.currentData() for marker_id, combo in self._combos.items()}
        self.accept()


class MarkerRegistryStatsDialog(QDialog):
    """Read-only health/statistics view for all local marker chains."""

    def __init__(self, statistics: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("История ID маркеров")
        self.resize(820, 440)
        self.setMinimumSize(680, 340)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel("Цепочки маркеров")
        title.setFont(QFont(".AppleSystemUIFont", 14, QFont.DemiBold))
        layout.addWidget(title)
        table = QTableWidget(len(statistics), 8)
        table.setHorizontalHeaderLabels(
            ["Цепочка", "Версий", "Активных", "Удалённых", "Восстановлено", "Ожидают", "Последний ID", "Состояние"]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        for row, item in enumerate(statistics):
            values = [
                item["chain_key"], item["versions"], item["active"], item["deleted"],
                item["restored"], item["pending"], f"M{item['max_issued']}" if item["max_issued"] else "—",
                {"ok": "Полная", "pending": "Ожидает загрузки", "offline": "Офлайн", "attention": "Требует проверки", "conflict": "Конфликт"}.get(
                    item.get("health"), item.get("health", "—")
                ),
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 8):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)
        empty = QLabel("История маркеров пока не создана.")
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet("color: #86868B;")
        empty.setVisible(not statistics)
        layout.addWidget(empty)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class YandexUploadDiffDialog(QDialog):
    """Краткая сводка сравнения (маркеры/блокеры/новые маркеры/параметры)

    с выбранной предыдущей версией того же эпизода. В режиме upload_mode=True
    внизу кнопки «Отправить»/«Отмена» (используется перед загрузкой на Диск),
    иначе — одна кнопка «Закрыть» (просто просмотр сравнения), плюс, если
    allow_pick_another=True, кнопка «Выбрать другую версию» (возвращает
    result_code == PICK_ANOTHER из exec_()). Если передан summary_generator
    (ConclusionGenerator), показывается блок AI-сводки изменений — связный
    текст от LLM по данным сравнения (см. VersionSummaryThread).
    """

    PICK_ANOTHER = 2

    # Поля-метаданные (не числовые измерения) — показываются компактной
    # приглушённой строкой под таблицей, а не наравне с LUFS/True Peak/LRA:
    # длинные имена файлов иначе некрасиво переносятся посреди стрелки.
    _METADATA_FIELDS = {"Файл", "Хронометраж", "Формат файла", "Дорожка"}

    def __init__(
        self, comparison, parent=None, upload_mode: bool = True,
        old_label: str = None, new_label: str = None, allow_pick_another: bool = False,
        summary_generator=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Сравнение с предыдущим отчётом")
        self.setModal(True)
        self.resize(460, 480)
        self.setMinimumWidth(420)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        # Для AI-сводки (summary_generator — ConclusionGenerator; None — блок
        # сводки не показывается вовсе, например в вызовах без LLM-поддержки).
        self._comparison = comparison
        self._old_label = old_label
        self._new_label = new_label
        self._summary_generator = summary_generator
        self._summary_thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel("Сравнение версий")
        title.setFont(QFont(".AppleSystemUIFont", 14, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        title.setWordWrap(True)
        layout.addWidget(title)

        if old_label and new_label:
            layout.addWidget(self._comparison_subtitle(old_label, new_label))

        card = QWidget()
        card.setObjectName("metricsCard")
        card.setStyleSheet("""
            QWidget#metricsCard {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 10px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(4)
        card_layout.addWidget(self._metric_row("Маркеров", comparison.marker_count_old, comparison.marker_count_new))
        card_layout.addWidget(self._metric_row(
            "из них новых", comparison.new_marker_count_old, comparison.new_marker_count_new, indent=True
        ))
        card_layout.addWidget(self._divider())
        card_layout.addWidget(self._metric_row(
            "Блокеров", comparison.blocker_count_old, comparison.blocker_count_new, warn_on_increase=True,
        ))
        layout.addWidget(card)

        # AI-сводка и параметры — в одном скролле вместе с остальным
        # переменным по длине контентом. Сводка — это связный текст от LLM
        # без ограничения длины; раньше карточка сводки стояла ВНЕ скролла
        # прямо в layout диалога, и длинный текст просто раздувал окно
        # вместо того, чтобы скроллиться — приходилось тянуть окно руками.
        # Карточка счётчиков (Маркеров/Блокеров) выше остаётся вне скролла
        # намеренно: у неё всегда ровно 3 строки, она не растёт, и удобнее,
        # когда она видна постоянно, не пропадая при скролле вниз.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        if summary_generator is not None:
            content_layout.addWidget(self._make_summary_card())

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

    def _make_summary_card(self) -> QWidget:
        """Карточка AI-сводки: кнопка запуска LLM-формулировки + текст результата.

        Сама генерация идёт в VersionSummaryThread (вызов LLM может занимать
        десятки секунд); доступность провайдера заранее не проверяем (это
        сетевой запрос на открытии диалога) — при ошибке текст причины
        показывается тут же, кнопка остаётся для повтора.
        """
        card = QWidget()
        card.setObjectName("summaryCard")
        card.setStyleSheet("""
            QWidget#summaryCard {
                background: #F5F0FF;
                border: 1px solid #E2D9F7;
                border-radius: 10px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        icon_label = QLabel()
        icon_label.setPixmap(make_icon_pixmap("sparkle", "#8B5CF6", 14))
        icon_label.setStyleSheet("background: transparent;")
        header_row.addWidget(icon_label)

        header = QLabel("AI-сводка изменений")
        header.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        header.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        header_row.addWidget(header)
        header_row.addStretch()

        self.summary_btn = QPushButton("Сформулировать")
        self.summary_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 4px 10px;
                font-family: ".AppleSystemUIFont";
                font-size: 12px;
            }
            QPushButton:hover { background: #F5F5F7; }
            QPushButton:disabled { color: #B3B3BA; }
        """)
        self.summary_btn.clicked.connect(self._start_version_summary)
        header_row.addWidget(self.summary_btn)
        card_layout.addLayout(header_row)

        self.summary_label = QLabel("")
        self.summary_label.setFont(QFont(".AppleSystemUIFont", 12))
        self.summary_label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        self.summary_label.setWordWrap(True)
        self.summary_label.setVisible(False)
        card_layout.addWidget(self.summary_label)
        return card

    def _start_version_summary(self) -> None:
        if self._summary_thread is not None:
            return
        self.summary_btn.setEnabled(False)
        self.summary_label.setStyleSheet("color: #86868B; background: transparent; border: none;")
        self.summary_label.setText("Формулируем сводку…")
        self.summary_label.setVisible(True)
        self._summary_thread = VersionSummaryThread(
            self._summary_generator, self._comparison,
            old_label=self._old_label, new_label=self._new_label,
        )
        self._summary_thread.resolved.connect(self._on_summary_resolved)
        self._summary_thread.failed.connect(self._on_summary_failed)
        self._summary_thread.start()

    def _on_summary_resolved(self, text: str) -> None:
        self._summary_thread = None
        self.summary_label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        self.summary_label.setText(text)
        # Кнопку прячем: повторная формулировка того же сравнения даст тот же
        # смысл другими словами — пользы мало, а повторные вызовы LLM стоят времени.
        self.summary_btn.setVisible(False)

    def _on_summary_failed(self, message: str) -> None:
        self._summary_thread = None
        self.summary_label.setStyleSheet("color: #FF3B30; background: transparent; border: none;")
        self.summary_label.setText(f"Не удалось сформулировать: {message}")
        self.summary_btn.setEnabled(True)

    def done(self, r):
        # Останавливаем LLM-поток до уничтожения диалога — иначе при живом
        # потоке процесс упал бы с "QThread: Destroyed while thread is still
        # running" (та же причина, что и в stop_threads() у остальных диалогов).
        _stop_thread(self._summary_thread)
        self._summary_thread = None
        super().done(r)

    @staticmethod
    def _version_row(caption: str, text: str, dialog_width: int, accent: bool = False) -> QWidget:
        """Строка «БЫЛО/СТАЛО»: короткая подпись-каптион слева + значение,

        обрезанное многоточием с полным текстом в tooltip (двух строк "Было:
        длинное_имя_файла..." без разделения подписи и значения читать
        неудобно — глаз не сразу находит, где кончилась подпись).
        """
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        caption_label = QLabel(caption)
        caption_label.setFont(QFont(".AppleSystemUIFont", 9, QFont.DemiBold))
        caption_label.setStyleSheet(
            f"color: {'#007AFF' if accent else '#9AA1AC'}; background: transparent; letter-spacing: 0.04em;"
        )
        caption_label.setFixedWidth(44)
        row_layout.addWidget(caption_label, 0, Qt.AlignTop)

        value_label = QLabel()
        value_label.setFont(QFont(".AppleSystemUIFont", 11, QFont.DemiBold if accent else QFont.Normal))
        value_label.setStyleSheet(f"color: {'#1D1D1F' if accent else '#5C5C62'}; background: transparent;")
        value_label.setToolTip(text)
        metrics = value_label.fontMetrics()
        available_width = dialog_width - 20 * 2 - 12 * 2 - 44 - 8
        value_label.setText(metrics.elidedText(text, Qt.ElideRight, max(available_width, 80)))
        row_layout.addWidget(value_label, 1)

        return row

    @classmethod
    def _comparison_subtitle(cls, old_label: str, new_label: str, dialog_width: int = 460) -> QWidget:
        """Бейдж «что с чем сравниваем» — чтобы не приходилось гадать,

        к какой именно версии относятся цифры ниже. Наведение показывает
        полный текст, если он не поместился и был обрезан многоточием.
        Нейтральный серый фон, а не жёлтый/предупреждающий — это просто
        контекст, а не сигнал о проблеме.
        """
        badge = QWidget()
        badge.setObjectName("compareBadge")
        badge.setStyleSheet("""
            QWidget#compareBadge {
                background: #F5F5F7;
                border: 1px solid #E5E5EA;
                border-radius: 10px;
            }
        """)
        layout = QVBoxLayout(badge)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        layout.addWidget(cls._version_row("БЫЛО", old_label, dialog_width))
        layout.addWidget(cls._version_row("СТАЛО", new_label, dialog_width, accent=True))

        return badge

    @staticmethod
    def _divider() -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #E5E5EA;")
        return line

    @staticmethod
    def _metric_row(label: str, old: int, new: int, indent: bool = False, warn_on_increase: bool = False) -> QWidget:
        """Строка «Маркеров/Блокеров: было → стало» с дельтой-«пилюлей».

        warn_on_increase — красная/зелёная пилюля по смыслу «больше = хуже»
        (годится для Блокеров: рост числа блокеров — это плохо). Без флага
        дельта нейтрально-серая: для «Маркеров»/«из них новых» само по себе
        увеличение количества не хорошо и не плохо, красить как проблему
        было бы вводящим в заблуждение сигналом.
        """
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(18 if indent else 0, 3, 0, 3)
        row_layout.setSpacing(8)

        label_widget = QLabel(label)
        label_widget.setFont(QFont(".AppleSystemUIFont", 11 if indent else 12, QFont.Normal if indent else QFont.DemiBold))
        label_widget.setStyleSheet(f"color: {'#86868B' if indent else '#1D1D1F'}; background: transparent;")
        row_layout.addWidget(label_widget)
        row_layout.addStretch()

        value_widget = QLabel(f"{old} → {new}")
        value_widget.setFont(QFont(".AppleSystemUIFont", 11 if indent else 12))
        value_widget.setStyleSheet("color: #1D1D1F; background: transparent;")
        row_layout.addWidget(value_widget)

        diff = new - old
        delta_widget = QLabel(f"{'+' if diff > 0 else ''}{diff}" if diff != 0 else "—")
        delta_widget.setFixedWidth(34)
        delta_widget.setAlignment(Qt.AlignCenter)
        delta_widget.setFont(QFont(".AppleSystemUIFont", 10, QFont.DemiBold))
        if diff == 0:
            delta_widget.setStyleSheet("color: #B3B3BA; background: transparent;")
        elif warn_on_increase:
            bad = diff > 0
            bg, fg = ("#FFEBEA", "#D92B2B") if bad else ("#E6F9EC", "#1F9D46")
            delta_widget.setStyleSheet(f"color: {fg}; background: {bg}; border-radius: 8px; padding: 1px 0;")
        else:
            delta_widget.setStyleSheet("color: #6B7280; background: #EEF0F2; border-radius: 8px; padding: 1px 0;")
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
    def _shorten_common_prefix(old: str, new: str) -> tuple:
        """Убирает общий префикс у пары строк, оставляя только различающийся

        хвост (с многоточием впереди) — версии одного файла отличаются
        обычно только датой/номером версии в конце, показывать оба полных
        имени целиком рядом с одной стрелкой было избыточно и разъезжалось
        переносом строки.
        """
        common = 0
        limit = min(len(old), len(new))
        while common < limit and old[common] == new[common]:
            common += 1
        cut = common - 8  # немного контекста перед местом расхождения
        if cut <= 0:
            return old, new
        return "…" + old[cut:], "…" + new[cut:]

    @classmethod
    def _parameter_card(cls, change: dict) -> QWidget:
        """Карточка одной дорожки: числовые измерения — выровненной таблицей

        (поле | было → стало, цвет только если есть статус bad/warn),
        метаданные (имя файла, хронометраж) — компактными приглушёнными
        строками ниже, отдельно от измерений.
        """
        card = QWidget()
        card.setObjectName("paramCard")
        card.setStyleSheet("""
            QWidget#paramCard {
                background: #F0F6FF;
                border: 1px solid #D6E6FF;
                border-radius: 10px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        label = QLabel(change["label"])
        label.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        label.setStyleSheet("color: #1D1D1F; background: transparent; border: none;")
        layout.addWidget(label)

        measurements = [c for c in change["changes"] if c["field"] not in cls._METADATA_FIELDS]
        metadata = [c for c in change["changes"] if c["field"] in cls._METADATA_FIELDS]

        if measurements:
            grid = QGridLayout()
            grid.setContentsMargins(0, 4, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            grid.setColumnStretch(1, 1)
            for row_idx, item in enumerate(measurements):
                field_label = QLabel(item["field"])
                field_label.setFont(QFont(".AppleSystemUIFont", 11))
                field_label.setStyleSheet("color: #6B7280; background: transparent; border: none;")
                grid.addWidget(field_label, row_idx, 0)

                # Приглушённое старое -> тёмное новое; красим новое только
                # если это реальная проблема (status из заливки в отчёте),
                # иначе синий "как ссылка" вводил в заблуждение — тут ничего
                # не кликабельно.
                new_color = {"bad": "#D92B2B", "warn": "#B25F00"}.get(item.get("status"), "#1D1D1F")
                value_label = QLabel()
                value_label.setTextFormat(Qt.RichText)
                value_label.setText(
                    f'<span style="color:#9AA1AC;">{html.escape(str(item["old"]))}</span>'
                    f'&nbsp;→&nbsp;'
                    f'<b style="color:{new_color};">{html.escape(str(item["new"]))}</b>'
                )
                value_label.setFont(QFont(".AppleSystemUIFont", 11))
                value_label.setStyleSheet("background: transparent; border: none;")
                value_label.setWordWrap(True)
                grid.addWidget(value_label, row_idx, 1)
            layout.addLayout(grid)

        if metadata:
            meta_layout = QVBoxLayout()
            meta_layout.setContentsMargins(0, 6 if measurements else 4, 0, 0)
            meta_layout.setSpacing(2)
            for item in metadata:
                old_text, new_text = str(item["old"]), str(item["new"])
                full_tooltip = f'{item["field"]}: {old_text} → {new_text}'
                if item["field"] == "Файл" and (len(old_text) > 26 or len(new_text) > 26):
                    old_text, new_text = cls._shorten_common_prefix(old_text, new_text)
                row = QLabel()
                row.setTextFormat(Qt.RichText)
                row.setText(
                    f'<span style="color:#9AA1AC;">{html.escape(item["field"])}: {html.escape(old_text)}</span>'
                    f'&nbsp;→&nbsp;'
                    f'<span style="color:#5C5C62;">{html.escape(new_text)}</span>'
                )
                row.setFont(QFont(".AppleSystemUIFont", 10))
                row.setStyleSheet("background: transparent; border: none;")
                row.setWordWrap(True)
                row.setToolTip(full_tooltip)
                meta_layout.addWidget(row)
            layout.addLayout(meta_layout)

        return card


class YandexVersionPickerDialog(QDialog):
    """Выбор двух версий отчёта для сравнения между собой (например, первой

    с четвёртой) — не только с текущим черновиком. По умолчанию: «Было» —
    самая свежая версия на Диске, «Стало» — текущий черновик. Оба поля можно
    поменять на любую другую версию; при большом количестве версий можно
    начать печатать дату или часть названия, чтобы отфильтровать список.
    """

    CURRENT_DRAFT = CURRENT_DRAFT  # см. src/yandex_ui/threads.py — единый источник значения

    def __init__(self, versions: list, parent=None, include_current_draft: bool = True):
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
        for i, version in enumerate(reversed(versions)):  # новые сверху
            date_text = _relative_date_label(version["date"]) if version["date"] else ""
            suffix = "  (последняя)" if i == 0 else ""
            label = f"{date_text}  {version['label']}{suffix}" if date_text else f"{version['label']}{suffix}"
            entries_old.append((label, version["path"]))

        # «Стало» — текущий черновик (по умолчанию, если он вообще
        # доступен для сравнения — include_current_draft=False, когда
        # сравнение запущено не из главного окна с активным отчётом, а
        # прямо из просмотрщика Диска) плюс те же версии на Диске, чтобы
        # можно было сравнить и две старые версии между собой.
        if include_current_draft:
            entries_new = [("Текущий черновик (ещё не отправлен)", self.CURRENT_DRAFT)] + entries_old
        else:
            entries_new = list(entries_old)

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

        if include_current_draft:
            if self.combo_old.count() > 0:
                self.combo_old.setCurrentIndex(0)  # самая свежая версия на Диске
            self.combo_new.setCurrentIndex(0)  # текущий черновик
        elif self.combo_new.count() > 1:
            # Нет черновика для сравнения по умолчанию — самое полезное
            # сравнение «что изменилось в последней версии»: вторая
            # свежая -> самая свежая, а не одна и та же версия дважды.
            self.combo_old.setCurrentIndex(1)
            self.combo_new.setCurrentIndex(0)

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


class VersionChainDialog(QDialog):
    """Список всех версий отчёта одной цепочкой (от старой к новой) —

    для каждой соседней пары кнопка «Сравнить с предыдущей» запускает
    сравнение лениво, по клику (не всё сразу при открытии: для N версий
    это было бы N-1 попарных загрузок docx). Сам diff не считает —
    только сообщает владельцу через compare_requested, чтобы вся логика
    сравнения (поток, диалог результата, «выбрать другую версию») жила
    в одном месте (см. YandexDiskBrowserDialog._run_version_compare).
    """

    compare_requested = pyqtSignal(str, str, str, str)  # (old_path, new_path, old_label, new_label)

    def __init__(self, versions: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Цепочка версий")
        self.setModal(True)
        # Высота — под типичную короткую цепочку (2-4 версии); для более
        # длинных список сам уезжает в скролл, раздувать дефолт под них не
        # нужно — раньше фиксированные 420 оставляли под короткими списками
        # пустое пространство высотой в пол-окна.
        self.resize(440, 360)
        self.setMinimumWidth(400)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel("Версии отчёта, от старой к новой")
        title.setFont(QFont(".AppleSystemUIFont", 13, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        title.setWordWrap(True)
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 2, 0)
        content_layout.setSpacing(0)

        for i, version in enumerate(versions):
            is_latest = i == len(versions) - 1
            date_text = _relative_date_label(version["date"]) if version["date"] else ""
            content_layout.addWidget(self._version_card(date_text, version["label"], is_latest))

            if i < len(versions) - 1:
                nxt = versions[i + 1]
                content_layout.addWidget(self._compare_connector(
                    lambda _checked, op=version["path"], np=nxt["path"], ol=version["label"], nl=nxt["label"]:
                        self.compare_requested.emit(op, np, ol, nl)
                ))

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _version_card(date_text: str, filename: str, is_latest: bool) -> QWidget:
        """Карточка одной версии: дата крупно, имя файла — приглушённым

        подзаголовком с эллипсисом и tooltip (не clip без многоточия и не
        горизонтальный скролл, как было раньше при длинных именах файлов).
        """
        card = QWidget()
        card.setObjectName("versionCard")
        card.setStyleSheet("""
            QWidget#versionCard {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 10px;
            }
        """)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 9, 12, 9)
        outer.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        date_label = QLabel(date_text or "Дата неизвестна")
        date_label.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        date_label.setStyleSheet("color: #1D1D1F; background: transparent;")
        top_row.addWidget(date_label)
        top_row.addStretch()
        if is_latest:
            badge = QLabel("ПОСЛЕДНЯЯ")
            badge.setFont(QFont(".AppleSystemUIFont", 9, QFont.DemiBold))
            badge.setStyleSheet("""
                color: #007AFF; background: #E5F1FF; border-radius: 7px;
                padding: 1px 7px; letter-spacing: 0.03em;
            """)
            top_row.addWidget(badge)
        outer.addLayout(top_row)

        name_label = QLabel()
        name_label.setFont(QFont(".AppleSystemUIFont", 11))
        name_label.setStyleSheet("color: #86868B; background: transparent;")
        name_label.setToolTip(filename)
        metrics = name_label.fontMetrics()
        name_label.setText(metrics.elidedText(filename, Qt.ElideMiddle, 360))
        outer.addWidget(name_label)

        return card

    def _compare_connector(self, on_click) -> QWidget:
        """Тонкий «коннектор» между соседними карточками — визуально

        подчёркивает, что кнопка сравнивает именно эту пару версий (а не
        просто ещё один элемент списка), и держит цепочку зрительно связной.
        """
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(0)

        line_left = QWidget()
        line_left.setFixedWidth(20)
        line_left.setFixedHeight(1)
        line_left.setStyleSheet("background: #D2D2D7;")
        row.addWidget(line_left, 0, Qt.AlignVCenter)

        btn = QPushButton("⇄  Сравнить")
        btn.setFont(QFont(".AppleSystemUIFont", 10, QFont.DemiBold))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 10px;
                padding: 3px 12px;
            }
            QPushButton:hover { background: #EBF5FF; border-color: #8DBEFF; }
        """)
        btn.clicked.connect(on_click)
        row.addWidget(btn, 0, Qt.AlignVCenter)

        line_right = QWidget()
        line_right.setFixedHeight(1)
        line_right.setStyleSheet("background: #D2D2D7;")
        row.addWidget(line_right, 1, Qt.AlignVCenter)

        return wrap


class YandexFolderTreeWidget(QWidget):
    """Дерево папок Диска с ленивой подгрузкой и созданием новых на лету.

    Общая часть `YandexFolderPickerDialog` (один такой виджет + OK/Cancel)
    и `CombinedFolderPickerDialog` (два таких виджета друг под другом —
    для отчёта и npr-проекта сразу, см. ниже). Сам виджет не решает, что
    делать с выбором — за это отвечает контейнер через `get_selected_path()`.

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

    root_ready = pyqtSignal()

    def __init__(
        self, client, roots: list = None, parent=None, *,
        prompt_text: str = "Выберите папку сериала/фильма или создайте новую",
        hint_text: str = (
            "Для сериала: внутри выбранной папки эпизод (например, e02) будет\n"
            "найден или создан автоматически — можно также сразу выбрать готовую\n"
            "папку эпизода. Для фильма выбранная папка используется как есть."
        ),
        aliases_path: Path = None,
        suggested_name: str = "",
    ):
        super().__init__(parent)
        from src.report_uploader import load_series_aliases, SERIES_ALIASES_FILE

        self.client = client
        self.roots = list(roots) if roots else ["/отчеты"]
        self._root_items = []
        self._expand_threads = {}  # path -> _ListFolderThread
        self._new_folder_thread = None
        # Имя, распознанное из имени файла отчёта/npr-проекта (серия,
        # сезон+эпизод) — подставляется как готовый вариант в диалог
        # «Новая папка», чтобы не перепечатывать его вручную (см.
        # _create_new_folder).
        self._suggested_name = suggested_name
        # Обратный поиск путь -> ключ алиаса — считается один раз при
        # открытии диалога (не на каждый элемент), чтобы уже привязанные
        # папки были видны сразу подсказкой, без похода в отдельный диалог
        # управления алиасами.
        aliases = load_series_aliases(aliases_path or SERIES_ALIASES_FILE)
        self._alias_by_path = {path: key for key, path in aliases.items()}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_icon = QLabel()
        title_icon.setPixmap(make_icon_pixmap("folder", "#86868B", 14))
        title_row.addWidget(title_icon)
        title = QLabel(prompt_text)
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        title_row.addWidget(title, 1)
        layout.addLayout(title_row)

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

        # Создание ПЕРВОЙ (основной) корневой папки — асинхронно, чтобы не
        # блокировать GUI-поток на медленной сети; дерево/кнопки недоступны
        # до готовности. Остальные корни (если есть) не создаются
        # автоматически — это, как правило, уже существующие папки
        # пользователя, добавленные вручную в настройках.
        self.tree.setEnabled(False)
        self.new_folder_btn.setEnabled(False)
        self._root_mkdir_thread = _MkdirThread(self.client, self.roots[0])
        self._root_mkdir_thread.finished_mkdir.connect(self._on_root_ready)
        self._root_mkdir_thread.start()

    def _on_root_ready(self, success: bool, message: str) -> None:
        self.tree.setEnabled(True)
        self.new_folder_btn.setEnabled(True)
        self.root_ready.emit()
        if not success:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        self._populate_root()

    def _make_item(self, name: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name])
        item.setData(0, Qt.UserRole, path)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        alias_key = self._alias_by_path.get(path)
        if alias_key:
            item.setToolTip(0, f"Уже привязан алиас: «{alias_key}»")
            item.setForeground(0, QColor("#007AFF"))
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
        thread.not_found.connect(
            lambda rpath, it=item: self._on_children_load_failed(it, rpath, "Папка больше не существует на Диске")
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

        # Готовое имя подставляем только при создании папки прямо в корне
        # (это и есть папка серии/сезона, для которой оно распознано) —
        # внутри уже выбранной серии/эпизода оно было бы неуместно.
        default_name = self._suggested_name if selected in self._root_items else ""
        name, ok = QInputDialog.getText(self, "Новая папка", "Название папки:", text=default_name)
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

    def get_selected_path(self) -> str | None:
        """Путь выбранного элемента, либо None, если ничего не выбрано или

        выбран корневой узел-заглушка (не настоящая папка на Диске).
        """
        selected = self.tree.currentItem()
        if not selected or selected in self._root_items:
            return None
        return selected.data(0, Qt.UserRole)

    def stop_threads(self) -> None:
        # Вызывается контейнером (диалогом) при закрытии — останавливаем
        # фоновые потоки (mkdir корня/новой папки, листинги разворачиваемых
        # папок), иначе при закрытии до их завершения PyQt валит процесс
        # "QThread: Destroyed while thread is still running".
        _stop_thread(getattr(self, "_root_mkdir_thread", None))
        _stop_thread(getattr(self, "_new_folder_thread", None))
        for thread in list(getattr(self, "_expand_threads", {}).values()):
            _stop_thread(thread)


class YandexFolderPickerDialog(QDialog):
    """Диалог ручного выбора/создания ОДНОЙ папки на Диске — тонкая

    обёртка вокруг YandexFolderTreeWidget с заголовком окна и OK/Cancel.
    Публичный контракт не меняется при рефакторинге: конструктор,
    `.selected_path`, `.exec_()` — как раньше, оба места вызова (npr,
    отчёт) не должны отличать эту версию от прежней.
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
        aliases_path: Path = None,
        suggested_name: str = "",
    ):
        super().__init__(parent)
        self.selected_path = None

        self.setWindowTitle(window_title)
        self.setModal(True)
        self.resize(420, 480)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.panel = YandexFolderTreeWidget(
            client, roots=roots, parent=self,
            prompt_text=prompt_text, hint_text=hint_text, aliases_path=aliases_path,
            suggested_name=suggested_name,
        )
        layout.addWidget(self.panel, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Выбрать")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.panel.root_ready.connect(lambda: self.buttons.button(QDialogButtonBox.Ok).setEnabled(True))

    def _on_accept(self):
        selected_path = self.panel.get_selected_path()
        if not selected_path:
            QMessageBox.warning(self, "Не выбрано", "Выберите папку или создайте новую.")
            return
        self.selected_path = selected_path
        self.accept()

    def done(self, r):
        self.panel.stop_threads()
        super().done(r)


class CombinedFolderPickerDialog(QDialog):
    """Два YandexFolderTreeWidget друг под другом — для отчёта (сверху) и

    npr-проекта Nuendo (снизу) сразу в одном окне. Показывается только
    когда ни папка отчёта, ни папка npr не резолвились автоматически (см.
    BeastApp._send_report_to_disk/_on_combined_targets_resolved) — если
    определился хоть один, по-прежнему показывается только недостающий
    одиночный YandexFolderPickerDialog, без изменений.
    """

    def __init__(
        self, client, report_roots: list, npr_root: str, parent=None, *,
        report_prompt_text: str = "Папка серии для отчёта",
        npr_prompt_text: str = "Папка сезона для Nuendo-проекта (.npr)",
        report_aliases_path: Path = None,
        npr_aliases_path: Path = None,
        report_suggested_name: str = "",
        npr_suggested_name: str = "",
    ):
        super().__init__(parent)
        self.report_selected_path = None
        self.npr_selected_path = None

        self.setWindowTitle("Папки для отчёта и Nuendo-проекта")
        self.setModal(True)
        self.resize(440, 620)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        intro = QLabel(
            "Для этой серии ещё нет ни папки отчёта, ни папки npr-проекта "
            "на Диске — выберите или создайте обе сразу."
        )
        intro.setFont(QFont(".AppleSystemUIFont", 11))
        intro.setStyleSheet("color: #86868B;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.report_panel = YandexFolderTreeWidget(
            client, roots=report_roots, parent=self,
            prompt_text=report_prompt_text, aliases_path=report_aliases_path,
            suggested_name=report_suggested_name,
        )
        layout.addWidget(self.report_panel, 1)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("background: #E5E5EA; max-height: 1px; border: none;")
        layout.addWidget(divider)

        self.npr_panel = YandexFolderTreeWidget(
            client, roots=[npr_root], parent=self,
            prompt_text=npr_prompt_text,
            hint_text=(
                "Все .npr-файлы этого сезона будут лежать в выбранной\n"
                "папке одним списком, без деления на эпизоды."
            ),
            aliases_path=npr_aliases_path,
            suggested_name=npr_suggested_name,
        )
        layout.addWidget(self.npr_panel, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Отправить")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._report_root_ready = False
        self._npr_root_ready = False
        self.report_panel.root_ready.connect(lambda: self._on_panel_root_ready("report"))
        self.npr_panel.root_ready.connect(lambda: self._on_panel_root_ready("npr"))

    def _on_panel_root_ready(self, which: str) -> None:
        if which == "report":
            self._report_root_ready = True
        else:
            self._npr_root_ready = True
        if self._report_root_ready and self._npr_root_ready:
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    def _on_accept(self):
        report_path = self.report_panel.get_selected_path()
        npr_path = self.npr_panel.get_selected_path()
        if not report_path or not npr_path:
            QMessageBox.warning(
                self, "Не выбрано",
                "Выберите (или создайте) папку и для отчёта, и для npr-проекта.",
            )
            return
        self.report_selected_path = report_path
        self.npr_selected_path = npr_path
        self.accept()

    def done(self, r):
        self.report_panel.stop_threads()
        self.npr_panel.stop_threads()
        super().done(r)


class _BrowserTreeItem(QTreeWidgetItem):
    """QTreeWidgetItem с корректной числовой/датной сортировкой колонок.

    По умолчанию Qt сравнивает отображаемый текст лексикографически
    («10 МБ» < «9 МБ», что неверно) — здесь сравниваются сырые значения,
    отдельно сохранённые в data() при создании элемента: байты размера
    в data(1, Qt.UserRole), ISO-дата (уже лексикографически сортируется
    правильно) в data(0, Qt.UserRole + 2), категория варианта отчёта
    (основной/ME/остальное — см. YandexDiskBrowserDialog._variant_sort_rank)
    в data(0, Qt.UserRole + 3).
    """

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else 0
        if column == 1:
            self_size = self.data(1, Qt.UserRole)
            other_size = other.data(1, Qt.UserRole) if isinstance(other, _BrowserTreeItem) else None
            return (self_size or 0) < (other_size or 0)
        if column == 2:
            self_modified = self.data(0, Qt.UserRole + 2)
            other_modified = other.data(0, Qt.UserRole + 2) if isinstance(other, _BrowserTreeItem) else None
            return (self_modified or "") < (other_modified or "")
        # Колонка «Имя»: сначала по категории отчёта (основные, затем ME,
        # затем всё остальное — AD и т.п.), внутри категории — по алфавиту.
        self_rank = self.data(0, Qt.UserRole + 3) or 0
        other_rank = (other.data(0, Qt.UserRole + 3) or 0) if isinstance(other, _BrowserTreeItem) else 0
        if self_rank != other_rank:
            return self_rank < other_rank
        return self.text(0).lower() < other.text(0).lower()


class _ColumnVersionDelegate(QStyledItemDelegate):
    """Красит только слово «последняя» в Finder-колонках.

    Обычный foreground у QListWidgetItem меняет цвет всей строки, включая
    длинное имя папки. Делегат сохраняет штатные фон, иконку и выделение,
    после чего рисует текст сегментами и выделяет только статус версии.
    """

    def paint(self, painter, option, index):
        if not index.data(Qt.UserRole + 13):
            super().paint(painter, option, index)
            return

        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        marker = "последняя"
        marker_start = text.find(marker)
        if marker_start < 0:
            super().paint(painter, option, index)
            return

        widget = styled.widget
        style = widget.style() if widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, styled, widget)
        styled.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, widget)

        normal_color = QColor(
            "#172B4D" if option.state & QStyle.State_Selected else "#202124"
        )
        parts = (
            (text[:marker_start], normal_color),
            (marker, QColor("#188038")),
            (text[marker_start + len(marker):], normal_color),
        )
        painter.save()
        painter.setClipRect(text_rect)
        painter.setFont(styled.font)
        metrics = painter.fontMetrics()
        x = text_rect.left()
        for segment, color in parts:
            if not segment:
                continue
            painter.setPen(color)
            segment_rect = QRect(x, text_rect.top(), text_rect.right() - x + 1, text_rect.height())
            painter.drawText(
                segment_rect,
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                segment,
            )
            x += metrics.horizontalAdvance(segment)
        painter.restore()


class _DragMoveTreeWidget(QTreeWidget):
    """QTreeWidget, который не переставляет элементы сам визуально —

    фактическое перемещение (подтверждение + client.move через API)
    делает владелец диалога через переданный callback, а не Qt. Также
    поддерживает перетаскивание файлов наружу в Finder (startDrag —
    только для выделений из одних файлов, папки по-прежнему двигаются
    только штатным внутренним drag'ом через super().startDrag) и приём
    файлов/папок, перетащенных из Finder внутрь (dropEvent различает
    внешний источник по mimeData().hasUrls()).
    """

    def __init__(self, on_items_dropped, on_external_drop, on_drag_out, parent=None):
        super().__init__(parent)
        self._on_items_dropped = on_items_dropped
        self._on_external_drop = on_external_drop
        self._on_drag_out = on_drag_out

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if items and all(i.data(0, Qt.UserRole + 1) == "file" for i in items):
            urls = self._on_drag_out(items)
            if urls:
                mime = QMimeData()
                mime.setUrls(urls)
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec_(Qt.CopyAction)
                return
        super().startDrag(supportedActions)  # папки/смешанное — как раньше, без изменений

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls():
            local_paths = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
            target = self.itemAt(event.pos())
            event.acceptProposedAction()
            if local_paths and target is not None:
                self._on_external_drop(local_paths, target)
            return
        target = self.itemAt(event.pos())
        dragged = self.selectedItems()
        event.ignore()  # никогда не даём Qt самому переставлять элементы
        if target is not None and dragged:
            self._on_items_dropped(dragged, target)


class TagEditDialog(QDialog):
    """Выбор тега (цвет + короткий комментарий) для файла/папки на Диске.

    Сохраняется в custom_properties самого ресурса (см.
    YandexDiskClient.set_custom_properties) — в отличие от типа отчёта/
    алиасов (только локальный конфиг этого приложения на этой машине),
    тег виден любому пользователю с доступом к этой папке на Диске и
    никак не переносится на новые папки/версии при последующих отправках
    (custom_properties привязаны к конкретному пути ресурса).
    """

    # custom_properties Диска ограничены по суммарному размеру — короткий
    # однострочный комментарий, а не поле для длинных заметок.
    _MAX_COMMENT_LENGTH = 60

    _PALETTE = [
        ("#FF3B30", "Красный"),
        ("#FF9500", "Оранжевый"),
        ("#FFCC00", "Жёлтый"),
        ("#34C759", "Зелёный"),
        ("#007AFF", "Синий"),
        ("#5856D6", "Фиолетовый"),
        ("#8E8E93", "Серый"),
    ]

    def __init__(self, parent=None, current_color: str = None, current_comment: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Тег")
        self.setModal(True)
        self.resize(320, 210)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")
        self.selected_color: str | None = None
        self._swatch_buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        color_label = QLabel("Цвет")
        color_label.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        color_label.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(color_label)

        swatches_row = QHBoxLayout()
        swatches_row.setSpacing(8)
        for color, tooltip in self._PALETTE:
            btn = QPushButton()
            btn.setToolTip(tooltip)
            btn.setFixedSize(24, 24)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, c=color: self._select_color(c))
            swatches_row.addWidget(btn)
            self._swatch_buttons[color] = btn
        swatches_row.addStretch(1)
        layout.addLayout(swatches_row)

        comment_label = QLabel("Комментарий (необязательно)")
        comment_label.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        comment_label.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(comment_label)

        self.comment_edit = QLineEdit(current_comment or "")
        self.comment_edit.setMaxLength(self._MAX_COMMENT_LENGTH)
        self.comment_edit.setFont(QFont(".AppleSystemUIFont", 12))
        layout.addWidget(self.comment_edit)
        layout.addStretch(1)

        buttons_row = QHBoxLayout()
        remove_btn = QPushButton("Убрать тег")
        remove_btn.clicked.connect(self._on_remove)
        buttons_row.addWidget(remove_btn)
        buttons_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

        # Синхронизировать чекнутость свотчей с уже выбранным цветом —
        # делаем ПОСЛЕ создания всех кнопок (иначе _select_color не найдёт
        # ещё не созданные соседние свотчи в self._swatch_buttons).
        self._select_color(current_color)

    def _swatch_style(self, color: str, selected: bool) -> str:
        border = "3px solid #1D1D1F" if selected else "1px solid #D2D2D7"
        return f"QPushButton {{ background: {color}; border-radius: 12px; border: {border}; }}"

    def _select_color(self, color: str | None) -> None:
        self.selected_color = color
        for c, btn in self._swatch_buttons.items():
            is_selected = c == color
            btn.setChecked(is_selected)
            btn.setStyleSheet(self._swatch_style(c, is_selected))

    def _on_remove(self) -> None:
        self._select_color(None)
        self.comment_edit.clear()
        self.accept()


class YandexDiskBrowserDialog(QDialog):
    """Просмотр содержимого папки «отчеты» на Яндекс.Диске: папки и файлы,

    размер и дата изменения, скачивание/открытие файла, переименование и
    удаление (в Корзину) файлов и папок — это не пикер, выбор ничего не
    возвращает вызывающей стороне.
    """

    def __init__(
        self, token: str, report_roots: list = None, npr_root: str = None, parent=None,
        upload_status_source=None, local_draft_docx_path: Path = None,
        summary_generator=None, shared_root: str = None, tiflo_root: str = None,
        view_mode: str = None,
    ):
        super().__init__(parent)
        from src.yandex_disk_client import YandexDiskClient, YandexDiskError
        from src.report_uploader import load_uploaded_reports

        self.token = token
        self._local_draft_docx_path = local_draft_docx_path
        # ConclusionGenerator для AI-сводки в диалоге сравнения версий
        # (None — блок сводки там не показывается).
        self._summary_generator = summary_generator
        self._yandex_versions_cache = []
        self._folder_versions_thread = None
        self._compare_thread = None
        self._upload_status_source = None
        # Отдельные группы корней — отчёты, Nuendo-проекты и (если есть
        # история отправок) недавние — переключаются кнопками ниже, чтобы
        # не захламлять дерево всеми сразу (см. _switch_group). У "recent"
        # нет обычного корневого пути — это плоский список уже известных
        # remote_path (см. _populate_recent), поэтому пустой список-заглушка.
        self._groups = {"reports": list(report_roots) if report_roots else ["/отчеты"]}
        if npr_root:
            self._groups["nuendo"] = [npr_root]
        if shared_root:
            self._groups["shared"] = [shared_root]
        if tiflo_root:
            self._groups["tiflo"] = [tiflo_root]
        if load_uploaded_reports():
            self._groups["recent"] = []
        self._active_group = "reports"
        stored_view = QSettings("Beast Auto Reporter", "Beast Auto Reporter").value(
            "disk_browser/view_mode", "list"
        )
        self._view_mode = view_mode if view_mode in {"list", "icons", "columns"} else stored_view
        if self._view_mode not in {"list", "icons", "columns"}:
            self._view_mode = "list"
        self._group_buttons = {}
        self.roots = self._groups[self._active_group]
        self._root_items = []
        # Снимки уже загруженных деревьев по группам (group -> (top_level_items,
        # root_items)) — чтобы повторное переключение "Отчёты" <-> "Nuendo" не
        # запрашивало листинг с сервера заново, а просто переставляло уже
        # построенные QTreeWidgetItem обратно в дерево (см. _switch_group).
        self._group_snapshots = {}
        self._group_ready = set()  # группы, для которых текущий снимок полон (не «Загрузка…»)
        self._active_aliases = {}  # remote_path -> ключ алиаса, для активной группы
        from src.report_uploader import load_variant_overrides
        self._variant_overrides = load_variant_overrides()  # remote_path -> "ME"/"AD"/"VO"/"MAIN", вручную через ПКМ
        self._cache = {}  # remote_path -> (local_path: Path, modified: str)
        self._inflight = {}  # remote_path -> YandexDiskDownloadThread
        self._pending = {}  # remote_path -> [(callback, silent), ...]
        self._rename_thread = None
        self._delete_thread = None
        self._publish_thread = None
        self._delete_queue = []  # оставшиеся элементы при массовом удалении
        self._move_queue = []  # оставшиеся (item, target_item, target_path) при перемещении
        self._size_threads = {}  # path -> _FolderSizeThread, по запросу («Посчитать размер»)
        self._mkdir_thread = None
        self._set_tag_thread = None
        self._external_upload_thread = None
        self._expand_threads = {}  # path -> _ListFolderThread
        self._column_threads = {}  # path -> _ListFolderThread для Finder-колонок
        self._column_lists = []
        self._column_proxy_items = []
        self._folder_listing_cache = {}  # path -> list[entry], кэш на время открытого окна
        # Настоящая навигация вперёд (двойной клик на папке — как в Finder):
        # стек предыдущих видов дерева, куда вернуться по «Назад». Каждый
        # элемент — либо None (обычный вид группы), либо (path, label)
        # ранее открытой папки. _nav_current — то, что показано сейчас
        # (None — обычный вид). См. _navigate_into/_navigate_back.
        self._nav_stack = []
        self._nav_current = None
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
        # Finder-режим обычно показывает сразу 3–4 колонки по 210px. Старой
        # стартовой ширины 720px для этого не хватало: четвёртая колонка
        # визуально наезжала на третью. На обычном экране открываем окно
        # заметно шире, а на небольшом ноутбуке ограничиваемся доступной
        # областью экрана с безопасными полями по краям.
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            initial_width = min(1040, max(680, available.width() - 96))
            initial_height = min(640, max(500, available.height() - 120))
        else:
            initial_width, initial_height = 1040, 640
        self.resize(initial_width, initial_height)
        # Минимум тоже стал шире прежних 460px, чтобы панель поиска и
        # переключатели режимов не начинали теснить друг друга сразу.
        self.setMinimumSize(min(780, initial_width), min(460, initial_height))
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Файлы на Яндекс.Диске")
        title.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        title.setStyleSheet("color: #1D1D1F;")
        layout.addWidget(title)

        if len(self._groups) > 1:
            switcher_row = QHBoxLayout()
            switcher_row.setSpacing(6)
            group_labels = {
                "reports": "Отчёты", "nuendo": "Nuendo", "shared": "Shared",
                "tiflo": "Tiflo", "recent": "Недавние",
            }
            for key in ("reports", "nuendo", "shared", "tiflo", "recent"):
                if key not in self._groups:
                    continue
                btn = QPushButton(group_labels[key])
                btn.setCheckable(True)
                btn.setFont(QFont(".AppleSystemUIFont", 12))
                btn.clicked.connect(lambda _checked, k=key: self._switch_group(k))
                switcher_row.addWidget(btn)
                self._group_buttons[key] = btn
            switcher_row.addStretch()
            layout.addLayout(switcher_row)

        self.nav_bar_widget = QWidget()
        nav_row = QHBoxLayout(self.nav_bar_widget)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(6)
        self.nav_back_btn = QPushButton("‹ Назад")
        self.nav_back_btn.setFont(QFont(".AppleSystemUIFont", 12))
        self.nav_back_btn.setCursor(Qt.PointingHandCursor)
        self.nav_back_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 5px 12px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """)
        self.nav_back_btn.clicked.connect(self._navigate_back)
        nav_row.addWidget(self.nav_back_btn)
        self.nav_breadcrumb_label = QLabel("")
        self.nav_breadcrumb_label.setFont(QFont(".AppleSystemUIFont", 12, QFont.DemiBold))
        self.nav_breadcrumb_label.setStyleSheet("color: #1D1D1F;")
        nav_row.addWidget(self.nav_breadcrumb_label)
        nav_row.addStretch()
        self.nav_bar_widget.setVisible(False)
        layout.addWidget(self.nav_bar_widget)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
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
        search_row.addWidget(self.search_edit, 1)

        self._type_filter = "all"
        self.type_filter_combo = QComboBox()
        self.type_filter_combo.setFont(QFont(".AppleSystemUIFont", 12))
        self.type_filter_combo.setStyleSheet("""
            QComboBox {
                background: #F5F5F7;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 6px 10px;
                color: #1D1D1F;
            }
        """)
        for label, value in (
            ("Все", "all"), ("Только основные", "main"), ("Только ME", "me"), ("Только VO", "vo"),
            ("Только DUB", "dub"), ("Только AD", "ad"), ("Только DCP", "dcp"),
        ):
            self.type_filter_combo.addItem(label, value)
        self.type_filter_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        search_row.addWidget(self.type_filter_combo)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setFixedSize(38, 38)
        self.refresh_btn.setToolTip("Обновить список (⌘R)")
        self.refresh_btn.setAccessibleName("Обновить список")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #F1F3F4;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover { background: #E4E8EE; }
            QPushButton:pressed { background: #D8DEE8; }
            QPushButton:disabled { background: #F5F5F7; }
        """)
        self.refresh_btn.setIcon(make_icon("refresh_expressive", "#4B5563", 20))
        self.refresh_btn.setIconSize(QSize(20, 20))
        self.refresh_btn.clicked.connect(self._refresh_current_group)
        search_row.addWidget(self.refresh_btn)

        self._view_buttons = {}
        for mode, icon_name, tooltip in (
            ("list", "view_list", "Список с размером и датой"),
            ("icons", "view_grid", "Значки"),
            ("columns", "view_columns", "Колонки как в Finder"),
        ):
            button = QPushButton()
            button.setCheckable(True)
            button.setFixedSize(38, 38)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("viewIconName", icon_name)
            button.clicked.connect(lambda _checked, value=mode: self._set_view_mode(value))
            self._view_buttons[mode] = button
            search_row.addWidget(button)
        layout.addLayout(search_row)

        self.tree = _DragMoveTreeWidget(
            self._on_items_dropped_for_move, self._on_external_drop, self._on_drag_out,
        )
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Имя", "Размер", "Дата изменения"])
        self.tree.setColumnWidth(0, 400)
        self.tree.setColumnWidth(1, 70)
        # Без явного stretch последняя колонка не подстраивалась под ширину
        # окна — при чуть более узком диалоге текст «Дата изменения» (там
        # же — метки версий, которые бывают длиннее самой даты, например
        # «v2 · последняя · 07.04.2026») обрезался о правый край без следа
        # многоточия и без скролла, будто пропадал в никуда.
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(60)
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
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDrop)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)

        self.icon_view = QListWidget()
        self.icon_view.setViewMode(QListView.IconMode)
        self.icon_view.setResizeMode(QListView.Adjust)
        self.icon_view.setMovement(QListView.Static)
        self.icon_view.setWrapping(True)
        self.icon_view.setWordWrap(True)
        self.icon_view.setIconSize(QSize(46, 46))
        self.icon_view.setGridSize(QSize(132, 94))
        self.icon_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.icon_view.setSpacing(4)
        self.icon_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.icon_view.itemSelectionChanged.connect(self._on_icon_selection_changed)
        self.icon_view.itemDoubleClicked.connect(self._on_icon_item_double_clicked)
        self.icon_view.customContextMenuRequested.connect(self._show_icon_context_menu)
        self.icon_view.installEventFilter(self)
        self.icon_view.setStyleSheet("""
            QListWidget {
                background: #F7F7F8;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
                color: #1D1D1F;
                padding: 8px;
            }
            QListWidget::item { border-radius: 8px; padding: 4px; }
            QListWidget::item:selected { background: #DCEBFF; color: #1D1D1F; }
        """)

        self.column_view = QSplitter(Qt.Horizontal)
        self.column_view.setChildrenCollapsible(False)
        self.column_view.setHandleWidth(1)
        self.column_view.setStyleSheet("""
            QSplitter { background: #F8F9FA; border: 1px solid #E1E5EA; border-radius: 12px; }
            QSplitter::handle { background: #DADDE2; }
        """)

        self.browser_stack = QStackedWidget()
        self.browser_stack.addWidget(self.tree)
        self.browser_stack.addWidget(self.icon_view)
        self.browser_stack.addWidget(self.column_view)
        layout.addWidget(self.browser_stack, 1)
        # Листинг большой папки приходит одним ответом, но элементы затем
        # добавляются в QTreeWidget по одному. Прямое подключение каждого
        # rowsInserted к _refresh_icon_view пересобирало уже набранный список
        # целиком N раз (O(N²)) и заметно подвешивало приложение при открытии
        # браузера Диска. Объединяем серию model-сигналов в один проход цикла
        # событий; пока режим иконок скрыт, обновлять его вообще не требуется.
        self._icon_refresh_pending = False
        self.tree.model().rowsInserted.connect(self._schedule_icon_view_refresh)
        self.tree.model().rowsRemoved.connect(self._schedule_icon_view_refresh)
        self.tree.model().dataChanged.connect(self._schedule_icon_view_refresh)
        self.tree.model().layoutChanged.connect(self._schedule_icon_view_refresh)

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

        self._connect_upload_status_source(upload_status_source)

        try:
            self.client = YandexDiskClient(token)
        except YandexDiskError as exc:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", str(exc))
            self.client = None
            return

        self._update_group_button_styles()
        self._set_view_mode(self._view_mode, persist=False)
        self._refresh_alias_hints()
        self._populate_root()

    def _connect_upload_status_source(self, source) -> None:
        if source is None:
            return
        self._upload_status_source = source
        source.yandex_upload_target_started.connect(self._on_upload_target_started)
        source.yandex_upload_target_finished.connect(self._on_upload_target_finished)

    def _find_item_by_path(self, path: str) -> QTreeWidgetItem | None:
        def _search(node: QTreeWidgetItem):
            if node.data(0, Qt.UserRole) == path:
                return node
            for i in range(node.childCount()):
                found = _search(node.child(i))
                if found is not None:
                    return found
            return None

        for i in range(self.tree.topLevelItemCount()):
            found = _search(self.tree.topLevelItem(i))
            if found is not None:
                return found
        return None

    def _on_upload_target_started(self, path: str) -> None:
        item = self._find_item_by_path(path)
        if item is None:
            return  # ветка не загружена/не видна сейчас — бонус-подсветка, не гарантия
        if not item.text(0).endswith(" ⏳"):
            item.setText(0, f"{item.text(0)} ⏳")

    def _on_upload_target_finished(self, path: str, success: bool) -> None:
        del success  # индикатор просто убирается — успех/ошибку уже сообщает основное окно
        item = self._find_item_by_path(path)
        if item is None:
            return
        if item.text(0).endswith(" ⏳"):
            item.setText(0, item.text(0)[: -len(" ⏳")])

    def _switch_group(self, group: str) -> None:
        if group == self._active_group or group not in self._groups:
            return
        self._stash_group_tree(self._active_group)
        self._reset_navigation()
        self._active_group = group
        self.roots = self._groups[group]
        for thread in list(self._expand_threads.values()):
            _stop_thread(thread)
        self._expand_threads.clear()
        self.search_edit.clear()
        self._update_group_button_styles()
        self._refresh_alias_hints()
        if not self._restore_group_tree(group):
            self.tree.clear()
            self._root_items = []
            self._populate_root()
        if group == "recent":
            # load_uploaded_reports() уже отдаёт новые сверху, но
            # setSortingEnabled(True) тут же переsортирует по текущей
            # колонке (по умолчанию — имя) — возвращаем порядок "по дате"
            # явно, иначе список выглядел бы отсортированным как попало.
            self.tree.sortByColumn(2, Qt.DescendingOrder)
        # Фильтр по типу (см. type_filter_combo) — предпочтение пользователя,
        # не сбрасывается при переключении вкладок (в отличие от текстового
        # поиска) — переприменяем к новому содержимому дерева.
        self._reapply_filters()

    def _reset_navigation(self) -> None:
        """Сбрасывает состояние навигации вперёд (см. _navigate_into) —

        вызывается при переключении/обновлении группы, чтобы «Назад» не
        указывал на папку из уже неактивной группы.
        """
        self._nav_stack = []
        self._nav_current = None
        self._update_nav_bar()

    def _refresh_current_group(self) -> None:
        """Перезагружает текущую группу с сервера заново, минуя кэш снимков

        (см. _stash_group_tree) — раньше увидеть изменения, сделанные с
        другого устройства или сессии, можно было только закрыв и заново
        открыв весь диалог. Доступно по кнопке рядом с поиском и по ⌘R.
        """
        if self.client is None:
            return
        self._reset_navigation()
        for thread in list(self._expand_threads.values()):
            _stop_thread(thread)
        self._expand_threads.clear()
        self._group_snapshots.pop(self._active_group, None)
        self._group_ready.discard(self._active_group)
        # Явное обновление должно обходить не только снимок дерева, но и
        # листинги, которые уже успел открыть колоночный режим.
        self._folder_listing_cache.clear()
        self.search_edit.clear()
        self.tree.clear()
        self._root_items = []
        self._refresh_alias_hints()
        self._populate_root()
        self._reapply_filters()

    def _refresh_selected_folder(self) -> None:
        """То же самое, но точечно для одной папки в дереве (ПКМ →

        «Обновить») — не сбрасывает всё дерево, только перезагружает
        содержимое выбранной папки.
        """
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "dir":
            return
        path = item.data(0, Qt.UserRole)
        if path in self._expand_threads:
            return
        item.takeChildren()
        if item.isExpanded():
            self._on_item_expanded(item)
        else:
            item.setExpanded(True)

    def _stash_group_tree(self, group: str) -> None:
        """Забирает уже построенное дерево группы из QTreeWidget "на память"

        вместо уничтожения (см. _restore_group_tree) — так повторное
        переключение на неё не требует нового сетевого листинга. Если группа
        ещё не успела полностью загрузиться (висит плейсхолдер «Загрузка…»
        или идёт фоновый запрос), кэшировать нечего — обычная очистка,
        следующее переключение на неё запустит загрузку как раньше.
        """
        if group == "recent" or group not in self._group_ready or self._nav_current is not None:
            # "Недавние" не кэшируется намеренно: список меняется в течение
            # той же сессии (новые отправки отчётов), а пересборка ничего не
            # стоит — читает локальный файл отправок, без сети (см.
            # _populate_recent), в отличие от «Отчёты»/«Nuendo». Если сейчас
            # показана не обычная корневая структура группы, а результат
            # навигации вперёд (см. _navigate_into) — тоже не кэшируем: это
            # не тот вид, который нужно увидеть при следующем переключении
            # на эту группу.
            #
            # Если для группы уже был снимок (см. _restore_group_tree) — его
            # элементы сейчас как раз и есть содержимое дерева, которое
            # tree.clear() ниже уничтожит на уровне C++. Снимок обязательно
            # инвалидируем, иначе следующий _restore_group_tree попытается
            # добавить в дерево уже удалённые QTreeWidgetItem и упадёт с
            # "wrapped C/C++ object ... has been deleted".
            self._group_snapshots.pop(group, None)
            self.tree.clear()
            self._root_items = []
            return
        # Снимаем фильтр поиска и фильтр по типу перед тем, как убрать
        # элементы из дерева — иначе скрытые (setHidden) узлы вернутся
        # скрытыми и при следующем переключении на эту группу часть
        # дерева окажется невидимой, даже если фильтр к тому моменту уже
        # снят/изменён.
        for i in range(self.tree.topLevelItemCount()):
            self._unhide_all(self.tree.topLevelItem(i))
        items = []
        while self.tree.topLevelItemCount():
            items.append(self.tree.takeTopLevelItem(0))
        self._group_snapshots[group] = (items, list(self._root_items))

    def _restore_group_tree(self, group: str) -> bool:
        # Снимок — одноразовый: как только его элементы возвращаются в
        # живое дерево, запись обязательно убираем из _group_snapshots.
        # Иначе тот же объект QTreeWidgetItem окажется одновременно и в
        # дереве, и в кэше — а следующий tree.clear() (например, в
        # _stash_group_tree при переходе в режим навигации, или в
        # _refresh_current_group) уничтожит его на уровне C++, оставив в
        # кэше висячую ссылку и обрушив следующий _restore_group_tree.
        snapshot = self._group_snapshots.pop(group, None)
        if snapshot is None:
            return False
        items, root_items = snapshot
        self.tree.clear()
        for item in items:
            self.tree.addTopLevelItem(item)
        self._root_items = root_items
        return True

    def _refresh_alias_hints(self) -> None:
        """Обратный поиск путь -> ключ алиаса для активной группы —

        считается один раз при заполнении дерева (не на каждый элемент,
        чтобы не читать файл алиасов заново на каждой папке). Для
        "recent" не применяется — алиасы связывают серию с папкой, а
        элементы этой группы — сами папки конкретных отчётов, не серий.
        """
        if self._active_group == "recent":
            self._active_aliases = {}
            return
        from src.report_uploader import load_series_aliases, NPR_ALIASES_FILE, SERIES_ALIASES_FILE
        aliases_path = NPR_ALIASES_FILE if self._active_group == "nuendo" else SERIES_ALIASES_FILE
        aliases = load_series_aliases(aliases_path)
        self._active_aliases = {path: key for key, path in aliases.items()}

    def _update_group_button_styles(self) -> None:
        active_style = """
            QPushButton {
                background: #007AFF;
                color: #FFFFFF;
                border: 1px solid #007AFF;
                border-radius: 8px;
                padding: 5px 14px;
                font-weight: 600;
            }
        """
        inactive_style = """
            QPushButton {
                background: #FFFFFF;
                color: #1D1D1F;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 5px 14px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """
        for key, btn in self._group_buttons.items():
            is_active = key == self._active_group
            btn.setChecked(is_active)
            btn.setStyleSheet(active_style if is_active else inactive_style)

    def _set_view_mode(self, mode: str, persist: bool = True) -> None:
        """Переключает Finder-подобное представление содержимого папки."""
        if mode not in {"list", "icons", "columns"}:
            return
        leaving_columns = (
            hasattr(self, "column_view")
            and self.browser_stack.currentWidget() is self.column_view
            and mode != "columns"
        )
        if leaving_columns:
            self._clear_column_proxy_items()
        self._view_mode = mode
        active_style = """
            QPushButton { background: #D8E2FF; border: none; border-radius: 13px; }
            QPushButton:hover { background: #CEDAFF; }
            QPushButton:pressed { background: #BCCBFA; }
        """
        inactive_style = """
            QPushButton { background: #F1F3F4; border: none; border-radius: 12px; }
            QPushButton:hover { background: #E4E8EE; }
            QPushButton:pressed { background: #D8DEE8; }
        """
        for key, button in self._view_buttons.items():
            is_active = key == mode
            button.setChecked(is_active)
            button.setStyleSheet(active_style if is_active else inactive_style)
            icon_name = button.property("viewIconName")
            button.setIcon(make_icon(icon_name, "#274690" if is_active else "#4B5563", 21))
            button.setIconSize(QSize(21, 21))

        if mode == "icons":
            self._refresh_icon_view()
            self.browser_stack.setCurrentWidget(self.icon_view)
        elif mode == "columns":
            self.browser_stack.setCurrentWidget(self.column_view)
            self._rebuild_column_view()
        else:
            self.browser_stack.setCurrentWidget(self.tree)
            self.tree.header().setVisible(True)
            self.tree.setColumnHidden(1, False)
            self.tree.setColumnHidden(2, False)
            self.tree.setRootIsDecorated(False)
            self.tree.setIndentation(14)
            self.tree.setIconSize(QSize(18, 18))
            self.tree.setStyleSheet("""
                QTreeWidget {
                    background: #F7F7F8; border: 1px solid #E5E5EA;
                    border-radius: 8px; color: #1D1D1F;
                }
                QTreeWidget::item { padding: 2px; }
                QTreeWidget::item:selected { background: #DCEBFF; color: #1D1D1F; }
            """)
        if persist:
            QSettings("Beast Auto Reporter", "Beast Auto Reporter").setValue(
                "disk_browser/view_mode", mode
            )

    def _schedule_icon_view_refresh(self, *_args) -> None:
        if (
            not hasattr(self, "browser_stack")
            or self.browser_stack.currentWidget() is not self.icon_view
            or self._icon_refresh_pending
        ):
            return
        self._icon_refresh_pending = True
        QTimer.singleShot(0, self._flush_scheduled_icon_view_refresh)

    def _flush_scheduled_icon_view_refresh(self) -> None:
        self._icon_refresh_pending = False
        if (
            not self._closing
            and self.browser_stack.currentWidget() is self.icon_view
        ):
            self._refresh_icon_view()

    def _refresh_icon_view(self, *_args) -> None:
        if not hasattr(self, "icon_view") or getattr(self, "_refreshing_icon_view", False):
            return
        self._refreshing_icon_view = True
        try:
            selected_paths = {
                item.data(Qt.UserRole) for item in self.icon_view.selectedItems()
            }
            self.icon_view.blockSignals(True)
            self.icon_view.clear()
            for index in range(self.tree.topLevelItemCount()):
                source = self.tree.topLevelItem(index)
                if source.isHidden() or source.flags() == Qt.NoItemFlags:
                    continue
                path = source.data(0, Qt.UserRole)
                kind = source.data(0, Qt.UserRole + 1)
                if not path or kind not in {"dir", "file"}:
                    continue
                icon = self._icon_for(
                    source.text(0), kind == "dir", path,
                    source.data(0, Qt.UserRole + 4), size=46,
                )
                item = QListWidgetItem(icon, source.text(0))
                item.setData(Qt.UserRole, path)
                item.setData(Qt.UserRole + 1, kind)
                item.setToolTip(source.toolTip(0))
                item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
                self.icon_view.addItem(item)
                if path in selected_paths:
                    item.setSelected(True)
        finally:
            self.icon_view.blockSignals(False)
            self._refreshing_icon_view = False

    def _sync_tree_selection_from_icons(self) -> QTreeWidgetItem | None:
        paths = [item.data(Qt.UserRole) for item in self.icon_view.selectedItems()]
        self.tree.clearSelection()
        current = None
        for path in paths:
            source = self._find_item_by_path(path)
            if source is not None:
                source.setSelected(True)
                current = current or source
        if current is not None:
            self.tree.setCurrentItem(current)
        return current

    def _on_icon_selection_changed(self) -> None:
        current = self._sync_tree_selection_from_icons()
        if current is not None:
            self._on_selection_changed(current, None)

    def _on_icon_item_double_clicked(self, item: QListWidgetItem) -> None:
        source = self._find_item_by_path(item.data(Qt.UserRole))
        if source is not None:
            self._on_item_double_clicked(source, 0)

    def _show_icon_context_menu(self, pos) -> None:
        clicked = self.icon_view.itemAt(pos)
        if clicked is not None and clicked not in self.icon_view.selectedItems():
            self.icon_view.clearSelection()
            clicked.setSelected(True)
            self.icon_view.setCurrentItem(clicked)
        source = self._sync_tree_selection_from_icons()
        self._show_context_menu_for_item(source, self.icon_view.viewport().mapToGlobal(pos))

    @staticmethod
    def _entry_from_tree_item(item: QTreeWidgetItem) -> dict:
        return {
            "name": item.text(0),
            "type": item.data(0, Qt.UserRole + 1),
            "path": item.data(0, Qt.UserRole),
            "modified": item.data(0, Qt.UserRole + 2) or "",
            "size": item.data(1, Qt.UserRole) or 0,
            "custom_properties": {
                "beast_tag_color": item.data(0, Qt.UserRole + 4),
                "beast_tag_comment": item.data(0, Qt.UserRole + 5),
            },
        }

    def _root_column_entries(self) -> list[dict]:
        entries = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            # isHidden() здесь намеренно не учитываем: обычное дерево и
            # Finder-колонки имеют разные модели показа контейнеров. В
            # колонках фильтр применяется ниже к исходным entry и умеет
            # заглядывать в уже закэшированные дочерние папки.
            if item in self._column_proxy_items or item.flags() == Qt.NoItemFlags:
                continue
            kind = item.data(0, Qt.UserRole + 1)
            path = item.data(0, Qt.UserRole)
            if kind in {"dir", "file"} and path:
                entries.append(self._entry_from_tree_item(item))
        return entries

    def _column_entry_matches_query(
        self, entry: dict, query: str, visited: set[str] | None = None,
    ) -> bool:
        if not query:
            return True
        name = str(entry.get("name", ""))
        if query in name.lower():
            return True
        if entry.get("type") != "dir":
            return False
        path = entry.get("path") or ""
        if not path:
            return False
        visited = visited or set()
        if path in visited:
            return False
        visited.add(path)
        return any(
            self._column_entry_matches_query(child, query, visited)
            for child in self._folder_listing_cache.get(path, [])
        )

    def _column_entry_matches_type(
        self, entry: dict, parent_path: str | None,
        visited: set[str] | None = None,
    ) -> bool:
        wanted = self._type_filter
        if wanted == "all":
            return True

        name = str(entry.get("name", ""))
        path = entry.get("path") or ""
        kind = "dir" if entry.get("type") == "dir" else "file"
        if self._variant_category(name, path) == wanted:
            return True

        # Файлы внутри папки конкретного варианта относятся к варианту
        # самой папки, даже если в именах PDF/CSV/DOCX маркер ME/AD/VO
        # отсутствует.
        if kind == "file" and parent_path:
            parent_name = Path(parent_path).name
            if self._variant_category(parent_name, parent_path) == wanted:
                return True
            return False

        if kind != "dir" or not path:
            return False

        # Реальные старые папки на Диске часто называются свободно —
        # например «отчеты_GAMES_EP01_MIX_3007» или
        # «отчеты_GMS_EP1_M&E_01.08». Строгий parser их не принимает, но
        # это уже готовые папки отчётов, а не контейнеры сериал/сезон/серия.
        # Если их категория не совпала выше, под чужим фильтром их нужно
        # сразу скрыть (в частности MIX и DCP при «Только ME»).
        if self._is_column_report_folder(name, path):
            return False

        visited = visited or set()
        if path in visited:
            return False
        visited.add(path)
        children = self._folder_listing_cache.get(path)
        if children is not None:
            return any(
                self._column_entry_matches_type(child, path, visited)
                for child in children
            )

        # Не загруженную папку без признака готового отчёта считаем
        # структурным контейнером (сериал/сезон/серия) и оставляем видимой:
        # иначе до подходящего ME-отчёта внутри неё невозможно добраться.
        return True

    def _is_column_report_folder(self, name: str, path: str) -> bool:
        if self._is_report_submission_name(name):
            return True
        if self._effective_variant(name, path) is not None:
            return True
        # «отчет» и историческое множественное «отчеты»; допускаем также
        # английский префикс report(s), пробел, дефис или подчёркивание.
        return bool(
            re.match(
                r"^(?:отч[её]т(?:ы)?|reports?)(?:[\s_-]|$)",
                name.strip(),
                flags=re.IGNORECASE,
            )
        )

    def _filtered_column_entries(
        self, entries: list[dict], parent_path: str | None,
    ) -> list[dict]:
        query = self.search_edit.text().strip().lower()
        filtered = [
            entry for entry in entries
            if self._column_entry_matches_query(entry, query)
            and self._column_entry_matches_type(entry, parent_path)
        ]
        # Тот же порядок, что задаёт _BrowserTreeItem.__lt__ для колонки
        # «Имя»: основные, ME, VO, DUB, AD, DCP, прочие; внутри категории
        # — алфавит. Ответ API сам по себе такого порядка не гарантирует.
        return sorted(
            filtered,
            key=lambda entry: (
                self._variant_sort_rank(
                    str(entry.get("name", "")), str(entry.get("path", "")),
                ) if entry.get("type") == "dir" else 0,
                str(entry.get("name", "")).casefold(),
            ),
        )

    def _clear_column_proxy_items(self) -> None:
        for item in list(self._column_proxy_items):
            try:
                index = self.tree.indexOfTopLevelItem(item)
                if index >= 0:
                    self.tree.takeTopLevelItem(index)
            except RuntimeError:
                pass
        self._column_proxy_items = []

    def _clear_column_view(self) -> None:
        for thread in list(self._column_threads.values()):
            _stop_thread(thread)
        self._column_threads.clear()
        self._clear_column_proxy_items()
        while self.column_view.count():
            widget = self.column_view.widget(0)
            widget.setParent(None)
            widget.deleteLater()
        self._column_lists = []

    def _rebuild_column_view(self) -> None:
        if not hasattr(self, "column_view"):
            return
        selected_paths = []
        for column in self._column_lists:
            current = column.currentItem()
            if current is None or current.flags() == Qt.NoItemFlags:
                break
            selected_paths.append(current.data(Qt.UserRole))
        entries = self._root_column_entries()
        self._clear_column_view()
        is_loading = not entries and bool(self._expand_threads)
        current_column = self._append_column(
            entries,
            parent_path=self._nav_current[0] if self._nav_current else None,
            loading=is_loading,
        )
        last_selected_column = None
        for selected_path in selected_paths:
            selected_item = next(
                (
                    current_column.item(index)
                    for index in range(current_column.count())
                    if current_column.item(index).data(Qt.UserRole) == selected_path
                ),
                None,
            )
            if selected_item is None:
                break
            current_column.blockSignals(True)
            current_column.setCurrentItem(selected_item)
            current_column.blockSignals(False)
            last_selected_column = current_column
            if selected_item.data(Qt.UserRole + 1) != "dir":
                break
            children = self._folder_listing_cache.get(selected_path)
            if children is None:
                break
            current_column = self._append_column(children, parent_path=selected_path)
        if last_selected_column is not None:
            self._sync_tree_selection_from_column(last_selected_column)

    def _refresh_column_root_if_active(self) -> None:
        if (
            hasattr(self, "browser_stack")
            and self.browser_stack.currentWidget() is self.column_view
        ):
            self._rebuild_column_view()

    def _append_column(
        self, entries: list[dict], parent_path: str | None = None,
        loading: bool = False,
    ) -> QListWidget:
        column = QListWidget()
        column.setProperty("parentPath", parent_path or "")
        column.setMinimumWidth(210)
        column.setIconSize(QSize(20, 20))
        column.setItemDelegate(_ColumnVersionDelegate(column))
        column.setSelectionMode(QAbstractItemView.ExtendedSelection)
        column.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        column.setContextMenuPolicy(Qt.CustomContextMenu)
        column.setStyleSheet("""
            QListWidget { background: #FFFFFF; border: none; color: #202124; outline: none; padding: 6px; }
            QListWidget::item { min-height: 28px; border-radius: 9px; padding: 3px 7px; }
            QListWidget::item:hover { background: #F1F3F4; }
            QListWidget::item:selected { background: #D8E2FF; color: #172B4D; }
        """)
        if loading:
            placeholder = QListWidgetItem("Загрузка…")
            placeholder.setFlags(Qt.NoItemFlags)
            placeholder.setForeground(QColor("#80868B"))
            column.addItem(placeholder)
        else:
            visible_entries = self._filtered_column_entries(entries, parent_path)
            version_labels = self._report_version_annotations(
                [str(entry.get("name", "")) for entry in visible_entries]
            )
            for entry_index, entry in enumerate(visible_entries):
                name = entry.get("name", "")
                path = entry.get("path") or (
                    f"{parent_path.rstrip('/')}/{name}" if parent_path else name
                )
                kind = "dir" if entry.get("type") == "dir" else "file"
                display_name = Path(name).name if parent_path is None and name.startswith("/") else name
                version_info = version_labels.get(entry_index)
                if version_info is not None:
                    full_version_label, is_latest = version_info
                    # Метку ставим ПЕРЕД длинным именем: иначе на узкой
                    # Finder-колонке важное «последняя» первым обрезалось бы.
                    compact_version_label = full_version_label.split("  ·  ", 1)[0]
                    label = f"{compact_version_label}  —  {display_name}"
                else:
                    full_version_label, is_latest = "", False
                    label = display_name
                if kind == "dir":
                    label += "  ›"
                item = QListWidgetItem(
                    self._icon_for(name, kind == "dir", path, size=20), label
                )
                normalized = dict(entry)
                normalized["path"] = path
                normalized["type"] = kind
                item.setData(Qt.UserRole, path)
                item.setData(Qt.UserRole + 1, kind)
                item.setData(Qt.UserRole + 10, normalized)
                item.setData(Qt.UserRole + 12, full_version_label)
                item.setData(Qt.UserRole + 13, is_latest)
                tooltip = name
                if full_version_label:
                    tooltip += f"\nВерсия: {full_version_label}"
                item.setToolTip(tooltip)
                column.addItem(item)
        column.currentItemChanged.connect(
            lambda current, _previous, widget=column: self._on_column_selection_changed(widget, current)
        )
        column.itemDoubleClicked.connect(
            lambda item, widget=column: self._on_column_item_double_clicked(widget, item)
        )
        column.customContextMenuRequested.connect(
            lambda pos, widget=column: self._show_column_context_menu(widget, pos)
        )
        column.installEventFilter(self)
        self.column_view.addWidget(column)
        self._column_lists.append(column)
        return column

    def _remove_columns_after(self, column: QListWidget) -> int:
        try:
            index = self._column_lists.index(column)
        except ValueError:
            return -1
        while len(self._column_lists) > index + 1:
            widget = self._column_lists.pop()
            splitter_index = self.column_view.indexOf(widget)
            if splitter_index >= 0:
                widget.setParent(None)
                widget.deleteLater()
        return index

    def _sync_tree_selection_from_column(self, column: QListWidget) -> QTreeWidgetItem | None:
        self._clear_column_proxy_items()
        self.tree.clearSelection()
        current = None
        for list_item in column.selectedItems():
            path = list_item.data(Qt.UserRole)
            source = self._find_item_by_path(path)
            if source is None:
                entry = list_item.data(Qt.UserRole + 10) or {}
                source = self._make_item(entry, path)
                source.setHidden(True)
                self.tree.addTopLevelItem(source)
                self._column_proxy_items.append(source)
            source.setSelected(True)
            current = source if list_item is column.currentItem() else (current or source)
        if current is not None:
            self.tree.setCurrentItem(current)
        return current

    def _on_column_selection_changed(self, column: QListWidget, item: QListWidgetItem | None) -> None:
        index = self._remove_columns_after(column)
        if item is None or item.flags() == Qt.NoItemFlags:
            return
        source = self._sync_tree_selection_from_column(column)
        if source is not None:
            self._on_selection_changed(source, None)
        if item.data(Qt.UserRole + 1) != "dir":
            return
        path = item.data(Qt.UserRole)
        cached = self._folder_listing_cache.get(path)
        if cached is not None:
            self._append_column(cached, parent_path=path)
            return
        loading_column = self._append_column([], parent_path=path, loading=True)
        previous_thread = self._column_threads.pop(path, None)
        if previous_thread is not None:
            _stop_thread(previous_thread)
        thread = _ListFolderThread(self.client, path)
        thread.resolved.connect(
            lambda resolved_path, children, expected=path, target=loading_column:
                self._on_column_folder_loaded(expected, target, resolved_path, children)
        )
        thread.failed.connect(
            lambda failed_path, message, expected=path, target=loading_column:
                self._on_column_folder_failed(expected, target, failed_path, message)
        )
        thread.not_found.connect(
            lambda missing_path, expected=path, target=loading_column:
                self._on_column_folder_failed(expected, target, missing_path, "Папка не найдена")
        )
        self._column_threads[path] = thread
        thread.start()

    def _replace_column(self, old_column: QListWidget, entries: list[dict], parent_path: str) -> None:
        try:
            index = self._column_lists.index(old_column)
        except ValueError:
            return
        while len(self._column_lists) > index:
            widget = self._column_lists.pop()
            widget.setParent(None)
            widget.deleteLater()
        replacement = self._append_column(entries, parent_path=parent_path)
        self.column_view.insertWidget(index, replacement)
        # addWidget внутри _append_column поместил список в конец; insertWidget
        # возвращает его на место заглушки и сохраняет порядок пути.
        self._column_lists.remove(replacement)
        self._column_lists.insert(index, replacement)

    def _on_column_folder_loaded(
        self, expected_path: str, target: QListWidget,
        resolved_path: str, children: list,
    ) -> None:
        self._column_threads.pop(expected_path, None)
        if resolved_path != expected_path or target not in self._column_lists:
            return
        self._folder_listing_cache[expected_path] = list(children)
        self._replace_column(target, children, expected_path)

    def _on_column_folder_failed(
        self, expected_path: str, target: QListWidget,
        failed_path: str, message: str,
    ) -> None:
        self._column_threads.pop(expected_path, None)
        if failed_path != expected_path or target not in self._column_lists:
            return
        target.clear()
        item = QListWidgetItem(message)
        item.setFlags(Qt.NoItemFlags)
        item.setForeground(QColor("#B3261E"))
        target.addItem(item)

    def _on_column_item_double_clicked(
        self, column: QListWidget, item: QListWidgetItem,
    ) -> None:
        if item.data(Qt.UserRole + 1) == "file":
            self._sync_tree_selection_from_column(column)
            self._open_selected()

    def _show_column_context_menu(self, column: QListWidget, pos) -> None:
        clicked = column.itemAt(pos)
        if clicked is not None and clicked not in column.selectedItems():
            column.clearSelection()
            clicked.setSelected(True)
            column.setCurrentItem(clicked)
        source = self._sync_tree_selection_from_column(column)
        self._show_context_menu_for_item(source, column.viewport().mapToGlobal(pos))

    # Отчёт основного варианта (без variant в имени) визуально ничем не
    # отличается от обычной папки — своей пометки в названии у него нет
    # (в отличие от ME/AD). ME/AD получают бейдж с инициалами вместо
    # обычной иконки папки — короткий текст читается однозначно даже на
    # маленьком размере, в отличие от силуэтных иконок (нота/динамик),
    # которые на 13px трудно отличить друг от друга и от обычной папки.
    _VARIANT_ICON_STYLES = {
        "ME": ("ME", "#5856D6"),
        "MNE": ("ME", "#5856D6"),  # M&E — то же самое, что ME, другое сокращение
        "M&E": ("ME", "#5856D6"),
        "M+E": ("ME", "#5856D6"),
        "DME": ("ME", "#5856D6"),  # студийный синоним M&E (см. report_filename)
        "MDE": ("ME", "#5856D6"),
        "DM&E": ("ME", "#5856D6"),
        "AD": ("AD", "#FF9500"),
        "DVS": ("AD", "#FF9500"),  # Descriptive Video Service — синоним AD
        "VO": ("VO", "#30B0C7"),
        "VOICEOVER": ("VO", "#30B0C7"),
        "VOICE-OVER": ("VO", "#30B0C7"),
        "DUB": ("DUB", "#AF52DE"),
        "DUBBED": ("DUB", "#AF52DE"),
        "DCP": ("DCP", "#34C759"),
        "DCDM": ("DCP", "#34C759"),  # Digital Cinema Distribution Master — синоним DCP
    }

    # Цвет и короткая метка позволяют отличить формат боковым зрением —
    # одной серой контурной пиктограммы документа для PDF/CSV/DOCX было
    # недостаточно, особенно в компактном списке Finder-подобного браузера.
    _FILE_BADGE_STYLES = {
        ".pdf": ("PDF", "#E5484D"),
        ".csv": ("CSV", "#30A46C"),
        ".xlsx": ("XLS", "#30A46C"),
        ".xls": ("XLS", "#30A46C"),
        ".docx": ("W", "#3478F6"),
        ".doc": ("W", "#3478F6"),
        ".rtf": ("RTF", "#3478F6"),
        ".txt": ("TXT", "#6B7280"),
        ".npr": ("NPR", "#7C3AED"),
        ".wav": ("WAV", "#AF52DE"),
        ".mp3": ("MP3", "#AF52DE"),
        ".flac": ("FLAC", "#AF52DE"),
        ".aiff": ("AIF", "#AF52DE"),
        ".aif": ("AIF", "#AF52DE"),
        ".mxf": ("MXF", "#00A6A6"),
        ".mov": ("MOV", "#00A6A6"),
        ".mp4": ("MP4", "#00A6A6"),
        ".mkv": ("MKV", "#00A6A6"),
        ".jpg": ("IMG", "#F59E0B"),
        ".jpeg": ("IMG", "#F59E0B"),
        ".png": ("IMG", "#F59E0B"),
        ".tif": ("TIF", "#F59E0B"),
        ".tiff": ("TIF", "#F59E0B"),
        ".zip": ("ZIP", "#9A6700"),
        ".rar": ("RAR", "#9A6700"),
    }

    def _icon_for(
        self, name: str, is_dir: bool, path: str = None,
        tag_color: str = None, size: int = 18,
    ) -> QIcon:
        if is_dir:
            variant = self._effective_variant(name, path)
            style = self._VARIANT_ICON_STYLES.get((variant or "").upper())
            if style:
                badge_text, color = style
                icon = make_text_badge_icon(badge_text, color, size)
            else:
                icon = make_icon("folder", "#3478F6", size)
        else:
            suffix = Path(name).suffix.lower()
            badge = self._FILE_BADGE_STYLES.get(suffix)
            if badge:
                badge_text, color = badge
                icon = make_text_badge_icon(badge_text, color, size)
            else:
                icon = make_icon("doc", "#6B7280", size)
        if tag_color:
            icon = make_tagged_icon(icon, tag_color, size)
        return icon

    @classmethod
    def _report_variant(cls, name: str) -> str | None:
        from src.report_filename import extract_variant_loosely

        return extract_variant_loosely(name)

    def _effective_variant(self, name: str, path: str) -> str | None:
        """Автоопределение по имени, но с приоритетом у ручного назначения

        через ПКМ («Назначить тип отчёта») — на случай, когда имя папки
        нестандартное и автоопределение (_report_variant) ошибается или
        не справляется вовсе. override == "MAIN" — явно «без варианта»
        (отличается от отсутствия override вообще, которое означает «Авто»).
        """
        override = self._variant_overrides.get(path)
        if override is not None:
            return None if override == "MAIN" else override
        return self._report_variant(name)

    def _variant_category(self, name: str, path: str) -> str:
        """Категория для фильтра/сортировки: "main" (основной), "me"

        (ME/MnE/M&E), "vo" (VO), "dub" (DUB/DUBBED), "ad" (AD), "dcp" (DCP)
        или "other" (всё остальное неопознанное).

        "Основной" определяется от противного — это не отдельное
        распознавание отчёта по строгой схеме имени, а просто «нет ни
        одного известного маркера варианта» (реальные папки на Диске
        часто не совпадают с REPORT_PATTERN вообще — другой формат даты,
        нет «_sNN_eNN_» и т.п. — но по факту являются обычными основными
        отчётами). Поэтому категория никогда не None — даже для
        папок-контейнеров (эпизод/серия), у них тоже нет своего варианта,
        и они наравне с обычным отчётом попадают в «основные».
        """
        from src.report_filename import categorize_variant

        return categorize_variant(self._effective_variant(name, path))

    def _variant_sort_rank(self, name: str, path: str) -> int:
        # Порядок: основные -> ME -> VO -> DUB -> AD -> DCP -> остальное.
        return {"me": 1, "vo": 2, "dub": 3, "ad": 4, "dcp": 5, "other": 6}.get(
            self._variant_category(name, path), 0
        )

    def _make_item(self, entry: dict, fallback_path: str) -> QTreeWidgetItem:
        from src.yandex_disk_client import parse_tag

        name = entry.get("name", "")
        is_dir = entry.get("type") == "dir"
        path = entry.get("path") or fallback_path
        size = entry.get("size") or 0
        tag = parse_tag(entry.get("custom_properties"))
        tag_color, tag_comment = tag if tag else (None, "")
        item = _BrowserTreeItem([
            name,
            "" if is_dir else _format_disk_file_size(size),
            _format_disk_modified_date(entry.get("modified", "")),
        ])
        item.setIcon(0, self._icon_for(name, is_dir, path, tag_color))
        item.setData(0, Qt.UserRole, path)
        item.setData(0, Qt.UserRole + 1, "dir" if is_dir else "file")
        item.setData(0, Qt.UserRole + 2, entry.get("modified", ""))
        item.setData(0, Qt.UserRole + 3, self._variant_sort_rank(name, path) if is_dir else 0)
        item.setData(0, Qt.UserRole + 4, tag_color)
        item.setData(0, Qt.UserRole + 5, tag_comment)
        item.setData(1, Qt.UserRole, size)
        # Полное имя в tooltip на случай, если колонка «Имя» его обрезала
        # многоточием — иначе узнать полное имя можно было только через
        # «Переименовать» или расширив колонку вручную. Плюс алиас (только
        # для папок) и комментарий тега (файл или папка) — если есть.
        tooltip_lines = [name]
        if is_dir:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            alias_key = self._active_aliases.get(path)
            if alias_key:
                tooltip_lines.append(f"Алиас: «{alias_key}»")
                item.setForeground(0, QColor("#007AFF"))
        if tag_comment:
            tooltip_lines.append(f"Тег: {tag_comment}")
        item.setToolTip(0, "\n".join(tooltip_lines))
        return item

    def _populate_root(self):
        if self._active_group == "recent":
            self._populate_recent()
            self._group_ready.add(self._active_group)
            self._refresh_column_root_if_active()
            return
        if len(self.roots) == 1:
            # Единственный настроенный корень для группы — кнопка-
            # переключатель наверху («Отчёты»/«Nuendo») уже говорит, какой
            # корень сейчас открыт, поэтому не дублируем это ещё и узлом
            # верхнего уровня («/отчеты»), который приходится разворачивать
            # вручную — сразу показываем содержимое корня.
            self._load_root_contents(self.roots[0])
            return
        # Несколько настроенных корней сразу — тут показать содержимое
        # каждого сразу нельзя без потери информации, из какого корня какая
        # папка (при создании папки/сравнении версий и т.п. нужно точно
        # знать родителя) — оставляем по отдельной ветке верхнего уровня на
        # корень, лениво разворачиваемой тем же _on_item_expanded.
        for root in self.roots:
            item = self._make_item({"name": root, "type": "dir"}, root)
            self.tree.addTopLevelItem(item)
            self._root_items.append(item)
        self._group_ready.add(self._active_group)
        self._refresh_column_root_if_active()

    def _load_root_contents(self, path: str) -> None:
        if path in self._expand_threads:
            return  # уже грузится (например, быстро переключились туда-обратно)
        placeholder = QTreeWidgetItem(["Загрузка…", "", ""])
        placeholder.setFlags(Qt.NoItemFlags)
        self.tree.addTopLevelItem(placeholder)
        thread = _ListFolderThread(self.client, path)
        thread.resolved.connect(self._on_root_contents_loaded)
        thread.failed.connect(self._on_root_contents_failed)
        self._expand_threads[path] = thread
        thread.start()
        self._refresh_column_root_if_active()

    def _on_root_contents_loaded(self, path: str, children: list) -> None:
        self._expand_threads.pop(path, None)
        if self._active_group == "recent" or self.roots != [path]:
            return  # группу успели переключить, пока шёл листинг — не тот ответ
        self.tree.clear()  # убираем placeholder «Загрузка…»
        for entry in children:
            name = entry.get("name", "")
            item = self._make_item(entry, f"{path.rstrip('/')}/{name}")
            self.tree.addTopLevelItem(item)
        # _root_items намеренно остаётся пустым — элементы верхнего уровня
        # теперь обычные папки/файлы самого корня (можно переименовывать,
        # удалять, считать размер, сравнивать версии прямо на них, как и
        # раньше — просто без лишнего клика внутрь корня).
        self._group_ready.add(self._active_group)
        self._folder_listing_cache[path] = list(children)
        self._reapply_filters()

    def _on_root_contents_failed(self, path: str, message: str) -> None:
        self._expand_threads.pop(path, None)
        if self._active_group == "recent" or self.roots != [path]:
            return
        self.tree.clear()
        self._refresh_column_root_if_active()
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _populate_recent(self):
        # Плоский список уже известных remote_path последних отправок —
        # без сетевого запроса (тип/дата у самой папки узнаются лениво
        # при разворачивании, как обычная папка). НЕ добавляются в
        # _root_items — в отличие от корневых веток «Отчёты»/«Nuendo» это
        # обычные папки конкретных отчётов, их можно переименовать/удалить
        # напрямую, как и любой другой элемент дерева.
        from src.report_uploader import load_uploaded_reports
        for entry in load_uploaded_reports():
            remote_path = entry.get("remote_path")
            if not remote_path:
                continue
            name = remote_path.rstrip("/").rsplit("/", 1)[-1]
            item = self._make_item(
                {"name": name, "type": "dir", "path": remote_path, "modified": entry.get("uploaded_at", "")},
                remote_path,
            )
            self.tree.addTopLevelItem(item)

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
        thread.not_found.connect(
            lambda rpath, it=item: self._on_children_load_not_found(it, rpath)
        )
        self._expand_threads[path] = thread
        thread.start()

    def _on_children_load_not_found(self, item: QTreeWidgetItem, path: str) -> None:
        """Папка на Диске больше не существует (404) — скорее всего, удалена

        мимо приложения. Убираем и элемент из дерева, и (если это была
        запись в "Недавних") её из uploaded_reports.json — иначе мёртвая
        ссылка на несуществующую папку продолжала бы всплывать в
        "Недавних" при каждом открытии браузера и падать той же ошибкой.
        Не трогаем корневые ветки "Отчёты"/"Nuendo" — если пропал сам
        настроенный корень, это отдельная, более серьёзная ситуация.
        """
        self._expand_threads.pop(path, None)
        if item in self._root_items:
            if not self._closing:
                QMessageBox.critical(self, "Ошибка Яндекс.Диска", f"Папка «{path}» не найдена на Диске.")
            return
        from src.report_uploader import forget_uploaded_reports
        forget_uploaded_reports([{"remote_path": path}])
        try:
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
            else:
                index = self.tree.indexOfTopLevelItem(item)
                if index >= 0:
                    self.tree.takeTopLevelItem(index)
        except RuntimeError:
            pass  # элемент уже уничтожен — нечего убирать

    def _on_children_loaded(self, item: QTreeWidgetItem, path: str, children: list):
        self._expand_threads.pop(path, None)
        try:
            item.takeChildren()  # убираем placeholder «Загрузка…»
            added = []
            for entry in children:
                name = entry.get("name", "")
                child_item = self._make_item(entry, f"{path}/{name}")
                item.addChild(child_item)
                added.append((child_item, name))
            if not self._is_report_submission_name(item.text(0)):
                # Если сама разворачиваемая папка уже похожа на отчёт (это
                # папка ОДНОЙ конкретной отправки — внутри неё docx и
                # сопутствующие файлы, которые нередко наследуют дату/имя
                # исходника и потому сами по себе тоже парсятся как «отчёт»)
                # — её содержимое НЕ цепочка версий, это файлы одного и
                # того же отчёта. Помечаем версии только когда
                # разворачиваемая папка — контейнер (серия/эпизод), а не
                # сам отчёт: иначе исходники/промежуточные файлы внутри
                # получают ложные «v1»/«v2 · последняя» вместо реального
                # отчёта (см. баг — метка оказывалась на файле, а не на
                # самой папке отправки).
                self._label_report_version_siblings(added)
        except RuntimeError:
            # Qt-объект элемента уже уничтожен (папку удалили/переименовали,
            # пока шёл листинг, или диалог закрывается) — некому показывать.
            return
        # Догруженные лениво потомки должны сразу попасть под текущий
        # поиск/фильтр по типу (см. _matches_type_filter) — вызов безопасен
        # и когда оба неактивны (query="", type_filter="all" ничего не скрывает).
        self._filter_item(item, self.search_edit.text().strip().lower())

    @staticmethod
    def _is_report_submission_name(name: str) -> bool:
        from src.report_filename import parse_legacy_versioned_filename, parse_report_filename

        return parse_report_filename(name) is not None or parse_legacy_versioned_filename(name) is not None

    @staticmethod
    def _report_version_annotations(names: list[str]) -> dict[int, tuple[str, bool]]:
        """Возвращает подпись версии и признак последней для каждого имени.

        Это единый расчёт для обычного дерева и Finder-колонок: варианты
        образуют независимые цепочки, CENS/UNCENS остаются в основной, а
        номер версии определяется датой, а не порядком ответа API.
        """
        from src.report_filename import (
            categorize_variant, parse_legacy_versioned_filename, parse_report_filename,
        )

        groups: dict = {}
        for index, name in enumerate(names):
            meta = parse_report_filename(name)
            if meta is not None:
                key = (meta.series, meta.season, meta.episode, categorize_variant(meta.variant))
                groups.setdefault(key, []).append((index, meta))
                continue
            legacy = parse_legacy_versioned_filename(name)
            if legacy is not None:
                series, legacy_date = legacy
                key = ("__legacy__", series)
                groups.setdefault(key, []).append(
                    (index, SimpleNamespace(date=legacy_date, variant=None))
                )

        annotations: dict[int, tuple[str, bool]] = {}
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda pair: pair[1].date)
            variants_in_group = {meta.variant for _, meta in group}
            homogeneous_variant = None
            if len(variants_in_group) == 1:
                only_variant = next(iter(variants_in_group))
                if only_variant and categorize_variant(only_variant) != "main":
                    homogeneous_variant = only_variant
            latest_index = len(group) - 1
            for position, (entry_index, meta) in enumerate(group):
                if homogeneous_variant:
                    tag = f"{homogeneous_variant} · v{position + 1}"
                else:
                    tag = f"v{position + 1}"
                    if meta.variant:
                        tag += f" · {meta.variant}"
                is_latest = position == latest_index
                if is_latest:
                    tag += " · последняя"
                date_label = _relative_date_label(meta.date)
                text = f"{tag}  ·  {date_label}" if date_label else tag
                annotations[entry_index] = (text, is_latest)
        return annotations

    def _label_report_version_siblings(self, added: list) -> None:
        """Помечает соседние версии одного отчёта прямо в дереве («v1»,

        «v2»… «последняя») сразу при разворачивании папки — чтобы понять,
        какая версия какая, не открывая отдельно «Цепочку версий». Метка
        ставится только когда версий в группе действительно несколько —
        одиночная запись в подписи не нуждается, это не цепочка (одинаково
        для основного отчёта и для ME: у ME может быть столько же версий,
        сколько и у основного, а не обязательно одна).

        В одной папке могут соседствовать основной отчёт и MnE-вариант того
        же эпизода (variant в имени файла, например «..._MnE_2025_06_23_...»)
        — это разные цепочки версий, не версии друг друга, поэтому
        КАТЕГОРИЯ варианта (см. categorize_variant) входит в ключ
        группировки, а не сырой variant из имени. CENS/UNCENS — исключение:
        это признак цензурирования самого основного отчёта, а не отдельный
        параллельный тип поставки вроде ME/VO/AD/DUB/DCP (categorize_variant
        относит их к "main"), поэтому такая версия присоединяется к обычной
        цепочке v1/v2/v3 наравне с остальными, а не повисает одиночной
        неподписанной записью в своей собственной группе-обломке. Сырой
        variant при этом не теряется — для НЕОДНОРОДНОЙ (main) группы он
        добавляется меткой у конкретной версии («v3 · uncens»), а не
        префиксом всей цепочки, как у настоящих параллельных вариантов
        («MnE · v1»), где во всей группе один и тот же variant.

        Встречается (нечасто) и более старая схема имени без season/episode
        вообще, с версией через суффикс «_v1»/«_V2» (например
        «отчет_KP_Orlov_2026_03_27_v1») — такие имена не разбираются
        REPORT_PATTERN'ом, но всё равно группируются и нумеруются по тем же
        правилам (по сериалу, порядок — по дате из имени, а не по суффиксу).
        """
        annotations = self._report_version_annotations([name for _, name in added])
        for index, (child_item, _name) in enumerate(added):
            version_info = annotations.get(index)
            if version_info is None:
                continue
            text, is_latest = version_info
            try:
                child_item.setText(2, text)
                if is_latest:
                    child_item.setForeground(2, QColor("#34C759"))
            except RuntimeError:
                continue  # элемент уже уничтожен (папка удалена/диалог закрывается)

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
        self._reapply_filters()

    def _on_type_filter_changed(self, _index: int) -> None:
        self._type_filter = self.type_filter_combo.currentData()
        self._reapply_filters()

    def _reapply_filters(self) -> None:
        query = self.search_edit.text().strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), query)
        self._refresh_icon_view()
        self._refresh_column_root_if_active()

    def _unhide_all(self, item: QTreeWidgetItem) -> None:
        """Снимает скрытие поиском/фильтром по типу со всего поддерева —

        используется только перед кэшированием снимка группы (см.
        _stash_group_tree), чтобы скрытые узлы не "залипали" скрытыми при
        следующем восстановлении снимка, даже если к тому моменту фильтры
        уже сброшены/изменены.
        """
        item.setHidden(False)
        for i in range(item.childCount()):
            self._unhide_all(item.child(i))

    def _filter_item(self, item: QTreeWidgetItem, query: str, ancestor_type_matched: bool = False) -> bool:
        """Скрывает элементы, не подходящие под поиск И под фильтр по типу

        (основные/ME/VO/DUB/остальное). Папку с подходящим потомком
        оставляет видимой и разворачивает (потомки, которые ещё не
        подгружены лениво, фильтром не охватываются).

        Фильтр по типу «каскадируется» вниз через ancestor_type_matched —
        если папка сама подходит под фильтр, все её внутренние файлы
        показываются вместе с ней, даже если по отдельности они бы не
        совпали с фильтром (например, "main"-папка с ME-фильтром: сама
        папка не проходит, но если внутри найдётся ME-файл — он не должен
        зависеть от категории родителя, чтобы стать видимым).

        Категория (_variant_category) считается одинаково для файлов и
        папок и никогда не None — «основные» определяются от противного
        (нет известного маркера варианта), так что и обычные
        папки-контейнеры (эпизод/серия), и отдельные файлы-отчёты без
        оборачивающей папки (см. навигацию — «плоский» вид после двойного
        клика) корректно попадают под фильтр «Только основные».
        """
        if self._type_filter == "all":
            type_self_matches = True
        else:
            category = self._variant_category(item.text(0), item.data(0, Qt.UserRole))
            type_self_matches = category == self._type_filter
        type_matched_here = ancestor_type_matched or type_self_matches

        search_matches = not query or query in item.text(0).lower()

        child_matches = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), query, type_matched_here):
                child_matches = True

        visible = (search_matches and type_matched_here) or child_matches
        item.setHidden(not visible)
        if (query or self._type_filter != "all") and child_matches:
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
        kind = item.data(0, Qt.UserRole + 1)
        if kind == "file":
            self._open_selected()
        elif kind == "dir":
            self._navigate_into(item.data(0, Qt.UserRole), item.text(0))

    def _navigate_into(self, path: str, label: str) -> None:
        """Настоящая навигация вперёд (двойной клик на папке) — дерево

        заменяется содержимым выбранной папки целиком, как в Finder/
        Explorer, а не разворачивается на месте с отступом (обычное
        поведение QTreeWidget по одиночному клику/стрелке остаётся, это
        для более быстрого «зайти глубоко» в один клик).
        """
        for thread in list(self._expand_threads.values()):
            _stop_thread(thread)
        self._expand_threads.clear()
        self._nav_stack.append(self._nav_current)
        self._nav_current = (path, label)
        self._update_nav_bar()
        self.search_edit.clear()
        self._load_folder_as_flat_view(path)

    def _navigate_back(self) -> None:
        if not self._nav_stack:
            return
        previous = self._nav_stack.pop()
        self._nav_current = previous
        self._update_nav_bar()
        self.search_edit.clear()
        for thread in list(self._expand_threads.values()):
            _stop_thread(thread)
        self._expand_threads.clear()
        if previous is None:
            self.tree.clear()
            self._root_items = []
            self._populate_root()
        else:
            self._load_folder_as_flat_view(previous[0])

    def _update_nav_bar(self) -> None:
        if self._nav_current is None:
            self.nav_bar_widget.setVisible(False)
            return
        self.nav_bar_widget.setVisible(True)
        # Полный путь, а не только имя папки — по одному имени (например,
        # "e06") не всегда понятно, в какой серии/корне вы сейчас находитесь.
        self.nav_breadcrumb_label.setText(self._nav_current[0])

    def _load_folder_as_flat_view(self, path: str) -> None:
        self.tree.clear()
        self._root_items = []
        placeholder = QTreeWidgetItem(["Загрузка…", "", ""])
        placeholder.setFlags(Qt.NoItemFlags)
        self.tree.addTopLevelItem(placeholder)
        thread = _ListFolderThread(self.client, path)
        thread.resolved.connect(self._on_nav_folder_loaded)
        thread.failed.connect(self._on_nav_folder_failed)
        thread.not_found.connect(self._on_nav_folder_not_found)
        self._expand_threads[path] = thread
        thread.start()

    def _on_nav_folder_loaded(self, path: str, children: list) -> None:
        self._expand_threads.pop(path, None)
        if self._nav_current is None or self._nav_current[0] != path:
            return  # пока шёл запрос, успели уйти дальше/назад — этот ответ уже не актуален
        self.tree.clear()
        added = []
        for entry in children:
            name = entry.get("name", "")
            item = self._make_item(entry, f"{path.rstrip('/')}/{name}")
            self.tree.addTopLevelItem(item)
            added.append((item, name))
        if not self._is_report_submission_name(Path(path).name):
            self._label_report_version_siblings(added)
        self._folder_listing_cache[path] = list(children)
        self._reapply_filters()

    def _on_nav_folder_failed(self, path: str, message: str) -> None:
        self._expand_threads.pop(path, None)
        if self._nav_current is None or self._nav_current[0] != path:
            return
        self.tree.clear()
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _on_nav_folder_not_found(self, path: str) -> None:
        self._expand_threads.pop(path, None)
        if self._nav_current is None or self._nav_current[0] != path:
            return
        from src.report_uploader import forget_uploaded_reports
        forget_uploaded_reports([{"remote_path": path}])
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", f"Папка «{path}» не найдена на Диске.")
        self._navigate_back()  # папки, в которую зашли, больше нет — возвращаемся сами

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

    def _get_public_link_for_selected(self) -> None:
        """Публикует выбранный файл/папку и копирует ссылку в буфер обмена.

        Ручное действие по явному запросу пользователя для ОДНОГО элемента
        — не путать с идеей «автоматически предлагать публичную ссылку
        сразу после отправки отчёта», которую раньше отклонили из
        соображений приватности (материал по умолчанию не должен становиться
        общедоступным без явного намерения).
        """
        item = self.tree.currentItem()
        if not item:
            return
        path = item.data(0, Qt.UserRole)
        if not path or self._publish_thread is not None:
            return
        self.tree.setEnabled(False)
        self._publish_thread = _PublishThread(self.client, path)
        self._publish_thread.resolved.connect(self._on_publish_resolved)
        self._publish_thread.failed.connect(self._on_publish_failed)
        self._publish_thread.start()

    def _on_publish_resolved(self, path: str, public_url: str) -> None:
        self.tree.setEnabled(True)
        self._publish_thread = None
        QApplication.clipboard().setText(public_url)
        QMessageBox.information(
            self, "Ссылка получена",
            f"Ссылка скопирована в буфер обмена:\n\n{public_url}",
        )

    def _on_publish_failed(self, path: str, message: str) -> None:
        self.tree.setEnabled(True)
        self._publish_thread = None
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _delete_selected(self):
        # Корневые ветки («Отчёты»/«Nuendo») исключаются — их удаление
        # снесло бы саму настроенную папку на Диске целиком, слишком
        # рискованно для случайного Cmd+Delete/множественного выделения.
        items = [i for i in self.tree.selectedItems() if i not in self._root_items]
        # Если в выделении одновременно и папка, и что-то внутри неё —
        # удаление папки уже уничтожит вложенный элемент и на Диске, и в
        # дереве (Qt удаляет C++-объекты всех потомков вместе с родителем).
        # Отдельно ставить потомка в очередь на удаление после этого не на
        # чем — .parent() падает на уже удалённом C++-объекте (см. краш
        # "wrapped C/C++ object of type _BrowserTreeItem has been deleted").
        items = [
            i for i in items
            if not any(other is not i and self._is_descendant(other, i) for other in items)
        ]
        if not items:
            return
        if len(items) == 1:
            name = items[0].text(0)
            kind = "папку" if items[0].data(0, Qt.UserRole + 1) == "dir" else "файл"
            text = f"Удалить {kind} «{name}»?\nОн будет перемещён в Корзину на Яндекс.Диске."
        else:
            text = f"Удалить {len(items)} объекта(ов)?\nОни будут перемещены в Корзину на Яндекс.Диске."
        choice = QMessageBox.question(
            self, "Удалить", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        self.tree.setEnabled(False)
        self._delete_queue = list(items)
        self._delete_next_queued()

    def _delete_next_queued(self) -> None:
        if not self._delete_queue:
            self.tree.setEnabled(True)
            self._on_selection_changed(self.tree.currentItem(), None)
            return
        item = self._delete_queue.pop(0)
        try:
            remote_path = item.data(0, Qt.UserRole)
        except RuntimeError:
            # C++-объект элемента уже уничтожен — например, фоновый
            # рефреш/сворачивание ветки успело перестроить дерево, пока
            # был открыт модальный QMessageBox.critical() из предыдущего
            # шага очереди (его вложенный event loop всё ещё доставляет
            # сигналы фоновых потоков). Просто пропускаем протухший
            # элемент и переходим к следующему в очереди.
            self._delete_next_queued()
            return
        self._delete_thread = _DeleteThread(self.token, remote_path)
        self._delete_thread.finished_delete.connect(
            lambda success, message: self._on_delete_finished(item, remote_path, success, message)
        )
        self._delete_thread.start()

    def _on_delete_finished(self, item: QTreeWidgetItem, remote_path: str, success: bool, message: str):
        if success:
            try:
                parent = item.parent()
                if parent is not None:
                    parent.removeChild(item)
                else:
                    index = self.tree.indexOfTopLevelItem(item)
                    if index >= 0:
                        self.tree.takeTopLevelItem(index)
            except RuntimeError:
                pass  # C++-объект элемента уже удалён (например, вместе с родителем)
            self._cache.pop(remote_path, None)
        else:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
        self._delete_next_queued()

    @staticmethod
    def _is_descendant(ancestor: QTreeWidgetItem, node: QTreeWidgetItem) -> bool:
        """True, если node — сам ancestor или вложен в него (на любом уровне)."""
        current = node
        while current is not None:
            if current is ancestor:
                return True
            current = current.parent()
        return False

    def _on_items_dropped_for_move(self, dragged_items: list, target_item: QTreeWidgetItem) -> None:
        if target_item.data(0, Qt.UserRole + 1) != "dir":
            return  # переместить можно только В папку
        target_path = target_item.data(0, Qt.UserRole)

        valid_items = []
        for item in dragged_items:
            if item in self._root_items:
                continue  # корневые ветки не перетаскиваются — как rename/delete
            if self._is_descendant(item, target_item):
                continue  # нельзя переместить папку в саму себя/своего потомка
            if item.parent() is target_item:
                continue  # уже там — нечего делать
            valid_items.append(item)
        if not valid_items:
            return

        target_name = target_item.text(0)
        if len(valid_items) == 1:
            text = f"Переместить «{valid_items[0].text(0)}» в «{target_name}»?"
        else:
            text = f"Переместить {len(valid_items)} элемент(ов) в «{target_name}»?"
        choice = QMessageBox.question(
            self, "Переместить", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        self.tree.setEnabled(False)
        self._move_queue = [(item, target_item, target_path) for item in valid_items]
        self._move_next_queued()

    def _move_next_queued(self) -> None:
        if not self._move_queue:
            self.tree.setEnabled(True)
            self._on_selection_changed(self.tree.currentItem(), None)
            return
        item, target_item, target_path = self._move_queue.pop(0)
        from_path = item.data(0, Qt.UserRole)
        new_path = f"{target_path}/{item.text(0)}"
        self._rename_thread = _RenameThread(self.token, from_path, new_path)
        self._rename_thread.finished_rename.connect(
            lambda success, message, it=item, tgt=target_item:
                self._on_move_finished(it, tgt, success, message)
        )
        self._rename_thread.start()

    def _on_move_finished(
        self, item: QTreeWidgetItem, target_item: QTreeWidgetItem, success: bool, message: str,
    ) -> None:
        if success:
            old_path = item.data(0, Qt.UserRole)
            old_parent = item.parent()
            if old_parent is not None:
                old_parent.removeChild(item)
            else:
                index = self.tree.indexOfTopLevelItem(item)
                if index >= 0:
                    self.tree.takeTopLevelItem(index)
            item.setData(0, Qt.UserRole, message)  # message == новый полный путь
            if item.data(0, Qt.UserRole + 1) == "dir" and item.childCount() > 0:
                # Пути уже загруженных потомков вычислены от старого пути —
                # тот же приём, что при переименовании: сбрасываем, лениво
                # перезагрузятся под новым путём при следующем разворачивании.
                item.takeChildren()
            self._cache.pop(old_path, None)
            # Добавляем к цели только если она уже раскрыта/загружена —
            # иначе лишнее: подтянется само при следующем разворачивании.
            if target_item.childCount() > 0 or target_item.isExpanded():
                target_item.addChild(item)
                target_item.setExpanded(True)
        else:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
        self._move_next_queued()

    def _on_drag_out(self, items: list) -> list:
        """Готовит локальные копии выбранных файлов для перетаскивания в

        Finder — переиспользует тот же кэш, что «Сохранить как»/«Открыть»
        (см. _get_local_copy/_cached_path). Если копии ещё нет — качает
        синхронно (startDrag и так блокирующий вызов, а типичные файлы
        отчётов некрупные), с курсором ожидания. Ошибка по одному файлу
        не прерывает перетаскивание остальных.
        """
        urls = []
        for item in items:
            remote_path, modified, name = self._selected_file_info(item)
            local_path = self._cached_path(remote_path, modified)
            if local_path is None:
                local_path = Path(tempfile.gettempdir()) / name
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    self.client.download_to_file(remote_path, local_path)
                except Exception as exc:
                    logger.error("Не удалось скачать «%s» для перетаскивания в Finder: %s", remote_path, exc, exc_info=True)
                    continue
                finally:
                    QApplication.restoreOverrideCursor()
                self._cache[remote_path] = (local_path, modified)
            urls.append(QUrl.fromLocalFile(str(local_path)))
        return urls

    def _on_external_drop(self, local_paths: list, target_item: QTreeWidgetItem) -> None:
        """Приём файлов/папок, перетащенных из Finder, на папку в дереве —

        грузит их на Диск через FinderDropUploadThread (единый поток на
        всю пачку, по образцу NprUploadThread), с одним подтверждением
        перед началом, как и при внутреннем перемещении.
        """
        if target_item.data(0, Qt.UserRole + 1) != "dir":
            return
        target_path = target_item.data(0, Qt.UserRole)

        if len(local_paths) == 1:
            text = f"Загрузить «{local_paths[0].name}» в «{target_item.text(0)}»?"
        else:
            text = f"Загрузить {len(local_paths)} элемент(ов) в «{target_item.text(0)}»?"
        choice = QMessageBox.question(
            self, "Загрузить на Диск", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        self.tree.setEnabled(False)
        self._external_upload_thread = FinderDropUploadThread(self.token, local_paths, target_path)
        self._external_upload_thread.finished_upload.connect(
            lambda success, message, tgt=target_item: self._on_external_upload_finished(success, message, tgt)
        )
        self._external_upload_thread.start()

    def _on_external_upload_finished(self, success: bool, message: str, target_item: QTreeWidgetItem) -> None:
        self.tree.setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        # Обновляем цель только если она уже раскрыта/загружена — иначе
        # лишнее: подтянется само при следующем разворачивании (тот же
        # приём, что и после перемещения — см. _on_move_finished).
        if target_item.data(0, Qt.UserRole) in self._expand_threads:
            return
        if target_item.childCount() > 0 or target_item.isExpanded():
            target_item.takeChildren()
            self._on_item_expanded(target_item)

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is not None and item not in self.tree.selectedItems():
            # ПКМ по элементу вне текущего выделения — как в Finder,
            # заменяет выделение на этот элемент. ПКМ по уже выделенному
            # сохраняет мульти-выбор целиком.
            self.tree.setCurrentItem(item)
        self._show_context_menu_for_item(item, self.tree.viewport().mapToGlobal(pos))

    def _show_context_menu_for_item(self, item, global_pos):
        menu = QMenu(self)
        if item is not None:
            is_multi = len(self.tree.selectedItems()) > 1
            is_file = item.data(0, Qt.UserRole + 1) == "file"
            is_root = item in self._root_items
            if is_file and not is_multi:
                menu.addAction("Сохранить как…", self._save_selected_as)
                menu.addAction("Открыть", self._open_selected)
                menu.addSeparator()
            if not is_multi:
                # Ручное действие по запросу — не автоматическая публикация
                # после отправки отчёта (эту идею раньше отклонили).
                menu.addAction(make_icon("link", "#6B7280", 13), "Получить ссылку", self._get_public_link_for_selected)
            if not is_multi and not is_root:
                # Тег — общий для файлов и папок, не привязан к типу отчёта
                # и не переносится на новые папки/версии при отправке
                # (хранится в custom_properties именно этого ресурса).
                menu.addAction("Тег…", self._edit_tag_for_selected)
            if not is_file and not is_multi:
                menu.addAction(make_icon("refresh", "#6B7280", 13), "Обновить", self._refresh_selected_folder)
            menu.addSeparator()
            if not is_root:
                if not is_multi:
                    menu.addAction("Переименовать", self._rename_selected)
                menu.addAction("Удалить", self._delete_selected)
                menu.addSeparator()
            if not is_file and not is_multi and not is_root:
                # Не для корневых веток — рекурсивный пересчёт целого
                # настроенного корня слишком дорог по сети.
                menu.addAction("Посчитать размер", self._calculate_folder_size)
                menu.addAction("Сравнить версии…", self._compare_versions_on_selected)
                menu.addAction("Цепочка версий…", self._show_version_chain)
                menu.addSeparator()
                self._build_variant_override_menu(menu, item)
                menu.addSeparator()
        menu.addAction("Новая папка", self._create_new_folder)
        menu.exec_(global_pos)

    # Порядок специально с "Авто" первым — это сброс к автоопределению,
    # самый частый выбор для отмены случайного/устаревшего назначения.
    _VARIANT_OVERRIDE_OPTIONS = [
        (None, "Авто"),
        ("MAIN", "Основной"),
        ("ME", "ME"),
        ("AD", "AD"),
        ("VO", "VO"),
        ("DUB", "DUB"),
        ("DCP", "DCP"),
    ]

    def _build_variant_override_menu(self, menu: QMenu, item: QTreeWidgetItem) -> None:
        """Ручное назначение типа отчёта (ПКМ) — на случай, когда

        автоопределение по имени папки не справляется (нестандартное имя,
        отсутствующие/неоднозначные маркеры). Текущий выбор отмечен галочкой.
        """
        path = item.data(0, Qt.UserRole)
        current_override = self._variant_overrides.get(path)
        submenu = menu.addMenu("Назначить тип отчёта")
        for value, label in self._VARIANT_OVERRIDE_OPTIONS:
            action = submenu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current_override == value)
            action.triggered.connect(
                lambda _checked, v=value, it=item: self._set_variant_override_on_item(it, v)
            )

    def _set_variant_override_on_item(self, item: QTreeWidgetItem, variant: str | None) -> None:
        from src.report_uploader import set_variant_override

        path = item.data(0, Qt.UserRole)
        if variant is None:
            self._variant_overrides.pop(path, None)
        else:
            self._variant_overrides[path] = variant
        set_variant_override(path, variant)
        name = item.text(0)
        item.setIcon(0, self._icon_for(name, True, path))
        item.setData(0, Qt.UserRole + 3, self._variant_sort_rank(name, path))
        self._reapply_filters()

    def _edit_tag_for_selected(self) -> None:
        item = self.tree.currentItem()
        if not item or item in self._root_items:
            return
        current_color = item.data(0, Qt.UserRole + 4)
        current_comment = item.data(0, Qt.UserRole + 5) or ""
        dialog = TagEditDialog(self, current_color=current_color, current_comment=current_comment)
        if dialog.exec_() != QDialog.Accepted:
            return
        new_color = dialog.selected_color
        new_comment = dialog.comment_edit.text().strip() if new_color else ""
        if new_color == current_color and new_comment == current_comment:
            return  # ничего не изменилось — не дёргаем сеть впустую

        from src.yandex_disk_client import TAG_COLOR_PROPERTY, TAG_COMMENT_PROPERTY

        path = item.data(0, Qt.UserRole)
        properties = {
            TAG_COLOR_PROPERTY: new_color,
            TAG_COMMENT_PROPERTY: new_comment if new_color else None,
        }
        self._set_tag_thread = _SetTagThread(self.client, path, properties)
        self._set_tag_thread.finished_set_tag.connect(
            lambda success, message, it=item, color=new_color, comment=new_comment:
                self._on_tag_set_finished(it, success, message, color, comment)
        )
        self._set_tag_thread.start()

    def _on_tag_set_finished(
        self, item: QTreeWidgetItem, success: bool, message: str, color: str | None, comment: str,
    ) -> None:
        if not success:
            if not self._closing:
                QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)
            return
        try:
            name = item.text(0)
            path = item.data(0, Qt.UserRole)
            is_dir = item.data(0, Qt.UserRole + 1) == "dir"
            item.setData(0, Qt.UserRole + 4, color)
            item.setData(0, Qt.UserRole + 5, comment)
            item.setIcon(0, self._icon_for(name, is_dir, path, color))
            tooltip_lines = [name]
            alias_key = self._active_aliases.get(path) if is_dir else None
            if alias_key:
                tooltip_lines.append(f"Алиас: «{alias_key}»")
            if comment:
                tooltip_lines.append(f"Тег: {comment}")
            item.setToolTip(0, "\n".join(tooltip_lines))
        except RuntimeError:
            pass  # элемент/диалог уже уничтожен (закрыт, пока сохранялся тег)

    def _calculate_folder_size(self) -> None:
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "dir" or item in self._root_items:
            return
        path = item.data(0, Qt.UserRole)
        if path in self._size_threads:
            return  # уже считаем — не дублируем запрос
        item.setText(1, "Считаем…")
        thread = _FolderSizeThread(self.client, path)
        thread.resolved.connect(
            lambda rpath, total, it=item: self._on_folder_size_resolved(it, rpath, total)
        )
        thread.failed.connect(
            lambda rpath, message, it=item: self._on_folder_size_failed(it, rpath, message)
        )
        self._size_threads[path] = thread
        thread.start()

    def _on_folder_size_resolved(self, item: QTreeWidgetItem, path: str, total: int) -> None:
        self._size_threads.pop(path, None)
        try:
            item.setData(1, Qt.UserRole, total)
            item.setText(1, _format_disk_file_size(total))
        except RuntimeError:
            pass  # элемент/диалог уже уничтожен (закрыт, пока считалось)

    def _on_folder_size_failed(self, item: QTreeWidgetItem, path: str, message: str) -> None:
        self._size_threads.pop(path, None)
        try:
            item.setText(1, "")
        except RuntimeError:
            return
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _compare_versions_on_selected(self) -> None:
        self._fetch_versions_for_selected("picker")

    def _show_version_chain(self) -> None:
        self._fetch_versions_for_selected("chain")

    def _fetch_versions_for_selected(self, action: str) -> None:
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole + 1) != "dir" or item in self._root_items:
            return
        if self._folder_versions_thread is not None:
            return  # уже идёт запрос версий — не дублируем

        from src.report_filename import categorize_variant, extract_variant_loosely

        # Определяем папку с цепочкой версий по САМОМУ элементу (его имени и
        # remote-пути), а не по _nav_current — иначе при плоской навигации,
        # где элементы можно ещё и раскрывать вглубь, выбранная версия
        # deep внутри бралась бы относительно корневой просматриваемой
        # папки (реальный баг: вошли в серию, раскрыли e01, выбрали версию
        # — а сравнение искало «версии» среди эпизодов e01…e08 серии).
        #
        # Все папки/файлы отчётов начинаются с «отчет_» (см. _is_report_entry/
        # _find_docx_in_report_folder) — надёжный маркер «это конкретная
        # версия», в отличие от разбора имени, который не справляется с
        # составными тегами вроде «cens_AD» и нестандартными именами.
        name = item.text(0)
        item_path = item.data(0, Qt.UserRole)
        fallback_path = None
        fallback_wanted_category = None
        if name.startswith("отчет_"):
            # Выбрана сама версия отчёта — цепочка это её соседи в той же
            # папке (родитель по remote-пути), а вариант берём из имени
            # лёгким сканированием, чтобы сразу выбрать нужную цепочку.
            path = item_path.rsplit("/", 1)[0]
            wanted_category = categorize_variant(extract_variant_loosely(name))
        else:
            # Выбрана папка-контейнер (эпизод eNN, серия) — версии это её
            # прямые дети; какой вариант нужен, заранее не известно.
            path = item_path
            wanted_category = None
            # Но это может быть и сама версия отчёта, загруженная ещё ДО
            # того, как в приложении появилось соглашение об имени с
            # префиксом «отчет_» (реальный случай: «one_last_sin_s01_e01_
            # Master_uncens_2025_05_14» лежит РЯДОМ со своей современной
            # копией «отчет_one_last_sin_..._05_14» — обе представляют
            # содержимое эпизода, но без префикса поиск внутри самой папки
            # ничего не найдёт). Если поиск внутри как контейнера окажется
            # пустым/недостаточным, один раз пробуем её РОДИТЕЛЯ — как если
            # бы это была версия среди соседей (см. _on_folder_versions_resolved).
            if "/" in item_path:
                fallback_path = item_path.rsplit("/", 1)[0]
                fallback_wanted_category = categorize_variant(extract_variant_loosely(name))
        logger.info(
            "_fetch_versions_for_selected: item=%r item_path=%r -> path=%r wanted_category=%r "
            "fallback_path=%r fallback_wanted_category=%r",
            name, item_path, path, wanted_category, fallback_path, fallback_wanted_category,
        )
        self._pending_versions_action = action
        self._pending_versions_wanted_category = wanted_category
        self._pending_versions_fallback_path = fallback_path
        self._pending_versions_fallback_wanted_category = fallback_wanted_category
        self._pending_versions_fallback_tried = False
        self.tree.setEnabled(False)
        self._folder_versions_thread = YandexDiskFolderVersionsThread(self.token, path)
        self._folder_versions_thread.resolved.connect(self._on_folder_versions_resolved)
        self._folder_versions_thread.failed.connect(self._on_folder_versions_failed)
        self._folder_versions_thread.start()

    def _on_folder_versions_resolved(self, versions: list) -> None:
        self.tree.setEnabled(True)
        self._folder_versions_thread = None
        wanted_category = getattr(self, "_pending_versions_wanted_category", None)
        logger.info(
            "_on_folder_versions_resolved: сырых версий=%d %s, wanted_category=%s",
            len(versions), [v.get("label") for v in versions], wanted_category,
        )
        resolved = self._resolve_variant_chain(versions, wanted_category)
        if resolved is None:
            return  # несколько вариантов отчёта в папке, пользователь отменил выбор цепочки
        logger.info(
            "_on_folder_versions_resolved: после _resolve_variant_chain=%d %s",
            len(resolved), [v.get("label") for v in resolved],
        )
        action = getattr(self, "_pending_versions_action", "picker")
        # Цепочка сравнивает только версии на Диске между собой — нужно
        # минимум две, независимо от локального черновика. Пикер же может
        # сравнить черновик с единственной версией на Диске, если черновик
        # доступен (см. include_current_draft).
        min_needed = 2 if (action == "chain" or not self._local_draft_docx_path) else 1
        fallback_path = getattr(self, "_pending_versions_fallback_path", None)
        fallback_tried = getattr(self, "_pending_versions_fallback_tried", True)
        if len(resolved) < min_needed and fallback_path and not fallback_tried:
            logger.info(
                "_on_folder_versions_resolved: недостаточно версий в контейнере, "
                "пробуем родителя как fallback: %r", fallback_path,
            )
            self._pending_versions_fallback_tried = True
            self._pending_versions_wanted_category = getattr(
                self, "_pending_versions_fallback_wanted_category", None
            )
            self.tree.setEnabled(False)
            self._folder_versions_thread = YandexDiskFolderVersionsThread(self.token, fallback_path)
            self._folder_versions_thread.resolved.connect(self._on_folder_versions_resolved)
            self._folder_versions_thread.failed.connect(self._on_folder_versions_failed)
            self._folder_versions_thread.start()
            return
        self._yandex_versions_cache = resolved
        if len(resolved) < min_needed:
            QMessageBox.information(self, "Сравнение версий", "Недостаточно версий для сравнения.")
            return
        if action == "chain":
            self._open_version_chain_dialog()
        else:
            self._open_version_picker_dialog()

    _VARIANT_CHAIN_LABELS = {
        "main": "Основной", "me": "ME", "vo": "VO", "dub": "DUB", "ad": "AD", "dcp": "DCP",
        "other": "Другое",
    }
    _VARIANT_CHAIN_ORDER = ["main", "me", "vo", "dub", "ad", "dcp", "other"]

    def _resolve_variant_chain(self, versions: list, wanted_category: str = None) -> list:
        """Один эпизод нередко лежит в одной папке вместе с ME-версией

        (variant в имени файла, например «..._MnE_2025_06_23_rus») — это не версии
        друг друга, а параллельные, не связанные отчёты (см. также
        _label_report_version_siblings, где та же логика разделяет их в
        дереве). Список версий из YandexDiskFolderVersionsThread/
        list_report_versions приходит НЕотфильтрованным по variant
        (строгая фильтрация внутри list_report_versions требует, чтобы
        каждый кандидат совпал с REPORT_PATTERN целиком, и молча
        выбрасывала версии с нестандартным именем — см.
        group_versions_by_category), поэтому здесь, если обнаружено
        больше одного варианта, разбираемся сами.

        Если wanted_category передан (правый клик пришёлся на саму
        конкретную версию с уже известным вариантом — см.
        _fetch_versions_for_selected) и такая категория среди найденных
        есть, выбираем её автоматически, без вопроса — пользователь уже
        неявно выбрал вариант, кликнув по конкретному файлу. Иначе (выбрана
        обычная папка-контейнер, вариант заранее не известен) спрашиваем
        явно, вместо того чтобы молча сравнивать разнородные отчёты между собой.

        Группировка — через тот же _variant_category, что и иконки/фильтр
        в дереве (лёгкое сканирование имени, не строгий REPORT_PATTERN):
        так составной тег вроде «..._cens_AD_...» всё равно находит «AD» по
        границам слова и не путается ни с «ME», ни с обычными версиями, а
        версии без единого известного маркера (нестандартное имя без
        "_sNN_eNN_" вообще — обычное дело у многих серий) остаются одной
        группой «Основной», а не рассыпаются на отдельные «неопознанные»
        подгруппы по одному файлу — раньше именно так папки с несколькими
        по-разному названными, но насамом деле однотипными отчётами
        внезапно требовали выбора варианта и после выбора показывали
        «Недостаточно версий», хотя версий с избытком.
        Возвращает None, если пользователь отменил выбор.
        """
        from src.report_uploader import group_versions_by_category

        groups = group_versions_by_category(versions, self._variant_overrides)

        if len(groups) <= 1:
            return versions  # один вариант — фильтровать нечего
        if wanted_category is not None and wanted_category in groups:
            return groups[wanted_category]

        options = [self._VARIANT_CHAIN_LABELS[key] for key in self._VARIANT_CHAIN_ORDER if key in groups]
        choice, ok = QInputDialog.getItem(
            self, "Несколько вариантов отчёта",
            "В этой папке несколько вариантов отчёта (например, обычный и ME).\n"
            "С какой цепочкой версий работать?",
            options, 0, False,
        )
        if not ok:
            return None
        chosen_key = next(key for key, label in self._VARIANT_CHAIN_LABELS.items() if label == choice)
        return groups[chosen_key]

    def _on_folder_versions_failed(self, message: str) -> None:
        self.tree.setEnabled(True)
        self._folder_versions_thread = None
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _open_version_picker_dialog(self) -> None:
        dialog = YandexVersionPickerDialog(
            self._yandex_versions_cache, parent=self,
            include_current_draft=bool(self._local_draft_docx_path),
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        self._run_version_compare(
            dialog.selection_old, dialog.selection_new,
            dialog.selection_old_label, dialog.selection_new_label,
        )

    def _open_version_chain_dialog(self) -> None:
        dialog = VersionChainDialog(self._yandex_versions_cache, parent=self)
        dialog.compare_requested.connect(self._run_version_compare)
        dialog.exec_()

    def _run_version_compare(self, old_path: str, new_path: str, old_label: str, new_label: str) -> None:
        self.tree.setEnabled(False)
        self._compare_thread = YandexDiskCompareThread(
            self.token, old_path, new_path, self._local_draft_docx_path,
        )
        self._compare_thread.resolved.connect(
            lambda comparison, ol=old_label, nl=new_label: self._on_version_compare_ready(comparison, ol, nl)
        )
        self._compare_thread.failed.connect(self._on_version_compare_failed)
        self._compare_thread.start()

    def _on_version_compare_ready(self, comparison, old_label: str, new_label: str) -> None:
        self.tree.setEnabled(True)
        self._compare_thread = None
        if comparison is None:
            QMessageBox.warning(self, "Сравнение", "Не удалось прочитать выбранную версию отчёта.")
            return
        dialog = YandexUploadDiffDialog(
            comparison, parent=self, upload_mode=False,
            old_label=old_label, new_label=new_label, allow_pick_another=True,
            summary_generator=self._summary_generator,
        )
        result_code = dialog.exec_()
        if result_code == YandexUploadDiffDialog.PICK_ANOTHER:
            self._open_version_picker_dialog()

    def _on_version_compare_failed(self, message: str) -> None:
        self.tree.setEnabled(True)
        self._compare_thread = None
        if not self._closing:
            QMessageBox.critical(self, "Ошибка Яндекс.Диска", message)

    def _create_new_folder(self):
        selected = self.tree.currentItem()
        if selected is not None and selected.data(0, Qt.UserRole + 1) == "file":
            parent_item = selected.parent()
        elif selected is not None:
            parent_item = selected
        elif self._root_items:
            parent_item = self._root_items[0]
        else:
            # Нет узла верхнего уровня, куда добавить папку по умолчанию —
            # группа "Недавние" (нет единого корня) или единственный корень
            # группы, показанный без узла-обёртки (см. _load_root_contents).
            # Без явного выделения создавать здесь некуда.
            return
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
        column_lists = getattr(self, "_column_lists", [])
        if obj in (self.tree, getattr(self, "icon_view", None), *column_lists) and event.type() == QEvent.KeyPress:
            if obj is getattr(self, "icon_view", None):
                self._sync_tree_selection_from_icons()
            elif obj in column_lists:
                self._sync_tree_selection_from_column(obj)
            if event.key() == Qt.Key_Space:
                remote_path, _ = self._selected_file()
                if remote_path is not None:
                    self._quicklook_selected()
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self.tree.currentItem() is not None and len(self.tree.selectedItems()) <= 1:
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
            if event.key() == Qt.Key_R and event.modifiers() & Qt.ControlModifier:
                self._refresh_current_group()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        self._closing = True
        for thread in list(self._inflight.values()):
            _stop_thread(thread)
        for thread in list(self._expand_threads.values()):
            _stop_thread(thread)
        for thread in list(self._size_threads.values()):
            _stop_thread(thread)
        for thread in list(self._column_threads.values()):
            _stop_thread(thread)
        self._column_threads.clear()
        _stop_thread(self._rename_thread)
        _stop_thread(self._delete_thread)
        _stop_thread(self._publish_thread)
        _stop_thread(self._mkdir_thread)
        _stop_thread(self._folder_versions_thread)
        _stop_thread(self._compare_thread)
        _stop_thread(self._set_tag_thread)
        _stop_thread(self._external_upload_thread)
        self._edit_sync.stop_all()
        if self._upload_status_source is not None:
            try:
                self._upload_status_source.yandex_upload_target_started.disconnect(self._on_upload_target_started)
                self._upload_status_source.yandex_upload_target_finished.disconnect(self._on_upload_target_finished)
            except TypeError:
                pass  # уже отключено
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
            QPushButton:hover { background: #FFF1F0; }
        """)
        remove_btn.clicked.connect(lambda: self._remove_alias(key))
        layout.addWidget(remove_btn)

        return row

    def _remove_alias(self, key: str) -> None:
        from src.report_uploader import forget_series_alias

        forget_series_alias(key, self._aliases_path)
        self._refresh()
