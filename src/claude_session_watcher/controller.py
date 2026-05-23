from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .browser import CamoufoxManager
from .models import Account, ClaudeSession, Watcher


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
