import pytest

from claude_session_watcher.models import Account
from claude_session_watcher.providers import FallbackUsageProvider, UsageFetchResult
from claude_session_watcher.usage import ClaudeUsageClient, UsageError


class FailingProvider:
    async def fetch(self, account):
        raise UsageError("no cookies")


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
    provider = FallbackUsageProvider(FailingProvider(), SuccessfulProvider())

    result = await provider.fetch(
        Account(id=1, name="work", profile_dir="profile"),
    )

    assert result.source == "fallback"
    assert result.snapshot.five_hour.utilization == 1.0
