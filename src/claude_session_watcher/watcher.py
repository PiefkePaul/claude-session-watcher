from __future__ import annotations

import asyncio
import json
import random

from .browser import CamoufoxManager
from .controller import BrowserSessionController, SessionController
from .engine import WatcherEngine
from .models import Account, AccountWatcher, Watcher
from .providers import (
    CamoufoxBrowserUsageProvider,
    CamoufoxCookiesHttpUsageProvider,
    FallbackUsageProvider,
    UsageProvider,
)
from .settings import Settings
from .store import Store


class WatcherService:
    def __init__(
        self,
        store: Store,
        browser: CamoufoxManager,
        settings: Settings,
        *,
        usage_provider: UsageProvider | None = None,
        session_controller: SessionController | None = None,
        engine: WatcherEngine | None = None,
    ):
        self.store = store
        self.browser = browser
        self.settings = settings
        self.usage_provider = usage_provider or FallbackUsageProvider(
            CamoufoxCookiesHttpUsageProvider(),
            CamoufoxBrowserUsageProvider(browser, keepalive=settings.browser_keepalive),
        )
        self.session_controller = session_controller or BrowserSessionController(
            browser,
            keepalive=settings.browser_keepalive,
        )
        self.engine = engine or WatcherEngine()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._next_due: dict[int, float] = {}

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="watcher-service")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def check_now(self, watcher_id: int) -> str:
        watcher = self.store.get_watcher(watcher_id)
        account = self.store.get_account(watcher.account_id)
        if account.id is None:
            raise ValueError("Account must be stored before checking")
        account_watcher = self.store.ensure_account_watcher(account.id)
        return await self._check_account_watcher(account, account_watcher)

    async def check_account_now(self, account_watcher_id: int) -> str:
        account_watcher = self.store.get_account_watcher(account_watcher_id)
        account = self.store.get_account(account_watcher.account_id)
        return await self._check_account_watcher(account, account_watcher)

    def reschedule_now(self, watcher_id: int) -> None:
        self._next_due.pop(watcher_id, None)

    async def _run(self) -> None:
        while not self._stop.is_set():
            loop_time = asyncio.get_running_loop().time()
            for watcher in self.store.list_account_watchers(enabled_only=True):
                if watcher.id is None:
                    continue
                due = self._next_due.get(watcher.id, 0)
                if loop_time < due:
                    continue
                try:
                    account = self.store.get_account(watcher.account_id)
                    await self._check_account_watcher(account, watcher)
                except Exception as exc:  # noqa: BLE001 - watcher loop must stay alive
                    self.store.update_account_watcher_runtime(
                        watcher.id,
                        state=watcher.state,
                        last_error=str(exc),
                    )
                    self.store.add_account_event(watcher.id, "error", str(exc))
                finally:
                    jitter = random.randint(0, max(0, self.settings.check_jitter_seconds))
                    self._next_due[watcher.id] = (
                        asyncio.get_running_loop().time()
                        + max(10, watcher.check_interval_seconds)
                        + jitter
                    )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except TimeoutError:
                pass

    async def _check_account_watcher(self, account: Account, watcher: AccountWatcher) -> str:
        if watcher.id is None:
            raise ValueError("Watcher must be stored before checking")

        result = await self.usage_provider.fetch(account)
        decision = self.engine.decide(watcher, result.snapshot)
        raw = dict(result.snapshot.raw)
        raw["_csw_usage_source"] = result.source
        raw_json = json.dumps(raw, separators=(",", ":"), sort_keys=True)

        if decision.message:
            await self._send_to_watched_sessions(watcher, account, decision.message)
        self.store.update_account_watcher_runtime(
            watcher.id,
            state=decision.state,
            last_usage_json=raw_json,
            last_reason=decision.reason,
            last_error=None,
        )
        if decision.event_level and decision.event_message:
            self.store.add_account_event(watcher.id, decision.event_level, decision.event_message)
        return decision.action

    async def _send(self, watcher: Watcher, account: Account, message: str) -> None:
        await self.session_controller.send(watcher, account, message)

    async def _send_to_watched_sessions(
        self,
        watcher: AccountWatcher,
        account: Account,
        message: str,
    ) -> None:
        if watcher.id is None or account.id is None:
            raise ValueError("Account watcher and account must be stored before sending")
        sessions = self.store.list_watched_sessions(account.id)
        if not sessions:
            self.store.add_account_event(watcher.id, "warning", "No selected sessions enabled")
            return
        for session in sessions:
            if session.id is None:
                continue
            if session.status in {"archived", "offline"} or not session.control_supported:
                self.store.add_account_event(
                    watcher.id,
                    "warning",
                    f"Skipped unavailable session: {session.title}",
                    session_id=session.id,
                )
                continue
            try:
                await self.session_controller.send_to_session(account, session, message)
                self.store.update_session_control_error(session.id, None)
            except Exception as exc:  # noqa: BLE001 - keep other sessions controllable
                self.store.update_session_control_error(session.id, str(exc))
                self.store.add_account_event(
                    watcher.id,
                    "error",
                    f"Could not control session {session.title}: {exc}",
                    session_id=session.id,
                )
