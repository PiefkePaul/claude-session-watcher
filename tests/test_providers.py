import pytest

from claude_session_watcher.models import Account
from claude_session_watcher.providers import FallbackUsageProvider, UsageFetchResult
from claude_session_watcher.usage import ClaudeUsageClient, UsageAuthError, UsageLoginRequiredError


class FailingProvider:
    async def fetch(self, account):
        raise UsageAuthError("invalid cookies")


class CookieErrorProvider:
    async def fetch(self, account):
        raise OSError("cookie store unavailable")


class LoginRequiredProvider:
    async def fetch(self, account):
        raise UsageLoginRequiredError("no cookies")


class SuccessfulProvider:
    async def fetch(self, account):
        snapshot = ClaudeUsageClient._parse(
            {
                "five_hour": {"utilization": 1.0, "resets_at": None},
                "seven_day": {"utilization": 2.0, "resets_at": None},
            }
        )
        return UsageFetchResult(snapshot=snapshot, source="fallback")


@pytest.mark.asyncio
async def test_fallback_usage_provider_uses_browser_fallback_on_cookie_error():
    provider = FallbackUsageProvider(CookieErrorProvider(), SuccessfulProvider())

    result = await provider.fetch(
        Account(id=1, name="work", profile_dir="profile"),
    )

    assert result.source == "fallback"
    assert result.snapshot.five_hour.utilization == 1.0


@pytest.mark.asyncio
async def test_fallback_usage_provider_does_not_fallback_on_auth_error():
    # A genuine auth error means the stored session is invalid server-side; the
    # browser shares the same cookies and would fail identically, so no fallback.
    provider = FallbackUsageProvider(FailingProvider(), SuccessfulProvider())

    with pytest.raises(UsageAuthError):
        await provider.fetch(Account(id=1, name="work", profile_dir="profile"))


@pytest.mark.asyncio
async def test_fallback_usage_provider_does_not_open_browser_without_login():
    provider = FallbackUsageProvider(LoginRequiredProvider(), SuccessfulProvider())

    with pytest.raises(UsageLoginRequiredError):
        await provider.fetch(Account(id=1, name="work", profile_dir="profile"))
