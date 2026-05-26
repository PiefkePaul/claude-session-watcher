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


def test_browser_console_url_env_is_loaded(monkeypatch):
    monkeypatch.setenv(
        "CSW_BROWSER_CONSOLE_URL",
        "http://127.0.0.1:47832/vnc.html?autoconnect=true",
    )
    monkeypatch.setenv("CSW_ENABLE_VNC", "true")
    monkeypatch.setenv("CSW_VNC_PORT", "47832")
    monkeypatch.setenv("CSW_AUTO_FINISH_LOGIN", "false")

    settings = Settings()

    assert settings.browser_console_url == "http://127.0.0.1:47832/vnc.html?autoconnect=true"
    assert settings.enable_vnc is True
    assert settings.vnc_port == 47832
    assert settings.auto_finish_login is False
