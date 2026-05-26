from claude_session_watcher.models import ClaudeSession, Watcher
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
    assert watcher.pause_template == "custom"
    assert store.list_watchers()[0].remote_url == "https://claude.ai/code/session"
    account_watcher = store.get_account_watcher_by_account(account.id)
    assert account_watcher is not None
    assert account_watcher.pause_template == "custom"
    sessions = store.list_sessions(account.id)
    assert sessions[0].title == "main"
    assert sessions[0].watch_enabled is True


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
        pause_template="worklog",
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
    assert saved.pause_template == "worklog"
    assert saved.pause_message == "pause safely"
    assert saved.continue_message == "resume now"


def test_store_account_watcher_pause_metadata(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    watcher = store.ensure_account_watcher(account.id)

    store.update_account_watcher_runtime(
        watcher.id,
        state="paused",
        paused_at="2026-05-23T10:00:00+00:00",
        paused_limit="five_hour",
        paused_until="2026-05-23T12:00:00+00:00",
    )
    paused = store.get_account_watcher(watcher.id)

    assert paused.paused_limit == "five_hour"
    assert paused.paused_until == "2026-05-23T12:00:00+00:00"

    store.update_account_watcher_runtime(watcher.id, state="active", clear_pause=True)

    active = store.get_account_watcher(watcher.id)
    assert active.paused_at is None
    assert active.paused_limit is None
    assert active.paused_until is None


def test_store_records_usage_samples(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    watcher = store.ensure_account_watcher(account.id)

    sample = store.add_usage_sample(
        watcher.id,
        source="test",
        five_hour_utilization=12.5,
        seven_day_utilization=44.0,
        five_hour_resets_at="2026-05-26T12:00:00+00:00",
        seven_day_resets_at="2026-05-31T12:00:00+00:00",
        raw_json='{"ok":true}',
    )

    samples = store.list_usage_samples(watcher.id)
    assert samples[0].id == sample.id
    assert samples[0].source == "test"
    assert samples[0].five_hour_utilization == 12.5
    assert samples[0].seven_day_resets_at == "2026-05-31T12:00:00+00:00"


def test_store_session_selection_survives_discovery_upsert(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))

    selected = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_1",
            title="main",
            url="https://claude.ai/code/session_1",
            kind="remote",
            status="unknown",
            watch_enabled=True,
            control_supported=True,
        )
    )
    rediscovered = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_1",
            title="main renamed",
            url="https://claude.ai/code/session_1",
            kind="remote",
            status="active",
            watch_enabled=False,
            control_supported=True,
        )
    )

    assert selected.id == rediscovered.id
    assert rediscovered.title == "main renamed"
    assert rediscovered.watch_enabled is True


def test_store_marks_missing_sessions_archived(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_1",
            title="main",
            url="https://claude.ai/code/session_1",
        )
    )

    store.mark_missing_sessions(account.id, {"session_2"})

    assert store.list_sessions(account.id)[0].status == "archived"
