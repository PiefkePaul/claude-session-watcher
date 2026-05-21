from claude_session_watcher.usage import ClaudeUsageClient


def test_pause_required_for_five_hour_threshold():
    snapshot = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 95.2, "resets_at": None},
            "seven_day": {"utilization": 12.0, "resets_at": None},
        }
    )

    assert snapshot.is_pause_required(95.0, 98.0) == "5-hour limit at 95.2%"


def test_weekly_limit_blocks_resume_even_when_five_hour_is_free():
    snapshot = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 0.0, "resets_at": None},
            "seven_day": {"utilization": 44.0, "resets_at": None},
        }
    )

    assert snapshot.is_resume_ready(95.0, 98.0) is True


def test_resume_ready_when_all_known_limits_are_reset():
    snapshot = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 1.0, "resets_at": None},
            "seven_day": {"utilization": 0.0, "resets_at": None},
        }
    )

    assert snapshot.is_resume_ready(95.0, 98.0) is True


def test_weekly_limit_blocks_resume_when_above_weekly_threshold():
    snapshot = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 0.0, "resets_at": None},
            "seven_day": {"utilization": 98.1, "resets_at": None},
        }
    )

    assert snapshot.is_resume_ready(95.0, 98.0) is False
