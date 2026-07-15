"""Cloudflare bot-challenge responses must not be treated as auth failures.

Depends: claude_session_watcher.usage, claude_session_watcher.providers
"""

from __future__ import annotations

import httpx
import pytest

from claude_session_watcher.providers import FallbackUsageProvider, UsageFetchResult
from claude_session_watcher.usage import (
    ClaudeUsageClient,
    UsageAuthError,
    UsageBlockedError,
    UsageSnapshot,
)

CF_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head>'
    "<body>checking your browser</body></html>"
)


def test_403_with_cf_mitigated_header_raises_blocked_error():
    response = httpx.Response(
        403,
        headers={"cf-mitigated": "challenge", "server": "cloudflare"},
        text=CF_CHALLENGE_HTML,
    )

    with pytest.raises(UsageBlockedError):
        ClaudeUsageClient._raise_for_response(response)


def test_403_with_challenge_html_raises_blocked_error():
    response = httpx.Response(
        403,
        headers={"content-type": "text/html; charset=UTF-8"},
        text=CF_CHALLENGE_HTML,
    )

    with pytest.raises(UsageBlockedError):
        ClaudeUsageClient._raise_for_response(response)


def test_plain_403_still_raises_auth_error():
    response = httpx.Response(
        403,
        headers={"content-type": "application/json"},
        text='{"error": "forbidden"}',
    )

    with pytest.raises(UsageAuthError):
        ClaudeUsageClient._raise_for_response(response)


def test_plain_401_still_raises_auth_error():
    response = httpx.Response(
        401,
        headers={"content-type": "application/json"},
        text='{"error": "unauthorized"}',
    )

    with pytest.raises(UsageAuthError):
        ClaudeUsageClient._raise_for_response(response)


class BlockedProvider:
    async def fetch(self, account):
        raise UsageBlockedError("Cloudflare challenge")


class StaticProvider:
    def __init__(self):
        self.calls = 0

    async def fetch(self, account):
        self.calls += 1
        snapshot = UsageSnapshot(raw={}, five_hour=None, seven_day=None)
        return UsageFetchResult(snapshot=snapshot, source="browser")


async def test_fallback_provider_falls_back_on_blocked_error():
    fallback = StaticProvider()
    provider = FallbackUsageProvider(BlockedProvider(), fallback)

    result = await provider.fetch(account=None)

    assert result.source == "browser"
    assert fallback.calls == 1
