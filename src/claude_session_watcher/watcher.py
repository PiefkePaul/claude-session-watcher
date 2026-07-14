from __future__ import annotations

import asyncio
import json
import random

from .browser import CamoufoxManager
from .controller import (
    BrowserSessionController,
    FallbackSessionController,
    HttpSessionController,
    SessionController,
)
from .discovery import ClaudeSessionDiscoveryProvider, SessionDiscoveryService
from .engine import WatcherEngine
from .models import Account, AccountWatcher, Watcher
from .notifications import NotificationEvent, Notifier, notifier_from_settings
from .providers import (
    CamoufoxBrowserUsageProvider,
    CamoufoxCookiesHttpUsageProvider,
    FallbackUsageProvider,
    UsageProvider,
)
from .settings import Settings
from .store import Store
from .usage import UsageAuthError, UsageLoginRequiredError


class WatcherService:
    def __init__(
        self,
        store: Store,
        browser: CamoufoxManager,
        settings: Settings,
        *,
        usage_provider: UsageProvider | None = None,
        session_controller: SessionController | None = None,
        session_discovery: SessionDiscoveryService | None = None,
        engine: WatcherEngine | None = None,
        notifier: Notifier | None = None,
    ):
        self.store = store
        self.browser = browser
        self.settings = settings
        self.usage_provider = usage_provider or FallbackUsageProvider(
            CamoufoxCookiesHttpUsageProvider(),
            CamoufoxBrowserUsageProvider(browser, keepalive=settings.browser_keepalive),
        )
        if session_controller is None:
            self.session_controller = FallbackSessionController(
                HttpSessionController(),
                BrowserSessionController(
                    browser,
                    keepalive=settings.browser_keepalive,
                ),
            )
        else:
            self.session_controller = session_controller
        self.session_discovery = session_discovery or SessionDiscoveryService(
            store,
            ClaudeSessionDiscoveryProvider(
                browser,
                keepalive=getattr(settings, "browser_keepalive", False),
            ),
        )
        self.engine = engine or WatcherEngine(
            resume_safety_margin_seconds=getattr(settings, "resume_safety_margin_seconds", 120)
        )
        self.notifier = notifier or notifier_from_settings(settings)
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
                    await self._notify(
                        "watcher_error",
                        "Claude Session Watcher error",
                        str(exc),
                        level="error",
                    )
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

        await self._auto_discover_sessions(account, watcher)

        try:
            result = await self.usage_provider.fetch(account)
        except (UsageAuthError, UsageLoginRequiredError) as exc:
            # The Claude session is no longer valid server-side. Downgrade the
            # account status so the UI stops showing a stale "logged-in".
            if account.id is not None:
                self.store.update_account_status(account.id, "login-expired", str(exc))
            raise
        if account.id is not None and account.status == "login-expired":
            # Usage is readable again (e.g. the user re-authenticated in the
            # open browser) — restore the account status.
            self.store.update_account_status(account.id, "logged-in")
        decision = self.engine.decide(watcher, result.snapshot)
        raw = dict(result.snapshot.raw)
        raw["_csw_usage_source"] = result.source
        raw_json = json.dumps(raw, separators=(",", ":"), sort_keys=True)
        self.store.add_usage_sample(
            watcher.id,
            source=result.source,
            five_hour_utilization=(
                result.snapshot.five_hour.utilization if result.snapshot.five_hour else None
            ),
            seven_day_utilization=(
                result.snapshot.seven_day.utilization if result.snapshot.seven_day else None
            ),
            five_hour_resets_at=(
                result.snapshot.five_hour.resets_at if result.snapshot.five_hour else None
            ),
            seven_day_resets_at=(
                result.snapshot.seven_day.resets_at if result.snapshot.seven_day else None
            ),
            raw_json=raw_json,
        )

        if decision.message:
            await self._send_to_watched_sessions(watcher, account, decision.message)
        self.store.update_account_watcher_runtime(
            watcher.id,
            state=decision.state,
            last_usage_json=raw_json,
            last_reason=decision.reason,
            last_error=None,
            paused_at=decision.paused_at,
            paused_limit=decision.paused_limit,
            paused_until=decision.paused_until,
            clear_pause=decision.clear_pause,
        )
        if decision.event_level and decision.event_message:
            self.store.add_account_event(watcher.id, decision.event_level, decision.event_message)
            await self._notify(
                decision.action,
                f"Claude watcher {decision.action}",
                decision.event_message,
                level=decision.event_level,
            )
        return decision.action

    async def _auto_discover_sessions(
        self,
        account: Account,
        watcher: AccountWatcher,
    ) -> None:
        if watcher.id is None:
            return
        try:
            result = await self.session_discovery.discover_account(account)
        except Exception as exc:  # noqa: BLE001 - discovery must not block usage checks
            self.store.add_account_event(
                watcher.id,
                "warning",
                f"Session auto-discovery failed: {exc}",
            )
            return
        if result.selected:
            self.store.add_account_event(
                watcher.id,
                "info",
                f"Auto-selected {result.selected} new remote session(s)",
            )

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
            await self._notify(
                "no_selected_sessions",
                "No selected Claude sessions",
                "The account watcher wanted to send a command, but no sessions are selected.",
                level="warning",
            )
            return
        attempted = 0
        succeeded = 0
        failed = 0
        skipped = 0
        for session in sessions:
            if session.id is None:
                continue
            if not session.control_supported:
                skipped += 1
                self.store.add_account_event(
                    watcher.id,
                    "warning",
                    f"Skipped uncontrollable session: {session.title}",
                    session_id=session.id,
                )
                await self._notify(
                    "session_skipped",
                    "Claude session skipped",
                    f"Skipped uncontrollable session: {session.title}",
                    level="warning",
                )
                continue
            attempted += 1
            if session.status in {"archived", "offline"}:
                self.store.add_account_event(
                    watcher.id,
                    "warning",
                    f"Session marked {session.status}; trying anyway: {session.title}",
                    session_id=session.id,
                )
            try:
                await self.session_controller.send_to_session(account, session, message)
                self.store.update_session_control_error(session.id, None)
                succeeded += 1
                self.store.add_account_event(
                    watcher.id,
                    "info",
                    f"Command sent to session: {session.title}",
                    session_id=session.id,
                )
            except Exception as exc:  # noqa: BLE001 - keep other sessions controllable
                failed += 1
                self.store.update_session_control_error(session.id, str(exc))
                self.store.add_account_event(
                    watcher.id,
                    "error",
                    f"Could not control session {session.title}: {exc}",
                    session_id=session.id,
                )
                await self._notify(
                    "remote_failed",
                    "Claude remote control failed",
                    f"Could not control session {session.title}: {exc}",
                    level="error",
                )
        self.store.add_account_event(
            watcher.id,
            "info",
            (
                f"Dispatch summary: selected={len(sessions)}, attempted={attempted}, "
                f"succeeded={succeeded}, failed={failed}, skipped={skipped}"
            ),
        )

    async def _notify(self, event_type: str, title: str, message: str, *, level: str) -> None:
        try:
            await self.notifier.notify(
                NotificationEvent(
                    event_type=event_type,
                    title=title,
                    message=message,
                    level=level,
                )
            )
        except Exception as exc:  # noqa: BLE001 - notifications must not stop watcher loops
            print(f"Notification failed: {exc}")
