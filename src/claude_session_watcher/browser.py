from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BrowserError(Exception):
    pass


@dataclass(slots=True)
class BrowserSession:
    manager: Any
    context: Any


class CamoufoxManager:
    def __init__(self, *, headless: str | bool = "virtual", os_name: str | None = None):
        self.headless = headless
        self.os_name = os_name
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.manager.__aexit__(None, None, None)

    async def context_for_profile(self, profile_dir: Path):
        key = str(profile_dir)
        async with self._lock:
            if key in self._sessions:
                return self._sessions[key].context
            profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                from camoufox.async_api import AsyncCamoufox
            except ImportError as exc:
                raise BrowserError("camoufox is not installed") from exc

            kwargs: dict[str, Any] = {
                "persistent_context": True,
                "user_data_dir": str(profile_dir),
                "headless": self.headless,
                "humanize": True,
            }
            if self.os_name:
                kwargs["os"] = self.os_name

            manager = AsyncCamoufox(**kwargs)
            context = await manager.__aenter__()
            self._sessions[key] = BrowserSession(manager=manager, context=context)
            return context

    async def open_login(self, profile_dir: Path) -> None:
        context = await self.context_for_profile(profile_dir)
        page = await context.new_page()
        await page.goto("https://claude.ai/code", wait_until="domcontentloaded")

    async def session_key(self, profile_dir: Path) -> str:
        context = await self.context_for_profile(profile_dir)
        cookies = await context.cookies("https://claude.ai")
        for cookie in cookies:
            if cookie.get("name") == "sessionKey" and cookie.get("value"):
                return str(cookie["value"])
        raise BrowserError("No sessionKey cookie found. Open login and sign in first.")

    async def send_prompt(self, profile_dir: Path, remote_url: str, prompt: str) -> None:
        context = await self.context_for_profile(profile_dir)
        page = await self._get_or_open_page(context, remote_url)
        await page.goto(remote_url, wait_until="domcontentloaded")
        editor = await self._find_prompt_editor(page)
        await editor.fill(prompt)
        await editor.press("Enter")

    async def _get_or_open_page(self, context, remote_url: str):
        for page in context.pages:
            if remote_url in page.url or page.url in remote_url:
                return page
        return await context.new_page()

    async def _find_prompt_editor(self, page):
        selectors = [
            "textarea",
            "[contenteditable='true']",
            "[role='textbox']",
            ".ProseMirror",
        ]
        last_error: Exception | None = None
        for selector in selectors:
            try:
                locator = page.locator(selector).last
                await locator.wait_for(timeout=10_000)
                return locator
            except Exception as exc:  # noqa: BLE001 - Playwright raises implementation-specific errors
                last_error = exc
        raise BrowserError(f"Could not find Claude prompt editor: {last_error}")
