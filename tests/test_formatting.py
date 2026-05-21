from claude_session_watcher.formatting import format_reset, format_timestamp


def test_format_timestamp_in_local_display():
    assert format_timestamp("2026-05-21T13:35:00+00:00") == "15:35  21.05.2026"


def test_format_five_hour_reset_as_time_only():
    assert format_reset("2026-05-21T13:35:00+00:00") == "15:35"


def test_format_weekly_reset_with_weekday():
    assert format_reset("2026-05-24T07:00:00+00:00", weekly=True) == "So. 09:00"


def test_format_missing_reset_as_empty_string():
    assert format_reset(None) == ""
