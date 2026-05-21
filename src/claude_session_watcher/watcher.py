from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from .browser import BrowserError, CamoufoxManager
from .models import Account, Watcher
from .settings import Settings
from .store import Store
from .usage import ClaudeUsageClient


class WatcherService:
    def __init__(self, store: Store, browser: CamoufoxManager, settings: Settings):
        self.store = store
        self.browser = browser
        self.settings = settings
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
        return await self._check_watcher(account, watcher)

    def reschedule_now(self, watcher_id: int) -> None:
        self._next_due.pop(watcher_id, None)

    async def _run(self) -> None:
        while not self._stop.is_set():
            loop_time = asyncio.get_running_loop().time()
            for watcher in self.store.list_watchers(enabled_only=True):
                if watcher.id is None:
                    continue
                due = self._next_due.get(watcher.id, 0)
                if loop_time < due:
                    continue
                try:
                    account = self.store.get_account(watcher.account_id)
                    await self._check_watcher(account, watcher)
                except Exception as exc:  # noqa: BLE001 - watcher loop must stay alive
                    self.store.update_watcher_runtime(
                        watcher.id,
                        state=watcher.state,
                        last_error=str(exc),
                    )
                    self.store.add_event(watcher.id, "error", str(exc))
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

    async def _check_watcher(self, account: Account, watcher: Watcher) -> str:
        if watcher.id is None:
            raise ValueError("Watcher must be stored before checking")

        profile_dir = Path(account.profile_dir)
        usage_data = await self.browser.fetch_usage(profile_dir)
        usage = ClaudeUsageClient._parse(usage_data)
        pause_reason = usage.is_pause_required(
            watcher.five_hour_threshold,
            watcher.seven_day_threshold,
        )

        raw_json = json.dumps(usage.raw, separators=(",", ":"), sort_keys=True)
        if pause_reason:
            if watcher.state != "paused":
                await self._send(watcher, account, watcher.pause_message)
                self.store.update_watcher_runtime(
                    watcher.id,
                    state="paused",
                    last_usage_json=raw_json,
                    last_reason=pause_reason,
                    last_error=None,
                )
                self.store.add_event(watcher.id, "warning", f"Pause sent: {pause_reason}")
                return "paused"
            self.store.update_watcher_runtime(
                watcher.id,
                state="paused",
                last_usage_json=raw_json,
                last_reason=pause_reason,
                last_error=None,
            )
            return "waiting"

        if watcher.state == "paused":
            if usage.is_resume_ready(
                watcher.five_hour_threshold,
                watcher.seven_day_threshold,
            ):
                await self._send(watcher, account, watcher.continue_message)
                self.store.update_watcher_runtime(
                    watcher.id,
                    state="active",
                    last_usage_json=raw_json,
                    last_reason="blocking limit cleared",
                    last_error=None,
                )
                self.store.add_event(watcher.id, "info", "Continue sent")
                return "continued"
            self.store.update_watcher_runtime(
                watcher.id,
                state="paused",
                last_usage_json=raw_json,
                last_reason="waiting for blocking limit to drop below threshold",
                last_error=None,
            )
            return "waiting"

        self.store.update_watcher_runtime(
            watcher.id,
            state="active",
            last_usage_json=raw_json,
            last_reason="usage ok",
            last_error=None,
        )
        return "ok"

    async def _send(self, watcher: Watcher, account: Account, message: str) -> None:
        try:
            await self.browser.send_prompt(Path(account.profile_dir), watcher.remote_url, message)
        except BrowserError:
            raise
