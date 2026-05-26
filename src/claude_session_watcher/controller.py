from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .browser import CamoufoxManager
from .models import Account, ClaudeSession, Watcher
from .profile_cookies import load_claude_cookies
from .session_list import ClaudeWebSessionsClient, SessionListAuthError, SessionListError


class SessionController(Protocol):
    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        pass

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        pass


class BrowserSessionController:
    def __init__(self, browser: CamoufoxManager, *, keepalive: bool = False):
        self.browser = browser
        self.keepalive = keepalive

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        await self._send_url(account, watcher.remote_url, message)

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        await self._send_url(account, session.url, message)

    async def _send_url(self, account: Account, url: str, message: str) -> None:
        profile_dir = Path(account.profile_dir)
        try:
            await self.browser.send_prompt(profile_dir, url, message)
        finally:
            if not self.keepalive:
                await self.browser.close_profile(profile_dir)


class HttpSessionController:
    """Send prompts through claude.ai session events (no browser automation)."""

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        session_key = _session_key_from_url(watcher.remote_url)
        if not session_key:
            raise SessionListError("Could not determine session id from remote URL")
        await self._send_session_key(account, session_key, message)

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        session_key = session.session_key or _session_key_from_url(session.url)
        if not session_key:
            raise SessionListError(f"Session id missing for session {session.title!r}")
        await self._send_session_key(account, session_key, message)

    async def _send_session_key(self, account: Account, session_key: str, message: str) -> None:
        cookies = load_claude_cookies(Path(account.profile_dir))
        client = ClaudeWebSessionsClient(cookies=cookies)
        await client.send_user_message(session_key, message)


class FallbackSessionController:
    """Try HTTP control first, then browser control as fallback."""

    def __init__(self, primary: SessionController, fallback: SessionController):
        self.primary = primary
        self.fallback = fallback

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        try:
            await self.primary.send(watcher, account, message)
        except (SessionListError, SessionListAuthError, OSError):
            await self.fallback.send(watcher, account, message)

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        try:
            await self.primary.send_to_session(account, session, message)
        except (SessionListError, SessionListAuthError, OSError):
            await self.fallback.send_to_session(account, session, message)


def _session_key_from_url(url: str) -> str | None:
    match = re.search(r"/code/(session_[A-Za-z0-9]+)", str(url or ""))
    if match:
        return match.group(1)
    return None
