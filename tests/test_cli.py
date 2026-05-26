import json

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
