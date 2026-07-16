"""Тесты жизненного цикла фоновых QThread (реестр _KeepAliveThread).

Регрессия краша "QThread: Destroyed while thread is still running":
финальный сигнал потока эмитится из run() ДО фактической остановки
потока, а слоты-владельцы (например, _on_children_loaded в диалогах)
сбрасывают свою — последнюю — ссылку на поток прямо в этом слоте.
Без реестра это уничтожало C++-объект работающего QThread и валило
процесс через abort(). Здесь проверяем, что реестр держит поток до
конца и опустошается после завершения.
"""

import sys

from PyQt5.QtCore import QCoreApplication, QTimer, pyqtSignal

from src.yandex_ui import threads as threads_module
from src.yandex_ui.threads import _KeepAliveThread


def _get_app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


class _EmitAndReturnThread(_KeepAliveThread):
    """Как _YandexWorkerThread: эмит финального сигнала — последняя строка run()."""

    done_sig = pyqtSignal(str)

    def run(self):
        self.done_sig.emit("result")


def test_owner_can_drop_last_reference_in_completion_slot():
    app = _get_app()
    holder = {}
    results = []

    def on_done(message):
        holder.pop("thread", None)  # последняя ссылка владельца — как в реальных слотах
        results.append(message)
        QTimer.singleShot(0, app.quit)

    thread = _EmitAndReturnThread()
    thread.done_sig.connect(on_done)
    holder["thread"] = thread
    thread.start()
    assert thread in threads_module._RUNNING_THREADS

    del thread  # у теста тоже не должно остаться ссылки — держит только реестр
    QTimer.singleShot(5000, app.quit)  # страховка от зависания теста
    app.exec_()

    assert results == ["result"]


def test_registry_drains_after_threads_finish():
    app = _get_app()
    finished_count = [0]
    total = 20
    holder = {}

    def on_done(key):
        holder.pop(key, None)
        finished_count[0] += 1
        if finished_count[0] >= total:
            QTimer.singleShot(0, app.quit)

    for i in range(total):
        thread = _EmitAndReturnThread()
        thread.done_sig.connect(lambda _message, k=i: on_done(k))
        holder[i] = thread
        thread.start()

    QTimer.singleShot(10000, app.quit)  # страховка от зависания теста
    app.exec_()

    assert finished_count[0] == total
    # Реестр не течёт: слот на finished снял регистрацию каждого потока.
    # Дожимаем возможные хвосты queued-доставки finished.
    for _ in range(50):
        if not threads_module._RUNNING_THREADS:
            break
        QTimer.singleShot(10, app.quit)
        app.exec_()
    assert not threads_module._RUNNING_THREADS
