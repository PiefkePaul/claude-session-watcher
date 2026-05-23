from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import webbrowser

from .browser import CamoufoxManager
from .formatting import build_ui_watcher
from .models import Watcher
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
    if args.command == "add":
        return _add(args, settings)
    if args.command == "edit":
        return _edit(args, settings)
    if args.command == "enable":
        return _set_enabled(args, settings, True)
    if args.command == "disable":
        return _set_enabled(args, settings, False)
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
        return _doctor(settings)

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

    status = subparsers.add_parser("status", help="Show watcher status")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    subparsers.add_parser("list", help="Alias for status")
    subparsers.add_parser("watchers", help="Alias for status")

    watch = subparsers.add_parser("watch", help="Continuously show watcher status")
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--json", action="store_true")

    check = subparsers.add_parser("check", help="Run one watcher check")
    check.add_argument("watcher", nargs="?", help="Watcher id or name")
    check.add_argument("--all", action="store_true", help="Check all enabled watchers")

    logs = subparsers.add_parser("logs", help="Show recent watcher events")
    logs.add_argument("watcher", nargs="?", help="Optional watcher id or name")
    logs.add_argument("--limit", type=int, default=30)

    add = subparsers.add_parser("add", help="Add a watcher")
    add.add_argument("name")
    add.add_argument("--account", required=True, help="Account id or name")
    add.add_argument("--remote-url", required=True)
    _add_watcher_fields(add, include_required=False)

    edit = subparsers.add_parser("edit", help="Edit a watcher")
    edit.add_argument("watcher")
    edit.add_argument("--name")
    edit.add_argument("--account")
    edit.add_argument("--remote-url")
    _add_watcher_fields(edit, include_required=False)

    enable = subparsers.add_parser("enable", help="Enable a watcher")
    enable.add_argument("watcher")
    disable = subparsers.add_parser("disable", help="Disable a watcher")
    disable.add_argument("watcher")

    subparsers.add_parser("start", help="Start the local background service")
    subparsers.add_parser("stop", help="Stop the local background service")
    subparsers.add_parser("restart", help="Restart the local background service")
    subparsers.add_parser("service-status", help="Show local background service status")
    subparsers.add_parser("doctor", help="Run basic environment checks")
    return parser


def _add_watcher_fields(parser: argparse.ArgumentParser, *, include_required: bool) -> None:
    parser.add_argument("--five-hour-threshold", type=float, default=None)
    parser.add_argument("--seven-day-threshold", type=float, default=None)
    parser.add_argument("--check-interval", type=int, default=None)
    parser.add_argument("--pause-message", default=None)
    parser.add_argument("--continue-message", default=None)
    if include_required:
        parser.add_argument("--enabled", action="store_true")


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
    rows: list[dict[str, object]] = []
    for watcher in _store(settings).list_watchers():
        ui = build_ui_watcher(watcher)
        usage = _usage_source(watcher)
        rows.append(
            {
                "id": watcher.id,
                "name": watcher.name,
                "state": watcher.state,
                "enabled": watcher.enabled,
                "five_hour": ui.five_hour.utilization,
                "seven_day": ui.seven_day.utilization,
                "reset_5h": ui.five_hour.reset_display,
                "reset_7d": ui.seven_day.reset_display,
                "last_check": ui.last_checked_display,
                "reason": watcher.last_reason,
                "error": watcher.last_error,
                "usage_source": usage,
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
        print("No watchers configured.")
        return
    headers = [
        "ID",
        "Name",
        "State",
        "Enabled",
        "5h",
        "7d",
        "Reset 5h",
        "Reset 7d",
        "Last check",
        "Source",
    ]
    table = [
        [
            row["id"],
            row["name"],
            row["state"],
            "yes" if row["enabled"] else "no",
            _pct(row["five_hour"]),
            _pct(row["seven_day"]),
            row["reset_5h"] or "",
            row["reset_7d"] or "",
            row["last_check"] or "",
            row["usage_source"] or "",
        ]
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for row in table:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(str(value)))
    print("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in table:
        print("  ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(row)))


def _pct(value: object) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%"


def _usage_source(watcher: Watcher) -> str | None:
    if not watcher.last_usage_json:
        return None
    try:
        data = json.loads(watcher.last_usage_json)
    except json.JSONDecodeError:
        return None
    source = data.get("_csw_usage_source")
    return str(source) if source else None


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
            watchers = store.list_watchers(enabled_only=True)
        else:
            watchers = [_resolve_watcher(store, args.watcher)]
        for watcher in watchers:
            assert watcher.id is not None
            result = await service.check_now(watcher.id)
            store.add_event(watcher.id, "info", f"CLI check: {result}")
            print(f"{watcher.name}: {result}")
    finally:
        await browser.close()
    return 0


def _logs(args, settings: Settings) -> int:
    store = _store(settings)
    watcher_id = None
    if args.watcher:
        watcher_id = _resolve_watcher(store, args.watcher).id
    for event in store.list_events(watcher_id=watcher_id, limit=args.limit):
        print(f"{event.created_at}  #{event.watcher_id}  {event.level:<7}  {event.message}")
    return 0


def _add(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    watcher = Watcher(
        id=None,
        name=args.name,
        account_id=account.id,
        remote_url=args.remote_url,
        five_hour_threshold=args.five_hour_threshold or 95.0,
        seven_day_threshold=args.seven_day_threshold or 98.0,
        check_interval_seconds=args.check_interval or 60,
        pause_message=args.pause_message or Watcher.pause_message,
        continue_message=args.continue_message or "continue",
    )
    created = store.create_watcher(watcher)
    print(f"Created watcher #{created.id}: {created.name}")
    return 0


def _edit(args, settings: Settings) -> int:
    store = _store(settings)
    existing = _resolve_watcher(store, args.watcher)
    account_id = existing.account_id
    if args.account:
        account_id = _resolve_account(store, args.account).id
    updated = Watcher(
        id=existing.id,
        name=args.name or existing.name,
        account_id=account_id,
        remote_url=args.remote_url or existing.remote_url,
        enabled=existing.enabled,
        state=existing.state,
        five_hour_threshold=args.five_hour_threshold or existing.five_hour_threshold,
        seven_day_threshold=args.seven_day_threshold or existing.seven_day_threshold,
        resume_threshold=existing.resume_threshold,
        check_interval_seconds=args.check_interval or existing.check_interval_seconds,
        pause_message=args.pause_message or existing.pause_message,
        continue_message=args.continue_message or existing.continue_message,
    )
    saved = store.update_watcher_config(existing.id, updated)
    print(f"Updated watcher #{saved.id}: {saved.name}")
    return 0


def _set_enabled(args, settings: Settings, enabled: bool) -> int:
    store = _store(settings)
    watcher = _resolve_watcher(store, args.watcher)
    store.set_watcher_enabled(watcher.id, enabled)
    print(f"{'Enabled' if enabled else 'Disabled'} watcher #{watcher.id}: {watcher.name}")
    return 0


def _resolve_watcher(store: Store, value: str | None) -> Watcher:
    if not value:
        watchers = store.list_watchers()
        if len(watchers) == 1:
            return watchers[0]
        raise SystemExit("Specify a watcher id or name.")
    for watcher in store.list_watchers():
        if str(watcher.id) == value or watcher.name == value:
            return watcher
    raise SystemExit(f"Watcher not found: {value}")


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


def _doctor(settings: Settings) -> int:
    checks: list[tuple[str, bool, str]] = []
    settings.ensure_dirs()
    checks.append(("data dir", settings.data_dir.exists(), str(settings.data_dir)))
    checks.append(("db", settings.db_path.parent.exists(), str(settings.db_path)))
    checks.append(("profiles dir", settings.profiles_dir.exists(), str(settings.profiles_dir)))
    checks.append(("web security", _doctor_web_security(settings), settings.host))
    try:
        import camoufox  # noqa: F401

        checks.append(("camoufox import", True, "available"))
    except ImportError as exc:
        checks.append(("camoufox import", False, str(exc)))
    for name, ok, detail in checks:
        print(f"{'ok' if ok else 'fail'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def _doctor_web_security(settings: Settings) -> bool:
    try:
        settings.validate_web_security()
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
