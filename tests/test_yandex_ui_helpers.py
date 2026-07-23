from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.yandex_ui.helpers import _relative_date_label, _relative_time_label, _stop_thread


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


def test_relative_date_label_none():
    assert _relative_date_label(None) == ""


def test_relative_date_label_today():
    assert _relative_date_label(date.today()) == "сегодня"


def test_relative_date_label_yesterday():
    assert _relative_date_label(date.today() - timedelta(days=1)) == "вчера"


def test_relative_date_label_few_days():
    assert _relative_date_label(date.today() - timedelta(days=3)) == "3 дн. назад"


def test_relative_date_label_weeks():
    assert _relative_date_label(date.today() - timedelta(days=14)) == "2 нед. назад"


def test_relative_date_label_old_date_falls_back_to_absolute():
    d = date.today() - timedelta(days=45)
    assert _relative_date_label(d) == d.strftime("%d.%m.%Y")


def test_relative_date_label_future_date_falls_back_to_absolute():
    d = date.today() + timedelta(days=5)
    assert _relative_date_label(d) == d.strftime("%d.%m.%Y")


def test_relative_time_label_none_or_empty():
    assert _relative_time_label(None) == ""
    assert _relative_time_label("") == ""


def test_relative_time_label_just_now():
    now = datetime.now(timezone.utc)
    assert _relative_time_label(now.isoformat()) == "только что"


def test_relative_time_label_minutes_ago():
    when = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _relative_time_label(when.isoformat()) == "5 мин. назад"


def test_relative_time_label_hours_ago():
    when = datetime.now(timezone.utc) - timedelta(hours=2)
    assert _relative_time_label(when.isoformat()) == "2 ч. назад"


def test_relative_time_label_older_than_a_day_falls_back_to_relative_date_label():
    when = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    assert _relative_time_label(when.isoformat()) == "вчера"


def test_relative_time_label_naive_datetime_treated_as_utc():
    when = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(tzinfo=None)
    assert _relative_time_label(when.isoformat()) == "10 мин. назад"


def test_relative_time_label_malformed_returns_empty():
    assert _relative_time_label("не дата") == ""
