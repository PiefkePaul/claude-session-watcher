from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Account:
    id: int | None
    name: str
    profile_dir: str
    status: str = "created"
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class Watcher:
    id: int | None
    name: str
    account_id: int
    remote_url: str
    enabled: bool = True
    state: str = "active"
    five_hour_threshold: float = 95.0
    seven_day_threshold: float = 98.0
    resume_threshold: float = 5.0
    check_interval_seconds: int = 60
    pause_message: str = (
        "Pause after the current safe checkpoint. Do not start new work. "
        "Wait until I send continue."
    )
    continue_message: str = "continue"
    last_usage_json: str | None = None
    last_reason: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class WatcherEvent:
    id: int | None
    watcher_id: int
    level: str
    message: str
    created_at: str = ""
