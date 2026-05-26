from __future__ import annotations

import json
import logging
import re
import secrets
import traceback
from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .browser import CamoufoxManager
from .discovery import ClaudeSessionDiscoveryProvider, SessionDiscoveryService
from .display import DisplayManager
from .formatting import build_ui_watcher, format_timestamp
from .insights import UsageInsights, build_usage_insights
from .models import Account, AccountWatcher, ClaudeSession, Watcher, utc_now
from .pause_templates import pause_template_options
from .probe import probe_account
from .profile_cookies import has_session_key
from .settings import Settings
from .store import Store
from .watcher import WatcherService


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "account"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate_web_security()
    settings.ensure_dirs()
    store = Store(settings.db_path)
    display = DisplayManager(
        enabled=settings.enable_vnc,
        display=settings.vnc_display,
        screen=settings.vnc_screen,
        vnc_port=settings.vnc_port,
        web_root=settings.vnc_web_root,
        console_url=settings.browser_console_url,
    )
    browser = CamoufoxManager(
        headless=settings.camoufox_headless,
        os_name=settings.camoufox_os,
        display_manager=display,
    )
    service = WatcherService(store, browser, settings)
    discovery = SessionDiscoveryService(
        store,
        ClaudeSessionDiscoveryProvider(browser, keepalive=settings.browser_keepalive),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = store
        app.state.browser = browser
        app.state.display = display
        app.state.service = service
        app.state.discovery = discovery
        service.start()
        yield
        await service.stop()
        await browser.close()
        await display.stop()

    app = FastAPI(title="Claude Session Watcher", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(settings_path("templates")))
    app.mount("/static", StaticFiles(directory=str(settings_path("static"))), name="static")

    @app.middleware("http")
    async def require_ui_token(request: Request, call_next):
        if not settings.ui_token or request.url.path == "/health":
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        if _valid_auth_header(authorization, settings.ui_token):
            return await call_next(request)
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Claude Session Watcher"'},
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        accounts = store.list_accounts()
        account_rows = [
            await _account_row(request, store, browser, display, settings, account)
            for account in accounts
        ]
        events = store.list_account_events(limit=50)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "accounts": account_rows,
                "events": events,
                "data_dir": settings.data_dir,
                "pause_templates": pause_template_options(),
            },
        )

    @app.post("/accounts")
    async def create_account(name: str = Form(...)):
        profile_dir = settings.profiles_dir / _slug(name)
        store.create_account(name=name.strip(), profile_dir=str(profile_dir))
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/discover")
    async def discover_account_sessions(account_id: int):
        account = store.get_account(account_id)
        try:
            result = await discovery.discover_account(account)
            account_watcher = store.ensure_account_watcher(account_id)
            store.add_account_event(
                account_watcher.id,
                "info",
                f"Discovery updated {result.updated} sessions",
            )
        except Exception as exc:  # noqa: BLE001
            account_watcher = store.ensure_account_watcher(account_id)
            store.add_account_event(account_watcher.id, "error", f"Discovery failed: {exc}")
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/probe")
    async def probe_account_capabilities(account_id: int):
        account = store.get_account(account_id)
        account_watcher = store.ensure_account_watcher(account_id)
        try:
            results = await probe_account(Path(account.profile_dir))
            for name, result in results.items():
                if result.ok:
                    summary = ""
                    if name == "usage":
                        five = result.details.get("five_hour") or {}
                        seven = result.details.get("seven_day") or {}
                        summary = (
                            f"5h={five.get('utilization')}% reset={five.get('resets_at')}; "
                            f"7d={seven.get('utilization')}% reset={seven.get('resets_at')}"
                        )
                    elif name == "sessions":
                        summary = f"count={result.details.get('count')}"
                    elif name == "events":
                        summary = (
                            f"session_id={result.details.get('session_id')} "
                            f"title={result.details.get('title')}"
                        )
                    elif name == "send_message":
                        summary = f"session_id={result.details.get('session_id')}"
                    elif name == "oauth_usage":
                        five = result.details.get("five_hour") or {}
                        seven = result.details.get("seven_day") or {}
                        summary = (
                            f"source={result.details.get('source')}; "
                            f"5h={five.get('utilization')}% 7d={seven.get('utilization')}%"
                        )
                    elif name == "capabilities":
                        caps = result.details or {}
                        fields = (
                            "usage_get",
                            "sessions_get",
                            "events_get",
                            "events_post",
                            "oauth_usage_get",
                        )
                        rendered = []
                        for field in fields:
                            info = caps.get(field) if isinstance(caps, dict) else None
                            if not isinstance(info, dict):
                                continue
                            if not info.get("checked"):
                                rendered.append(f"{field}=n/a")
                            else:
                                rendered.append(
                                    f"{field}={'yes' if info.get('supported') else 'no'}"
                                )
                        summary = ", ".join(rendered)
                    store.add_account_event(
                        account_watcher.id,
                        "info",
                        f"Probe {name}: ok{(' (' + summary + ')') if summary else ''}",
                    )
                else:
                    store.add_account_event(
                        account_watcher.id,
                        "warning",
                        f"Probe {name}: failed ({result.details.get('error')})",
                    )
        except Exception as exc:  # noqa: BLE001
            store.add_account_event(account_watcher.id, "error", f"Probe failed: {exc}")
        return RedirectResponse("/", status_code=303)

    @app.post("/account-watchers/{account_watcher_id}/check")
    @app.post("/accounts/{account_watcher_id}/check")
    async def check_account_watcher(account_watcher_id: int):
        try:
            result = await service.check_account_now(account_watcher_id)
            store.add_account_event(account_watcher_id, "info", f"Manual check: {result}")
        except Exception as exc:  # noqa: BLE001
            store.add_account_event(account_watcher_id, "error", f"Manual check failed: {exc}")
        return RedirectResponse("/", status_code=303)

    @app.post("/account-watchers/{account_watcher_id}")
    async def update_account_watcher(
        account_watcher_id: int,
        enabled: bool = Form(False),
        five_hour_threshold: float = Form(95.0),
        seven_day_threshold: float = Form(98.0),
        check_interval_seconds: int = Form(60),
        pause_template: str = Form("custom"),
        pause_message: str = Form(...),
        continue_message: str = Form("continue"),
    ):
        existing = store.get_account_watcher(account_watcher_id)
        updated = AccountWatcher(
            id=existing.id,
            account_id=existing.account_id,
            enabled=enabled,
            state=existing.state,
            five_hour_threshold=five_hour_threshold,
            seven_day_threshold=seven_day_threshold,
            resume_threshold=existing.resume_threshold,
            check_interval_seconds=check_interval_seconds,
            pause_template=pause_template,
            pause_message=pause_message,
            continue_message=continue_message,
            last_usage_json=existing.last_usage_json,
            last_reason=existing.last_reason,
            last_error=existing.last_error,
            last_checked_at=existing.last_checked_at,
        )
        store.update_account_watcher_config(account_watcher_id, updated)
        service.reschedule_now(account_watcher_id)
        store.add_account_event(account_watcher_id, "info", "Account watcher configuration updated")
        return RedirectResponse("/", status_code=303)

    @app.post("/account-watchers/{account_watcher_id}/enable")
    async def enable_account_watcher(account_watcher_id: int):
        store.set_account_watcher_enabled(account_watcher_id, True)
        service.reschedule_now(account_watcher_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/account-watchers/{account_watcher_id}/disable")
    async def disable_account_watcher(account_watcher_id: int):
        store.set_account_watcher_enabled(account_watcher_id, False)
        service.reschedule_now(account_watcher_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/sessions")
    async def add_session(
        account_id: int = Form(...),
        title: str = Form(...),
        remote_url: str = Form(...),
        watch_enabled: bool = Form(False),
    ):
        store.upsert_session(
            ClaudeSession(
                id=None,
                account_id=account_id,
                session_key=store.session_key_from_url(remote_url.strip()),
                title=title.strip(),
                url=remote_url.strip(),
                kind="remote",
                status="unknown",
                watch_enabled=watch_enabled,
                control_supported=True,
                last_seen_at=utc_now(),
            )
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/sessions/{session_id}/enable")
    async def enable_session(session_id: int):
        store.set_session_watch_enabled(session_id, True)
        return RedirectResponse("/", status_code=303)

    @app.post("/sessions/{session_id}/disable")
    async def disable_session(session_id: int):
        store.set_session_watch_enabled(session_id, False)
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/login")
    async def open_login(account_id: int):
        await _open_account_login(store, browser, display, account_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/finish-login")
    async def finish_login(account_id: int):
        await _finish_account_login(store, browser, settings, account_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/close-browser")
    async def close_account_browser(account_id: int):
        await _close_account_browser(store, browser, account_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/browser-console", response_class=HTMLResponse)
    async def browser_console(request: Request, account_id: int, wait: bool = False):
        account = store.get_account(account_id)
        return templates.TemplateResponse(
            request,
            "browser_console.html",
            {
                "account": account,
                "wait": wait,
                "auto_finish_login": settings.auto_finish_login,
            },
        )

    @app.post("/watchers")
    async def create_watcher(
        name: str = Form(...),
        account_id: int = Form(...),
        remote_url: str = Form(...),
        five_hour_threshold: float = Form(95.0),
        seven_day_threshold: float = Form(98.0),
        check_interval_seconds: int = Form(60),
        pause_message: str = Form(
            "Pause after the current safe checkpoint. Do not start new work. "
            "Wait until I send continue."
        ),
        continue_message: str = Form("continue"),
    ):
        watcher = Watcher(
            id=None,
            name=name.strip(),
            account_id=account_id,
            remote_url=remote_url.strip(),
            five_hour_threshold=five_hour_threshold,
            seven_day_threshold=seven_day_threshold,
            check_interval_seconds=check_interval_seconds,
            pause_message=pause_message,
            continue_message=continue_message,
        )
        store.create_watcher(watcher)
        return RedirectResponse("/", status_code=303)

    @app.get("/watchers/{watcher_id}/edit", response_class=HTMLResponse)
    async def edit_watcher(request: Request, watcher_id: int):
        watcher = store.get_watcher(watcher_id)
        return templates.TemplateResponse(
            request,
            "watcher_edit.html",
            {
                "accounts": store.list_accounts(),
                "watcher": watcher,
            },
        )

    @app.post("/watchers/{watcher_id}")
    async def update_watcher(
        watcher_id: int,
        name: str = Form(...),
        account_id: int = Form(...),
        remote_url: str = Form(...),
        enabled: bool = Form(False),
        five_hour_threshold: float = Form(95.0),
        seven_day_threshold: float = Form(98.0),
        check_interval_seconds: int = Form(60),
        pause_message: str = Form(...),
        continue_message: str = Form("continue"),
    ):
        existing = store.get_watcher(watcher_id)
        updated = Watcher(
            id=watcher_id,
            name=name.strip(),
            account_id=account_id,
            remote_url=remote_url.strip(),
            enabled=enabled,
            state=existing.state,
            five_hour_threshold=five_hour_threshold,
            seven_day_threshold=seven_day_threshold,
            resume_threshold=existing.resume_threshold,
            check_interval_seconds=check_interval_seconds,
            pause_template=existing.pause_template,
            pause_message=pause_message,
            continue_message=continue_message,
        )
        store.update_watcher_config(watcher_id, updated)
        service.reschedule_now(watcher_id)
        store.add_event(watcher_id, "info", "Watcher configuration updated")
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers/{watcher_id}/enable")
    async def enable_watcher(watcher_id: int):
        store.set_watcher_enabled(watcher_id, True)
        service.reschedule_now(watcher_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers/{watcher_id}/disable")
    async def disable_watcher(watcher_id: int):
        store.set_watcher_enabled(watcher_id, False)
        service.reschedule_now(watcher_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/watchers/{watcher_id}/check")
    async def check_watcher(watcher_id: int):
        try:
            result = await service.check_now(watcher_id)
            store.add_event(watcher_id, "info", f"Manual check: {result}")
        except Exception as exc:  # noqa: BLE001
            store.add_event(watcher_id, "error", f"Manual check failed: {exc}")
        return RedirectResponse("/", status_code=303)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/status")
    async def api_status():
        return {
            "accounts": len(store.list_accounts()),
            "account_watchers": [
                _api_account_watcher(store, watcher)
                for watcher in store.list_account_watchers()
            ],
            "watchers": [_api_watcher(watcher) for watcher in store.list_watchers()],
        }

    @app.get("/api/accounts")
    async def api_accounts():
        return [_api_account(store, account) for account in store.list_accounts()]

    @app.get("/api/accounts/{account_id}/sessions")
    async def api_account_sessions(account_id: int):
        return [_api_session(session) for session in store.list_sessions(account_id)]

    @app.get("/api/accounts/{account_id}/browser-state")
    async def api_account_browser_state(request: Request, account_id: int):
        account = store.get_account(account_id)
        return await _browser_state(request, browser, display, settings, account)

    @app.post("/api/accounts/{account_id}/login")
    async def api_open_login(request: Request, account_id: int):
        account = await _open_account_login(store, browser, display, account_id)
        return await _browser_state(request, browser, display, settings, account)

    @app.post("/api/accounts/{account_id}/finish-login")
    async def api_finish_login(request: Request, account_id: int):
        account = await _finish_account_login(store, browser, settings, account_id)
        return await _browser_state(request, browser, display, settings, account)

    @app.post("/api/accounts/{account_id}/close-browser")
    async def api_close_browser(request: Request, account_id: int):
        account = await _close_account_browser(store, browser, account_id)
        return await _browser_state(request, browser, display, settings, account)

    @app.get("/api/account-watchers/{account_watcher_id}/usage-history")
    async def api_usage_history(account_watcher_id: int, limit: int = 200):
        return [
            _api_usage_sample(sample)
            for sample in store.list_usage_samples(account_watcher_id, limit=limit)
        ]

    @app.post("/api/accounts/{account_id}/discover")
    async def api_discover_account(account_id: int):
        result = await discovery.discover_account(store.get_account(account_id))
        return {"account_id": account_id, "found": result.found, "updated": result.updated}

    @app.post("/api/accounts/{account_id}/probe")
    async def api_probe_account(account_id: int):
        account = store.get_account(account_id)
        results = await probe_account(Path(account.profile_dir))
        return {
            "account_id": account_id,
            "results": {
                name: {"ok": result.ok, "details": result.details}
                for name, result in results.items()
            },
        }

    @app.post("/api/account-watchers/{account_watcher_id}/check")
    @app.post("/api/accounts/{account_watcher_id}/check")
    async def api_check_account(account_watcher_id: int):
        result = await service.check_account_now(account_watcher_id)
        return {
            "result": result,
            "account_watcher": _api_account_watcher(
                store,
                store.get_account_watcher(account_watcher_id),
            ),
        }

    @app.post("/api/account-watchers/{account_watcher_id}/enable")
    async def api_enable_account_watcher(account_watcher_id: int):
        store.set_account_watcher_enabled(account_watcher_id, True)
        return _api_account_watcher(store, store.get_account_watcher(account_watcher_id))

    @app.post("/api/account-watchers/{account_watcher_id}/disable")
    async def api_disable_account_watcher(account_watcher_id: int):
        store.set_account_watcher_enabled(account_watcher_id, False)
        return _api_account_watcher(store, store.get_account_watcher(account_watcher_id))

    @app.post("/api/sessions/{session_id}/enable")
    async def api_enable_session(session_id: int):
        store.set_session_watch_enabled(session_id, True)
        return _api_session(store.get_session(session_id))

    @app.post("/api/sessions/{session_id}/disable")
    async def api_disable_session(session_id: int):
        store.set_session_watch_enabled(session_id, False)
        return _api_session(store.get_session(session_id))

    @app.get("/api/watchers")
    async def api_watchers():
        return [_api_watcher(watcher) for watcher in store.list_watchers()]

    @app.post("/api/watchers/{watcher_id}/check")
    async def api_check_watcher(watcher_id: int):
        try:
            result = await service.check_now(watcher_id)
            store.add_event(watcher_id, "info", f"API check: {result}")
            return {"result": result, "watcher": _api_watcher(store.get_watcher(watcher_id))}
        except Exception as exc:  # noqa: BLE001
            store.add_event(watcher_id, "error", f"API check failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


def settings_path(kind: str):
    from importlib.resources import files

    return files("claude_session_watcher").joinpath(kind)


def _valid_auth_header(authorization: str, token: str) -> bool:
    if authorization.startswith("Bearer "):
        return secrets.compare_digest(authorization.removeprefix("Bearer ").strip(), token)
    if authorization.startswith("Basic "):
        import base64
        import binascii

        try:
            decoded = base64.b64decode(authorization.removeprefix("Basic ").strip()).decode()
        except (binascii.Error, UnicodeDecodeError):
            return False
        _username, _separator, password = decoded.partition(":")
        return secrets.compare_digest(password, token)
    return False


async def _account_row(
    request: Request,
    store: Store,
    browser: CamoufoxManager,
    display: DisplayManager,
    settings: Settings,
    account: Account,
) -> dict[str, object]:
    if account.id is None:
        raise ValueError("Account must be stored before rendering")
    account_watcher = store.ensure_account_watcher(account.id)
    sessions = store.list_sessions(account.id)
    samples = store.list_usage_samples(account_watcher.id)
    insights = build_usage_insights(account_watcher, samples)
    browser_state = await _browser_state(request, browser, display, settings, account)
    return {
        "account": account,
        "watcher": account_watcher,
        "ui": build_ui_watcher(account_watcher),
        "insights": insights,
        "insight_display": _insight_display(insights),
        "browser": browser_state,
        "sessions": sessions,
        "session_count": len(sessions),
        "watched_count": sum(1 for session in sessions if session.watch_enabled),
        "controllable_count": sum(1 for session in sessions if session.control_supported),
    }


async def _browser_state(
    request: Request,
    browser: CamoufoxManager,
    display: DisplayManager,
    settings: Settings,
    account: Account,
) -> dict[str, object]:
    profile_dir = Path(account.profile_dir)
    try:
        browser_open = await browser.is_profile_open(profile_dir)
    except Exception as exc:  # noqa: BLE001 - best-effort status endpoint
        browser_open = False
        store_error = str(exc)
    else:
        store_error = None
    display_state = display.state()
    login_detected = has_session_key(profile_dir)
    return {
        "account_id": account.id,
        "browser_open": browser_open,
        "display_enabled": display_state.enabled,
        "display_running": display_state.running,
        "vnc_ready": display_state.ready,
        "console_url": (
            _resolve_console_url(request, settings, display_state) if browser_open else None
        ),
        "login_detected": login_detected,
        "status": account.status,
        "last_error": account.last_error or store_error,
    }


def _resolve_console_url(
    request: Request,
    settings: Settings,
    display_state,
) -> str | None:
    configured = settings.browser_console_url
    if configured:
        parsed = urlsplit(configured)
        configured_host = parsed.hostname
        request_host = request.url.hostname
        if (
            _is_loopback_host(configured_host)
            and request_host
            and not _is_loopback_host(request_host)
        ):
            port = (
                settings.browser_console_public_port
                if settings.browser_console_public_port is not None
                else parsed.port
            )
            scheme = request.url.scheme or parsed.scheme or "http"
            return _build_url(
                scheme=scheme,
                host=request_host,
                port=port,
                path=parsed.path or "/vnc.html",
                query=parsed.query,
            )
        return configured

    request_host = request.url.hostname
    if not request_host:
        return None
    port = (
        settings.browser_console_public_port
        if settings.browser_console_public_port is not None
        else display_state.vnc_port
    )
    return _build_url(
        scheme=request.url.scheme or "http",
        host=request_host,
        port=port,
        path="/vnc.html",
        query="autoconnect=true&resize=scale&path=websockify",
    )


def _build_url(*, scheme: str, host: str, port: int | None, path: str, query: str) -> str:
    default_port = 443 if scheme == "https" else 80
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if port is not None and port != default_port:
        netloc = f"{host}:{port}"
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower().strip("[]")
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


async def _open_account_login(
    store: Store,
    browser: CamoufoxManager,
    display: DisplayManager,
    account_id: int,
) -> Account:
    account = store.get_account(account_id)
    account_watcher = store.ensure_account_watcher(account_id)
    try:
        profile_dir = Path(account.profile_dir)
        try:
            await browser.open_login(profile_dir)
        except Exception:
            # Rarely the first headful launch can fail due to transient display/browser startup
            # issues. A single retry is cheap and makes "Open login" much less flaky.
            await browser.close_profile(profile_dir)
            await display.stop()
            await browser.open_login(profile_dir)
        store.update_account_status(account_id, "login-opened")
        store.add_account_event(account_watcher.id, "info", "Login browser opened")
        return store.get_account(account_id)
    except Exception as exc:  # noqa: BLE001
        await display.stop()
        logging.getLogger("claude_session_watcher").exception(
            "Open login failed for account_id=%s", account_id
        )
        traceback.print_exc()
        store.update_account_status(account_id, "error", str(exc))
        store.add_account_event(account_watcher.id, "error", f"Login browser failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _finish_account_login(
    store: Store,
    browser: CamoufoxManager,
    settings: Settings,
    account_id: int,
) -> Account:
    account = store.get_account(account_id)
    account_watcher = store.ensure_account_watcher(account_id)
    profile_dir = Path(account.profile_dir)
    try:
        if not has_session_key(profile_dir):
            await browser.session_key(profile_dir)
        portal = await browser.code_portal_status(profile_dir)
        if portal.get("disabled"):
            if getattr(settings, "auto_switch_to_pro_plan", True):
                store.add_account_event(
                    account_watcher.id,
                    "info",
                    "Claude Code disabled. Attempting automatic profile switch to Pro plan...",
                )
                try:
                    result = await browser.ensure_pro_plan(profile_dir)
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "reason": str(exc)}
                if result.get("ok"):
                    portal = await browser.code_portal_status(profile_dir)
                if not portal.get("disabled"):
                    await browser.close_profile(profile_dir)
                    store.update_account_status(account_id, "logged-in")
                    store.add_account_event(account_watcher.id, "info", "Login finished")
                    return store.get_account(account_id)

            message = str(portal.get("message") or "Claude Code disabled.")
            store.update_account_status(account_id, "code-disabled", message)
            store.add_account_event(
                account_watcher.id,
                "error",
                f"Claude Code disabled for this organization: {message}",
            )
            # Keep the browser open so the user can switch profile/organization.
            return store.get_account(account_id)

        await browser.close_profile(profile_dir)
        store.update_account_status(account_id, "logged-in")
        store.add_account_event(account_watcher.id, "info", "Login finished")
    except Exception as exc:  # noqa: BLE001
        store.update_account_status(account_id, "login-incomplete", str(exc))
        store.add_account_event(account_watcher.id, "warning", f"Login check failed: {exc}")
    return store.get_account(account_id)


async def _close_account_browser(
    store: Store,
    browser: CamoufoxManager,
    account_id: int,
) -> Account:
    account = store.get_account(account_id)
    account_watcher = store.ensure_account_watcher(account_id)
    await browser.close_profile(Path(account.profile_dir))
    store.update_account_status(account_id, "browser-closed")
    store.add_account_event(account_watcher.id, "info", "Browser closed")
    return store.get_account(account_id)


def _api_account(store: Store, account: Account) -> dict[str, object]:
    if account.id is None:
        raise ValueError("Account must be stored before serializing")
    account_watcher = store.ensure_account_watcher(account.id)
    return {
        "id": account.id,
        "name": account.name,
        "profile_dir": account.profile_dir,
        "status": account.status,
        "last_error": account.last_error,
        "watcher": _api_account_watcher(store, account_watcher),
        "sessions": [_api_session(session) for session in store.list_sessions(account.id)],
    }


def _api_account_watcher(store: Store, watcher: AccountWatcher) -> dict[str, object]:
    ui = build_ui_watcher(watcher)
    account = store.get_account(watcher.account_id)
    sessions = store.list_sessions(watcher.account_id)
    samples = store.list_usage_samples(watcher.id)
    insights = build_usage_insights(watcher, samples)
    return {
        "id": watcher.id,
        "account_id": watcher.account_id,
        "account_name": account.name,
        "enabled": watcher.enabled,
        "state": watcher.state,
        "five_hour_threshold": watcher.five_hour_threshold,
        "seven_day_threshold": watcher.seven_day_threshold,
        "check_interval_seconds": watcher.check_interval_seconds,
        "pause_template": watcher.pause_template,
        "paused_at": watcher.paused_at,
        "paused_limit": watcher.paused_limit,
        "paused_until": watcher.paused_until,
        "five_hour": ui.five_hour.utilization,
        "seven_day": ui.seven_day.utilization,
        "reset_5h": ui.five_hour.reset_display,
        "reset_7d": ui.seven_day.reset_display,
        "last_check": ui.last_checked_display,
        "last_reason": watcher.last_reason,
        "last_error": watcher.last_error,
        "usage_source": _usage_source(watcher.last_usage_json),
        "session_count": len(sessions),
        "watched_session_count": sum(1 for session in sessions if session.watch_enabled),
        "controllable_session_count": sum(1 for session in sessions if session.control_supported),
        "insights": _api_usage_insights(insights),
    }


def _api_session(session: ClaudeSession) -> dict[str, object]:
    return {
        "id": session.id,
        "account_id": session.account_id,
        "session_key": session.session_key,
        "title": session.title,
        "url": session.url,
        "kind": session.kind,
        "status": session.status,
        "watch_enabled": session.watch_enabled,
        "control_supported": session.control_supported,
        "last_seen_at": session.last_seen_at,
        "last_checked_at": session.last_checked_at,
        "last_control_error": session.last_control_error,
    }


def _api_usage_sample(sample) -> dict[str, object]:
    return {
        "id": sample.id,
        "account_watcher_id": sample.account_watcher_id,
        "source": sample.source,
        "five_hour_utilization": sample.five_hour_utilization,
        "seven_day_utilization": sample.seven_day_utilization,
        "five_hour_resets_at": sample.five_hour_resets_at,
        "seven_day_resets_at": sample.seven_day_resets_at,
        "created_at": sample.created_at,
    }


def _api_usage_insights(insights: UsageInsights) -> dict[str, object]:
    return {
        "status": insights.status,
        "reason": insights.reason,
        "sample_count": insights.sample_count,
        "five_hour_burn_per_hour": insights.five_hour_burn_per_hour,
        "seven_day_burn_per_hour": insights.seven_day_burn_per_hour,
        "five_hour_pause_at": insights.five_hour_pause_at,
        "seven_day_pause_at": insights.seven_day_pause_at,
        "next_pause_at": insights.next_pause_at,
    }


def _insight_display(insights: UsageInsights) -> dict[str, str]:
    return {
        "five_hour_burn": _format_burn(insights.five_hour_burn_per_hour),
        "seven_day_burn": _format_burn(insights.seven_day_burn_per_hour),
        "next_pause": format_timestamp(insights.next_pause_at),
    }


def _format_burn(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.1f}%/h"


def _usage_source(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        usage_source = json.loads(raw_json).get("_csw_usage_source")
    except json.JSONDecodeError:
        return None
    return str(usage_source) if usage_source else None


def _api_watcher(watcher: Watcher) -> dict[str, object]:
    ui = build_ui_watcher(watcher)
    return {
        "id": watcher.id,
        "name": watcher.name,
        "account_id": watcher.account_id,
        "remote_url": watcher.remote_url,
        "enabled": watcher.enabled,
        "state": watcher.state,
        "five_hour": ui.five_hour.utilization,
        "seven_day": ui.seven_day.utilization,
        "reset_5h": ui.five_hour.reset_display,
        "reset_7d": ui.seven_day.reset_display,
        "last_check": ui.last_checked_display,
        "last_reason": watcher.last_reason,
        "last_error": watcher.last_error,
        "usage_source": _usage_source(watcher.last_usage_json),
    }


app = create_app()
