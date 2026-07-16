"""Тесты цепочки авто-обновления: санитизация имени файла и ссылок."""

from types import SimpleNamespace

from src.update_checker import _filename_from_download, _links_from_release_notes


def _resp(content_disposition: str):
    return SimpleNamespace(headers={"Content-Disposition": content_disposition})


FALLBACK_URL = "https://example.com/releases/Beast_Auto_Reporter_2.0_arm64.dmg"


def test_filename_from_content_disposition_plain():
    resp = _resp('attachment; filename="update.dmg"')
    assert _filename_from_download(resp, FALLBACK_URL) == "update.dmg"


def test_filename_traversal_in_content_disposition_is_stripped():
    # Заголовок приходит с сервера: "../../x" не должен позволить записать
    # файл за пределами каталога загрузки.
    resp = _resp('attachment; filename="../../Library/LaunchAgents/evil.plist"')
    assert _filename_from_download(resp, FALLBACK_URL) == "evil.plist"


def test_absolute_path_in_content_disposition_is_stripped():
    # У pathlib абсолютная правая часть при соединении заменяет левую —
    # абсолютный путь в filename обязан быть сведён к базовому имени.
    resp = _resp('attachment; filename="/tmp/evil.dmg"')
    assert _filename_from_download(resp, FALLBACK_URL) == "evil.dmg"


def test_filename_falls_back_to_url_when_no_header():
    resp = SimpleNamespace(headers={})
    assert _filename_from_download(resp, FALLBACK_URL) == "Beast_Auto_Reporter_2.0_arm64.dmg"


def test_filename_falls_back_to_default_when_nothing_usable():
    resp = SimpleNamespace(headers={})
    assert _filename_from_download(resp, "https://example.com/download/") == "Beast_Auto_Reporter_update.dmg"


def test_release_notes_links_accept_https():
    body = "arm64: https://disk.yandex.ru/d/XXXX\nintel: https://disk.yandex.ru/d/YYYY"
    links = _links_from_release_notes(body)
    assert links["arm"] == "https://disk.yandex.ru/d/XXXX"
    assert links["intel"] == "https://disk.yandex.ru/d/YYYY"


def test_release_notes_links_reject_http():
    # Скачанный файл открывается автоматически — http-ссылка позволила бы
    # подменить его по пути (MITM), поэтому принимается только https.
    body = "arm64: http://disk.yandex.ru/d/XXXX"
    links = _links_from_release_notes(body)
    assert links["arm"] == ""
