"""Regression tests: a failed browser launch must not leak the Playwright driver.

Depends: claude_session_watcher.browser.CamoufoxManager
"""

from __future__ import annotations

import sys
import types

import pytest

from claude_session_watcher.browser import CamoufoxManager


class FakeLaunchFailingCamoufox:
    """Mimics AsyncCamoufox whose startup fails mid-launch (e.g. protocol error).

    Records lifecycle calls so tests can assert cleanup happened.
    """

    instances: list[FakeLaunchFailingCamoufox] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.aenter_called = False
        self.aexit_called = False
        FakeLaunchFailingCamoufox.instances.append(self)

    async def __aenter__(self):
        self.aenter_called = True
        raise RuntimeError(
            "BrowserType.launch_persistent_context: Protocol error "
            "(Browser.setDefaultViewport): unexpected property isMobile"
        )

    async def __aexit__(self, *args):
        self.aexit_called = True
        return False


@pytest.fixture
def fake_camoufox_module(monkeypatch):
    FakeLaunchFailingCamoufox.instances = []
    module = types.ModuleType("camoufox.async_api")
    module.AsyncCamoufox = FakeLaunchFailingCamoufox
    monkeypatch.setitem(sys.modules, "camoufox.async_api", module)
    return module


async def test_failed_launch_calls_aexit_to_release_driver(tmp_path, fake_camoufox_module):
    manager = CamoufoxManager(headless=True)

    with pytest.raises(RuntimeError, match="Protocol error"):
        await manager.context_for_profile(tmp_path / "profile")

    assert len(FakeLaunchFailingCamoufox.instances) == 1
    instance = FakeLaunchFailingCamoufox.instances[0]
    assert instance.aenter_called
    assert instance.aexit_called, (
        "manager.__aexit__ must be called when __aenter__ fails, "
        "otherwise the Playwright driver process/pipes leak file descriptors"
    )


async def test_failed_launch_does_not_register_session(tmp_path, fake_camoufox_module):
    manager = CamoufoxManager(headless=True)
    profile = tmp_path / "profile"

    with pytest.raises(RuntimeError):
        await manager.context_for_profile(profile)

    assert not await manager.is_profile_open(profile)
