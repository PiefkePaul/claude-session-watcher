import json
from types import SimpleNamespace

from claude_session_watcher.browser import BrowserError
from claude_session_watcher.cli import main
from claude_session_watcher.models import Watcher
from claude_session_watcher.store import Store


def test_cli_status_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    store.create_watcher(
        Watcher(id=None, name="main", account_id=account.id, remote_url="https://example.com")
    )

    assert main(["status", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data[0]["account"] == "work"
    assert data[0]["enabled"] is True
    assert data[0]["sessions_watched"] == 1
    assert data[0]["status"] == "unknown"
    assert data[0]["sample_count"] == 0


def test_cli_history_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    account_watcher = store.ensure_account_watcher(account.id)
    store.add_usage_sample(
        account_watcher.id,
        source="test",
        five_hour_utilization=10.0,
        seven_day_utilization=20.0,
        five_hour_resets_at=None,
        seven_day_resets_at=None,
        raw_json=None,
    )

    assert main(["history", "work", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data[0]["source"] == "test"
    assert data[0]["five_hour_utilization"] == 10.0


def test_cli_account_create(monkeypatch, tmp_path):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))

    assert main(["account-create", "pc"]) == 0

    store = Store(tmp_path / "watcher.sqlite3")
    accounts = store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].name == "pc"
    assert accounts[0].profile_dir.endswith("profiles\\pc") or accounts[0].profile_dir.endswith(
        "profiles/pc"
    )


def test_cli_account_login_headless(monkeypatch, tmp_path):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("pc", str(tmp_path / "profiles" / "pc"))

    class FakeBrowser:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def start_email_login(self, profile_dir, email):
            assert str(profile_dir).endswith("pc")
            assert email == "pc@example.com"
            return {"ok": True, "state": "code_form"}

        async def submit_otp(self, profile_dir, code):
            assert str(profile_dir).endswith("pc")
            assert code == "123456"
            return {"ok": True, "state": "logged_in"}

        async def session_key(self, profile_dir):
            return "session-key"

        async def code_portal_status(self, profile_dir):
            return {"disabled": False, "message": None}

        async def close_profile(self, profile_dir):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("claude_session_watcher.cli.CamoufoxManager", FakeBrowser)
    monkeypatch.setattr("builtins.input", lambda prompt="": "123456")

    assert main(["account-login", str(account.id), "--email", "pc@example.com"]) == 0

    saved = store.get_account(account.id)
    assert saved.status == "logged-in"


def test_cli_account_login_missing_camoufox_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("pc", str(tmp_path / "profiles" / "pc"))

    class FakeBrowser:
        def __init__(self, *args, **kwargs):
            return None

        async def start_email_login(self, profile_dir, email):
            raise BrowserError("Camoufox is not installed (missing camoufox.async_api).")

        async def close_profile(self, profile_dir):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("claude_session_watcher.cli.CamoufoxManager", FakeBrowser)

    assert main(["account-login", str(account.id), "--email", "pc@example.com"]) == 1
    out = capsys.readouterr().out
    assert "Login failed:" in out
    assert "camoufox fetch" in out


def test_cli_account_login_stabilizes_email_form_to_logged_in(monkeypatch, tmp_path):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("pc", str(tmp_path / "profiles" / "pc"))

    class FakeBrowser:
        def __init__(self, *args, **kwargs):
            self.session_key_checks = 0

        async def start_email_login(self, profile_dir, email):
            return {"ok": True, "state": "code_form"}

        async def submit_otp(self, profile_dir, code):
            return {"ok": True, "state": "email_form"}

        async def session_key(self, profile_dir):
            self.session_key_checks += 1
            if self.session_key_checks < 2:
                raise Exception("not yet")
            return "session-key"

        async def get_login_page_state(self, profile_dir):
            return {"state": "email_form"}

        async def code_portal_status(self, profile_dir):
            return {"disabled": False, "message": None}

        async def close_profile(self, profile_dir):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("claude_session_watcher.cli.CamoufoxManager", FakeBrowser)
    monkeypatch.setattr("builtins.input", lambda prompt="": "123456")

    assert main(["account-login", str(account.id), "--email", "pc@example.com"]) == 0
    saved = store.get_account(account.id)
    assert saved.status == "logged-in"


def test_cli_new_account_command_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))

    assert main(["account", "add", "pc2"]) == 0

    store = Store(tmp_path / "watcher.sqlite3")
    accounts = store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].name == "pc2"


def test_cli_account_login_reports_email_rejected_reason(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("pc", str(tmp_path / "profiles" / "pc"))

    class FakeBrowser:
        def __init__(self, *args, **kwargs):
            return None

        async def start_email_login(self, profile_dir, email):
            return {
                "ok": False,
                "state": "email_form",
                "reason": "Disposable/temporary email domains are rejected by Claude.",
            }

        async def close_profile(self, profile_dir):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("claude_session_watcher.cli.CamoufoxManager", FakeBrowser)

    assert main(["account-login", str(account.id), "--email", "pc@example.com"]) == 1
    out = capsys.readouterr().out
    assert "Login start failed:" in out
    assert "Disposable/temporary email domains are rejected by Claude." in out


def test_cli_dashboard_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    store.create_watcher(
        Watcher(id=None, name="main", account_id=account.id, remote_url="https://example.com")
    )

    assert main(["dashboard", "--once"]) == 0
    out = capsys.readouterr().out
    assert "Claude Session Watcher" in out
    assert "Accounts" in out


def test_cli_native_backend(monkeypatch, capsys):
    monkeypatch.setattr("claude_session_watcher.cli.background_backend", lambda: "systemd-user")
    assert main(["native", "backend"]) == 0
    assert "systemd-user" in capsys.readouterr().out


def test_cli_native_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "claude_session_watcher.cli.background_service_status",
        lambda _settings: SimpleNamespace(
            installed=True,
            running=True,
            backend="systemd-user",
            detail="active",
        ),
    )
    assert main(["native", "status"]) == 0
    out = capsys.readouterr().out
    assert "Backend: systemd-user" in out
    assert "Installed: yes" in out
    assert "Running: yes" in out


def test_cli_native_service_install_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    calls: dict[str, int] = {"install": 0}

    def fake_install(_settings):
        calls["install"] += 1
        return SimpleNamespace(
            installed=True,
            running=True,
            backend="systemd-user",
            detail="active",
        )

    monkeypatch.setattr("claude_session_watcher.cli.install_background_service", fake_install)
    assert main(["native", "service-install"]) == 0
    assert calls["install"] == 1


def test_cli_native_mode_set(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    assert main(["native", "mode-set", "installed"]) == 0
    out = capsys.readouterr().out
    assert "Mode set to: installed" in out


def test_cli_native_autostart_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "claude_session_watcher.cli.desktop_task_status",
        lambda _settings: SimpleNamespace(installed=False, running=False, detail=""),
    )
    assert main(["native", "autostart", "status"]) == 0
    out = capsys.readouterr().out
    assert "Autostart:" in out
    assert "Autostart entry installed: no" in out


def test_cli_native_open_sends_show(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CSW_DATA_DIR", str(tmp_path))
    sent: dict[str, str] = {}
    monkeypatch.setattr("claude_session_watcher.cli.agent_running", lambda _settings: (True, 42))
    monkeypatch.setattr(
        "claude_session_watcher.cli.send_agent_command",
        lambda _settings, command: sent.setdefault("cmd", command),
    )
    assert main(["native", "open"]) == 0
    assert sent["cmd"] == "show"
    out = capsys.readouterr().out
    assert "Open request sent" in out
