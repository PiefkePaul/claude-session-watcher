import pytest

from claude_session_watcher.discovery import (
    ClaudeSessionDiscoveryProvider,
    SessionDiscoveryService,
)
from claude_session_watcher.models import ClaudeSession
from claude_session_watcher.store import Store
from claude_session_watcher.usage import ClaudeCookie


class StaticDiscoveryProvider:
    def __init__(self, sessions):
        self.sessions = sessions

    async def discover(self, account):
        return self.sessions


@pytest.mark.asyncio
async def test_discovery_provider_accepts_session_id_aliases(monkeypatch, tmp_path):
    class FakeClient:
        def __init__(self, *, cookies):
            self.cookies = cookies

        async def list_all(self):
            return [
                {
                    "session_id": "session_new",
                    "name": "New Remote",
                    "remote_control": True,
                    "session_status": "active",
                }
            ]

    monkeypatch.setattr(
        "claude_session_watcher.discovery.load_claude_cookies",
        lambda _path: [ClaudeCookie(name="sessionKey", value="cookie")],
    )
    monkeypatch.setattr(
        "claude_session_watcher.discovery.ClaudeWebSessionsClient",
        FakeClient,
    )
    account = Store(tmp_path / "watcher.sqlite3").create_account(
        "work",
        str(tmp_path / "profile"),
    )

    sessions = await ClaudeSessionDiscoveryProvider(browser=object()).discover(account)

    assert len(sessions) == 1
    assert sessions[0].session_key == "session_new"
    assert sessions[0].url == "https://claude.ai/code/session_new"
    assert sessions[0].title == "New Remote"
    assert sessions[0].kind == "remote"
    assert sessions[0].control_supported is True


@pytest.mark.asyncio
async def test_discovery_auto_selects_new_remote_sessions(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    service = SessionDiscoveryService(
        store,
        StaticDiscoveryProvider(
            [
                ClaudeSession(
                    id=None,
                    account_id=account.id,
                    session_key="session_remote",
                    title="remote",
                    url="https://claude.ai/code/session_remote",
                    kind="remote",
                    status="active",
                    control_supported=True,
                ),
                ClaudeSession(
                    id=None,
                    account_id=account.id,
                    session_key="session_cloud",
                    title="cloud",
                    url="https://claude.ai/code/session_cloud",
                    kind="cloud",
                    status="active",
                    control_supported=False,
                ),
            ]
        ),
    )

    result = await service.discover_account(account)

    remote = store.get_session_by_key(account.id, "session_remote")
    cloud = store.get_session_by_key(account.id, "session_cloud")
    assert result.found == 2
    assert result.updated == 2
    assert result.selected == 1
    assert remote.watch_enabled is True
    assert cloud.watch_enabled is False


@pytest.mark.asyncio
async def test_discovery_preserves_existing_disabled_remote_session(tmp_path):
    store = Store(tmp_path / "watcher.sqlite3")
    account = store.create_account("work", str(tmp_path / "profile"))
    existing = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key="session_remote",
            title="remote",
            url="https://claude.ai/code/session_remote",
            kind="remote",
            status="active",
            watch_enabled=False,
            control_supported=True,
        )
    )
    service = SessionDiscoveryService(
        store,
        StaticDiscoveryProvider(
            [
                ClaudeSession(
                    id=None,
                    account_id=account.id,
                    session_key="session_remote",
                    title="remote renamed",
                    url="https://claude.ai/code/session_remote",
                    kind="remote",
                    status="active",
                    control_supported=True,
                )
            ]
        ),
    )

    result = await service.discover_account(account)

    saved = store.get_session(existing.id)
    assert result.selected == 0
    assert saved.title == "remote renamed"
    assert saved.watch_enabled is False
