"""Auth-rejected cookie requests must fall back to the browser provider.

claude.ai rejects session cookies used outside the browser
(account_session_invalid), so an HTTP 401/403 no longer proves the login
is gone — only a failed browser fetch does.

Depends: claude_session_watcher.providers
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_session_watcher.providers import (
    CamoufoxBrowserUsageProvider,
    FallbackUsageProvider,
    UsageFetchResult,
)
from claude_session_watcher.usage import (
    UsageAuthError,
    UsageLoginRequiredError,
    UsageSnapshot,
)


class RaisingProvider:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    async def fetch(self, account):
        self.calls += 1
        raise self.exc


class SucceedingProvider:
    def __init__(self):
        self.calls = 0

    async def fetch(self, account):
        self.calls += 1
        snapshot = UsageSnapshot(raw={}, five_hour=None, seven_day=None)
        return UsageFetchResult(snapshot=snapshot, source="browser")


async def test_auth_error_does_not_touch_browser_fallback():
    # A genuine auth error means the browser (same cookies) would fail identically;
    # falling back only wastes a launch and can disrupt an in-progress manual login.
    fallback = SucceedingProvider()
    provider = FallbackUsageProvider(RaisingProvider(UsageAuthError("HTTP 403")), fallback)

    with pytest.raises(UsageAuthError):
        await provider.fetch(account=None)

    assert fallback.calls == 0


async def test_login_required_does_not_touch_browser_fallback():
    fallback = SucceedingProvider()
    provider = FallbackUsageProvider(
        RaisingProvider(UsageLoginRequiredError("no cookie")), fallback
    )

    with pytest.raises(UsageLoginRequiredError):
        await provider.fetch(account=None)

    assert fallback.calls == 0


class FakeAccount:
    profile_dir = "/tmp/profile"


class FakeBrowserManager:
    def __init__(self, *, profile_open: bool):
        self.profile_open = profile_open
        self.closed: list[Path] = []

    async def is_profile_open(self, profile_dir: Path) -> bool:
        return self.profile_open

    async def fetch_usage(self, profile_dir: Path):
        return {"five_hour": {"utilization": 10.0, "resets_at": None}}

    async def close_profile(self, profile_dir: Path) -> None:
        self.closed.append(profile_dir)


async def test_browser_provider_keeps_already_open_browser_open():
    browser = FakeBrowserManager(profile_open=True)
    provider = CamoufoxBrowserUsageProvider(browser, keepalive=False)

    await provider.fetch(FakeAccount())

    assert browser.closed == []


async def test_browser_provider_closes_browser_it_opened():
    browser = FakeBrowserManager(profile_open=False)
    provider = CamoufoxBrowserUsageProvider(browser, keepalive=False)

    await provider.fetch(FakeAccount())

    assert browser.closed == [Path("/tmp/profile")]
