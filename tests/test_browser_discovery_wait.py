"""Browser DOM discovery must wait for the SPA to render session links.

Depends: claude_session_watcher.browser.CamoufoxManager
"""

from __future__ import annotations

from claude_session_watcher.browser import CamoufoxManager


class FakeSlowRenderPage:
    """Mimics claude.ai/code: link scan returns [] until React has rendered."""

    def __init__(self, ready_after_calls: int, sessions: list[dict]):
        self.ready_after_calls = ready_after_calls
        self.sessions = sessions
        self.evaluate_calls = 0

    async def evaluate(self, script, *args):
        self.evaluate_calls += 1
        if self.evaluate_calls < self.ready_after_calls:
            return []
        return self.sessions


async def test_collect_links_polls_until_spa_rendered():
    session = {"session_key": "session_abc", "url": "https://claude.ai/code/session_abc"}
    page = FakeSlowRenderPage(ready_after_calls=3, sessions=[session])
    manager = CamoufoxManager(headless=True)

    result = await manager._collect_code_session_links(
        page, timeout_s=5.0, interval_s=0.01
    )

    assert result == [session]
    assert page.evaluate_calls >= 3


async def test_collect_links_returns_empty_after_timeout():
    page = FakeSlowRenderPage(ready_after_calls=10_000, sessions=[])
    manager = CamoufoxManager(headless=True)

    result = await manager._collect_code_session_links(
        page, timeout_s=0.05, interval_s=0.01
    )

    assert result == []
    assert page.evaluate_calls >= 2
