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
import time

from PyQt5.QtCore import QCoreApplication, pyqtSignal

from src.yandex_ui import threads as threads_module
from src.yandex_ui.threads import _KeepAliveThread


def _get_app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


def _run_until(condition, timeout_seconds=10):
    deadline = time.monotonic() + timeout_seconds
    while not condition() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)


class _EmitAndReturnThread(_KeepAliveThread):
    """Как _YandexWorkerThread: эмит финального сигнала — последняя строка run()."""

    done_sig = pyqtSignal(str)

    def run(self):
        self.done_sig.emit("result")


def test_owner_can_drop_last_reference_in_completion_slot():
    _get_app()
    holder = {}
    results = []

    def on_done(message):
        holder.pop("thread", None)  # последняя ссылка владельца — как в реальных слотах
        results.append(message)

    thread = _EmitAndReturnThread()
    thread.done_sig.connect(on_done)
    holder["thread"] = thread
    thread.start()
    assert thread in threads_module._RUNNING_THREADS

    del thread  # у теста тоже не должно остаться ссылки — держит только реестр
    _run_until(lambda: bool(results), timeout_seconds=5)

    assert results == ["result"]


def test_registry_drains_after_threads_finish():
    _get_app()
    finished_count = [0]
    total = 20
    holder = {}

    def on_done(key):
        holder.pop(key, None)
        finished_count[0] += 1

    for i in range(total):
        thread = _EmitAndReturnThread()
        thread.done_sig.connect(lambda _message, k=i: on_done(k))
        holder[i] = thread
        thread.start()

    _run_until(lambda: finished_count[0] >= total)

    assert finished_count[0] == total
    # Реестр не течёт: слот на finished снял регистрацию каждого потока.
    # Дожимаем возможные хвосты queued-доставки finished.
    _run_until(lambda: not threads_module._RUNNING_THREADS)
    assert not threads_module._RUNNING_THREADS
