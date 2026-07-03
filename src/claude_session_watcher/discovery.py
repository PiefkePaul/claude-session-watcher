from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .browser import CamoufoxManager
from .models import Account, ClaudeSession, utc_now
from .profile_cookies import load_claude_cookies
from .session_list import (
    ClaudeWebSessionsClient,
    raw_session_id,
    raw_session_is_remote_control,
    raw_session_status,
    raw_session_title,
    raw_session_url,
)
from .store import Store
from .usage import UsageLoginRequiredError


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    account_id: int
    found: int
    updated: int
    selected: int = 0


class ClaudeSessionDiscoveryProvider:
    def __init__(self, browser: CamoufoxManager, *, keepalive: bool = False):
        self.browser = browser
        self.keepalive = keepalive

    async def discover(self, account: Account) -> list[ClaudeSession]:
        if account.id is None:
            raise ValueError("Account must be stored before session discovery")
        profile_dir = Path(account.profile_dir)
        raw_sessions: list[dict[str, object]]
        try:
            # Prefer the lightweight web API endpoint used by the Claude Code UI.
            cookies = load_claude_cookies(profile_dir)
            client = ClaudeWebSessionsClient(cookies=cookies)
            raw_sessions = await client.list_all()
        except UsageLoginRequiredError:
            # No cookies/session means there's nothing to discover yet.
            raise
        except Exception as primary_exc:  # noqa: BLE001
            # Fallback to browser-driven discovery for resilience when the web endpoint
            # changes or a corporate proxy blocks it.
            try:
                raw_sessions = await self.browser.discover_code_sessions(profile_dir)
            except Exception as fallback_exc:  # noqa: BLE001
                # Surface the primary failure first (it is usually clearer: auth/403).
                raise primary_exc from fallback_exc
            finally:
                if not self.keepalive:
                    await self.browser.close_profile(profile_dir)
        # Cookie-based discovery does not require opening/closing the browser.
        sessions: list[ClaudeSession] = []
        for raw in raw_sessions:
            # Two discovery backends exist:
            # - cookie/http: returns v1 session objects where "id" is the session id
            # - browser/dom: returns our internal dict with "session_key" + "url"
            session_key = raw_session_id(raw)
            if not session_key:
                continue
            is_remote = raw_session_is_remote_control(raw)
            url = raw_session_url(raw, session_key)
            status = raw_session_status(raw)
            sessions.append(
                ClaudeSession(
                    id=None,
                    account_id=account.id,
                    session_key=session_key,
                    title=raw_session_title(raw, session_key),
                    url=url,
                    kind=str(raw.get("kind") or ("remote" if is_remote else "cloud")),
                    status=str(status or "unknown"),
                    watch_enabled=False,
                    control_supported=bool(raw.get("control_supported", is_remote)),
                    raw_json=json.dumps(raw, separators=(",", ":"), sort_keys=True),
                    last_seen_at=utc_now(),
                )
            )
        return sessions


class SessionDiscoveryService:
    def __init__(self, store: Store, provider: ClaudeSessionDiscoveryProvider):
        self.store = store
        self.provider = provider

    async def discover_account(self, account: Account) -> DiscoveryResult:
        if account.id is None:
            raise ValueError("Account must be stored before session discovery")
        discovered = await self.provider.discover(account)
        seen_keys: set[str] = set()
        updated = 0
        selected = 0
        for session in discovered:
            existing = self._existing_session(account.id, session.session_key)
            if existing is None and _should_auto_select(session):
                session.watch_enabled = True
            saved = self.store.upsert_session(session)
            if existing is None and saved.watch_enabled:
                selected += 1
            seen_keys.add(session.session_key)
            updated += 1
        if seen_keys:
            self.store.mark_missing_sessions(account.id, seen_keys)
        return DiscoveryResult(
            account_id=account.id,
            found=len(discovered),
            updated=updated,
            selected=selected,
        )

    def _existing_session(self, account_id: int, session_key: str) -> ClaudeSession | None:
        try:
            return self.store.get_session_by_key(account_id, session_key)
        except KeyError:
            return None


def _should_auto_select(session: ClaudeSession) -> bool:
    if session.status.lower() == "archived":
        return False
    return session.control_supported or session.kind.lower() == "remote"
