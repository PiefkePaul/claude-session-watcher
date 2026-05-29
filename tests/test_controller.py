import httpx
import pytest

from claude_session_watcher.controller import HttpSessionController
from claude_session_watcher.models import Account, ClaudeSession
from claude_session_watcher.usage import ClaudeCookie


def _http_error(status_code: int, session_id: str) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "POST",
        f"https://claude.ai/v1/sessions/{session_id}/events",
    )
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


@pytest.mark.asyncio
async def test_http_controller_rebinds_session_id_after_400(monkeypatch):
    class FakeClient:
        calls: list[str] = []
        listed = False

        def __init__(self, *, cookies):
            self.cookies = cookies

        async def send_user_message(self, session_id: str, text: str) -> None:
            self.calls.append(session_id)
            if session_id == "session_old":
                raise _http_error(400, session_id)
            if session_id == "session_new":
                return
            raise AssertionError(f"unexpected session id {session_id}")

        async def list_all(self):
            type(self).listed = True
            return [
                {
                    "id": "session_new",
                    "title": "HealthAI",
                    "url": "https://claude.ai/code/session_new",
                    "tags": ["remote-control-repl"],
                    "status": "active",
                }
            ]

    monkeypatch.setattr(
        "claude_session_watcher.controller.load_claude_cookies",
        lambda _path: [ClaudeCookie(name="sessionKey", value="cookie")],
    )
    monkeypatch.setattr(
        "claude_session_watcher.controller.ClaudeWebSessionsClient",
        FakeClient,
    )
    controller = HttpSessionController()
    account = Account(
        id=1,
        name="pro",
        profile_dir="/tmp/profile",
    )
    session = ClaudeSession(
        id=10,
        account_id=1,
        session_key="session_old",
        title="HealthAI",
        url="https://claude.ai/code/session_old",
        kind="remote",
        status="active",
        watch_enabled=True,
        control_supported=True,
    )

    await controller.send_to_session(account, session, "pause")

    assert FakeClient.calls == ["session_old", "session_new"]
    assert FakeClient.listed is True
    assert session.session_key == "session_new"
    assert session.url == "https://claude.ai/code/session_new"


@pytest.mark.asyncio
async def test_http_controller_does_not_retry_on_403(monkeypatch):
    class FakeClient:
        listed = False

        def __init__(self, *, cookies):
            self.cookies = cookies

        async def send_user_message(self, session_id: str, text: str) -> None:
            raise _http_error(403, session_id)

        async def list_all(self):
            self.listed = True
            return []

    monkeypatch.setattr(
        "claude_session_watcher.controller.load_claude_cookies",
        lambda _path: [ClaudeCookie(name="sessionKey", value="cookie")],
    )
    monkeypatch.setattr(
        "claude_session_watcher.controller.ClaudeWebSessionsClient",
        FakeClient,
    )
    controller = HttpSessionController()
    account = Account(
        id=1,
        name="pro",
        profile_dir="/tmp/profile",
    )
    session = ClaudeSession(
        id=10,
        account_id=1,
        session_key="session_old",
        title="HealthAI",
        url="https://claude.ai/code/session_old",
        kind="remote",
        status="active",
        watch_enabled=True,
        control_supported=True,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await controller.send_to_session(account, session, "pause")
    assert FakeClient.listed is False
