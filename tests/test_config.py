from monarchbot.config import Settings, load_settings


def test_settings_accepts_nonempty_token_and_admin_ids():
    settings = Settings("123456789:TEST_TOKEN_NEVER_USED", "data/test.sqlite3", frozenset({42}))
    assert settings.bot_token.startswith("123456789:")
    assert settings.admin_ids == frozenset({42})
    assert "TEST_TOKEN_NEVER_USED" not in repr(settings)


def test_database_path_tilde_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123456789:TEST_TOKEN_NEVER_USED")
    monkeypatch.setenv("DATABASE_PATH", "~/monarch-test.sqlite3")
    monkeypatch.setenv("ADMIN_IDS", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = load_settings()
    assert settings.database_path == str(tmp_path / "monarch-test.sqlite3")
