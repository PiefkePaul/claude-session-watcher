from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import AccountWatcher, UsageSample
from .usage import ClaudeUsageClient


@dataclass(frozen=True, slots=True)
class UsageInsights:
    status: str
    reason: str
    sample_count: int
    five_hour_burn_per_hour: float | None
    seven_day_burn_per_hour: float | None
    five_hour_pause_at: str | None
    seven_day_pause_at: str | None
    next_pause_at: str | None


@dataclass(frozen=True, slots=True)
class _CurrentUsage:
    five_hour_utilization: float | None
    seven_day_utilization: float | None
    five_hour_resets_at: str | None
    seven_day_resets_at: str | None
    created_at: str | None


def build_usage_insights(
    watcher: AccountWatcher,
    samples: list[UsageSample],
    *,
    near_limit_ratio: float = 0.9,
    soon_window_minutes: int = 30,
) -> UsageInsights:
    chronological = sorted(samples, key=lambda sample: sample.created_at)
    current = _current_usage(watcher, chronological)
    five_burn = _burn_rate_per_hour(chronological, "five_hour")
    seven_burn = _burn_rate_per_hour(chronological, "seven_day")
    five_pause_at = _project_pause_at(
        current.created_at,
        current.five_hour_utilization,
        watcher.five_hour_threshold,
        five_burn,
    )
    seven_pause_at = _project_pause_at(
        current.created_at,
        current.seven_day_utilization,
        watcher.seven_day_threshold,
        seven_burn,
    )
    next_pause_at = _earliest([five_pause_at, seven_pause_at])
    status, reason = _status(
        watcher,
        current,
        next_pause_at,
        near_limit_ratio=near_limit_ratio,
        soon_window_minutes=soon_window_minutes,
    )
    if status in {"paused", "weekly-blocked"}:
        next_pause_at = None
    return UsageInsights(
        status=status,
        reason=reason,
        sample_count=len(samples),
        five_hour_burn_per_hour=five_burn,
        seven_day_burn_per_hour=seven_burn,
        five_hour_pause_at=five_pause_at,
        seven_day_pause_at=seven_pause_at,
        next_pause_at=next_pause_at,
    )


def _current_usage(watcher: AccountWatcher, samples: list[UsageSample]) -> _CurrentUsage:
    if samples:
        latest = samples[-1]
        return _CurrentUsage(
            five_hour_utilization=latest.five_hour_utilization,
            seven_day_utilization=latest.seven_day_utilization,
            five_hour_resets_at=latest.five_hour_resets_at,
            seven_day_resets_at=latest.seven_day_resets_at,
            created_at=latest.created_at,
        )
    if watcher.last_usage_json:
        try:
            data = json.loads(watcher.last_usage_json)
        except json.JSONDecodeError:
            data = {}
        snapshot = ClaudeUsageClient._parse(data)
        return _CurrentUsage(
            five_hour_utilization=(
                snapshot.five_hour.utilization if snapshot.five_hour else None
            ),
            seven_day_utilization=(
                snapshot.seven_day.utilization if snapshot.seven_day else None
            ),
            five_hour_resets_at=snapshot.five_hour.resets_at if snapshot.five_hour else None,
            seven_day_resets_at=snapshot.seven_day.resets_at if snapshot.seven_day else None,
            created_at=watcher.last_checked_at,
        )
    return _CurrentUsage(None, None, None, None, None)


def _burn_rate_per_hour(samples: list[UsageSample], key: str) -> float | None:
    latest = _latest_with_value(samples, key)
    if latest is None:
        return None
    latest_created = _parse_dt(latest.created_at)
    latest_value = _sample_value(latest, key)
    latest_reset = _sample_reset(latest, key)
    if latest_created is None or latest_value is None:
        return None

    same_reset_candidates: list[UsageSample] = []
    fallback_candidates: list[UsageSample] = []
    for sample in samples:
        if sample.id == latest.id:
            continue
        sample_value = _sample_value(sample, key)
        if sample_value is None:
            continue
        created = _parse_dt(sample.created_at)
        if created is None or created >= latest_created:
            continue
        if _sample_reset(sample, key) == latest_reset and latest_reset:
            same_reset_candidates.append(sample)
        if sample_value <= latest_value:
            fallback_candidates.append(sample)

    # Preferred baseline: oldest sample in the same reset window.
    baseline: UsageSample | None = None
    if same_reset_candidates:
        baseline = same_reset_candidates[0]
    # Fallback for rolling windows where reset timestamps can move between checks.
    # Use the closest prior sample that is <= latest utilization to avoid
    # crossing a hard drop/reset boundary.
    elif fallback_candidates:
        baseline = fallback_candidates[-1]
    if baseline is None:
        return None

    baseline_created = _parse_dt(baseline.created_at)
    baseline_value = _sample_value(baseline, key)
    if baseline_created is None or baseline_value is None:
        return None
    elapsed_hours = (latest_created - baseline_created).total_seconds() / 3600
    if elapsed_hours <= 0:
        return None
    delta = latest_value - baseline_value
    if delta <= 0:
        return None
    return delta / elapsed_hours


def _latest_with_value(samples: list[UsageSample], key: str) -> UsageSample | None:
    for sample in reversed(samples):
        if _sample_value(sample, key) is not None:
            return sample
    return None


def _sample_value(sample: UsageSample, key: str) -> float | None:
    if key == "five_hour":
        return sample.five_hour_utilization
    return sample.seven_day_utilization


def _sample_reset(sample: UsageSample, key: str) -> str | None:
    if key == "five_hour":
        return sample.five_hour_resets_at
    return sample.seven_day_resets_at


def _project_pause_at(
    current_created_at: str | None,
    current_utilization: float | None,
    threshold: float,
    burn_per_hour: float | None,
) -> str | None:
    if current_created_at is None or current_utilization is None:
        return None
    current_dt = _parse_dt(current_created_at)
    if current_dt is None:
        return None
    if current_utilization >= threshold:
        return current_dt.isoformat()
    if burn_per_hour is None or burn_per_hour <= 0:
        return None
    hours_until = (threshold - current_utilization) / burn_per_hour
    if hours_until < 0:
        return current_dt.isoformat()
    return (current_dt + timedelta(hours=hours_until)).isoformat()


def _status(
    watcher: AccountWatcher,
    current: _CurrentUsage,
    next_pause_at: str | None,
    *,
    near_limit_ratio: float,
    soon_window_minutes: int,
) -> tuple[str, str]:
    five = current.five_hour_utilization
    seven = current.seven_day_utilization

    if five is None and seven is None:
        return "unknown", "no usage data yet"
    if seven is not None and seven >= watcher.seven_day_threshold:
        return "weekly-blocked", f"7-day limit at {seven:.1f}%"
    if watcher.state == "paused":
        return "paused", watcher.last_reason or "paused"
    if five is not None and five >= watcher.five_hour_threshold:
        return "near-limit", f"5-hour limit at {five:.1f}%"

    near_five = (
        five is not None
        and watcher.five_hour_threshold > 0
        and five >= watcher.five_hour_threshold * near_limit_ratio
    )
    near_seven = (
        seven is not None
        and watcher.seven_day_threshold > 0
        and seven >= watcher.seven_day_threshold * near_limit_ratio
    )
    if near_five or near_seven:
        return "near-limit", "usage is near a configured threshold"

    base_dt = _parse_dt(current.created_at) or datetime.now(UTC)
    if _is_soon(next_pause_at, base_dt=base_dt, minutes=soon_window_minutes):
        return "near-limit", "projected to reach threshold soon"

    return "safe", "usage below configured thresholds"


def _is_soon(value: str | None, *, base_dt: datetime, minutes: int) -> bool:
    dt = _parse_dt(value)
    if dt is None:
        return False
    return base_dt <= dt <= base_dt + timedelta(minutes=minutes)


def _earliest(values: list[str | None]) -> str | None:
    parsed = [(value, _parse_dt(value)) for value in values if value]
    parsed = [(value, dt) for value, dt in parsed if dt is not None]
    if not parsed:
        return None
    return min(parsed, key=lambda item: item[1])[0]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
