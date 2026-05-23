from datetime import UTC, datetime, timedelta

from claude_session_watcher.engine import WatcherEngine
from claude_session_watcher.models import AccountWatcher, Watcher
from claude_session_watcher.usage import ClaudeUsageClient


def test_engine_pauses_at_five_hour_threshold():
    usage = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 95.1, "resets_at": None},
            "seven_day": {"utilization": 10.0, "resets_at": None},
        }
    )

    decision = WatcherEngine().decide(
        Watcher(id=1, name="main", account_id=1, remote_url="https://claude.ai/code/x"),
        usage,
    )

    assert decision.action == "paused"
    assert decision.state == "paused"
    assert decision.message is not None


def test_engine_continues_when_blocking_limit_cleared():
    usage = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 1.0, "resets_at": None},
            "seven_day": {"utilization": 82.0, "resets_at": None},
        }
    )

    decision = WatcherEngine().decide(
        Watcher(
            id=1,
            name="main",
            account_id=1,
            remote_url="https://claude.ai/code/x",
            state="paused",
        ),
        usage,
    )

    assert decision.action == "continued"
    assert decision.state == "active"
    assert decision.message == "continue"


def test_engine_keeps_waiting_when_weekly_limit_blocks():
    usage = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 0.0, "resets_at": None},
            "seven_day": {"utilization": 98.5, "resets_at": None},
        }
    )

    decision = WatcherEngine().decide(
        Watcher(
            id=1,
            name="main",
            account_id=1,
            remote_url="https://claude.ai/code/x",
            state="paused",
        ),
        usage,
    )

    assert decision.action == "waiting"
    assert decision.state == "paused"
    assert decision.message is None


def test_engine_waits_for_resume_safety_margin():
    usage = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 1.0, "resets_at": None},
            "seven_day": {"utilization": 1.0, "resets_at": None},
        }
    )
    paused_until = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()

    decision = WatcherEngine(resume_safety_margin_seconds=120).decide(
        AccountWatcher(
            id=1,
            account_id=1,
            state="paused",
            paused_until=paused_until,
        ),
        usage,
    )

    assert decision.action == "waiting"
    assert decision.state == "paused"
    assert "safety margin" in decision.reason


def test_engine_clears_pause_metadata_after_resume_margin():
    usage = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 1.0, "resets_at": None},
            "seven_day": {"utilization": 1.0, "resets_at": None},
        }
    )
    paused_until = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()

    decision = WatcherEngine(resume_safety_margin_seconds=1).decide(
        AccountWatcher(
            id=1,
            account_id=1,
            state="paused",
            paused_until=paused_until,
        ),
        usage,
    )

    assert decision.action == "continued"
    assert decision.clear_pause is True


def test_engine_uses_pause_template_message():
    usage = ClaudeUsageClient._parse(
        {
            "five_hour": {"utilization": 99.0, "resets_at": None},
            "seven_day": {"utilization": 1.0, "resets_at": None},
        }
    )

    decision = WatcherEngine().decide(
        AccountWatcher(
            id=1,
            account_id=1,
            pause_template="worklog",
            pause_message="custom",
        ),
        usage,
    )

    assert decision.action == "paused"
    assert decision.message is not None
    assert "WORKLOG.md" in decision.message
