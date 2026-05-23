from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import Watcher
from .usage import UsageSnapshot

WatcherAction = Literal["paused", "continued", "waiting", "ok"]


@dataclass(frozen=True, slots=True)
class WatcherDecision:
    action: WatcherAction
    state: str
    reason: str
    message: str | None = None
    event_level: str | None = None
    event_message: str | None = None


class WatcherEngine:
    def decide(self, watcher: Watcher, usage: UsageSnapshot) -> WatcherDecision:
        pause_reason = usage.is_pause_required(
            watcher.five_hour_threshold,
            watcher.seven_day_threshold,
        )

        if pause_reason:
            if watcher.state != "paused":
                return WatcherDecision(
                    action="paused",
                    state="paused",
                    reason=pause_reason,
                    message=watcher.pause_message,
                    event_level="warning",
                    event_message=f"Pause sent: {pause_reason}",
                )
            return WatcherDecision(action="waiting", state="paused", reason=pause_reason)

        if watcher.state == "paused":
            return WatcherDecision(
                action="continued",
                state="active",
                reason="blocking limit cleared",
                message=watcher.continue_message,
                event_level="info",
                event_message="Continue sent",
            )

        return WatcherDecision(action="ok", state="active", reason="usage ok")
