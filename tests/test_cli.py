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
    assert data[0]["name"] == "main"
    assert data[0]["enabled"] is True
