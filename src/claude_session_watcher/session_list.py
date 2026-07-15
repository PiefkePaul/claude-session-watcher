from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from .usage import ClaudeCookie, is_cloudflare_challenge


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
            if is_cloudflare_challenge(response):
                raise SessionListError(
                    "Claude sessions request was blocked by a Cloudflare challenge "
                    "(bot protection, not an auth problem)"
                )
            raise SessionListAuthError(
                f"Claude sessions request was rejected: HTTP {response.status_code}"
            )
        response.raise_for_status()

    @staticmethod
    def _parse_page(payload: object) -> SessionListPage:
        if not isinstance(payload, dict):
            raise SessionListError("Claude sessions response was not a JSON object")
        raw_sessions = _find_session_list(payload)
        if raw_sessions is None:
            raise SessionListError("Claude sessions response did not contain a session list")
        sessions: list[dict[str, Any]] = [
            item for item in raw_sessions if isinstance(item, dict)
        ]
        has_more = bool(
            _first_page_value(
                payload,
                ("has_more", "hasMore", "has_next_page", "hasNextPage"),
            )
        )
        last_id = _first_page_value(
            payload,
            ("last_id", "lastId", "end_cursor", "endCursor", "next_cursor", "nextCursor"),
        )
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


def raw_session_id(raw: dict[str, Any]) -> str | None:
    for key in ("id", "session_key", "session_id", "sessionId", "uuid"):
        value = raw.get(key)
        if value:
            return str(value)
    return None


def raw_session_title(raw: dict[str, Any], fallback: str) -> str:
    for key in ("title", "name", "display_name", "displayName"):
        value = raw.get(key)
        if value:
            return str(value)
    return fallback


def raw_session_status(raw: dict[str, Any]) -> str:
    for key in ("session_status", "status", "state", "connection_status", "connectionStatus"):
        value = raw.get(key)
        if value:
            return str(value)
    return "unknown"


def raw_session_url(raw: dict[str, Any], session_key: str) -> str:
    for key in ("url", "remote_url", "remoteUrl", "href", "link"):
        value = raw.get(key)
        if value:
            return str(value)
    return f"https://claude.ai/code/{session_key}"


def raw_session_is_remote_control(raw: dict[str, Any]) -> bool:
    for key in (
        "control_supported",
        "controlSupported",
        "remote_control",
        "remoteControl",
        "remote_control_supported",
        "remoteControlSupported",
    ):
        value = raw.get(key)
        if isinstance(value, bool):
            return value
    kind = str(raw.get("kind") or raw.get("type") or "").lower()
    if kind in {"remote", "remote_control", "remote-control"}:
        return True
    tags = raw.get("tags")
    if isinstance(tags, str):
        tag_values = [tags]
    elif isinstance(tags, list):
        tag_values = [str(tag) for tag in tags]
    else:
        tag_values = []
    normalized_tags = {
        "".join(char for char in tag.lower() if char.isalnum())
        for tag in tag_values
    }
    return bool(
        normalized_tags
        & {
            "remote",
            "remotecontrol",
            "remotecontrolrepl",
            "ccr",
            "claudecode",
        }
    )


def _find_session_list(value: object) -> list[object] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("data", "sessions", "items", "results", "nodes"):
        nested = value.get(key)
        if isinstance(nested, list):
            return nested
        found = _find_session_list(nested)
        if found is not None:
            return found
    return None


def _first_page_value(payload: dict[str, Any], keys: tuple[str, ...]) -> object | None:
    sources: list[dict[str, Any]] = [payload]
    for key in ("data", "pagination", "page_info", "pageInfo", "meta"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
            for nested_key in ("pagination", "page_info", "pageInfo", "meta"):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    sources.append(nested)
    for source in sources:
        for key in keys:
            if key in source:
                return source[key]
    return None
