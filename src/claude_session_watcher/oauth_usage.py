from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from .usage import UsageLoginRequiredError


class OAuthUsageError(Exception):
    pass


class OAuthUsageAuthError(OAuthUsageError):
    pass


class OAuthUsageRateLimitError(OAuthUsageError):
    pass


def default_oauth_credentials_path() -> Path:
    config_dir = os.getenv("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / ".credentials.json"
    return Path.home() / ".claude" / ".credentials.json"


def load_oauth_access_token(credentials_path: Path | None = None) -> tuple[str, Path]:
    token_from_env = (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token_from_env:
        return token_from_env, Path("<env:CLAUDE_CODE_OAUTH_TOKEN>")

    path = credentials_path or default_oauth_credentials_path()
    if not path.exists():
        raise UsageLoginRequiredError(f"OAuth credentials not found at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OAuthUsageError(f"Could not parse OAuth credentials file: {path}") from exc

    token = _extract_access_token(payload)
    if not token:
        raise OAuthUsageError(f"No OAuth access token found in {path}")
    return token, path


def _extract_access_token(value: Any) -> str | None:
    # Prefer explicit keys first.
    direct = _extract_from_known_keys(value)
    if direct:
        return direct

    if isinstance(value, dict):
        for nested in value.values():
            token = _extract_access_token(nested)
            if token:
                return token
    elif isinstance(value, list):
        for nested in value:
            token = _extract_access_token(nested)
            if token:
                return token
    return None


def _extract_from_known_keys(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("accessToken", "access_token", "token"):
        token = value.get(key)
        if isinstance(token, str) and token.strip():
            if key == "token" and "refresh" in token.lower():
                continue
            return token.strip()
    return None


class ClaudeOAuthUsageClient:
    def __init__(self, access_token: str):
        access_token = access_token.strip()
        if not access_token:
            raise OAuthUsageError("Missing OAuth access token")
        self.access_token = access_token

    async def fetch_raw(self) -> dict[str, Any]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": f"Bearer {self.access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) "
                "Gecko/20100101 Firefox/139.0"
            ),
        }
        async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False) as client:
            response = await client.get("https://api.anthropic.com/api/oauth/usage")
        if response.status_code in {401, 403}:
            raise OAuthUsageAuthError(
                f"OAuth usage request was rejected: HTTP {response.status_code}"
            )
        if response.status_code == 429:
            raise OAuthUsageRateLimitError("OAuth usage request was rate-limited: HTTP 429")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise OAuthUsageError("OAuth usage response was not a JSON object")
        return payload
