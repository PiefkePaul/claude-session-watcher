from claude_session_watcher.insights import build_usage_insights
from claude_session_watcher.models import AccountWatcher, UsageSample


def test_insights_calculate_burn_rate_and_projection():
    watcher = AccountWatcher(id=1, account_id=1, five_hour_threshold=95.0)
    samples = [
        UsageSample(
            id=1,
            account_watcher_id=1,
            source="test",
            five_hour_utilization=50.0,
            five_hour_resets_at="2026-05-26T15:00:00+00:00",
            created_at="2026-05-26T10:00:00+00:00",
        ),
        UsageSample(
            id=2,
            account_watcher_id=1,
            source="test",
            five_hour_utilization=70.0,
            five_hour_resets_at="2026-05-26T15:00:00+00:00",
            created_at="2026-05-26T11:00:00+00:00",
        ),
    ]

    insights = build_usage_insights(watcher, samples)

    assert insights.status == "safe"
    assert insights.five_hour_burn_per_hour == 20.0
    assert insights.five_hour_pause_at == "2026-05-26T12:15:00+00:00"
    assert insights.next_pause_at == "2026-05-26T12:15:00+00:00"


def test_insights_ignore_reset_boundary_for_burn_rate():
    watcher = AccountWatcher(id=1, account_id=1, five_hour_threshold=95.0)
    samples = [
        UsageSample(
            id=1,
            account_watcher_id=1,
            source="test",
            five_hour_utilization=92.0,
            five_hour_resets_at="2026-05-26T10:00:00+00:00",
            created_at="2026-05-26T09:55:00+00:00",
        ),
        UsageSample(
            id=2,
            account_watcher_id=1,
            source="test",
            five_hour_utilization=2.0,
            five_hour_resets_at="2026-05-26T15:00:00+00:00",
            created_at="2026-05-26T10:05:00+00:00",
        ),
    ]

    insights = build_usage_insights(watcher, samples)

    assert insights.five_hour_burn_per_hour is None
    assert insights.five_hour_pause_at is None


def test_insights_report_weekly_blocked():
    watcher = AccountWatcher(id=1, account_id=1, seven_day_threshold=98.0)
    samples = [
        UsageSample(
            id=1,
            account_watcher_id=1,
            source="test",
            seven_day_utilization=98.5,
            created_at="2026-05-26T10:00:00+00:00",
        )
    ]

    insights = build_usage_insights(watcher, samples)

    assert insights.status == "weekly-blocked"
    assert insights.reason == "7-day limit at 98.5%"
    assert insights.next_pause_at is None
