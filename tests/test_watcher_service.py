import pytest

from claude_session_watcher.discovery import SessionDiscoveryService
from claude_session_watcher.models import ClaudeSession
from claude_session_watcher.providers import UsageFetchResult
from claude_session_watcher.store import Store
from claude_session_watcher.usage import ClaudeUsageClient
from claude_session_watcher.watcher import WatcherService


class StaticLimitProvider:
    async def fetch(self, account):
        snapshot = ClaudeUsageClient._parse(
            {
                "five_hour": {"utilization": 99.0, "resets_at": None},
                "seven_day": {"utilization": 2.0, "resets_at": None},
            }
        )
        return UsageFetchResult(snapshot=snapshot, source="test")


class SequenceLimitProvider:
    def __init__(self, snapshots):
        self.snapshots = [ClaudeUsageClient._parse(payload) for payload in snapshots]
        self.index = 0

    async def fetch(self, account):
        if self.index >= len(self.snapshots):
            snapshot = self.snapshots[-1]
        else:
            snapshot = self.snapshots[self.index]
            self.index += 1
        return UsageFetchResult(snapshot=snapshot, source="test-seq")


class RecordingController:
    def __init__(self):
        self.sent = []

    async def send(self, watcher, account, message):
        self.sent.append((watcher.remote_url, message))

    async def send_to_session(self, account, session, message):
        self.sent.append((session.session_key, message))


class StaticDiscoveryProvider:
    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = 0

    async def discover(self, account):
        self.calls += 1
        return self.sessions


@pytest.mark.asyncio
async def test_service_sends_only_to_selected_sessions(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    account_watcher = store.ensure_account_watcher(account.id)
    selected = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_selected",
            title="selected",
            url="https://claude.ai/code/session_selected",
            kind="remote",
            status="active",
            watch_enabled=True,
            control_supported=True,
        )
    )
    unselected = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_unselected",
            title="unselected",
            url="https://claude.ai/code/session_unselected",
            kind="remote",
            status="active",
            watch_enabled=False,
            control_supported=True,
        )
    )
    controller = RecordingController()
    service = WatcherService(
        store,
        browser=None,
        settings=object(),
        usage_provider=StaticLimitProvider(),
        session_controller=controller,
    )

    result = await service.check_account_now(account_watcher.id)

    assert result == "paused"
    assert controller.sent == [("session_selected", account_watcher.pause_message)]
    assert store.get_session(selected.id).last_control_error is None
    assert store.get_session(unselected.id).last_control_error is None
    samples = store.list_usage_samples(account_watcher.id)
    assert samples[0].source == "test"
    assert samples[0].five_hour_utilization == 99.0


@pytest.mark.asyncio
async def test_service_continues_all_selected_sessions(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    account_watcher = store.ensure_account_watcher(account.id)
    session_a = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_a",
            title="session-a",
            url="https://claude.ai/code/session_a",
            kind="remote",
            status="active",
            watch_enabled=True,
            control_supported=True,
        )
    )
    session_b = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_b",
            title="session-b",
            url="https://claude.ai/code/session_b",
            kind="remote",
            status="active",
            watch_enabled=True,
            control_supported=True,
        )
    )
    controller = RecordingController()
    provider = SequenceLimitProvider(
        [
            {"five_hour": {"utilization": 99.0, "resets_at": None}},
            {"five_hour": {"utilization": 10.0, "resets_at": None}},
        ]
    )
    service = WatcherService(
        store,
        browser=None,
        settings=object(),
        usage_provider=provider,
        session_controller=controller,
    )

    pause_result = await service.check_account_now(account_watcher.id)
    continue_result = await service.check_account_now(account_watcher.id)

    assert pause_result == "paused"
    assert continue_result == "continued"
    assert controller.sent == [
        ("session_a", account_watcher.pause_message),
        ("session_b", account_watcher.pause_message),
        ("session_a", account_watcher.continue_message),
        ("session_b", account_watcher.continue_message),
    ]
    assert store.get_session(session_a.id).last_control_error is None
    assert store.get_session(session_b.id).last_control_error is None


@pytest.mark.asyncio
async def test_service_attempts_archived_selected_sessions(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    account_watcher = store.ensure_account_watcher(account.id)
    store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_active",
            title="active",
            url="https://claude.ai/code/session_active",
            kind="remote",
            status="active",
            watch_enabled=True,
            control_supported=True,
        )
    )
    store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_archived",
            title="archived",
            url="https://claude.ai/code/session_archived",
            kind="remote",
            status="archived",
            watch_enabled=True,
            control_supported=True,
        )
    )
    controller = RecordingController()
    service = WatcherService(
        store,
        browser=None,
        settings=object(),
        usage_provider=StaticLimitProvider(),
        session_controller=controller,
    )

    result = await service.check_account_now(account_watcher.id)

    assert result == "paused"
    assert controller.sent == [
        ("session_active", account_watcher.pause_message),
        ("session_archived", account_watcher.pause_message),
    ]


@pytest.mark.asyncio
async def test_service_auto_discovers_and_selects_new_remote_sessions(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    account_watcher = store.ensure_account_watcher(account.id)
    provider = StaticDiscoveryProvider(
        [
            ClaudeSession(
                id=None,
                account_id=account.id,
                session_key="session_new",
                title="new",
                url="https://claude.ai/code/session_new",
                kind="remote",
                status="active",
                control_supported=True,
            )
        ]
    )
    controller = RecordingController()
    service = WatcherService(
        store,
        browser=None,
        settings=object(),
        usage_provider=StaticLimitProvider(),
        session_controller=controller,
        session_discovery=SessionDiscoveryService(store, provider),
    )

    result = await service.check_account_now(account_watcher.id)

    saved = store.get_session_by_key(account.id, "session_new")
    assert result == "paused"
    assert provider.calls == 1
    assert saved.watch_enabled is True
    assert controller.sent == [("session_new", account_watcher.pause_message)]
