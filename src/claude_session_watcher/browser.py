from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .display import DisplayManager


class BrowserError(Exception):
    pass


@dataclass(slots=True)
class BrowserSession:
    manager: Any
    context: Any
    headless: str | bool


class CamoufoxManager:
    def __init__(
        self,
        *,
        headless: str | bool = "virtual",
        os_name: str | None = None,
        display_manager: DisplayManager | None = None,
    ):
        self.headless = headless
        self.os_name = os_name
        self.display_manager = display_manager
        self.display = display_manager.display if display_manager else os.environ.get("DISPLAY")
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await self._close_session(session)
        await self._stop_display_if_idle()

    async def close_profile(self, profile_dir: Path) -> None:
        await self._discard_profile(profile_dir)
        # Defensive: persistent context keys must match exactly. If callers passed
        # a slightly different path representation, also discard by resolved path.
        try:
            resolved = profile_dir.resolve()
        except Exception:
            return
        if resolved != profile_dir:
            await self._discard_profile(resolved)
        await self._kill_profile_processes(resolved)

    async def _kill_profile_processes(self, profile_dir: Path) -> None:
        # Best-effort safety net: if the Playwright/Camoufox teardown fails,
        # ensure no stray browser processes keep running for this profile.
        if os.name == "nt":
            return
        profile_str = str(profile_dir)
        try:
            subprocess.run(
                # Match both camoufox-bin and child processes that keep the profile path.
                # Note: patterns beginning with '-' must be preceded by '--' so pkill
                # doesn't treat them as options.
                ["pkill", "-f", "--", f"-profile {profile_str}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return

    def _default_viewport(self) -> dict[str, int]:
        # If we have a VNC screen configured, match it to avoid awkward scaling.
        screen = None
        if self.display_manager:
            screen = getattr(self.display_manager, "screen", None)
        if isinstance(screen, str) and "x" in screen:
            parts = screen.split("x")
            try:
                width = int(parts[0])
                height = int(parts[1])
                if width > 0 and height > 0:
                    return {"width": width, "height": height}
            except Exception:
                pass
        return {"width": 1280, "height": 900}

    async def context_for_profile(
        self,
        profile_dir: Path,
        *,
        headless: str | bool | None = None,
        reset: bool = False,
    ):
        key = str(profile_dir.resolve())
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
            resolved_dir = Path(key)
            resolved_dir.mkdir(parents=True, exist_ok=True)
            try:
                from camoufox.async_api import AsyncCamoufox
            except ImportError as exc:
                raise BrowserError("camoufox is not installed") from exc

            if desired_headless is False and self.display_manager:
                await self.display_manager.ensure_started()

            kwargs: dict[str, Any] = {
                "persistent_context": True,
                "user_data_dir": str(resolved_dir),
                "headless": desired_headless,
                "humanize": True,
                "env": self._browser_env(),
                # Make the visible browser window large enough for OAuth dialogs
                # when running against a virtual display/VNC.
                "viewport": self._default_viewport(),
            }
            if self.os_name:
                kwargs["os"] = self.os_name
            # Improve UX for OAuth providers (Google sign-in tends to open small popups).
            # Prefer opening new windows as tabs.
            kwargs["firefox_user_prefs"] = {
                "browser.link.open_newwindow": 3,
                "browser.link.open_newwindow.restriction": 0,
            }

            try:
                manager = AsyncCamoufox(**kwargs)
            except TypeError:
                # Camoufox/Playwright bindings differ slightly across versions.
                # Drop optional UX knobs if unsupported.
                kwargs.pop("firefox_user_prefs", None)
                kwargs.pop("viewport", None)
                manager = AsyncCamoufox(**kwargs)
            context = await manager.__aenter__()
            self._sessions[key] = BrowserSession(
                manager=manager,
                context=context,
                headless=desired_headless,
            )
            return context

    def _browser_env(self) -> dict[str, str]:
        if self.display_manager:
            return self.display_manager.browser_env()
        env = dict(os.environ)
        if self.display:
            env["DISPLAY"] = self.display
        return env

    async def is_profile_open(self, profile_dir: Path) -> bool:
        try:
            key = str(profile_dir.resolve())
        except Exception:
            key = str(profile_dir)
        async with self._lock:
            session = self._sessions.get(key)
            if not session:
                return False
            if await self._session_alive(session):
                return True
            self._sessions.pop(key, None)
        await self._stop_display_if_idle()
        return False

    async def has_open_sessions(self) -> bool:
        async with self._lock:
            sessions = list(self._sessions.items())
        for key, session in sessions:
            if await self._session_alive(session):
                return True
            async with self._lock:
                self._sessions.pop(key, None)
        await self._stop_display_if_idle()
        return False

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
            await session.context.close()
        except Exception:
            pass
        try:
            await session.manager.__aexit__(None, None, None)
        except Exception:
            pass

    @staticmethod
    def _is_closed_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "has been closed" in message or "target closed" in message

    async def open_login(self, profile_dir: Path) -> None:
        # Be idempotent: if a login browser is already open, reuse it instead of
        # resetting (resetting while the wrapper is polling can be flaky).
        context = await self.context_for_profile(profile_dir, headless=False, reset=False)
        page = await self._get_or_open_page(context, "https://claude.ai/code")
        if not page.url.startswith("https://claude.ai/"):
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

    async def fetch_usage(self, profile_dir: Path) -> dict[str, Any]:
        try:
            return await self._fetch_usage_once(profile_dir)
        except Exception as exc:  # noqa: BLE001
            if not self._is_closed_error(exc):
                raise
            await self._discard_profile(profile_dir)
            return await self._fetch_usage_once(profile_dir)

    async def _fetch_usage_once(self, profile_dir: Path) -> dict[str, Any]:
        context = await self.context_for_profile(profile_dir)
        page = await self._get_or_open_page(context, "https://claude.ai/code")
        if not page.url.startswith("https://claude.ai/"):
            await page.goto("https://claude.ai/code", wait_until="domcontentloaded")
        orgs = await self._browser_json(page, "/api/organizations")
        if not isinstance(orgs, list) or not orgs:
            raise BrowserError("Claude did not return any organizations for this browser profile")

        errors: list[str] = []
        for org in orgs:
            if not isinstance(org, dict):
                continue
            org_id = org.get("uuid") or org.get("id")
            if not org_id:
                continue
            try:
                usage = await self._browser_json(page, f"/api/organizations/{org_id}/usage")
            except BrowserError as exc:
                errors.append(f"{org_id}: {exc}")
                continue
            if isinstance(usage, dict) and (
                "five_hour" in usage or "seven_day" in usage
            ):
                usage["_csw_org_id"] = org_id
                usage["_csw_org_name"] = org.get("name")
                return usage
            errors.append(f"{org_id}: usage payload missing expected keys")

        detail = "; ".join(errors) if errors else "no usable organization ids found"
        raise BrowserError(f"Could not read Claude usage for this browser profile: {detail}")

    async def discover_code_sessions(self, profile_dir: Path) -> list[dict[str, Any]]:
        context = await self.context_for_profile(profile_dir)
        page = await self._get_or_open_page(context, "https://claude.ai/code")
        await page.goto("https://claude.ai/code", wait_until="domcontentloaded")
        return await page.evaluate(
            """
            () => {
              const seen = new Map();
              const links = Array.from(document.querySelectorAll("a[href]"));
              for (const link of links) {
                const href = link.href || "";
                if (!href.includes("/code/") || href.endsWith("/code")) continue;
                const url = new URL(href, window.location.origin).toString();
                const key = url.split("/").filter(Boolean).pop() || url;
                const text = (link.innerText || link.textContent || "").trim();
                const label = link.getAttribute("aria-label") || "";
                const title = text || label || key;
                const haystack = `${text} ${label} ${link.className || ""}`.toLowerCase();
                const remoteHint = haystack.includes("remote")
                  || haystack.includes("computer")
                  || key.startsWith("session_");
                const status = haystack.includes("archived")
                  ? "archived"
                  : haystack.includes("offline")
                    ? "offline"
                    : "unknown";
                seen.set(key, {
                  session_key: key,
                  title,
                  url,
                  kind: remoteHint ? "remote" : "unknown",
                  status,
                  control_supported: remoteHint,
                });
              }
              return Array.from(seen.values());
            }
            """,
        )

    async def _browser_json(self, page, path: str):
        return await page.evaluate(
            """
            async (path) => {
              const response = await fetch(path, {
                credentials: "include",
                headers: { "accept": "application/json" }
              });
              const text = await response.text();
              if (!response.ok) {
                throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 300)}`);
              }
              return text ? JSON.parse(text) : null;
            }
            """,
            path,
        )

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
        try:
            key = str(profile_dir.resolve())
        except Exception:
            key = str(profile_dir)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            await self._close_session(session)
        await self._stop_display_if_idle()

    async def _stop_display_if_idle(self) -> None:
        if not self.display_manager:
            return
        async with self._lock:
            has_sessions = bool(self._sessions)
        if not has_sessions:
            await self.display_manager.stop()

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
