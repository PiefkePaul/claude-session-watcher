from claude_session_watcher.engine import WatcherEngine
from claude_session_watcher.models import Watcher
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
