"""Гарантирует, что единственный на процесс Qt-синглтон — полноценный
QApplication, а не QCoreApplication.

QApplication.instance()/QCoreApplication.instance() делят один и тот же
синглтон-указатель — если какой-то тест первым создаст лёгкий
QCoreApplication (как tests/test_yandex_threads.py — без GUI, чтобы не
требовать платформенный плагин), любой последующий тест, которому нужны
QWidget-диалоги (offscreen-платформа), получит через `.instance() or
QApplication(...)` тот же QCoreApplication вместо QApplication — Qt падает
с Fatal Python error: Aborted при первом же создании QWidget. Создаём
настоящий QApplication здесь, до сбора и запуска любых тестов, чтобы
порядок файлов тестов не имел значения.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

# Ссылка на уровне модуля обязательна: PyQt5 удаляет C++-объект QApplication,
# как только последняя Python-ссылка на него исчезает (даже при живом Qt-
# синглтоне) — если бы это была локальная переменная внутри pytest_configure,
# обёртка уничтожалась бы сразу после возврата из хука, и следующий тест,
# создающий QWidget, падал бы с "QWidget: Cannot create a QWidget without
# QApplication" (сам синглтон-указатель на C++ стороне уже мёртв).
_app = None


def pytest_configure(config):
    global _app
    _app = QApplication.instance() or QApplication([])
