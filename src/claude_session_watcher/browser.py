from __future__ import annotations

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
                raise BrowserError(
                    "Camoufox is not installed (missing camoufox.async_api). "
                    "Install: python -m pip install -U camoufox[geoip] "
                    "and then run: python -m camoufox fetch"
                ) from exc

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
            try:
                context = await manager.__aenter__()
            except Exception:
                # A failed launch (e.g. Playwright protocol error) leaves the
                # driver process and its pipes running; without this cleanup the
                # server leaks file descriptors until EMFILE takes it down.
                try:
                    await manager.__aexit__(None, None, None)
                except Exception:
                    pass
                raise
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
        # Use /new instead of /code:
        # - /new reliably shows the profile/plan picker (needed when the user has both Free/Pro)
        # - /code can redirect to /code/disabled before the user can switch plans
        page = await self._get_or_open_page(context, "https://claude.ai/new")
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        try:
            await page.bring_to_front()
        except Exception:
            pass

    async def start_google_login(self, profile_dir: Path) -> dict[str, Any]:
        """Best-effort helper for the common Claude "Continue with Google" flow.

        This intentionally does not handle credentials. It only navigates to /new,
        accepts cookie banners, and clicks a visible Google sign-in trigger.
        """
        context = await self.context_for_profile(profile_dir, headless=False, reset=False)
        page = await self._get_or_open_page(context, "https://claude.ai/new")
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        try:
            await page.bring_to_front()
        except Exception:
            pass

        await self._accept_cookies_banner(page)

        user_menu = page.locator('button[data-testid="user-menu-button"]').first
        try:
            await user_menu.wait_for(state="visible", timeout=2_000)
            return {"ok": True, "already_logged_in": True}
        except Exception:
            pass

        selectors: list[tuple[str, Any]] = [
            (
                "button-role-google",
                page.get_by_role("button", name=re.compile("google", re.IGNORECASE)).first,
            ),
            (
                "link-role-google",
                page.get_by_role("link", name=re.compile("google", re.IGNORECASE)).first,
            ),
            ("button-text-google", page.locator("button:has-text('Google')").first),
            ("link-text-google", page.locator("a:has-text('Google')").first),
        ]

        for name, locator in selectors:
            try:
                await locator.wait_for(state="visible", timeout=1_500)
                before = len(context.pages)
                await locator.click(timeout=2_500)
                await page.wait_for_timeout(500)
                if len(context.pages) > before:
                    try:
                        await context.pages[-1].bring_to_front()
                    except Exception:
                        pass
                return {"ok": True, "clicked": True, "selector": name}
            except Exception:
                continue

        return {
            "ok": False,
            "clicked": False,
            "reason": "No visible Google sign-in button/link was found on claude.ai/new",
        }

    async def ensure_pro_plan(self, profile_dir: Path, *, page=None) -> dict[str, Any]:
        """Best-effort attempt to switch the active Claude profile/plan to Pro.

        This is needed when a user has multiple profiles (e.g. Free + Pro) and Claude Code
        is disabled for the currently selected one. The profile switcher is available on
        https://claude.ai/new, not reliably on /code.
        """
        context = await self.context_for_profile(profile_dir, headless=False, reset=False)
        if page is None:
            page = await self._get_or_open_page(context, "https://claude.ai/new")
        try:
            await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        except Exception:
            # If navigation fails, keep going with whatever page we have.
            pass
        try:
            await page.bring_to_front()
        except Exception:
            pass

        await self._accept_cookies_banner(page)

        menu_button = page.locator('button[data-testid="user-menu-button"]').first
        try:
            await menu_button.wait_for(state="visible", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "switched": False,
                "reason": f"Could not find profile menu button: {exc}",
            }

        try:
            current_text = (await menu_button.inner_text(timeout=2_000)) or ""
        except Exception:
            current_text = ""

        if self._text_is_pro_plan(current_text):
            return {"ok": True, "switched": False, "current": current_text, "method": "label-check"}

        # Fast path discovered via runtime tracing:
        # selecting a different profile/plan updates the `lastActiveOrg` cookie and
        # the app reloads org-scoped data via GET requests. We can script this directly
        # without brittle menu-item clicks by resolving the Pro org id from
        # `/api/organizations` and setting `lastActiveOrg`.
        cookie_switch = await self._switch_to_pro_by_cookie(context, page)
        if cookie_switch.get("ok"):
            return cookie_switch

        # Open the menu and try to click the Pro entry. The DOM is Radix-based and can
        # change, so we rely on role/testid + innerText heuristics.
        try:
            await menu_button.click()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "switched": False, "reason": f"Could not open menu: {exc}"}

        await page.wait_for_timeout(250)

        switched = await self._click_pro_plan_entry(page)
        if not switched:
            return {
                "ok": False,
                "switched": False,
                "reason": "Could not find a Pro profile entry in the menu.",
                "current": current_text,
            }

        # Wait for the button text to reflect the selected plan.
        for _ in range(40):
            await page.wait_for_timeout(250)
            try:
                updated = (await menu_button.inner_text(timeout=2_000)) or ""
            except Exception:
                updated = ""
            if self._text_is_pro_plan(updated):
                return {
                    "ok": True,
                    "switched": True,
                    "current": updated,
                    "method": "menu-click",
                }

        return {
            "ok": True,
            "switched": True,
            "method": "menu-click",
            "reason": "Clicked Pro plan entry, but menu label did not update in time.",
        }

    async def _switch_to_pro_by_cookie(self, context, page) -> dict[str, Any]:
        try:
            orgs = await self._browser_json(page, "/api/organizations")
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "switched": False,
                "reason": f"Could not load organizations for cookie-based switch: {exc}",
            }
        pro_org_id = self._pro_org_id_from_organizations(orgs)
        if not pro_org_id:
            return {
                "ok": False,
                "switched": False,
                "reason": "No Pro-capable organization found in /api/organizations.",
            }

        current_org = await self._last_active_org_cookie(context)
        if current_org == pro_org_id:
            return {
                "ok": True,
                "switched": False,
                "method": "org-cookie",
                "org_id": pro_org_id,
            }

        expires_at = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        try:
            await context.add_cookies(
                [
                    {
                        "name": "lastActiveOrg",
                        "value": pro_org_id,
                        "domain": ".claude.ai",
                        "path": "/",
                        "expires": expires_at,
                        "httpOnly": False,
                        "secure": True,
                        "sameSite": "Lax",
                    }
                ]
            )
            await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
            await page.wait_for_timeout(900)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "switched": False,
                "reason": f"Could not set lastActiveOrg cookie: {exc}",
            }

        # Verification 1: UI label now shows Pro.
        try:
            menu_button = page.locator('button[data-testid="user-menu-button"]').first
            await menu_button.wait_for(state="visible", timeout=5_000)
            current_text = (await menu_button.inner_text(timeout=2_000)) or ""
            if self._text_is_pro_plan(current_text):
                return {
                    "ok": True,
                    "switched": True,
                    "method": "org-cookie",
                    "org_id": pro_org_id,
                    "current": current_text,
                }
        except Exception:
            pass

        # Verification 2: fallback to cookie value.
        current_org = await self._last_active_org_cookie(context)
        if current_org == pro_org_id:
            return {
                "ok": True,
                "switched": True,
                "method": "org-cookie",
                "org_id": pro_org_id,
                "reason": "lastActiveOrg updated; menu label update not yet observed.",
            }
        return {
            "ok": False,
            "switched": False,
            "reason": "Cookie-based switch did not take effect.",
        }

    async def code_portal_status(self, profile_dir: Path, *, page=None) -> dict[str, Any]:
        """Check whether claude.ai/code is usable for this profile.

        Returns a dict with:
          - url: final URL after navigation
          - disabled: bool
          - message: human-readable reason (when disabled)
        """
        context = await self.context_for_profile(profile_dir)
        if page is None:
            page = await self._get_or_open_page(context, "https://claude.ai/code")
            await page.goto("https://claude.ai/code", wait_until="domcontentloaded")
        # claude.ai/code often performs a client-side navigation shortly after initial load.
        await page.wait_for_timeout(1500)
        final_url = getattr(page, "url", "") or ""
        disabled = self._is_code_disabled(final_url)
        if not disabled:
            try:
                body_text = await page.evaluate(
                    "() => document.body ? (document.body.innerText || '') : ''"
                )
            except Exception:
                body_text = ""
            normalized = (body_text or "").lower()
            if "organiz" in normalized and ("deaktiv" in normalized or "disabled" in normalized):
                disabled = True

        if disabled:
            return {
                "url": final_url,
                "disabled": True,
                "message": (
                    "Claude Code is disabled for this account/organization "
                    "(redirected to /code/disabled). Switch to the correct profile/plan or "
                    "ask your organization admin to enable Claude Code / Remote Control."
                ),
            }
        return {"url": final_url, "disabled": False, "message": None}

    @staticmethod
    def _is_code_disabled(url: str) -> bool:
        try:
            return "/code/disabled" in (url or "")
        except Exception:
            return False

    @staticmethod
    async def _accept_cookies_banner(page) -> None:
        # Best-effort: the cookie banner can block clicks, especially on first use.
        candidates = [
            '[data-testid="consent-accept"]',
            "text=Alle Cookies akzeptieren",
            "text=Accept all cookies",
            "text=Accept all",
            "button:has-text(\"Alle Cookies akzeptieren\")",
            "button:has-text(\"Accept all\")",
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                if await locator.count():
                    if await locator.is_visible(timeout=300):
                        await locator.click(timeout=2_000)
                        await page.wait_for_timeout(250)
                        return
            except Exception:
                continue

    @staticmethod
    async def _last_active_org_cookie(context) -> str | None:
        try:
            cookies = await context.cookies("https://claude.ai")
        except Exception:
            return None
        for cookie in cookies:
            if cookie.get("name") == "lastActiveOrg":
                value = cookie.get("value")
                if value:
                    return str(value)
        return None

    @classmethod
    def _pro_org_id_from_organizations(cls, value: Any) -> str | None:
        if not isinstance(value, list):
            return None
        for item in value:
            if not isinstance(item, dict):
                continue
            org_id = str(item.get("uuid") or item.get("id") or "").strip()
            if not org_id:
                continue
            caps = item.get("capabilities")
            if isinstance(caps, list):
                lowered = {str(cap).strip().lower() for cap in caps}
                if "claude_pro" in lowered:
                    return org_id
        return None

    @staticmethod
    def _text_is_pro_plan(text: str) -> bool:
        normalized = (text or "").strip().lower()
        if not normalized:
            return False
        # Common UI strings observed across locales.
        if "pro-plan" in normalized or "pro plan" in normalized:
            return True
        # Sometimes the plan label is just "Pro".
        # Use a conservative check to avoid matching "profile".
        tokens = {t for t in normalized.replace("\n", " ").replace("\r", " ").split(" ") if t}
        return "pro" in tokens

    async def _click_pro_plan_entry(self, page) -> bool:
        # Radix menu content: role=menu or data-radix-menu-content.
        menu = page.locator("div[role='menu'], div[data-radix-menu-content]").last
        try:
            await menu.wait_for(state="visible", timeout=5_000)
        except Exception:
            # Fallback: scan the whole page, but prefer menu-like containers.
            menu = page.locator("body")

        # Try the most specific and safe patterns first.
        patterns = [
            r"pro-plan",
            r"pro plan",
            r"\bpro\b",
        ]

        js_patterns = patterns
        try:
            result = await page.evaluate(
                """
                (args) => {
                  const { patterns } = args;
                  const regexes = patterns.map((p) => {
                    try { return new RegExp(p, "i"); } catch (e) { return null; }
                  }).filter(Boolean);
                  const menus = Array.from(
                    document.querySelectorAll(
                      "div[role='menu'], div[data-radix-menu-content]",
                    ),
                  ).filter((el) => el && el.offsetParent !== null);
                  const scope = menus.length ? menus[menus.length - 1] : document.body;
                  const candidates = Array.from(
                    scope.querySelectorAll(
                      "button,[role='menuitemradio'],[role='menuitem'],a,[data-state]",
                    ),
                  );
                  function textOf(el) {
                    return (el.innerText || el.textContent || "").trim();
                  }
                  function isPro(el) {
                    const t = textOf(el);
                    if (!t) return false;
                    return regexes.some((re) => re.test(t));
                  }
                  // Prefer an unchecked radio item that mentions Pro.
                  const unchecked = candidates.find(
                    (el) => el.getAttribute("data-state") === "unchecked" && isPro(el),
                  );
                  const any = candidates.find(
                    (el) => isPro(el) && el.getAttribute("data-state") !== "checked",
                  );
                  const target = unchecked || any;
                  if (!target) {
                    return { clicked: false };
                  }
                  const clickable =
                    target.closest("button,[role='menuitemradio'],[role='menuitem'],a")
                      || target;
                  clickable.click();
                  return {
                    clicked: true,
                    text: textOf(target),
                    dataState: target.getAttribute("data-state"),
                  };
                }
                """,
                {"patterns": js_patterns},
            )
        except Exception:
            result = None

        if isinstance(result, dict) and result.get("clicked"):
            return True

        # Last resort: locate by text in Playwright and click the first actionable hit.
        for needle in ["Pro-Plan", "Pro plan", "Pro"]:
            try:
                loc = page.locator(f"text={needle}").first
                if await loc.count() and await loc.is_visible(timeout=300):
                    await loc.click(timeout=2_000)
                    return True
            except Exception:
                continue
        return False

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
            if isinstance(usage, dict):
                from .usage import ClaudeUsageClient

                snapshot = ClaudeUsageClient._parse(usage)
                if snapshot.five_hour or snapshot.seven_day:
                    normalized_usage = dict(snapshot.raw)
                    normalized_usage["_csw_org_id"] = org_id
                    normalized_usage["_csw_org_name"] = org.get("name")
                    return normalized_usage
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

    # ──────────────────────────────────────────────────────────────
    # Screenshot proxy — replaces VNC for the login UI
    # ──────────────────────────────────────────────────────────────

    async def screenshot(self, profile_dir: Path, *, page_index: int = -1) -> bytes:
        """Return a JPEG screenshot of the specified browser page (default: last/newest).

        Raises BrowserError immediately if the profile is not already open — this prevents
        the WebSocket stream from accidentally launching a new headless browser.
        """
        if not await self.is_profile_open(profile_dir):
            raise BrowserError("Browser is not open for this profile")
        context = await self.context_for_profile(profile_dir)
        pages = context.pages
        if not pages:
            raise BrowserError("No browser pages open for this profile")
        try:
            page = pages[page_index % len(pages)]
        except (IndexError, ZeroDivisionError):
            page = pages[-1]
        return await page.screenshot(type="jpeg", quality=80, full_page=False)

    async def page_infos(self, profile_dir: Path) -> list[dict[str, Any]]:
        """Return info (index, url, title) for all open pages of this profile."""
        if not await self.is_profile_open(profile_dir):
            return []
        try:
            context = await self.context_for_profile(profile_dir)
        except Exception:
            return []
        infos = []
        for i, page in enumerate(context.pages):
            try:
                title = await page.title()
            except Exception:
                title = ""
            infos.append({"index": i, "url": page.url or "", "title": title})
        return infos

    async def send_input(
        self,
        profile_dir: Path,
        event: dict[str, Any],
        *,
        page_index: int = -1,
    ) -> None:
        """Forward a mouse/keyboard event to the browser page.

        Supported event types:
          click   – {type, x, y}              mouse left-click at page coordinates
          dblclick– {type, x, y}              double-click
          key     – {type, key}               keyboard.press() (special keys, combos)
          type    – {type, text}              keyboard.type() (printable characters)
          scroll  – {type, dx, dy}            mouse wheel delta
        """
        context = await self.context_for_profile(profile_dir)
        pages = context.pages
        if not pages:
            raise BrowserError("No browser pages open for this profile")
        try:
            page = pages[page_index % len(pages)]
        except (IndexError, ZeroDivisionError):
            page = pages[-1]

        etype = event.get("type")
        if etype == "click":
            await page.mouse.click(float(event["x"]), float(event["y"]))
        elif etype == "dblclick":
            await page.mouse.dblclick(float(event["x"]), float(event["y"]))
        elif etype == "key":
            key = str(event.get("key", ""))
            if key:
                await page.keyboard.press(key)
        elif etype == "type":
            text = str(event.get("text", ""))
            if text:
                await page.keyboard.type(text)
        elif etype == "scroll":
            await page.mouse.wheel(float(event.get("dx", 0)), float(event.get("dy", 0)))

    async def fill_login_form(
        self,
        profile_dir: Path,
        email: str,
        password: str,
    ) -> dict[str, Any]:
        """Best-effort: find and fill an email+password login form in the active page."""
        context = await self.context_for_profile(profile_dir)
        pages = context.pages
        if not pages:
            return {"ok": False, "reason": "No browser pages open"}

        # Prefer a claude.ai page, otherwise use last page
        login_page = next(
            (p for p in reversed(pages) if "claude.ai" in (p.url or "")),
            pages[-1],
        )

        email_selectors = [
            "input[type='email']",
            "input[name='email']",
            "input[id*='email']",
            "input[placeholder*='email' i]",
        ]
        filled_email = False
        for sel in email_selectors:
            try:
                el = login_page.locator(sel).first
                await el.wait_for(state="visible", timeout=2_000)
                await el.fill(email)
                filled_email = True
                break
            except Exception:
                continue

        if not filled_email:
            return {"ok": False, "reason": "Email field not found on current page"}

        filled_password = False
        try:
            pw_el = login_page.locator("input[type='password']").first
            await pw_el.wait_for(state="visible", timeout=3_000)
            await pw_el.fill(password)
            filled_password = True
        except Exception:
            pass

        try:
            await login_page.keyboard.press("Enter")
        except Exception:
            pass

        return {
            "ok": filled_email,
            "filled_email": filled_email,
            "filled_password": filled_password,
        }

    async def _navigate_to_login(self, context) -> Any:
        """Navigate to claude.ai/login robustly.

        Handles the two common Camoufox/Gecko startup failure modes:

        NS_BINDING_ABORTED  — Firefox aborted our goto() because the browser was already
                              mid-navigation (another tab was loading, or Camoufox's own
                              startup navigation was running).  The page may have ended up
                              somewhere useful anyway — we check the URL afterwards and
                              return the page if it's on claude.ai rather than retrying.

        Target/context closed — the initial about:blank Page object was replaced during
                              browser startup.  We sleep and retry with a fresh reference.

        After all retries: instead of raising, return whatever claude.ai page we have so
        the caller can decide based on the actual page state.
        """
        last_exc: Exception | None = None

        for attempt in range(4):
            # Always re-fetch page reference to avoid stale objects
            pages = context.pages
            if pages:
                page = next(
                    (p for p in pages if "claude.ai" in (p.url or "")),
                    pages[-1],
                )
            else:
                page = await context.new_page()

            current_url = page.url or ""

            # Already on the login page — no navigation needed
            if "claude.ai/login" in current_url:
                return page

            # Already elsewhere on claude.ai — but NOT /new.
            # /new is excluded because open_login() navigates there with
            # wait_until=domcontentloaded, so the URL may still be /new for
            # unauthenticated users BEFORE React's client-side redirect fires.
            # Returning early for /new would bypass the /login navigation and
            # cause _detect_login_state to see an empty page (→ unknown or worse,
            # false logged_in in older code).  For all other claude.ai paths
            # (e.g. /chat/…, /code) this early return is safe — they are only
            # reachable for authenticated sessions.
            if (
                "claude.ai" in current_url
                and "/new" not in current_url
                and current_url not in ("about:blank", "about:newtab", "")
            ):
                return page

            try:
                await page.goto("https://claude.ai/login", wait_until="domcontentloaded")
                return page
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()

                # NS_BINDING_ABORTED: Gecko aborted our goto() — browser was already
                # navigating internally.  Wait, then check if we landed somewhere useful.
                if "ns_binding" in msg or "binding_aborted" in msg or "aborted" in msg:
                    await asyncio.sleep(1.2)
                    pages = context.pages
                    if pages:
                        p = next((p for p in pages if "claude.ai" in (p.url or "")), pages[-1])
                        if "claude.ai" in (p.url or ""):
                            return p  # Browser got there on its own
                    if attempt < 3:
                        await asyncio.sleep(1.0)
                        continue
                    break

                # Target/context closed: browser still in startup — retry
                if "closed" in msg or "target" in msg or "browser has been closed" in msg:
                    if attempt < 3:
                        await asyncio.sleep(2.0)
                        continue
                    break

                # Any other error: stop immediately
                break

        # All retries exhausted — return whatever claude.ai page we have rather than
        # raising: the caller will detect the actual state via _detect_login_state.
        pages = context.pages
        if pages:
            best = next((p for p in pages if "claude.ai" in (p.url or "")), pages[-1])
            return best
        raise BrowserError(
            f"Could not navigate to claude.ai/login and no browser pages available: {last_exc}"
        )

    async def prewarm(self, profile_dir: Path) -> dict[str, Any]:
        """Pre-open the Camoufox browser process without navigating anywhere.

        Call this in the background as soon as the user starts interacting with the
        login form so the Firefox startup time is hidden behind their typing latency.
        """
        try:
            context = await self.context_for_profile(profile_dir, headless=True, reset=False)
            return {"ok": True, "pages": len(context.pages)}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    async def start_email_login(self, profile_dir: Path, email: str) -> dict[str, Any]:
        """Start the email OTP login flow via Camoufox (headless).

        Opens the browser if not already open, navigates to the Claude login page,
        accepts the cookie banner, fills the email field, and clicks Continue.

        Returns dict with:
          ok:    bool
          state: 'code_form' | 'logged_in' | 'new_account_setup' | 'email_form' | 'unknown'
          reason: (str, only when ok=False)
        """
        context = await self.context_for_profile(profile_dir, headless=True, reset=False)

        # ── Pre-flight: skip navigation if browser is already on the OTP step ──
        # Handles double-submits and NS_BINDING_ABORTED cases where the previous
        # call raised but the browser had already progressed to code_form.
        #
        # NOTE: we do NOT short-circuit for logged_in here.  The profile may
        # contain a stale session-key file whose cookie has since expired, or the
        # browser was opened by prewarm but hasn't navigated yet.  By proceeding
        # with _navigate_to_login we let Claude itself validate the session:
        # a valid cookie will redirect to the main page (detected post-nav as
        # logged_in), an expired one will show the login form (→ OTP flow).
        for p in list(context.pages):
            try:
                pre_state = await self._detect_login_state(p)
            except Exception:
                continue
            if pre_state == "code_form":
                return {"ok": True, "state": "code_form"}
            if pre_state == "new_account_setup":
                return {"ok": False, "state": "new_account_setup",
                        "reason": "new_account_setup"}

        # ── Navigate to login page ────────────────────────────────────────────
        # Gracefully handles NS_BINDING_ABORTED and target-closed races.
        try:
            page = await self._navigate_to_login(context)
        except Exception as exc:
            return {"ok": False, "reason": f"Could not open login page: {exc}"}

        # ── Post-nav state check ──────────────────────────────────────────────
        # _navigate_to_login may return a page already past email_form when the
        # browser navigated autonomously while our goto() was being aborted.
        post_nav_state = await self._detect_login_state(page)
        if post_nav_state == "code_form":
            return {"ok": True, "state": "code_form"}
        if post_nav_state == "logged_in":
            return {"ok": True, "state": "logged_in"}
        if post_nav_state == "new_account_setup":
            return {"ok": False, "state": "new_account_setup", "reason": "new_account_setup"}

        await self._accept_cookies_banner(page)

        # ── Fill email field ──────────────────────────────────────────────────
        # Camoufox humanize=True types char-by-char — JS-clear first to prevent
        # appending to existing content on retry.
        try:
            email_el = await self._find_email_input(page, timeout_ms=10_000)
            if email_el is None:
                await self._open_email_login_step(page)
                email_el = await self._find_email_input(page, timeout_ms=12_000)
            if email_el is None:
                fallback = await self._detect_login_state(page)
                url = (page.url or "").strip()
                return {
                    "ok": False,
                    "state": fallback,
                    "reason": (
                        "Email field not found on login page. "
                        f"state={fallback} url={url}"
                    ),
                }
            await email_el.evaluate(
                "el => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles:true})); }"
            )
            await email_el.fill(email)
        except Exception as exc:
            # Last-chance state check before giving up
            fallback = await self._detect_login_state(page)
            if fallback in ("code_form", "logged_in"):
                return {"ok": True, "state": fallback}
            return {"ok": False, "reason": f"Email field not found on login page: {exc}"}

        # ── Click Continue ────────────────────────────────────────────────────
        try:
            clicked = await self._click_continue_button(page, timeout_ms=10_000)
            if not clicked:
                raise BrowserError("continue button not found")
        except Exception as exc:
            fallback = await self._detect_login_state(page)
            if fallback in ("code_form", "logged_in"):
                return {"ok": True, "state": fallback}
            return {"ok": False, "reason": f"Continue button not found: {exc}"}

        # ── Wait for state transition ─────────────────────────────────────────
        state = await self._wait_for_login_state_change(
            page, from_state="email_form", timeout_ms=25_000
        )
        if state == "email_form":
            error_text = await self._extract_login_error(page)
            if error_text:
                return {
                    "ok": False,
                    "state": "email_form",
                    "reason": self._classify_login_error(error_text),
                }
            return {
                "ok": False,
                "state": "email_form",
                "reason": "Email was not accepted on the login form.",
            }
        return {"ok": True, "state": state}

    async def _find_email_input(self, page, *, timeout_ms: int = 8_000):
        selectors = [
            '[data-testid="email"]',
            "input[type='email']",
            "input[name='email']",
            "input[id*='email']",
            "input[autocomplete='email']",
            "input[placeholder*='email' i]",
            "input[placeholder*='e-mail' i]",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=timeout_ms)
                return locator
            except Exception:
                continue
        return None

    async def _open_email_login_step(self, page) -> None:
        candidates = [
            'button:has-text("Continue with email")',
            'button:has-text("Continue with Email")',
            'button:has-text("Continue with e-mail")',
            'button:has-text("Mit E-Mail fortfahren")',
            'button:has-text("E-Mail")',
            'a:has-text("Continue with email")',
            'a:has-text("Mit E-Mail fortfahren")',
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    await locator.click(timeout=3_000)
                    await page.wait_for_timeout(400)
                    return
            except Exception:
                continue

    async def _click_continue_button(self, page, *, timeout_ms: int = 10_000) -> bool:
        candidates = [
            '[data-testid="continue"]',
            'button:has-text("Continue")',
            'button:has-text("Weiter")',
            'button:has-text("Sign in")',
            'button:has-text("Anmelden")',
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=timeout_ms)
                await locator.click(timeout=3_000)
                return True
            except Exception:
                continue
        try:
            await page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    async def _extract_login_error(self, page) -> str | None:
        """Best-effort extraction of visible login-form errors after email submit."""
        try:
            raw = await page.evaluate(
                """
                () => {
                    const root = document.body;
                    if (!root) return "";

                    const blocks = [];
                    const selectors = [
                        '[role="alert"]',
                        '[aria-live="assertive"]',
                        '[aria-live="polite"]',
                        '[data-testid*="error"]',
                        '[data-error]',
                        '[aria-invalid="true"]',
                        'p',
                        'div',
                        'span',
                    ];
                    const seen = new Set();
                    const terms = new RegExp(
                        '(error|invalid|domain|disposable|temporary' +
                        '|not available|new users|trouble sending' +
                        "|cannot send|can't send|email)", 'i'
                    );
                    for (const selector of selectors) {
                        for (const node of document.querySelectorAll(selector)) {
                            const text = (node.innerText || "").trim();
                            if (!text) continue;
                            if (text.length > 300) continue;
                            if (!terms.test(text)) continue;
                            const key = text.toLowerCase();
                            if (seen.has(key)) continue;
                            seen.add(key);
                            // Avoid noisy parent containers with repeated CTA text.
                            if (text.length > 180) continue;
                            blocks.push(text.replace(/\\s+/g, ' ').trim());
                            if (blocks.length >= 4) {
                                return blocks.join(" | ");
                            }
                        }
                    }
                    return blocks.join(" | ");
                }
                """
            )
        except Exception:
            return None
        text = str(raw or "").strip()
        return text or None

    def _classify_login_error(self, text: str) -> str:
        fragment = text.split("|", 1)[0].strip()
        fragment_lower = fragment.lower()
        lower = text.lower()
        if (
            "disposable" in lower
            or "temporary" in lower
            or "domain rejected" in lower
            or "email domain" in lower
        ):
            return "Disposable/temporary email domains are rejected by Claude."
        if "not available to new users" in lower or "new users" in lower:
            return "Claude does not allow creating a new account for this email right now."
        if "trouble sending" in lower or "cannot send" in lower or "can't send" in lower:
            return "Claude could not send a login email to this address."
        if fragment:
            return fragment
        if fragment_lower:
            return fragment_lower
        return text

    async def submit_otp(self, profile_dir: Path, code: str) -> dict[str, Any]:
        """Submit the 6-digit OTP verification code to the Camoufox browser.

        Expects the browser to be open and already on the code-entry step
        (after start_email_login completed with state='code_form').

        Returns dict with:
          ok:    bool
          state: 'logged_in' | 'new_account_setup' | 'code_form' | 'unknown'
          reason: (str, only when ok=False)
        """
        if not await self.is_profile_open(profile_dir):
            return {"ok": False, "reason": "Browser is not open. Call start_email_login first."}

        context = await self.context_for_profile(profile_dir)
        pages = context.pages
        if not pages:
            return {"ok": False, "reason": "No browser pages open"}

        # Prefer a claude.ai page
        login_page = next(
            (p for p in reversed(pages) if "claude.ai" in (p.url or "")),
            pages[-1],
        )

        # Pre-flight state check — avoid the confusing timeout error when we're already
        # past the code entry step (e.g. new account redirected to onboarding).
        current_state = await self._detect_login_state(login_page)
        if current_state == "new_account_setup":
            return {"ok": False, "state": "new_account_setup",
                    "reason": "new_account_setup"}
        if current_state == "logged_in":
            return {"ok": True, "state": "logged_in"}

        # Fill the 6-digit code
        cleaned = code.strip()[:6]
        try:
            code_el = login_page.locator('[data-testid="code"]').first
            await code_el.wait_for(state="visible", timeout=5_000)
            # JS-clear first (same humanize-safe approach as email field)
            await code_el.evaluate(
                "el => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles:true})); }"
            )
            await code_el.fill(cleaned)
        except Exception as exc:
            current = await self._detect_login_state(login_page)
            return {
                "ok": False,
                "state": current,
                "reason": f"Code input not found (state: {current}): {exc}",
            }

        # Click Continue / submit
        submitted = False
        try:
            continue_btn = login_page.locator('[data-testid="continue"]').first
            await continue_btn.wait_for(state="visible", timeout=3_000)
            await continue_btn.click()
            submitted = True
        except Exception:
            # Fallback: press Enter
            try:
                await login_page.keyboard.press("Enter")
                submitted = True
            except Exception:
                pass

        if not submitted:
            return {"ok": False, "reason": "Could not find Continue button to submit code"}

        # Wait for state change
        state = await self._wait_for_login_state_change(
            login_page, from_state="code_form", timeout_ms=25_000
        )
        if state == "email_form":
            state = await self._wait_for_post_otp_state(
                context,
                login_page,
                timeout_ms=20_000,
            )
        return {"ok": True, "state": state}

    async def get_login_page_state(self, profile_dir: Path) -> dict[str, Any]:
        """Return the current login page state for this profile.

        Returns dict with:
          state: 'email_form' | 'code_form' | 'logged_in' | 'new_account_setup'
                 | 'unknown' | 'browser_closed'
        """
        if not await self.is_profile_open(profile_dir):
            return {"state": "browser_closed"}

        try:
            context = await self.context_for_profile(profile_dir)
        except Exception:
            return {"state": "browser_closed"}

        pages = context.pages
        if not pages:
            return {"state": "browser_closed"}

        # Quick check via cookies first
        try:
            cookies = await context.cookies("https://claude.ai")
            if any(c.get("name") == "sessionKey" and c.get("value") for c in cookies):
                return {"state": "logged_in"}
        except Exception:
            pass

        # Find claude.ai page
        login_page = next(
            (p for p in reversed(pages) if "claude.ai" in (p.url or "")),
            pages[-1],
        )
        state = await self._detect_login_state(login_page)
        return {"state": state}

    async def _detect_login_state(self, page) -> str:
        """Read the current DOM/URL to determine which step of the login flow we're on."""
        try:
            result = await page.evaluate(
                """
                () => {
                    // OTP code entry step
                    if (document.querySelector('[data-testid="code"]')) return 'code_form';
                    // Email entry step
                    if (document.querySelector('[data-testid="email"]')) return 'email_form';
                    // Logged in: user menu is present in the DOM.
                    // NOTE: Do NOT use URL patterns like /new or /code to infer logged_in.
                    // open_login() navigates to /new with wait_until=domcontentloaded, so the
                    // URL may be /new BEFORE React's client-side redirect to /login fires for
                    // unauthenticated users.  URL-based detection causes false logged_in for
                    // brand-new accounts and is not needed — the user-menu selector is the
                    // reliable indicator.
                    if (document.querySelector('[data-testid="user-menu-button"]'))
                        return 'logged_in';
                    // New account onboarding
                    const text = (document.body ? document.body.innerText : '').toLowerCase();
                    if (text.includes('how do you plan') || text.includes('tell us about yourself')
                        || text.includes('create your account') || text.includes('onboarding')) {
                        return 'new_account_setup';
                    }
                    // Login page URL
                    if (window.location.href.includes('/login')) return 'email_form';
                    return 'unknown';
                }
                """
            )
            return str(result) if result else "unknown"
        except Exception:
            return "unknown"

    async def _wait_for_login_state_change(
        self,
        page,
        *,
        from_state: str,
        timeout_ms: int = 10_000,
    ) -> str:
        """Poll the page until the login state changes away from `from_state`.

        Transient 'unknown' states (React mid-navigation, page loading) are
        skipped — we keep waiting until a meaningful state is reached so callers
        never get a spurious 'unknown' return from a page that is still rendering.
        """
        start = asyncio.get_event_loop().time()
        deadline = start + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            state = await self._detect_login_state(page)
            if state != from_state and state != "unknown":
                return state
            await asyncio.sleep(0.4)
        # Timed out — return whatever state we're in now (including unknown)
        return await self._detect_login_state(page)

    async def _wait_for_post_otp_state(
        self,
        context,
        page,
        *,
        timeout_ms: int = 20_000,
    ) -> str:
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        last_state = "unknown"
        while asyncio.get_event_loop().time() < deadline:
            try:
                cookies = await context.cookies("https://claude.ai")
                if any(
                    cookie.get("name") == "sessionKey" and cookie.get("value")
                    for cookie in cookies
                ):
                    return "logged_in"
            except Exception:
                pass
            state = await self._detect_login_state(page)
            if state != "unknown":
                last_state = state
            if state in {"logged_in", "new_account_setup", "code_form"}:
                return state
            await asyncio.sleep(0.5)
        return last_state

    async def _get_or_open_page(self, context, remote_url: str):
        pages = context.pages
        # 1. Prefer a page already at (or near) the target URL.
        for page in pages:
            if remote_url in page.url or page.url in remote_url:
                return page
        # 2. Reuse a blank/empty page rather than spawning a redundant window.
        #    Camoufox opens with an initial about:blank tab; without this check
        #    every call would create an additional window.
        for page in pages:
            url = page.url or ""
            if url in ("about:blank", "about:newtab", ""):
                return page
        # 3. No suitable page found — open a new one.
        return await context.new_page()

    async def _find_prompt_editor(self, page):
        # Primary selectors: require contenteditable='true' so we never match the
        # disabled editor (Claude sets contenteditable="false" while generating a
        # response).  Use a generous timeout — the watcher may arrive while a
        # previous generation is still running and must wait for it to finish.
        primary = [
            ".ProseMirror[contenteditable='true']",
            "[contenteditable='true']",
        ]
        last_error: Exception | None = None
        for selector in primary:
            try:
                locator = page.locator(selector).last
                await locator.wait_for(timeout=90_000)
                return locator
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        # Fallback: older Claude UI variants (textarea, ARIA textbox) — short wait
        for selector in ("textarea", "[role='textbox']"):
            try:
                locator = page.locator(selector).last
                await locator.wait_for(timeout=5_000)
                return locator
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        raise BrowserError(f"Could not find Claude prompt editor: {last_error}")
