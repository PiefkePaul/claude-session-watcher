from claude_session_watcher.settings import Settings


def test_headless_env_false_is_boolean_false(monkeypatch):
    monkeypatch.setenv("CSW_CAMOUFOX_HEADLESS", "false")

    settings = Settings()

    assert settings.camoufox_headless is False


def test_headless_env_virtual_is_preserved(monkeypatch):
    monkeypatch.setenv("CSW_CAMOUFOX_HEADLESS", "virtual")

    settings = Settings()

    assert settings.camoufox_headless == "virtual"


def test_notification_env_is_loaded(monkeypatch):
    monkeypatch.setenv("CSW_NOTIFY_NTFY_URL", "https://ntfy.sh/example")
    monkeypatch.setenv("CSW_RESUME_SAFETY_MARGIN_SECONDS", "30")

    settings = Settings()

    assert settings.notify_ntfy_url == "https://ntfy.sh/example"
    assert settings.resume_safety_margin_seconds == 30
