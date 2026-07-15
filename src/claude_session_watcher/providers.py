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
        # Never close a browser the user already has open (e.g. a login flow in
        # progress) — only clean up sessions this fetch opened itself.
        was_open = await self.browser.is_profile_open(profile_dir)
        try:
            usage_data = await self.browser.fetch_usage(profile_dir)
            return UsageFetchResult(
                snapshot=ClaudeUsageClient._parse(usage_data),
                source=self.source,
            )
        finally:
            if not self.keepalive and not was_open:
                await self.browser.close_profile(profile_dir)


class FallbackUsageProvider:
    def __init__(self, primary: UsageProvider, fallback: UsageProvider):
        self.primary = primary
        self.fallback = fallback

    async def fetch(self, account: Account) -> UsageFetchResult:
        try:
            return await self.primary.fetch(account)
        except UsageLoginRequiredError:
            # No cookies at all — the browser cannot be logged in either.
            raise
        except UsageAuthError as auth_exc:
            # claude.ai rejects session cookies used outside the browser
            # (account_session_invalid), so a 401/403 no longer proves the login
            # is gone. Only when the browser-driven fetch also fails do we
            # surface the auth error (→ genuine login problem).
            try:
                return await self.fallback.fetch(account)
            except Exception as fallback_exc:
                raise auth_exc from fallback_exc
        except (UsageError, OSError, sqlite3.Error):
            return await self.fallback.fetch(account)
