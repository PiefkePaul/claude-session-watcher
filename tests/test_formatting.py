from claude_session_watcher.formatting import build_ui_watcher, format_reset, format_timestamp
from claude_session_watcher.models import AccountWatcher


def test_format_timestamp_in_local_display():
    assert format_timestamp("2026-05-21T13:35:00+00:00") == "15:35  21.05.2026"


def test_format_five_hour_reset_as_time_only():
    assert format_reset("2026-05-21T13:35:00+00:00") == "15:35"


def test_format_weekly_reset_with_weekday():
    assert format_reset("2026-05-24T07:00:00+00:00", weekly=True) == "So. 09:00"


def test_format_missing_reset_as_empty_string():
    assert format_reset(None) == ""


def test_build_ui_watcher_reads_rate_limits_payload():
    watcher = AccountWatcher(
        id=1,
        account_id=1,
        last_usage_json=(
            '{"rate_limits":{"five_hour":{"used_percentage":42,'
            '"resets_at":"2026-05-21T13:35:00+00:00"}}}'
        ),
    )

    ui = build_ui_watcher(watcher)

    assert ui.five_hour.utilization == 42.0
    assert ui.five_hour.reset_display == "15:35"
