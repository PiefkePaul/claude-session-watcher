from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .browser import CamoufoxManager
from .models import Account
from .profile_cookies import load_claude_cookies
from .usage import (
    ClaudeUsageClient,
    UsageAuthError,
    UsageError,
    UsageLoginRequiredError,
    UsageSnapshot,
)


@dataclass(frozen=True, slots=True)
class UsageFetchResult:
    snapshot: UsageSnapshot
    source: str


class UsageProvider(Protocol):
    async def fetch(self, account: Account) -> UsageFetchResult:
        pass


class CamoufoxCookiesHttpUsageProvider:
    source = "camoufox-cookies-http"

    async def fetch(self, account: Account) -> UsageFetchResult:
        cookies = load_claude_cookies(Path(account.profile_dir))
        if not any(cookie.name == "sessionKey" and cookie.value for cookie in cookies):
            raise UsageLoginRequiredError(
                "No sessionKey cookie found in the browser profile. Open login and sign in first."
            )
        client = ClaudeUsageClient(cookies=cookies)
        return UsageFetchResult(snapshot=await client.fetch(), source=self.source)


class CamoufoxBrowserUsageProvider:
    source = "camoufox-browser-ui"

    def __init__(self, browser: CamoufoxManager, *, keepalive: bool = False):
        self.browser = browser
        self.keepalive = keepalive

    async def fetch(self, account: Account) -> UsageFetchResult:
        profile_dir = Path(account.profile_dir)
        try:
            usage_data = await self.browser.fetch_usage(profile_dir)
            return UsageFetchResult(
                snapshot=ClaudeUsageClient._parse(usage_data),
                source=self.source,
            )
        finally:
            if not self.keepalive:
                await self.browser.close_profile(profile_dir)


class FallbackUsageProvider:
    def __init__(self, primary: UsageProvider, fallback: UsageProvider):
        self.primary = primary
        self.fallback = fallback

    async def fetch(self, account: Account) -> UsageFetchResult:
        try:
            return await self.primary.fetch(account)
        except UsageLoginRequiredError:
            raise
        except UsageAuthError:
            # Auth/session problems cannot be fixed by falling back to a browser-driven
            # provider without user interaction. Falling back here can also interfere
            # with an in-progress login flow by opening/closing the same profile.
            raise
        except (UsageError, OSError, sqlite3.Error):
            return await self.fallback.fetch(account)
