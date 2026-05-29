from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import httpx

from .browser import CamoufoxManager
from .models import Account, ClaudeSession, Watcher
from .profile_cookies import load_claude_cookies
from .session_list import ClaudeWebSessionsClient, SessionListError


class SessionController(Protocol):
    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        pass

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        pass


class BrowserSessionController:
    def __init__(self, browser: CamoufoxManager, *, keepalive: bool = False):
        self.browser = browser
        self.keepalive = keepalive

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        await self._send_url(account, watcher.remote_url, message)

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        await self._send_url(account, session.url, message)

    async def _send_url(self, account: Account, url: str, message: str) -> None:
        profile_dir = Path(account.profile_dir)
        try:
            await self.browser.send_prompt(profile_dir, url, message)
        finally:
            if not self.keepalive:
                await self.browser.close_profile(profile_dir)


class HttpSessionController:
    """Send prompts through claude.ai session events (no browser automation)."""

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        session_key = _session_key_from_url(watcher.remote_url)
        if not session_key:
            raise SessionListError("Could not determine session id from remote URL")
        await self._send_session_key(account, session_key, message)

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        primary_key = session.session_key or _session_key_from_url(session.url)
        if not primary_key:
            raise SessionListError(f"Session id missing for session {session.title!r}")

        cookies = load_claude_cookies(Path(account.profile_dir))
        client = ClaudeWebSessionsClient(cookies=cookies)
        attempted_keys = _unique_nonempty([primary_key, _session_key_from_url(session.url)])
        last_exc: Exception | None = None
        for key in attempted_keys:
            try:
                await client.send_user_message(key, message)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_retryable_session_send_error(exc):
                    raise

        if last_exc is None:
            raise SessionListError("No usable session id available for HTTP control")

        resolved_id, resolved_url = await self._resolve_live_session_id(
            client, session, attempted_keys
        )
        if not resolved_id:
            raise last_exc
        await client.send_user_message(resolved_id, message)
        # Keep in-memory session object aligned for the current watcher cycle.
        session.session_key = resolved_id
        if resolved_url:
            session.url = resolved_url

    async def _send_session_key(self, account: Account, session_key: str, message: str) -> None:
        cookies = load_claude_cookies(Path(account.profile_dir))
        client = ClaudeWebSessionsClient(cookies=cookies)
        await client.send_user_message(session_key, message)

    async def _resolve_live_session_id(
        self,
        client: ClaudeWebSessionsClient,
        session: ClaudeSession,
        attempted_keys: list[str],
    ) -> tuple[str | None, str | None]:
        try:
            all_sessions = await client.list_all()
        except Exception:  # noqa: BLE001
            return (None, None)
        if not all_sessions:
            return (None, None)

        # 1) Exact ID/url-key match.
        for raw in all_sessions:
            raw_id = _raw_session_id(raw)
            raw_url = str(raw.get("url") or "")
            raw_url_key = _session_key_from_url(raw_url)
            if raw_id and raw_id in attempted_keys:
                return (raw_id, raw_url or None)
            if raw_url_key and raw_url_key in attempted_keys and raw_id:
                return (raw_id, raw_url or None)

        # 2) Title-based fallback (prefer non-archived + remote-control capable).
        wanted_title = str(session.title or "").strip().casefold()
        if not wanted_title:
            return (None, None)
        candidates: list[tuple[int, dict[str, object]]] = []
        for raw in all_sessions:
            raw_id = _raw_session_id(raw)
            if not raw_id:
                continue
            title = str(raw.get("title") or "").strip().casefold()
            if title != wanted_title:
                continue
            status = str(raw.get("session_status") or raw.get("status") or "").strip().lower()
            tags = raw.get("tags")
            is_remote = isinstance(tags, list) and any(
                str(tag) == "remote-control-repl" for tag in tags
            )
            rank = 0
            if status in {"active", "online", "idle"}:
                rank += 2
            if status == "archived":
                rank -= 2
            if is_remote:
                rank += 2
            if raw_id.startswith("session_"):
                rank += 1
            candidates.append((rank, raw))
        if not candidates:
            return (None, None)
        candidates.sort(key=lambda item: item[0], reverse=True)
        best = candidates[0][1]
        best_id = _raw_session_id(best)
        best_url = str(best.get("url") or "")
        return (best_id, best_url or None)


class FallbackSessionController:
    """Try HTTP control first, then browser control as fallback."""

    def __init__(self, primary: SessionController, fallback: SessionController):
        self.primary = primary
        self.fallback = fallback

    async def send(self, watcher: Watcher, account: Account, message: str) -> None:
        try:
            await self.primary.send(watcher, account, message)
        except Exception:  # noqa: BLE001 - fallback must handle evolving claude.ai API errors
            await self.fallback.send(watcher, account, message)

    async def send_to_session(self, account: Account, session: ClaudeSession, message: str) -> None:
        try:
            await self.primary.send_to_session(account, session, message)
        except Exception:  # noqa: BLE001 - fallback must handle evolving claude.ai API errors
            await self.fallback.send_to_session(account, session, message)


def _session_key_from_url(url: str) -> str | None:
    match = re.search(r"/code/(session_[A-Za-z0-9]+)", str(url or ""))
    if match:
        return match.group(1)
    return None


def _raw_session_id(raw: dict[str, object]) -> str | None:
    value = raw.get("id") or raw.get("session_key")
    if not value:
        return None
    return str(value)


def _unique_nonempty(values: list[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _is_retryable_session_send_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        return status in {400, 404}
    text = str(exc).lower()
    if "400 bad request" in text or "404 not found" in text:
        return "/v1/sessions/" in text
    return False
