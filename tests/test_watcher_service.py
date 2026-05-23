import pytest

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


class RecordingController:
    def __init__(self):
        self.sent = []

    async def send(self, watcher, account, message):
        self.sent.append((watcher.remote_url, message))

    async def send_to_session(self, account, session, message):
        self.sent.append((session.session_key, message))


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
