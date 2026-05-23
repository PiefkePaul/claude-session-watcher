from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from .models import AccountWatcher, Watcher
from .pause_templates import render_pause_message
from .usage import LimitSection, UsageSnapshot

WatcherAction = Literal["paused", "continued", "waiting", "ok"]


@dataclass(frozen=True, slots=True)
class WatcherDecision:
    action: WatcherAction
    state: str
    reason: str
    message: str | None = None
    event_level: str | None = None
    event_message: str | None = None
    paused_at: str | None = None
    paused_limit: str | None = None
    paused_until: str | None = None
    clear_pause: bool = False


class WatcherEngine:
    def __init__(self, *, resume_safety_margin_seconds: int = 120):
        self.resume_safety_margin_seconds = resume_safety_margin_seconds

    def decide(self, watcher: Watcher | AccountWatcher, usage: UsageSnapshot) -> WatcherDecision:
        blocked = self._blocked_limits(watcher, usage)
        pause_reason = self._pause_reason(blocked)

        if pause_reason:
            paused_limit = self._blocked_limit_key(blocked)
            paused_until = self._blocked_until(blocked)
            paused_at = getattr(watcher, "paused_at", None) or self._now().isoformat()
            pause_message = render_pause_message(
                getattr(watcher, "pause_template", None),
                watcher.pause_message,
            )
            if watcher.state != "paused":
                return WatcherDecision(
                    action="paused",
                    state="paused",
                    reason=pause_reason,
                    message=pause_message,
                    event_level="warning",
                    event_message=f"Pause sent: {pause_reason}",
                    paused_at=paused_at,
                    paused_limit=paused_limit,
                    paused_until=paused_until,
                )
            return WatcherDecision(
                action="waiting",
                state="paused",
                reason=pause_reason,
                paused_at=paused_at,
                paused_limit=paused_limit,
                paused_until=paused_until,
            )

        if watcher.state == "paused":
            wait_reason = self._resume_wait_reason(watcher)
            if wait_reason:
                return WatcherDecision(action="waiting", state="paused", reason=wait_reason)
            return WatcherDecision(
                action="continued",
                state="active",
                reason="blocking limit cleared",
                message=watcher.continue_message,
                event_level="info",
                event_message="Continue sent",
                clear_pause=True,
            )

        return WatcherDecision(action="ok", state="active", reason="usage ok", clear_pause=True)

    def _blocked_limits(
        self,
        watcher: Watcher | AccountWatcher,
        usage: UsageSnapshot,
    ) -> list[tuple[str, LimitSection]]:
        blocked: list[tuple[str, LimitSection]] = []
        if usage.five_hour and usage.five_hour.utilization >= watcher.five_hour_threshold:
            blocked.append(("five_hour", usage.five_hour))
        if usage.seven_day and usage.seven_day.utilization >= watcher.seven_day_threshold:
            blocked.append(("seven_day", usage.seven_day))
        return blocked

    @staticmethod
    def _pause_reason(blocked: list[tuple[str, LimitSection]]) -> str | None:
        reasons = []
        for key, section in blocked:
            label = "5-hour" if key == "five_hour" else "7-day"
            reasons.append((section.utilization, f"{label} limit at {section.utilization:.1f}%"))
        if not reasons:
            return None
        return max(reasons, key=lambda item: item[0])[1]

    @staticmethod
    def _blocked_limit_key(blocked: list[tuple[str, LimitSection]]) -> str | None:
        if not blocked:
            return None
        return max(blocked, key=lambda item: item[1].utilization)[0]

    @staticmethod
    def _blocked_until(blocked: list[tuple[str, LimitSection]]) -> str | None:
        resets = [
            section.resets_at
            for _key, section in blocked
            if section.resets_at
        ]
        if not resets:
            return None
        parsed = [_parse_dt(value) for value in resets]
        parsed = [value for value in parsed if value is not None]
        if not parsed:
            return resets[0]
        return max(parsed).isoformat()

    def _resume_wait_reason(self, watcher: Watcher | AccountWatcher) -> str | None:
        paused_until = getattr(watcher, "paused_until", None)
        if not paused_until:
            return None
        reset_at = _parse_dt(paused_until)
        if not reset_at:
            return None
        resume_after = reset_at + timedelta(seconds=max(0, self.resume_safety_margin_seconds))
        if self._now() < resume_after:
            return f"waiting for reset safety margin until {resume_after.isoformat()}"
        return None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
