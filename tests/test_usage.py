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


def test_parse_statusline_rate_limits_payload():
    snapshot = ClaudeUsageClient._parse(
        {
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 91.25,
                    "resets_at": "2026-07-03T18:00:00Z",
                },
                "seven_day": {
                    "used_percentage": "33.5",
                    "resets_at": "2026-07-06T08:00:00Z",
                },
            }
        }
    )

    assert snapshot.five_hour.utilization == 91.25
    assert snapshot.five_hour.resets_at == "2026-07-03T18:00:00Z"
    assert snapshot.seven_day.utilization == 33.5
    assert snapshot.raw["five_hour"]["utilization"] == 91.25
    assert snapshot.raw["seven_day"]["utilization"] == 33.5


def test_parse_rate_limit_list_payload():
    snapshot = ClaudeUsageClient._parse(
        {
            "rateLimits": [
                {
                    "window": "5-hour",
                    "used_percentage": 96,
                    "resetAt": "2026-07-03T19:00:00Z",
                },
                {
                    "window": "7-day",
                    "usage_percent": 71,
                    "reset_at": "2026-07-07T09:00:00Z",
                },
            ]
        }
    )

    assert snapshot.five_hour.utilization == 96.0
    assert snapshot.five_hour.resets_at == "2026-07-03T19:00:00Z"
    assert snapshot.seven_day.utilization == 71.0
    assert snapshot.seven_day.resets_at == "2026-07-07T09:00:00Z"


def test_parse_used_and_limit_payload():
    snapshot = ClaudeUsageClient._parse(
        {
            "limits": {
                "5h": {"used": 48, "limit": 50},
                "weekly": {"used": 160, "limit": 200},
            }
        }
    )

    assert snapshot.five_hour.utilization == 96.0
    assert snapshot.seven_day.utilization == 80.0
