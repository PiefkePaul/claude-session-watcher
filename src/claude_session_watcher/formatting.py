from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Berlin")


@dataclass(slots=True)
class UiLimit:
    utilization: float | None
    reset_display: str


@dataclass(slots=True)
class UiWatcher:
    watcher: object
    five_hour: UiLimit
    seven_day: UiLimit
    last_checked_display: str
    usage_source: str


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
    except ValueError:
        return None


def format_timestamp(value: str | None) -> str:
    dt = parse_dt(value)
    if not dt:
        return ""
    return dt.strftime("%H:%M  %d.%m.%Y")


def format_reset(value: str | None, *, weekly: bool = False) -> str:
    dt = parse_dt(value)
    if not dt:
        return ""
    if weekly:
        weekdays = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]
        return f"{weekdays[dt.weekday()]} {dt.strftime('%H:%M')}"
    return dt.strftime("%H:%M")


def _limit_from_usage(raw_json: str | None, key: str, *, weekly: bool = False) -> UiLimit:
    if not raw_json:
        return UiLimit(utilization=None, reset_display="")
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return UiLimit(utilization=None, reset_display="")
    section = data.get(key)
    if not isinstance(section, dict):
        return UiLimit(utilization=None, reset_display="")
    utilization = section.get("utilization")
    try:
        utilization = float(utilization)
    except (TypeError, ValueError):
        utilization = None
    return UiLimit(
        utilization=utilization,
        reset_display=format_reset(section.get("resets_at"), weekly=weekly),
    )


def _usage_source(raw_json: str | None) -> str:
    if not raw_json:
        return ""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return ""
    source = data.get("_csw_usage_source")
    return str(source) if source else ""


def build_ui_watcher(watcher) -> UiWatcher:
    return UiWatcher(
        watcher=watcher,
        five_hour=_limit_from_usage(watcher.last_usage_json, "five_hour"),
        seven_day=_limit_from_usage(watcher.last_usage_json, "seven_day", weekly=True),
        last_checked_display=format_timestamp(watcher.last_checked_at),
        usage_source=_usage_source(watcher.last_usage_json),
    )
