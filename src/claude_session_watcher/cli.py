from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .browser import CamoufoxManager
from .discovery import ClaudeSessionDiscoveryProvider, SessionDiscoveryService
from .formatting import build_ui_watcher, format_timestamp
from .insights import build_usage_insights
from .models import AccountWatcher, ClaudeSession, utc_now
from .notifications import NotificationEvent, notifier_from_settings
from .pause_templates import CUSTOM_TEMPLATE, PAUSE_TEMPLATES
from .probe import probe_account
from .profile_cookies import load_claude_cookies
from .service_control import service_status, start_service, stop_service
from .settings import Settings
from .store import Store
from .watcher import WatcherService


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command in (None, "serve"):
        return _serve(args, settings)
    if args.command == "open-ui":
        webbrowser.open(f"http://{settings.host}:{settings.port}")
        return 0
    if args.command == "fetch-browser":
        return subprocess.call([sys.executable, "-m", "camoufox", "fetch"])
    if args.command == "status":
        return _status(args, settings)
    if args.command in {"list", "watchers"}:
        return _status(args, settings)
    if args.command == "watch":
        return _watch(args, settings)
    if args.command == "check":
        return asyncio.run(_check(args, settings))
    if args.command == "logs":
        return _logs(args, settings)
    if args.command == "history":
        return _history(args, settings)
    if args.command == "add":
        return _add(args, settings)
    if args.command == "edit":
        return _edit(args, settings)
    if args.command == "enable":
        return _set_enabled(args, settings, True)
    if args.command == "disable":
        return _set_enabled(args, settings, False)
    if args.command == "sessions":
        return _sessions(args, settings)
    if args.command == "discover":
        return asyncio.run(_discover(args, settings))
    if args.command == "probe":
        return asyncio.run(_probe(args, settings))
    if args.command == "session-add":
        return _session_add(args, settings)
    if args.command == "session-enable":
        return _set_session_enabled(args, settings, True)
    if args.command == "session-disable":
        return _set_session_enabled(args, settings, False)
    if args.command == "start":
        return _print_service_status(start_service(settings), ok_when_stopped=False)
    if args.command == "stop":
        return _print_service_status(stop_service(settings), ok_when_stopped=True)
    if args.command == "restart":
        stop_service(settings)
        return _print_service_status(start_service(settings), ok_when_stopped=False)
    if args.command == "service-status":
        return _print_service_status(service_status(settings), ok_when_stopped=True)
    if args.command == "doctor":
        return _doctor(args, settings)
    if args.command == "notify-test":
        return asyncio.run(_notify_test(settings))

    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude Session Watcher")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the background service and web UI")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--open-ui", action="store_true", help="Open the web UI in your browser")

    subparsers.add_parser("open-ui", help="Open the configured local web UI")
    subparsers.add_parser("fetch-browser", help="Download the pinned Camoufox browser build")

    status = subparsers.add_parser("status", help="Show account watcher status")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    subparsers.add_parser("list", help="Alias for status")
    subparsers.add_parser("watchers", help="Alias for status")

    watch = subparsers.add_parser("watch", help="Continuously show account watcher status")
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--json", action="store_true")

    check = subparsers.add_parser("check", help="Run one account check")
    check.add_argument("account", nargs="?", help="Account watcher id, account id, or account name")
    check.add_argument("--all", action="store_true", help="Check all enabled account watchers")

    logs = subparsers.add_parser("logs", help="Show recent account watcher events")
    logs.add_argument("account", nargs="?", help="Optional account watcher id, account id, or name")
    logs.add_argument("--limit", type=int, default=30)

    history = subparsers.add_parser("history", help="Show recent usage history")
    history.add_argument(
        "account",
        nargs="?",
        help="Optional account watcher id, account id, or name",
    )
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true")

    add = subparsers.add_parser("add", help="Add and select a remote session")
    add.add_argument("name", help="Session title")
    add.add_argument("--account", required=True, help="Account id or name")
    add.add_argument("--remote-url", required=True)
    add.add_argument("--no-watch", action="store_true", help="Add the session without selecting it")
    _add_watcher_fields(add)

    edit = subparsers.add_parser("edit", help="Edit an account watcher")
    edit.add_argument("account", help="Account watcher id, account id, or account name")
    _add_watcher_fields(edit)

    enable = subparsers.add_parser("enable", help="Enable an account watcher")
    enable.add_argument("account", help="Account watcher id, account id, or account name")
    disable = subparsers.add_parser("disable", help="Disable an account watcher")
    disable.add_argument("account", help="Account watcher id, account id, or account name")

    sessions = subparsers.add_parser("sessions", help="List Claude sessions")
    sessions.add_argument("account", nargs="?", help="Optional account id or name")
    sessions.add_argument("--json", action="store_true")

    discover = subparsers.add_parser("discover", help="Discover sessions for an account")
    discover.add_argument("account", help="Account id or name")

    probe = subparsers.add_parser("probe", help="Probe claude.ai HTTP capabilities for an account")
    probe.add_argument("account", help="Account id or name")
    probe.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    probe.add_argument(
        "--session",
        dest="session_id",
        help="Optional session id (session_...) to probe events against",
    )
    probe.add_argument(
        "--send-message",
        dest="send_message",
        help="Send a test user message via POST /v1/sessions/{id}/events (requires --session)",
    )

    session_add = subparsers.add_parser("session-add", help="Add a remote-control session")
    session_add.add_argument("title")
    session_add.add_argument("--account", required=True, help="Account id or name")
    session_add.add_argument("--remote-url", required=True)
    session_add.add_argument("--watch", action="store_true", help="Select the session immediately")

    session_enable = subparsers.add_parser("session-enable", help="Select a session for watching")
    session_enable.add_argument("session", help="Session id, key, or title")
    session_disable = subparsers.add_parser("session-disable", help="Unselect a session")
    session_disable.add_argument("session", help="Session id, key, or title")

    subparsers.add_parser("start", help="Start the local background service")
    subparsers.add_parser("stop", help="Stop the local background service")
    subparsers.add_parser("restart", help="Restart the local background service")
    subparsers.add_parser("service-status", help="Show local background service status")
    doctor = subparsers.add_parser("doctor", help="Run basic environment checks")
    doctor.add_argument("--account", help="Check login cookies for an account id or name")
    subparsers.add_parser("notify-test", help="Send a test notification if configured")
    return parser


def _add_watcher_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--five-hour-threshold", type=float, default=None)
    parser.add_argument("--seven-day-threshold", type=float, default=None)
    parser.add_argument("--check-interval", type=int, default=None)
    parser.add_argument(
        "--pause-template",
        choices=[CUSTOM_TEMPLATE, *PAUSE_TEMPLATES.keys()],
        default=None,
    )
    parser.add_argument("--pause-message", default=None)
    parser.add_argument("--continue-message", default=None)


def _serve(args, settings: Settings) -> int:
    import uvicorn

    from .app import create_app

    settings.host = args.host or settings.host
    settings.port = args.port or settings.port
    settings.validate_web_security()
    if getattr(args, "open_ui", False):
        webbrowser.open(f"http://{settings.host}:{settings.port}")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        reload=False,
        access_log=True,
    )
    return 0


def _store(settings: Settings) -> Store:
    settings.ensure_dirs()
    return Store(settings.db_path)


def _status_rows(settings: Settings) -> list[dict[str, object]]:
    store = _store(settings)
    rows: list[dict[str, object]] = []
    for watcher in store.list_account_watchers():
        account = store.get_account(watcher.account_id)
        sessions = store.list_sessions(watcher.account_id)
        samples = store.list_usage_samples(watcher.id)
        ui = build_ui_watcher(watcher)
        insights = build_usage_insights(watcher, samples)
        rows.append(
            {
                "id": watcher.id,
                "account_id": account.id,
                "account": account.name,
                "state": watcher.state,
                "status": insights.status,
                "status_reason": insights.reason,
                "enabled": watcher.enabled,
                "sessions_total": len(sessions),
                "sessions_watched": sum(1 for session in sessions if session.watch_enabled),
                "sessions_controllable": sum(
                    1 for session in sessions if session.control_supported
                ),
                "five_hour": ui.five_hour.utilization,
                "seven_day": ui.seven_day.utilization,
                "reset_5h": ui.five_hour.reset_display,
                "reset_7d": ui.seven_day.reset_display,
                "last_check": ui.last_checked_display,
                "reason": watcher.last_reason,
                "error": watcher.last_error,
                "usage_source": ui.usage_source,
                "pause_template": watcher.pause_template,
                "paused_until": watcher.paused_until,
                "sample_count": insights.sample_count,
                "five_hour_burn_per_hour": insights.five_hour_burn_per_hour,
                "seven_day_burn_per_hour": insights.seven_day_burn_per_hour,
                "next_pause_at": insights.next_pause_at,
                "next_pause": format_timestamp(insights.next_pause_at),
            }
        )
    return rows


def _status(args, settings: Settings) -> int:
    rows = _status_rows(settings)
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return 0
    _print_status_table(rows)
    return 0


def _print_status_table(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("No account watchers configured.")
        return
    headers = [
        "ID",
        "Account",
        "Status",
        "Enabled",
        "Sessions",
        "5h",
        "7d",
        "Burn 5h",
        "Pause ETA",
        "Reset 5h",
        "Reset 7d",
        "Last check",
        "Source",
    ]
    table = [
        [
            row["id"],
            row["account"],
            row["status"],
            "yes" if row["enabled"] else "no",
            f"{row['sessions_watched']}/{row['sessions_total']}",
            _pct(row["five_hour"]),
            _pct(row["seven_day"]),
            _burn(row["five_hour_burn_per_hour"]),
            row["next_pause"] or "",
            row["reset_5h"] or "",
            row["reset_7d"] or "",
            row["last_check"] or "",
            row["usage_source"] or "",
        ]
        for row in rows
    ]
    _print_table(headers, table)


def _pct(value: object) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%"


def _burn(value: object) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%/h"


def _watch(args, settings: Settings) -> int:
    try:
        while True:
            rows = _status_rows(settings)
            if args.json:
                print(json.dumps(rows))
            else:
                print(time.strftime("%H:%M:%S"))
                _print_status_table(rows)
                print()
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        return 0


async def _check(args, settings: Settings) -> int:
    store = _store(settings)
    browser = CamoufoxManager(
        headless=settings.camoufox_headless,
        os_name=settings.camoufox_os,
    )
    service = WatcherService(store, browser, settings)
    try:
        if args.all:
            watchers = store.list_account_watchers(enabled_only=True)
        else:
            watchers = [_resolve_account_watcher(store, args.account)]
        for watcher in watchers:
            assert watcher.id is not None
            account = store.get_account(watcher.account_id)
            result = await service.check_account_now(watcher.id)
            store.add_account_event(watcher.id, "info", f"CLI check: {result}")
            print(f"{account.name}: {result}")
    finally:
        await browser.close()
    return 0


def _logs(args, settings: Settings) -> int:
    store = _store(settings)
    account_watcher_id = None
    if args.account:
        account_watcher_id = _resolve_account_watcher(store, args.account).id
    for event in store.list_account_events(account_watcher_id=account_watcher_id, limit=args.limit):
        session = f"/{event.session_id}" if event.session_id else ""
        print(
            f"{event.created_at}  #{event.account_watcher_id}{session}  "
            f"{event.level:<7}  {event.message}"
        )
    return 0


def _history(args, settings: Settings) -> int:
    store = _store(settings)
    watcher = _resolve_account_watcher(store, args.account)
    assert watcher.id is not None
    samples = store.list_usage_samples(watcher.id, limit=args.limit)
    if args.json:
        print(json.dumps([_usage_sample_row(sample) for sample in samples], indent=2))
        return 0
    headers = ["Time", "Source", "5h", "7d", "Reset 5h", "Reset 7d"]
    table = [
        [
            format_timestamp(sample.created_at),
            sample.source,
            _pct(sample.five_hour_utilization),
            _pct(sample.seven_day_utilization),
            format_timestamp(sample.five_hour_resets_at),
            format_timestamp(sample.seven_day_resets_at),
        ]
        for sample in samples
    ]
    _print_table(headers, table)
    return 0


def _usage_sample_row(sample) -> dict[str, object]:
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


def _add(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    if account.id is None:
        raise SystemExit("Account has not been stored yet.")
    watcher = store.ensure_account_watcher(account.id)
    _apply_account_watcher_args(store, watcher, args)
    session = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key=store.session_key_from_url(args.remote_url),
            title=args.name,
            url=args.remote_url,
            kind="remote",
            status="unknown",
            watch_enabled=not args.no_watch,
            control_supported=True,
            last_seen_at=utc_now(),
        )
    )
    print(
        f"Added session #{session.id}: {session.title} "
        f"({'selected' if session.watch_enabled else 'not selected'})"
    )
    return 0


def _edit(args, settings: Settings) -> int:
    store = _store(settings)
    watcher = _resolve_account_watcher(store, args.account)
    saved = _apply_account_watcher_args(store, watcher, args)
    account = store.get_account(saved.account_id)
    print(f"Updated account watcher #{saved.id}: {account.name}")
    return 0


def _apply_account_watcher_args(
    store: Store,
    watcher: AccountWatcher,
    args: argparse.Namespace,
) -> AccountWatcher:
    updated = AccountWatcher(
        id=watcher.id,
        account_id=watcher.account_id,
        enabled=watcher.enabled,
        state=watcher.state,
        five_hour_threshold=args.five_hour_threshold or watcher.five_hour_threshold,
        seven_day_threshold=args.seven_day_threshold or watcher.seven_day_threshold,
        resume_threshold=watcher.resume_threshold,
        check_interval_seconds=args.check_interval or watcher.check_interval_seconds,
        pause_template=args.pause_template or watcher.pause_template,
        pause_message=args.pause_message or watcher.pause_message,
        continue_message=args.continue_message or watcher.continue_message,
        last_usage_json=watcher.last_usage_json,
        last_reason=watcher.last_reason,
        last_error=watcher.last_error,
        last_checked_at=watcher.last_checked_at,
    )
    assert watcher.id is not None
    return store.update_account_watcher_config(watcher.id, updated)


def _set_enabled(args, settings: Settings, enabled: bool) -> int:
    store = _store(settings)
    watcher = _resolve_account_watcher(store, args.account)
    assert watcher.id is not None
    store.set_account_watcher_enabled(watcher.id, enabled)
    account = store.get_account(watcher.account_id)
    print(f"{'Enabled' if enabled else 'Disabled'} account watcher #{watcher.id}: {account.name}")
    return 0


def _sessions(args, settings: Settings) -> int:
    store = _store(settings)
    account_id = None
    if args.account:
        account_id = _resolve_account(store, args.account).id
    sessions = store.list_sessions(account_id)
    if args.json:
        print(json.dumps([_session_row(store, session) for session in sessions], indent=2))
        return 0
    _print_sessions_table(store, sessions)
    return 0


async def _discover(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    browser = CamoufoxManager(
        headless=settings.camoufox_headless,
        os_name=settings.camoufox_os,
    )
    discovery = SessionDiscoveryService(
        store,
        ClaudeSessionDiscoveryProvider(browser, keepalive=settings.browser_keepalive),
    )
    try:
        result = await discovery.discover_account(account)
    finally:
        await browser.close()
    print(f"{account.name}: discovered {result.found}, updated {result.updated}")
    return 0


async def _probe(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    results = await probe_account(
        Path(account.profile_dir),
        session_id=getattr(args, "session_id", None),
        send_message=getattr(args, "send_message", None),
    )
    payload = {
        name: {"ok": result.ok, "details": result.details} for name, result in results.items()
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        for name, result in results.items():
            if result.ok:
                print(f"ok   {name}")
            else:
                print(f"fail {name}: {result.details.get('error')}")
    return 0 if all(result.ok for result in results.values()) else 1


def _session_add(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    if account.id is None:
        raise SystemExit("Account has not been stored yet.")
    session = store.upsert_session(
        ClaudeSession(
            id=None,
            account_id=account.id,
            session_key=store.session_key_from_url(args.remote_url),
            title=args.title,
            url=args.remote_url,
            kind="remote",
            status="unknown",
            watch_enabled=args.watch,
            control_supported=True,
            last_seen_at=utc_now(),
        )
    )
    print(
        f"Added session #{session.id}: {session.title} "
        f"({'selected' if session.watch_enabled else 'not selected'})"
    )
    return 0


def _set_session_enabled(args, settings: Settings, enabled: bool) -> int:
    store = _store(settings)
    session = _resolve_session(store, args.session)
    assert session.id is not None
    store.set_session_watch_enabled(session.id, enabled)
    print(f"{'Selected' if enabled else 'Unselected'} session #{session.id}: {session.title}")
    return 0


def _session_row(store: Store, session: ClaudeSession) -> dict[str, object]:
    account = store.get_account(session.account_id)
    return {
        "id": session.id,
        "account": account.name,
        "title": session.title,
        "session_key": session.session_key,
        "kind": session.kind,
        "status": session.status,
        "selected": session.watch_enabled,
        "control_supported": session.control_supported,
        "last_seen_at": session.last_seen_at,
        "last_control_error": session.last_control_error,
        "url": session.url,
    }


def _print_sessions_table(store: Store, sessions: list[ClaudeSession]) -> None:
    if not sessions:
        print("No sessions configured.")
        return
    headers = ["ID", "Account", "Selected", "Title", "Kind", "Status", "Control", "Last seen"]
    table = []
    for session in sessions:
        row = _session_row(store, session)
        table.append(
            [
                row["id"],
                row["account"],
                "yes" if row["selected"] else "no",
                row["title"],
                row["kind"],
                row["status"],
                "yes" if row["control_supported"] else "no",
                row["last_seen_at"] or "",
            ]
        )
    _print_table(headers, table)


def _print_table(headers: list[str], table: list[list[object]]) -> None:
    widths = [len(header) for header in headers]
    for row in table:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(str(value)))
    print("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in table:
        print("  ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(row)))


def _resolve_account_watcher(store: Store, value: str | None) -> AccountWatcher:
    watchers = store.list_account_watchers()
    if not value:
        if len(watchers) == 1:
            return watchers[0]
        raise SystemExit("Specify an account watcher id, account id, or account name.")

    matches: dict[int, AccountWatcher] = {}
    for watcher in watchers:
        account = store.get_account(watcher.account_id)
        if str(watcher.id) == value or str(account.id) == value or account.name == value:
            assert watcher.id is not None
            matches[watcher.id] = watcher
    if not matches:
        raise SystemExit(f"Account watcher not found: {value}")
    if len(matches) > 1:
        raise SystemExit(f"Account watcher reference is ambiguous: {value}")
    return next(iter(matches.values()))


def _resolve_session(store: Store, value: str) -> ClaudeSession:
    matches = [
        session
        for session in store.list_sessions()
        if str(session.id) == value or session.session_key == value or session.title == value
    ]
    if not matches:
        raise SystemExit(f"Session not found: {value}")
    if len(matches) > 1:
        raise SystemExit(f"Session reference is ambiguous: {value}")
    return matches[0]


def _resolve_account(store: Store, value: str):
    for account in store.list_accounts():
        if str(account.id) == value or account.name == value:
            return account
    raise SystemExit(f"Account not found: {value}")


def _print_service_status(status, *, ok_when_stopped: bool) -> int:
    print(f"Service: {'running' if status.running else 'stopped'}")
    print(f"PID: {status.pid or ''}")
    print(f"PID file: {status.pid_path}")
    return 0 if status.running or ok_when_stopped else 1


def _doctor(args, settings: Settings) -> int:
    checks: list[tuple[str, bool, str]] = []
    settings.ensure_dirs()
    checks.append(("data dir", settings.data_dir.exists(), str(settings.data_dir)))
    checks.append(
        ("data dir writable", _doctor_writable(settings.data_dir), str(settings.data_dir))
    )
    checks.append(("db", settings.db_path.parent.exists(), str(settings.db_path)))
    checks.append(("profiles dir", settings.profiles_dir.exists(), str(settings.profiles_dir)))
    checks.append(("web security", _doctor_web_security(settings), settings.host))
    checks.append(
        (
            "notifications",
            True,
            "ntfy configured" if settings.notify_ntfy_url else "not configured",
        )
    )
    try:
        store = _store(settings)
        accounts = store.list_accounts()
        sessions = store.list_sessions()
        selected_sessions = sum(1 for session in sessions if session.watch_enabled)
        checks.append(("accounts", True, str(len(accounts))))
        checks.append(("selected sessions", True, f"{selected_sessions}/{len(sessions)}"))
        if getattr(args, "account", None):
            account = _resolve_account(store, args.account)
            try:
                cookies = load_claude_cookies(Path(account.profile_dir))
                checks.append(
                    ("account cookies", True, f"{account.name}: {len(cookies)} Claude cookies")
                )
            except Exception as exc:  # noqa: BLE001
                checks.append(("account cookies", False, f"{account.name}: {exc}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("db readable", False, str(exc)))
    try:
        from camoufox.async_api import AsyncCamoufox  # noqa: F401

        checks.append(("camoufox import", True, "available (async_api)"))
    except ImportError:
        checks.append(
            (
                "camoufox import",
                False,
                "missing camoufox.async_api (install: pip install -U camoufox[geoip])",
            )
        )
    for name, ok, detail in checks:
        print(f"{'ok' if ok else 'fail'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def _doctor_writable(path) -> bool:
    marker = path / ".doctor-write-test"
    try:
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _doctor_web_security(settings: Settings) -> bool:
    try:
        settings.validate_web_security()
        return True
    except ValueError:
        return False


async def _notify_test(settings: Settings) -> int:
    if not settings.notify_ntfy_url:
        print("Notifications are not configured. Set CSW_NOTIFY_NTFY_URL.")
        return 1
    notifier = notifier_from_settings(settings)
    await notifier.notify(
        NotificationEvent(
            event_type="test",
            title="Claude Session Watcher",
            message="Test notification from Claude Session Watcher.",
        )
    )
    print("Notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
