from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .browser import CamoufoxManager
from .models import Account, Watcher


class SessionController(Protocol):
    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        pass


class BrowserSessionController:
    def __init__(self, browser: CamoufoxManager, *, keepalive: bool = False):
        self.browser = browser
        self.keepalive = keepalive

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        profile_dir = Path(account.profile_dir)
        try:
            await self.browser.send_prompt(profile_dir, watcher.remote_url, message)
        finally:
            if not self.keepalive:
                await self.browser.close_profile(profile_dir)
