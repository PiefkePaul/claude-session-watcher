from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .usage import ClaudeCookie


class SessionListError(Exception):
    pass


class SessionListAuthError(SessionListError):
    pass


@dataclass(frozen=True, slots=True)
class SessionListPage:
    sessions: list[dict[str, Any]]
    has_more: bool
    last_id: str | None


class ClaudeWebSessionsClient:
    """Lists Claude Code sessions via claude.ai's web endpoint (/v1/sessions).

    This is an internal endpoint used by the claude.ai/code UI. It is *not* the public
    api.anthropic.com Sessions API. It relies on an already-authenticated claude.ai
    browser session (cookies) and a small set of headers the web UI uses.
    """

    def __init__(self, *, cookies: list[ClaudeCookie]):
        if not cookies:
            raise SessionListError("Missing claude.ai cookies")
        self.cookies = cookies

    def _headers(self) -> dict[str, str]:
        # Mirrors what the web UI uses for Claude Code session management.
        # (See: community script that calls /v1/sessions from the browser console.)
        return {
            "accept": "*/*",
            "origin": "https://claude.ai",
            "referer": "https://claude.ai/code",
            "anthropic-beta": "ccr-byoc-2025-07-29",
            "anthropic-client-feature": "ccr",
            "anthropic-client-platform": "web_claude_ai",
            "anthropic-version": "2023-06-01",
            # Included by the web console script; harmless for GET and required for DELETE.
            "content-type": "application/json",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) "
                "Gecko/20100101 Firefox/139.0"
            ),
        }

    def _client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            headers=self._headers(),
            timeout=20,
            follow_redirects=False,
        )
        for cookie in self.cookies:
            client.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain,
                path=cookie.path or "/",
            )
        return client

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise SessionListAuthError(
                f"Claude sessions request was rejected: HTTP {response.status_code}"
            )
        response.raise_for_status()

    @staticmethod
    def _parse_page(payload: object) -> SessionListPage:
        if not isinstance(payload, dict):
            raise SessionListError("Claude sessions response was not a JSON object")
        raw_sessions = payload.get("data") or payload.get("sessions") or []
        if not isinstance(raw_sessions, list):
            raise SessionListError("Claude sessions response did not contain a session list")
        sessions: list[dict[str, Any]] = [
            item for item in raw_sessions if isinstance(item, dict)
        ]
        has_more = bool(payload.get("has_more"))
        last_id = payload.get("last_id")
        last_id_str = str(last_id) if last_id else None
        return SessionListPage(sessions=sessions, has_more=has_more, last_id=last_id_str)

    async def list_page(self, *, after_id: str | None = None) -> SessionListPage:
        params = {"after_id": after_id} if after_id else None
        async with self._client() as client:
            response = await client.get("https://claude.ai/v1/sessions", params=params)
            self._raise_for_response(response)
            return self._parse_page(response.json())

    async def list_all(self, *, max_pages: int = 50) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        after_id: str | None = None
        for _ in range(max_pages):
            page = await self.list_page(after_id=after_id)
            sessions.extend(page.sessions)
            if not page.has_more or not page.last_id:
                break
            after_id = page.last_id
        return sessions

