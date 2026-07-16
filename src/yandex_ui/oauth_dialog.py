"""Диалог входа в Яндекс через OAuth Device Flow — см. src/yandex_oauth.py

для деталей самого протокола. UI-часть: показывает пользователю короткий
код и открывает страницу подтверждения в браузере, пока фоновый поток
поллит эндпоинт токена.
"""

from __future__ import annotations

import logging
import webbrowser

from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class YandexOAuthThread(QThread):
    """Запрашивает device_code и поллит токен в фоне — весь HTTP/сон здесь,

    GUI-поток свободен всё время ожидания подтверждения пользователем.
    """

    code_ready = pyqtSignal(str, str)  # (user_code, verification_url)
    succeeded = pyqtSignal(str)  # access_token
    failed = pyqtSignal(str)  # error message
    expired = pyqtSignal()  # код истёк раньше, чем пользователь подтвердил

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def stop(self) -> None:
        # Проверяется между попытками поллинга (см. run()) — polling делает
        # шаги по interval секунд, не блокирует остановку надолго.
        self._stop_requested = True

    def run(self):
        from src.yandex_oauth import YandexOAuthError, poll_for_token, request_device_code

        try:
            info = request_device_code()
        except YandexOAuthError as exc:
            self.failed.emit(str(exc))
            return

        self.code_ready.emit(info.user_code, info.verification_url)

        interval = max(info.interval, 1)
        elapsed = 0
        while elapsed < info.expires_in:
            if self._stop_requested:
                return
            self.sleep(interval)
            elapsed += interval
            if self._stop_requested:
                return

            try:
                result = poll_for_token(info.device_code)
            except YandexOAuthError as exc:
                self.failed.emit(str(exc))
                return

            if result.status == "success":
                self.succeeded.emit(result.access_token)
                return
            if result.status == "slow_down":
                interval += 5
                continue
            if result.status == "pending":
                continue
            self.failed.emit(result.error_message or "Не удалось войти")
            return

        self.expired.emit()


class YandexOAuthDialog(QDialog):
    """Показывает код подтверждения и ждёт, пока пользователь введёт его

    на странице Яндекса (открывается автоматически в системном браузере).
    После exec_() с результатом Accepted — access_token лежит в self.token.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.token: str = None
        self._browser_opened = False

        self.setWindowTitle("Вход в Яндекс")
        self.setModal(True)
        self.setFixedWidth(360)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        self.status_label = QLabel("Получаем код подтверждения…")
        self.status_label.setFont(QFont(".AppleSystemUIFont", 12))
        self.status_label.setStyleSheet("color: #1D1D1F;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.code_label = QLabel("")
        code_font = QFont("Menlo", 26, QFont.DemiBold)
        # Моноширинный шрифт с интервалом между буквами — код нужно вручную
        # перепечатать на странице Яндекса, а пропорциональный системный
        # шрифт визуально путает похожие символы (0/O, 1/l, b/6) и слипает
        # соседние буквы без интервала. setLetterSpacing не меняет сам текст
        # (Qt.TextSelectableByMouse copy/paste по-прежнему даёт исходный код).
        code_font.setLetterSpacing(QFont.PercentageSpacing, 160)
        self.code_label.setFont(code_font)
        self.code_label.setStyleSheet("color: #007AFF; background: #F0F6FF; "
                                       "border-radius: 8px; padding: 10px;")
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_label.setVisible(False)
        layout.addWidget(self.code_label)

        self.hint_label = QLabel("")
        self.hint_label.setFont(QFont(".AppleSystemUIFont", 10))
        self.hint_label.setStyleSheet("color: #86868B;")
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(False)
        layout.addWidget(self.hint_label)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        self.reopen_browser_btn = QPushButton("Открыть браузер ещё раз")
        self.reopen_browser_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                color: #007AFF;
                border: 1px solid #D2D2D7;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #F5F5F7; }
        """)
        self.reopen_browser_btn.clicked.connect(self._open_browser)
        self.reopen_browser_btn.setVisible(False)
        buttons_row.addWidget(self.reopen_browser_btn)
        buttons_row.addStretch()

        button_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        button_box.button(QDialogButtonBox.Cancel).setText("Отмена")
        button_box.rejected.connect(self.reject)
        buttons_row.addWidget(button_box)
        layout.addLayout(buttons_row)

        self._verification_url = None
        self._thread = YandexOAuthThread()
        self._thread.code_ready.connect(self._on_code_ready)
        self._thread.succeeded.connect(self._on_succeeded)
        self._thread.failed.connect(self._on_failed)
        self._thread.expired.connect(self._on_expired)
        self._thread.start()

    def _on_code_ready(self, user_code: str, verification_url: str):
        self._verification_url = verification_url
        self.status_label.setText("Введите этот код на странице Яндекса:")
        self.code_label.setText(user_code)
        self.code_label.setVisible(True)
        self.hint_label.setText(
            f"Открылся браузер ({verification_url}). Если страница не открылась "
            f"сама — нажмите «Открыть браузер ещё раз»."
        )
        self.hint_label.setVisible(True)
        self.reopen_browser_btn.setVisible(True)
        self._open_browser()

    def _open_browser(self):
        if not self._verification_url:
            return
        try:
            webbrowser.open(self._verification_url)
        except Exception as exc:
            logger.warning(f"Не удалось открыть браузер для входа в Яндекс: {exc}")

    def _on_succeeded(self, token: str):
        self.token = token
        self.status_label.setText("Готово — вход выполнен.")
        QTimer.singleShot(400, self.accept)

    def _on_failed(self, message: str):
        self.status_label.setText(f"Не удалось войти: {message}")
        self.code_label.setVisible(False)

    def _on_expired(self):
        self.status_label.setText("Код истёк — попробуйте ещё раз.")
        self.code_label.setVisible(False)

    def done(self, r):
        # Cancel/крестик — останавливаем поллинг, иначе фоновый поток
        # переживёт диалог (PyQt валит процесс на "Destroyed while running").
        self._thread.stop()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait()
        super().done(r)
