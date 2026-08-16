"""Тесты хранения токена в Связке ключей macOS (src/secret_store.py).

subprocess.run подменяется — реальная Связка ключей в тестах не трогается.
Коды возврата и форма вывода утилиты security проверены вручную:
add (через `security -i`) -> 0, find -w -> токен + \n, «записи нет» -> 44.
"""

import subprocess
from types import SimpleNamespace

import pytest

from src import secret_store


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    secret_store.invalidate_cache()
    # Тесты пишут ожидания под macOS-ветку независимо от платформы CI.
    monkeypatch.setattr(secret_store.sys, "platform", "darwin")
    yield
    secret_store.invalidate_cache()


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_save_token_uses_interactive_mode_and_caches(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert secret_store.save_token("y0_secret-token") is True

    cmd, kwargs = calls[0]
    # Токен передаётся через stdin интерактивного режима, а не в argv —
    # argv любого процесса виден через `ps` другим процессам пользователя.
    assert cmd == ["security", "-i"]
    assert "y0_secret-token" in kwargs["input"]
    assert "add-generic-password" in kwargs["input"]

    # После сохранения чтение идёт из кэша — без нового субпроцесса.
    assert secret_store.load_token() == "y0_secret-token"
    assert len(calls) == 1


def test_load_token_strips_trailing_newline(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(0, stdout="tok123\n"))
    assert secret_store.load_token() == "tok123"


def test_load_token_returns_empty_when_not_stored(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44, stderr="not found"))
    assert secret_store.load_token() == ""


def test_load_token_returns_none_on_error(monkeypatch):
    # None — «Связка недоступна»: вызывающий код откатывается на settings.json.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(1, stderr="boom"))
    assert secret_store.load_token() is None


def test_load_token_returns_none_off_macos(monkeypatch):
    monkeypatch.setattr(secret_store.sys, "platform", "linux")
    assert secret_store.load_token() is None
    assert secret_store.save_token("x") is False


def test_save_empty_token_deletes_keychain_entry(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert secret_store.save_token("") is True
    assert calls[0][:2] == ["security", "delete-generic-password"]
    assert secret_store.load_token() == ""  # кэш обновлён, субпроцессов больше нет
    assert len(calls) == 1


def test_delete_token_treats_missing_entry_as_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44))
    assert secret_store.delete_token() is True


def test_save_token_failure_returns_false_and_does_not_cache(monkeypatch):
    results = [_result(1, stderr="denied"), _result(0, stdout="old\n")]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: results.pop(0))

    assert secret_store.save_token("new") is False
    # Кэш не должен «поверить» в несохранённый токен — следующее чтение
    # идёт в реальную Связку (второй fake-результат).
    assert secret_store.load_token() == "old"


def test_quote_for_security_escapes_quotes_and_backslashes():
    assert secret_store._quote_for_security('a"b\\c') == '"a\\"b\\\\c"'


def test_groq_key_and_yandex_token_are_cached_independently(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert secret_store.save_token("yandex-tok") is True
    assert secret_store.save_groq_key("gsk_groq-key") is True

    # Оба читаются из кэша, без новых субпроцессов, и не путают друг друга.
    assert secret_store.load_token() == "yandex-tok"
    assert secret_store.load_groq_key() == "gsk_groq-key"
    assert len(calls) == 2


def test_load_groq_key_returns_empty_when_not_stored(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44, stderr="not found"))
    assert secret_store.load_groq_key() == ""


def test_delete_groq_key_treats_missing_entry_as_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44))
    assert secret_store.delete_groq_key() is True


def test_yandexgpt_key_and_folder_id_are_cached_independently(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert secret_store.save_yandexgpt_key("AQVN_test-key") is True
    assert secret_store.save_yandexgpt_folder_id("b1g...folder") is True

    assert secret_store.load_yandexgpt_key() == "AQVN_test-key"
    assert secret_store.load_yandexgpt_folder_id() == "b1g...folder"
    assert len(calls) == 2


def test_load_yandexgpt_key_returns_empty_when_not_stored(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44, stderr="not found"))
    assert secret_store.load_yandexgpt_key() == ""


def test_delete_yandexgpt_folder_id_treats_missing_entry_as_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44))
    assert secret_store.delete_yandexgpt_folder_id() is True


def test_gigachat_key_is_cached_independently_from_groq(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _result(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert secret_store.save_groq_key("gsk_groq-key") is True
    assert secret_store.save_gigachat_key("Y2xpZW50OnNlY3JldA==") is True

    assert secret_store.load_groq_key() == "gsk_groq-key"
    assert secret_store.load_gigachat_key() == "Y2xpZW50OnNlY3JldA=="
    assert len(calls) == 2


def test_load_gigachat_key_returns_empty_when_not_stored(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44, stderr="not found"))
    assert secret_store.load_gigachat_key() == ""


def test_delete_gigachat_key_treats_missing_entry_as_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _result(44))
    assert secret_store.delete_gigachat_key() is True
