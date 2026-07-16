from src.app_paths import ensure_parent_dir, migrate_legacy_config_file


def test_ensure_parent_dir_creates_missing_directories(tmp_path):
    # Сценарий чистой установки: CONFIG_DIR ещё не существует, и без
    # ensure_parent_dir каждая save_*-функция молча падала бы при записи.
    target = tmp_path / "config_dir" / "nested" / "settings.json"

    ensure_parent_dir(target)

    assert target.parent.is_dir()
    target.write_text("{}", encoding="utf-8")  # запись теперь возможна


def test_ensure_parent_dir_noop_when_directory_exists(tmp_path):
    target = tmp_path / "settings.json"

    ensure_parent_dir(target)  # tmp_path уже существует — не должно падать

    assert tmp_path.is_dir()


def test_migrate_legacy_config_file_copies_old_to_new(tmp_path):
    old_path = tmp_path / "old" / ".legacy_settings.json"
    old_path.parent.mkdir()
    old_path.write_text('{"a": 1}', encoding="utf-8")
    new_path = tmp_path / "new" / "settings.json"

    migrate_legacy_config_file(old_path, new_path)

    assert new_path.exists()
    assert new_path.read_text(encoding="utf-8") == '{"a": 1}'
    assert old_path.exists()  # старый файл не удаляется


def test_migrate_legacy_config_file_noop_when_new_already_exists(tmp_path):
    old_path = tmp_path / "old.json"
    old_path.write_text('{"a": 1}', encoding="utf-8")
    new_path = tmp_path / "new.json"
    new_path.write_text('{"b": 2}', encoding="utf-8")

    migrate_legacy_config_file(old_path, new_path)

    assert new_path.read_text(encoding="utf-8") == '{"b": 2}'  # не перезаписан


def test_migrate_legacy_config_file_noop_when_old_missing(tmp_path):
    old_path = tmp_path / "does_not_exist.json"
    new_path = tmp_path / "new.json"

    migrate_legacy_config_file(old_path, new_path)

    assert not new_path.exists()
