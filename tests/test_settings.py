from claude_session_watcher.settings import Settings


def test_headless_env_false_is_boolean_false(monkeypatch):
    monkeypatch.setenv("CSW_CAMOUFOX_HEADLESS", "false")

    settings = Settings()

    assert settings.camoufox_headless is False


def test_headless_env_virtual_is_preserved(monkeypatch):
    monkeypatch.setenv("CSW_CAMOUFOX_HEADLESS", "virtual")

    settings = Settings()

    assert settings.camoufox_headless == "virtual"
