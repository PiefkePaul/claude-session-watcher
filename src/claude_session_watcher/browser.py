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
    headless: str | bool


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
            await self._close_session(session)

    async def context_for_profile(
        self,
        profile_dir: Path,
        *,
        headless: str | bool | None = None,
        reset: bool = False,
    ):
        key = str(profile_dir)
        desired_headless = self.headless if headless is None else headless
        async with self._lock:
            existing = self._sessions.get(key)
            if existing:
                if reset:
                    self._sessions.pop(key, None)
                    await self._close_session(existing)
                elif await self._session_alive(existing):
                    return existing.context
                else:
                    self._sessions.pop(key, None)
            profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                from camoufox.async_api import AsyncCamoufox
            except ImportError as exc:
                raise BrowserError("camoufox is not installed") from exc

            kwargs: dict[str, Any] = {
                "persistent_context": True,
                "user_data_dir": str(profile_dir),
                "headless": desired_headless,
                "humanize": True,
            }
            if self.os_name:
                kwargs["os"] = self.os_name

            manager = AsyncCamoufox(**kwargs)
            context = await manager.__aenter__()
            self._sessions[key] = BrowserSession(
                manager=manager,
                context=context,
                headless=desired_headless,
            )
            return context

    async def _session_alive(self, session: BrowserSession) -> bool:
        try:
            await session.context.cookies()
            return True
        except Exception as exc:  # noqa: BLE001 - browser backends use varying error types
            if self._is_closed_error(exc):
                await self._close_session(session)
                return False
            raise

    async def _close_session(self, session: BrowserSession) -> None:
        try:
            await session.manager.__aexit__(None, None, None)
        except Exception:
            pass

    @staticmethod
    def _is_closed_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "has been closed" in message or "target closed" in message

    async def open_login(self, profile_dir: Path) -> None:
        context = await self.context_for_profile(profile_dir, headless=False, reset=True)
        page = await context.new_page()
        await page.goto("https://claude.ai/code", wait_until="domcontentloaded")

    async def session_key(self, profile_dir: Path) -> str:
        context = await self.context_for_profile(profile_dir)
        try:
            cookies = await context.cookies("https://claude.ai")
        except Exception as exc:  # noqa: BLE001
            if not self._is_closed_error(exc):
                raise
            await self._discard_profile(profile_dir)
            context = await self.context_for_profile(profile_dir)
            cookies = await context.cookies("https://claude.ai")
        for cookie in cookies:
            if cookie.get("name") == "sessionKey" and cookie.get("value"):
                return str(cookie["value"])
        raise BrowserError("No sessionKey cookie found. Open login and sign in first.")

    async def send_prompt(self, profile_dir: Path, remote_url: str, prompt: str) -> None:
        try:
            await self._send_prompt_once(profile_dir, remote_url, prompt)
        except Exception as exc:  # noqa: BLE001
            if not self._is_closed_error(exc):
                raise
            await self._discard_profile(profile_dir)
            await self._send_prompt_once(profile_dir, remote_url, prompt)

    async def _send_prompt_once(self, profile_dir: Path, remote_url: str, prompt: str) -> None:
        context = await self.context_for_profile(profile_dir)
        page = await self._get_or_open_page(context, remote_url)
        await page.goto(remote_url, wait_until="domcontentloaded")
        editor = await self._find_prompt_editor(page)
        await editor.fill(prompt)
        await editor.press("Enter")

    async def _discard_profile(self, profile_dir: Path) -> None:
        key = str(profile_dir)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            await self._close_session(session)

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
