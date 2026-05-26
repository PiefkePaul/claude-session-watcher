from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .pause_templates import CUSTOM_TEMPLATE, DEFAULT_PAUSE_MESSAGE


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
    pause_template: str = CUSTOM_TEMPLATE
    pause_message: str = DEFAULT_PAUSE_MESSAGE
    continue_message: str = "continue"
    last_usage_json: str | None = None
    last_reason: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class AccountWatcher:
    id: int | None
    account_id: int
    enabled: bool = True
    state: str = "active"
    five_hour_threshold: float = 95.0
    seven_day_threshold: float = 98.0
    resume_threshold: float = 5.0
    check_interval_seconds: int = 60
    pause_template: str = CUSTOM_TEMPLATE
    pause_message: str = DEFAULT_PAUSE_MESSAGE
    continue_message: str = "continue"
    paused_at: str | None = None
    paused_limit: str | None = None
    paused_until: str | None = None
    last_usage_json: str | None = None
    last_reason: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ClaudeSession:
    id: int | None
    account_id: int
    session_key: str
    title: str
    url: str
    kind: str = "unknown"
    status: str = "unknown"
    watch_enabled: bool = False
    control_supported: bool = False
    raw_json: str | None = None
    last_seen_at: str | None = None
    last_checked_at: str | None = None
    last_control_error: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class WatcherEvent:
    id: int | None
    watcher_id: int
    level: str
    message: str
    created_at: str = ""


@dataclass(slots=True)
class AccountWatcherEvent:
    id: int | None
    account_watcher_id: int
    session_id: int | None
    level: str
    message: str
    created_at: str = ""


@dataclass(slots=True)
class UsageSample:
    id: int | None
    account_watcher_id: int
    source: str
    five_hour_utilization: float | None = None
    seven_day_utilization: float | None = None
    five_hour_resets_at: str | None = None
    seven_day_resets_at: str | None = None
    raw_json: str | None = None
    created_at: str = ""
