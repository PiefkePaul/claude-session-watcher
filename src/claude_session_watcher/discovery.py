from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .browser import CamoufoxManager
from .models import Account, ClaudeSession, utc_now
from .store import Store


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    account_id: int
    found: int
    updated: int


class ClaudeSessionDiscoveryProvider:
    def __init__(self, browser: CamoufoxManager, *, keepalive: bool = False):
        self.browser = browser
        self.keepalive = keepalive

    async def discover(self, account: Account) -> list[ClaudeSession]:
        if account.id is None:
            raise ValueError("Account must be stored before session discovery")
        profile_dir = Path(account.profile_dir)
        try:
            raw_sessions = await self.browser.discover_code_sessions(profile_dir)
        finally:
            if not self.keepalive:
                await self.browser.close_profile(profile_dir)
        sessions: list[ClaudeSession] = []
        for raw in raw_sessions:
            session_key = str(raw.get("session_key") or "")
            url = str(raw.get("url") or "")
            if not session_key or not url:
                continue
            sessions.append(
                ClaudeSession(
                    id=None,
                    account_id=account.id,
                    session_key=session_key,
                    title=str(raw.get("title") or session_key),
                    url=url,
                    kind=str(raw.get("kind") or "unknown"),
                    status=str(raw.get("status") or "unknown"),
                    watch_enabled=False,
                    control_supported=bool(raw.get("control_supported")),
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
        for session in discovered:
            self.store.upsert_session(session)
            seen_keys.add(session.session_key)
            updated += 1
        if seen_keys:
            self.store.mark_missing_sessions(account.id, seen_keys)
        return DiscoveryResult(account_id=account.id, found=len(discovered), updated=updated)
