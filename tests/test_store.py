from claude_session_watcher.models import Watcher
from claude_session_watcher.store import Store


def test_store_creates_account_and_watcher(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profiles" / "work"))

    watcher = store.create_watcher(
        Watcher(
            id=None,
            name="main",
            account_id=account.id,
            remote_url="https://claude.ai/code/session",
        )
    )

    assert watcher.id is not None
    assert watcher.five_hour_threshold == 95.0
    assert store.list_watchers()[0].remote_url == "https://claude.ai/code/session"


def test_store_runtime_updates_and_events(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    watcher = store.create_watcher(
        Watcher(id=None, name="main", account_id=account.id, remote_url="https://example.com")
    )

    store.update_watcher_runtime(watcher.id, state="paused", last_reason="limit")
    store.add_event(watcher.id, "warning", "Pause sent")

    updated = store.get_watcher(watcher.id)
    events = store.list_events(watcher.id)

    assert updated.state == "paused"
    assert updated.last_reason == "limit"
    assert events[0].message == "Pause sent"


def test_store_updates_watcher_configuration(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    watcher = store.create_watcher(
        Watcher(id=None, name="main", account_id=account.id, remote_url="https://example.com")
    )

    updated = Watcher(
        id=watcher.id,
        name="renamed",
        account_id=account.id,
        remote_url="https://claude.ai/code/updated",
        enabled=False,
        five_hour_threshold=91.5,
        seven_day_threshold=97.5,
        resume_threshold=3.0,
        check_interval_seconds=120,
        pause_message="pause safely",
        continue_message="resume now",
    )
    saved = store.update_watcher_config(watcher.id, updated)

    assert saved.name == "renamed"
    assert saved.remote_url == "https://claude.ai/code/updated"
    assert saved.enabled is False
    assert saved.five_hour_threshold == 91.5
    assert saved.seven_day_threshold == 97.5
    assert saved.resume_threshold == 3.0
    assert saved.check_interval_seconds == 120
    assert saved.pause_message == "pause safely"
    assert saved.continue_message == "resume now"
