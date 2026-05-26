from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

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
        self._cookie_map = {
            cookie.name: cookie.value
            for cookie in cookies
            if cookie.name and cookie.value
        }

    def _headers(self) -> dict[str, str]:
        # Mirrors what the web UI uses for Claude Code session management.
        # (See: community script that calls /v1/sessions from the browser console.)
        headers = {
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
        # When multiple orgs exist, claude.ai sometimes relies on these hints.
        last_active_org = self._cookie_map.get("lastActiveOrg")
        if last_active_org:
            headers["x-organization-uuid"] = last_active_org
        activity_session = self._cookie_map.get("activitySessionId")
        if activity_session:
            headers["x-activity-session-id"] = activity_session
        device_id = self._cookie_map.get("anthropic-device-id")
        if device_id:
            headers["anthropic-device-id"] = device_id
        return headers

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

    async def delete_session(self, session_id: str) -> None:
        # The claude.ai UI issues a DELETE with an (empty) JSON body.
        async with self._client() as client:
            response = await client.request(
                "DELETE",
                f"https://claude.ai/v1/sessions/{session_id}",
                content="{}",
            )
            self._raise_for_response(response)

    async def archive_session(self, session_id: str) -> None:
        async with self._client() as client:
            response = await client.post(
                f"https://claude.ai/v1/sessions/{session_id}/archive",
                content="{}",
            )
            self._raise_for_response(response)

    async def list_events(
        self,
        session_id: str,
        *,
        after_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, object] = {}
        if after_id:
            params["after_id"] = after_id
        if limit is not None:
            params["limit"] = int(limit)
        async with self._client() as client:
            response = await client.get(
                f"https://claude.ai/v1/sessions/{session_id}/events",
                params=params or None,
            )
            self._raise_for_response(response)
            data = response.json()
        if not isinstance(data, dict):
            raise SessionListError("Claude session events response was not a JSON object")
        return data

    async def send_user_message(self, session_id: str, text: str) -> None:
        # Observed from claude.ai/code remote control:
        # POST /v1/sessions/{id}/events
        # {
        #   "events":[
        #     {
        #       "type":"user",
        #       "uuid":"...",
        #       "session_id":"session_...",
        #       "parent_tool_use_id":null,
        #       "message":{"role":"user","content":"..."}
        #     }
        #   ]
        # }
        primary_payload = {
            "events": [
                {
                    "type": "user",
                    "uuid": str(uuid4()),
                    "session_id": session_id,
                    "parent_tool_use_id": None,
                    "message": {"role": "user", "content": text},
                }
            ]
        }

        # Fallback payloads kept for compatibility experiments.
        payload_candidates = [
            primary_payload,
            {
                "events": [
                    {
                        "type": "user_message",
                        "content": [{"type": "text", "text": text}],
                    }
                ]
            },
            {
                "events": [
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": text}],
                    }
                ]
            },
            {
                "type": "user_message",
                "content": [{"type": "text", "text": text}],
            },
            {
                "type": "user.message",
                "content": [{"type": "text", "text": text}],
            },
        ]

        last_exc: Exception | None = None
        async with self._client() as client:
            for payload in payload_candidates:
                try:
                    response = await client.post(
                        f"https://claude.ai/v1/sessions/{session_id}/events",
                        json=payload,
                    )
                    self._raise_for_response(response)
                    try:
                        data = response.json()
                    except Exception:  # noqa: BLE001
                        return
                    # Soft verification: prefer responses that echo a user event.
                    if isinstance(data, dict):
                        events = data.get("events")
                        if isinstance(events, list) and any(
                            isinstance(event, dict) and event.get("type") == "user"
                            for event in events
                        ):
                            return
                    # If endpoint accepted payload but response is not event-shaped,
                    # still treat as success to preserve compatibility.
                    return
                except Exception as exc:  # noqa: BLE001 - try next candidate
                    last_exc = exc
                    continue
        if last_exc:
            raise last_exc
        raise SessionListError("Failed to send message (no payload candidates attempted)")
