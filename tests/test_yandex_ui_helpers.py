from unittest.mock import MagicMock

from src.yandex_ui.helpers import _stop_thread


def test_stop_thread_ignores_none():
    _stop_thread(None)  # не должно поднимать исключение


def test_stop_thread_skips_already_finished_thread():
    thread = MagicMock()
    thread.isRunning.return_value = False

    _stop_thread(thread)

    thread.wait.assert_not_called()
    thread.terminate.assert_not_called()


def test_stop_thread_waits_for_running_thread():
    thread = MagicMock()
    thread.isRunning.return_value = True
    thread.wait.return_value = True

    _stop_thread(thread)

    thread.wait.assert_called_once_with(3000)
    thread.terminate.assert_not_called()


def test_stop_thread_terminates_if_wait_times_out():
    thread = MagicMock()
    thread.isRunning.return_value = True
    thread.wait.return_value = False

    _stop_thread(thread)

    thread.terminate.assert_called_once()
    assert thread.wait.call_count == 2


def test_stop_thread_swallows_exceptions_from_a_deleted_qt_wrapper():
    # closeEvent-цепочки вызывают _stop_thread подряд для нескольких
    # потоков — если бы один вызов поднял исключение (например, C++-
    # обёртка Qt-объекта уже уничтожена), последующие потоки в цепочке
    # остались бы неостановленными именно в момент закрытия окна.
    broken_thread = MagicMock()
    broken_thread.isRunning.side_effect = RuntimeError("wrapped C/C++ object has been deleted")

    _stop_thread(broken_thread)  # не должно поднимать исключение


def test_stop_thread_failure_does_not_block_subsequent_calls_in_a_chain():
    broken_thread = MagicMock()
    broken_thread.isRunning.side_effect = RuntimeError("boom")

    healthy_thread = MagicMock()
    healthy_thread.isRunning.return_value = True
    healthy_thread.wait.return_value = True

    _stop_thread(broken_thread)
    _stop_thread(healthy_thread)  # должен всё равно выполниться

    healthy_thread.wait.assert_called_once_with(3000)
