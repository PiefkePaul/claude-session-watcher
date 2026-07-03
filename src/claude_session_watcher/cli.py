from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .background_service import (
    BackgroundServiceError,
    background_backend,
    background_service_status,
    install_background_service,
    restart_background_service,
    start_background_service,
    stop_background_service,
    uninstall_background_service,
)
from .browser import BrowserError, CamoufoxManager
from .desktop_runtime import (
    MODE_INSTALLED,
    MODE_TEMPORARY,
    DesktopRuntimeError,
    agent_running,
    desktop_task_status,
    load_mode_state,
    request_agent_quit,
    send_agent_command,
    set_autostart,
    set_mode,
    start_agent_process,
)
from .discovery import ClaudeSessionDiscoveryProvider, SessionDiscoveryService
from .formatting import build_ui_watcher, format_timestamp
from .insights import build_usage_insights
from .models import Account, AccountWatcher, ClaudeSession, utc_now
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

    if args.command == "serve":
        return _serve(args, settings)
    if args.command == "run":
        try:
            return asyncio.run(_run(settings))
        except KeyboardInterrupt:
            return 0
    if args.command == "open-ui":
        webbrowser.open(f"http://{settings.host}:{settings.port}")
        return 0
    if args.command == "fetch-browser":
        return subprocess.call([sys.executable, "-m", "camoufox", "fetch"])
    if args.command == "dashboard":
        return _dashboard(args, settings)
    if args.command == "account":
        return _account_command(args, settings)
    if args.command == "session":
        return _session_command(args, settings)
    if args.command == "watcher":
        return _watcher_command(args, settings)
    if args.command == "config":
        return _config_command(args, settings)
    if args.command == "native":
        return _native_command(args, settings)
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
    if args.command == "accounts":
        return _accounts(args, settings)
    if args.command == "account-create":
        return _account_create(args, settings)
    if args.command == "account-delete":
        return asyncio.run(_account_delete(args, settings))
    if args.command == "account-login":
        return asyncio.run(_account_login(args, settings))
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
    parser = argparse.ArgumentParser(
        description="Claude Session Watcher (CLI-first)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Core commands:\n"
            "  csw account add PC\n"
            "  csw account login PC --email you@example.com\n"
            "  csw session discover PC\n"
            "  csw session list PC\n"
            "  csw watcher run\n"
            "  csw dashboard --once\n\n"
            "Legacy top-level aliases are still supported for compatibility."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="{dashboard,account,session,watcher,config,native,fetch-browser}",
    )

    serve = subparsers.add_parser(
        "serve",
        help=argparse.SUPPRESS,
        description="Legacy command: run the background service and web UI.",
    )
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--open-ui", action="store_true", help="Open the web UI in your browser")
    subparsers.add_parser(  # used by daemon/service_control
        "run",
        help=argparse.SUPPRESS,
        description="Legacy command: run watcher loop in foreground.",
    )

    subparsers.add_parser(
        "open-ui",
        help=argparse.SUPPRESS,
        description="Legacy command: open the configured local web UI.",
    )
    subparsers.add_parser("fetch-browser", help="Download the pinned Camoufox browser build")

    dashboard = subparsers.add_parser("dashboard", help="Show ASCII dashboard")
    dashboard.add_argument("--interval", type=int, default=3, help="Refresh interval in seconds")
    dashboard.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    dashboard.add_argument("--json", action="store_true", help="Print dashboard payload as JSON")

    account = subparsers.add_parser("account", help="Manage Claude accounts")
    account_sub = account.add_subparsers(dest="account_command")
    account_list = account_sub.add_parser("list", help="List configured accounts")
    account_list.add_argument("--json", action="store_true")
    account_add = account_sub.add_parser("add", help="Create an account")
    account_add.add_argument("name", help="Account name")
    account_add.add_argument(
        "--profile-dir",
        default=None,
        help="Optional explicit profile directory",
    )
    _add_watcher_fields(account_add)
    account_remove = account_sub.add_parser("remove", help="Delete an account")
    account_remove.add_argument("account", help="Account id or name")
    account_remove.add_argument("--purge-profile", action="store_true")
    account_login = account_sub.add_parser("login", help="Headless email OTP login")
    account_login.add_argument("account", help="Account id or name")
    account_login.add_argument("--email", required=True)
    account_login.add_argument("--otp", default=None)
    account_login.add_argument("--no-close-browser", action="store_true")

    session = subparsers.add_parser("session", help="Manage Claude sessions")
    session_sub = session.add_subparsers(dest="session_command")
    session_list = session_sub.add_parser("list", help="List sessions")
    session_list.add_argument("account", nargs="?", help="Optional account id or name")
    session_list.add_argument("--json", action="store_true")
    session_add = session_sub.add_parser("add", help="Add session")
    session_add.add_argument("title")
    session_add.add_argument("--account", required=True)
    session_add.add_argument("--remote-url", required=True)
    session_add.add_argument("--watch", action="store_true")
    session_enable = session_sub.add_parser("enable", help="Select session for watching")
    session_enable.add_argument("session", help="Session id, key, or title")
    session_disable = session_sub.add_parser("disable", help="Unselect session")
    session_disable.add_argument("session", help="Session id, key, or title")
    session_discover = session_sub.add_parser("discover", help="Discover sessions for an account")
    session_discover.add_argument("account", help="Account id or name")
    session_probe = session_sub.add_parser("probe", help="Probe claude.ai HTTP capabilities")
    session_probe.add_argument("account", help="Account id or name")
    _add_probe_args(session_probe)

    watcher = subparsers.add_parser("watcher", help="Watcher controls")
    watcher_sub = watcher.add_subparsers(dest="watcher_command")
    watcher_sub.add_parser("start", help="Start local watcher daemon")
    watcher_sub.add_parser("stop", help="Stop local watcher daemon")
    watcher_sub.add_parser("restart", help="Restart local watcher daemon")
    watcher_sub.add_parser("status", help="Show local watcher daemon status")
    watcher_sub.add_parser("run", help="Run watcher loop in foreground")
    watcher_check = watcher_sub.add_parser("check", help="Run one watcher check")
    watcher_check.add_argument(
        "account",
        nargs="?",
        help="Account watcher id, account id, or account",
    )
    watcher_check.add_argument("--all", action="store_true")
    watcher_logs = watcher_sub.add_parser("logs", help="Show watcher logs")
    watcher_logs.add_argument("account", nargs="?")
    watcher_logs.add_argument("--limit", type=int, default=30)
    watcher_history = watcher_sub.add_parser("history", help="Show watcher usage history")
    watcher_history.add_argument("account", nargs="?")
    watcher_history.add_argument("--limit", type=int, default=20)
    watcher_history.add_argument("--json", action="store_true")
    watcher_doctor = watcher_sub.add_parser("doctor", help="Run basic environment checks")
    watcher_doctor.add_argument("--account", help="Check login cookies for an account id or name")
    watcher_sub.add_parser("notify-test", help="Send a test notification if configured")

    config = subparsers.add_parser("config", help="Show or change CLI config")
    config_sub = config.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Show effective config")
    config_set = config_sub.add_parser("set", help="Persist config key in .env")
    config_set.add_argument("key", help="Config key (e.g. host, port, check-jitter)")
    config_set.add_argument("value", help="Config value")

    native = subparsers.add_parser("native", help="Native app and OS service controls")
    native_sub = native.add_subparsers(dest="native_command")
    native_launch = native_sub.add_parser("launch", help="Launch native desktop app (PySide6)")
    native_launch.add_argument(
        "--show-window",
        action="store_true",
        help="Force opening the native window (ignore tray-first behavior)",
    )
    native_sub.add_parser("backend", help="Show background backend for this OS")
    native_sub.add_parser("status", help="Show OS background service status")
    native_sub.add_parser("open", help="Open/focus native app window")
    native_sub.add_parser("quit", help="Quit native app agent")
    native_sub.add_parser("mode", help="Show desktop mode")
    native_mode_set = native_sub.add_parser("mode-set", help="Set desktop mode")
    native_mode_set.add_argument("value", choices=[MODE_TEMPORARY, MODE_INSTALLED])
    native_autostart = native_sub.add_parser("autostart", help="Desktop autostart control")
    native_autostart.add_argument("value", choices=["on", "off", "status"])
    native_sub.add_parser("service-install", help="Install OS background service")
    native_sub.add_parser("service-uninstall", help="Uninstall OS background service")
    native_sub.add_parser("service-start", help="Start OS background service")
    native_sub.add_parser("service-stop", help="Stop OS background service")
    native_sub.add_parser("service-restart", help="Restart OS background service")

    status = subparsers.add_parser(
        "status",
        help=argparse.SUPPRESS,
        description="Legacy command: show account watcher status.",
    )
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    subparsers.add_parser(
        "list",
        help=argparse.SUPPRESS,
        description="Legacy command: alias for status.",
    )
    subparsers.add_parser(
        "watchers",
        help=argparse.SUPPRESS,
        description="Legacy command: alias for status.",
    )

    watch = subparsers.add_parser(
        "watch",
        help=argparse.SUPPRESS,
        description="Legacy command: continuously show account watcher status.",
    )
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--json", action="store_true")

    check = subparsers.add_parser(
        "check",
        help=argparse.SUPPRESS,
        description="Legacy command: run one account check.",
    )
    check.add_argument("account", nargs="?", help="Account watcher id, account id, or account name")
    check.add_argument("--all", action="store_true", help="Check all enabled account watchers")

    logs = subparsers.add_parser(
        "logs",
        help=argparse.SUPPRESS,
        description="Legacy command: show recent account watcher events.",
    )
    logs.add_argument("account", nargs="?", help="Optional account watcher id, account id, or name")
    logs.add_argument("--limit", type=int, default=30)

    history = subparsers.add_parser(
        "history",
        help=argparse.SUPPRESS,
        description="Legacy command: show recent usage history.",
    )
    history.add_argument(
        "account",
        nargs="?",
        help="Optional account watcher id, account id, or name",
    )
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true")

    accounts = subparsers.add_parser(
        "accounts",
        help=argparse.SUPPRESS,
        description="Legacy command: list configured accounts.",
    )
    accounts.add_argument("--json", action="store_true")

    account_create = subparsers.add_parser(
        "account-create",
        help=argparse.SUPPRESS,
        description="Legacy command: create an account.",
    )
    account_create.add_argument("name", help="Account name")
    account_create.add_argument(
        "--profile-dir",
        default=None,
        help="Optional explicit profile directory path",
    )
    _add_watcher_fields(account_create)

    account_delete = subparsers.add_parser(
        "account-delete",
        help=argparse.SUPPRESS,
        description="Legacy command: delete an account.",
    )
    account_delete.add_argument("account", help="Account id or name")
    account_delete.add_argument(
        "--purge-profile",
        action="store_true",
        help="Also delete the account browser profile directory",
    )

    account_login = subparsers.add_parser(
        "account-login",
        help=argparse.SUPPRESS,
        description="Legacy command: headless email OTP login.",
    )
    account_login.add_argument("account", help="Account id or name")
    account_login.add_argument("--email", required=True, help="Claude account email")
    account_login.add_argument("--otp", default=None, help="Optional 6-digit OTP code")
    account_login.add_argument(
        "--no-close-browser",
        action="store_true",
        help="Keep browser context open after command (debug only)",
    )

    add = subparsers.add_parser(
        "add",
        help=argparse.SUPPRESS,
        description="Legacy command: add and select a remote session.",
    )
    add.add_argument("name", help="Session title")
    add.add_argument("--account", required=True, help="Account id or name")
    add.add_argument("--remote-url", required=True)
    add.add_argument("--no-watch", action="store_true", help="Add the session without selecting it")
    _add_watcher_fields(add)

    edit = subparsers.add_parser(
        "edit",
        help=argparse.SUPPRESS,
        description="Legacy command: edit an account watcher.",
    )
    edit.add_argument("account", help="Account watcher id, account id, or account name")
    _add_watcher_fields(edit)

    enable = subparsers.add_parser(
        "enable",
        help=argparse.SUPPRESS,
        description="Legacy command: enable an account watcher.",
    )
    enable.add_argument("account", help="Account watcher id, account id, or account name")
    disable = subparsers.add_parser(
        "disable",
        help=argparse.SUPPRESS,
        description="Legacy command: disable an account watcher.",
    )
    disable.add_argument("account", help="Account watcher id, account id, or account name")

    sessions = subparsers.add_parser(
        "sessions",
        help=argparse.SUPPRESS,
        description="Legacy command: list Claude sessions.",
    )
    sessions.add_argument("account", nargs="?", help="Optional account id or name")
    sessions.add_argument("--json", action="store_true")

    discover = subparsers.add_parser(
        "discover",
        help=argparse.SUPPRESS,
        description="Legacy command: discover sessions for an account.",
    )
    discover.add_argument("account", help="Account id or name")

    probe = subparsers.add_parser(
        "probe",
        help=argparse.SUPPRESS,
        description="Legacy command: probe claude.ai HTTP capabilities for an account.",
    )
    probe.add_argument("account", help="Account id or name")
    _add_probe_args(probe)

    session_add = subparsers.add_parser(
        "session-add",
        help=argparse.SUPPRESS,
        description="Legacy command: add a remote-control session.",
    )
    session_add.add_argument("title")
    session_add.add_argument("--account", required=True, help="Account id or name")
    session_add.add_argument("--remote-url", required=True)
    session_add.add_argument("--watch", action="store_true", help="Select the session immediately")

    session_enable = subparsers.add_parser(
        "session-enable",
        help=argparse.SUPPRESS,
        description="Legacy command: select a session for watching.",
    )
    session_enable.add_argument("session", help="Session id, key, or title")
    session_disable = subparsers.add_parser(
        "session-disable",
        help=argparse.SUPPRESS,
        description="Legacy command: unselect a session.",
    )
    session_disable.add_argument("session", help="Session id, key, or title")

    subparsers.add_parser(
        "start",
        help=argparse.SUPPRESS,
        description="Legacy command: start local CLI watcher daemon.",
    )
    subparsers.add_parser(
        "stop",
        help=argparse.SUPPRESS,
        description="Legacy command: stop local CLI watcher daemon.",
    )
    subparsers.add_parser(
        "restart",
        help=argparse.SUPPRESS,
        description="Legacy command: restart local CLI watcher daemon.",
    )
    subparsers.add_parser(
        "service-status",
        help=argparse.SUPPRESS,
        description="Legacy command: show local CLI watcher daemon status.",
    )
    doctor = subparsers.add_parser(
        "doctor",
        help=argparse.SUPPRESS,
        description="Legacy command: run basic environment checks.",
    )
    doctor.add_argument("--account", help="Check login cookies for an account id or name")
    subparsers.add_parser(
        "notify-test",
        help=argparse.SUPPRESS,
        description="Legacy command: send a test notification.",
    )
    # Hide deprecated/legacy aliases from the command help table while keeping
    # them callable for compatibility.
    subparsers._choices_actions = [  # type: ignore[attr-defined]
        action for action in subparsers._choices_actions if action.help != argparse.SUPPRESS
    ]
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


def _add_probe_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--session",
        dest="session_id",
        help="Optional session id (session_...) to probe events against",
    )
    parser.add_argument(
        "--send-message",
        dest="send_message",
        help="Send a test user message via POST /v1/sessions/{id}/events (requires --session)",
    )
    parser.add_argument(
        "--no-oauth",
        action="store_true",
        help="Skip local Claude Code OAuth usage probe",
    )
    parser.add_argument(
        "--oauth-credentials",
        dest="oauth_credentials",
        help="Optional path to .claude/.credentials.json for OAuth usage probe",
    )


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


def _account_command(args, settings: Settings) -> int:
    command = getattr(args, "account_command", None)
    if command == "list":
        return _accounts(args, settings)
    if command == "add":
        return _account_create(args, settings)
    if command == "remove":
        return asyncio.run(_account_delete(args, settings))
    if command == "login":
        return asyncio.run(_account_login(args, settings))
    raise SystemExit("Use one of: account list|add|remove|login")


def _session_command(args, settings: Settings) -> int:
    command = getattr(args, "session_command", None)
    if command == "list":
        return _sessions(args, settings)
    if command == "add":
        return _session_add(args, settings)
    if command == "enable":
        return _set_session_enabled(args, settings, True)
    if command == "disable":
        return _set_session_enabled(args, settings, False)
    if command == "discover":
        return asyncio.run(_discover(args, settings))
    if command == "probe":
        return asyncio.run(_probe(args, settings))
    raise SystemExit("Use one of: session list|add|enable|disable|discover|probe")


def _watcher_command(args, settings: Settings) -> int:
    command = getattr(args, "watcher_command", None)
    if command == "start":
        return _print_service_status(start_service(settings), ok_when_stopped=False)
    if command == "stop":
        return _print_service_status(stop_service(settings), ok_when_stopped=True)
    if command == "restart":
        stop_service(settings)
        return _print_service_status(start_service(settings), ok_when_stopped=False)
    if command == "status":
        return _print_service_status(service_status(settings), ok_when_stopped=True)
    if command == "run":
        try:
            return asyncio.run(_run(settings))
        except KeyboardInterrupt:
            return 0
    if command == "check":
        return asyncio.run(_check(args, settings))
    if command == "logs":
        return _logs(args, settings)
    if command == "history":
        return _history(args, settings)
    if command == "doctor":
        return _doctor(args, settings)
    if command == "notify-test":
        return asyncio.run(_notify_test(settings))
    raise SystemExit(
        "Use one of: watcher start|stop|restart|status|run|check|logs|history|doctor|notify-test"
    )


def _config_command(args, settings: Settings) -> int:
    command = getattr(args, "config_command", None)
    if command == "show":
        rows = [
            ("data_dir", str(settings.data_dir)),
            ("db_path", str(settings.db_path)),
            ("profiles_dir", str(settings.profiles_dir)),
            ("host", settings.host),
            ("port", str(settings.port)),
            ("browser_keepalive", str(settings.browser_keepalive).lower()),
            ("check_jitter_seconds", str(settings.check_jitter_seconds)),
            ("resume_safety_margin_seconds", str(settings.resume_safety_margin_seconds)),
        ]
        _print_table(["Key", "Value"], [[k, v] for k, v in rows])
        return 0
    if command == "set":
        return _config_set(args.key, args.value)
    raise SystemExit("Use one of: config show|set")


def _native_command(args, settings: Settings) -> int:
    command = getattr(args, "native_command", None)
    try:
        if command == "launch":
            show_window = bool(getattr(args, "show_window", False))
            running, _pid = agent_running(settings)
            if running:
                send_agent_command(settings, "show" if show_window else "show")
                print("Native app agent already running. Open request sent.")
                return 0
            if os.name == "nt" and os.getenv("CSW_NATIVE_CHILD") != "1":
                start_agent_process(show_window=show_window)
                return 0
            if show_window:
                os.environ["CSW_NATIVE_SHOW_WINDOW"] = "1"
            from .native_app import run_native_app

            return run_native_app(settings)
        if command == "backend":
            print(background_backend())
            return 0
        if command == "open":
            running, _pid = agent_running(settings)
            if not running:
                start_agent_process(show_window=True)
                print("Native app agent started.")
                return 0
            send_agent_command(settings, "show")
            print("Open request sent to native app agent.")
            return 0
        if command == "quit":
            stopped = request_agent_quit(settings)
            print("Native app agent stopped." if stopped else "Native app agent is not running.")
            return 0
        if command == "mode":
            state = load_mode_state(settings)
            print(f"Mode: {state.mode}")
            print(f"Autostart: {'on' if state.autostart else 'off'}")
            return 0
        if command == "mode-set":
            state = set_mode(settings, getattr(args, "value", MODE_TEMPORARY))
            if state.mode == MODE_TEMPORARY and state.autostart:
                state = set_autostart(settings, False)
            print(f"Mode set to: {state.mode}")
            print(f"Autostart: {'on' if state.autostart else 'off'}")
            return 0
        if command == "autostart":
            action = str(getattr(args, "value", "status"))
            if action == "status":
                state = load_mode_state(settings)
                task = desktop_task_status(settings)
                print(f"Autostart: {'on' if state.autostart else 'off'}")
                print(f"Autostart entry installed: {'yes' if task.installed else 'no'}")
                print(f"Agent running: {'yes' if task.running else 'no'}")
                return 0
            if action == "on":
                state = set_autostart(settings, True)
                print(f"Autostart: {'on' if state.autostart else 'off'}")
                return 0
            state = set_autostart(settings, False)
            print(f"Autostart: {'on' if state.autostart else 'off'}")
            return 0
        if command == "status":
            _print_native_runtime_status(settings)
            return _print_background_service_status(
                background_service_status(settings),
                ok_when_stopped=True,
            )
        if command == "service-install":
            return _print_background_service_status(
                install_background_service(settings),
                ok_when_stopped=False,
            )
        if command == "service-uninstall":
            return _print_background_service_status(
                uninstall_background_service(settings), ok_when_stopped=True
            )
        if command == "service-start":
            return _print_background_service_status(
                start_background_service(settings),
                ok_when_stopped=False,
            )
        if command == "service-stop":
            return _print_background_service_status(
                stop_background_service(settings),
                ok_when_stopped=True,
            )
        if command == "service-restart":
            return _print_background_service_status(
                restart_background_service(settings), ok_when_stopped=False
            )
        raise SystemExit(
            "Use one of: native launch|open|quit|mode|mode-set|autostart|backend|status|"
            "service-install|service-uninstall|service-start|service-stop|service-restart"
        )
    except BackgroundServiceError as exc:
        print(f"Native service error: {exc}")
        return 1
    except DesktopRuntimeError as exc:
        print(f"Desktop runtime error: {exc}")
        return 1


def _config_set(key: str, value: str) -> int:
    env_key_map = {
        "host": "CSW_HOST",
        "port": "CSW_PORT",
        "browser-keepalive": "CSW_BROWSER_KEEPALIVE",
        "check-jitter": "CSW_CHECK_JITTER_SECONDS",
        "resume-margin": "CSW_RESUME_SAFETY_MARGIN_SECONDS",
        "camoufox-headless": "CSW_CAMOUFOX_HEADLESS",
    }
    normalized = key.strip().lower()
    env_key = env_key_map.get(normalized)
    if not env_key:
        choices = ", ".join(sorted(env_key_map))
        raise SystemExit(f"Unknown config key '{key}'. Use one of: {choices}")
    env_path = Path.cwd() / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{env_key}="):
            lines[idx] = f"{env_key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{env_key}={value}")
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Saved {env_key}={value} to {env_path}")
    return 0


def _dashboard(args, settings: Settings) -> int:
    interval = max(1, int(getattr(args, "interval", 3) or 3))
    if getattr(args, "once", False):
        return _print_dashboard_once(settings, json_output=getattr(args, "json", False))
    try:
        while True:
            _clear_terminal()
            _print_dashboard_once(settings, json_output=getattr(args, "json", False))
            if getattr(args, "json", False):
                print()
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _clear_terminal() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _print_dashboard_once(settings: Settings, *, json_output: bool) -> int:
    store = _store(settings)
    status = service_status(settings)
    accounts = store.list_accounts()
    rows = _status_rows(settings)
    sessions = store.list_sessions()
    events = store.list_account_events(limit=8)
    if json_output:
        payload = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "daemon_running": status.running,
            "daemon_pid": status.pid,
            "accounts": [_account_row(account) for account in accounts],
            "watchers": rows,
            "sessions_total": len(sessions),
            "events": [
                {
                    "time": event.created_at,
                    "watcher_id": event.account_watcher_id,
                    "level": event.level,
                    "message": event.message,
                }
                for event in events
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    header = _render_box(
        "Claude Session Watcher",
        [
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Daemon: {'RUNNING' if status.running else 'STOPPED'}"
            + (f" (PID {status.pid})" if status.pid else ""),
            f"Accounts: {len(accounts)}  Watchers: {len(rows)}  Sessions: {len(sessions)}",
        ],
    )
    account_lines = [
        f"#{row['id']} {row['name']} [{row['status']}]"
        for row in [_account_row(account) for account in accounts]
    ] or ["No accounts configured."]
    watchers_lines = [
        (
            f"#{row['id']} {row['account']}: {str(row['status']).upper()} "
            f"(5h {row['five_hour'] if row['five_hour'] is not None else '-'}%, "
            f"7d {row['seven_day'] if row['seven_day'] is not None else '-'}%) "
            f"sessions {row['sessions_watched']}/{row['sessions_total']}"
        )
        for row in rows
    ] or ["No account watchers configured."]
    events_lines = [
        f"{format_timestamp(event.created_at)} [{str(event.level).upper()}] {event.message}"
        for event in events
    ] or ["No recent events."]
    print(header)
    print(_render_box("Accounts", account_lines))
    print(_render_box("Watchers", watchers_lines))
    print(_render_box("Recent Events", events_lines))
    print("Hint: `csw account list`, `csw session list`, `csw watcher check --all`")
    return 0


def _render_box(title: str, lines: list[str], *, max_width: int = 110) -> str:
    content = [line if len(line) <= max_width else line[: max_width - 3] + "..." for line in lines]
    width = max([len(title), *(len(line) for line in content)], default=len(title))
    border = "+" + "-" * (width + 2) + "+"
    body = [f"| {line.ljust(width)} |" for line in content]
    return "\n".join([border, f"| {title.ljust(width)} |", border, *body, border])


async def _run(settings: Settings) -> int:
    store = _store(settings)
    browser = CamoufoxManager(
        headless=settings.camoufox_headless,
        os_name=settings.camoufox_os,
    )
    service = WatcherService(store, browser, settings)
    watchers = store.list_account_watchers(enabled_only=True)
    print(
        f"Watcher loop started (enabled account watchers: {len(watchers)}). "
        "Press Ctrl+C to stop."
    )
    service.start()
    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await service.stop()
        await browser.close()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "account"


def _account_row(account: Account) -> dict[str, object]:
    return {
        "id": account.id,
        "name": account.name,
        "status": account.status,
        "last_error": account.last_error,
        "profile_dir": account.profile_dir,
    }


def _accounts(args, settings: Settings) -> int:
    store = _store(settings)
    accounts = store.list_accounts()
    rows = [_account_row(account) for account in accounts]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No accounts configured.")
        return 0
    headers = ["ID", "Account", "Status", "Profile dir"]
    table = [
        [
            row["id"],
            row["name"],
            row["status"],
            row["profile_dir"],
        ]
        for row in rows
    ]
    _print_table(headers, table)
    return 0


def _account_create(args, settings: Settings) -> int:
    store = _store(settings)
    name = args.name.strip()
    if not name:
        raise SystemExit("Account name must not be empty.")
    profile_dir = (
        Path(args.profile_dir).expanduser()
        if args.profile_dir
        else settings.profiles_dir / _slug(name)
    )
    try:
        account = store.create_account(name=name, profile_dir=str(profile_dir))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Could not create account '{name}': {exc}") from exc
    if account.id is None:
        raise SystemExit("Account creation failed.")
    watcher = store.ensure_account_watcher(account.id)
    watcher = _apply_account_watcher_args(store, watcher, args)
    print(f"Created account #{account.id}: {account.name}")
    print(f"Profile dir: {account.profile_dir}")
    print(f"Watcher #{watcher.id}: enabled={'yes' if watcher.enabled else 'no'}")
    return 0


async def _account_delete(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    if account.id is None:
        raise SystemExit("Account has not been stored yet.")
    profile_dir = Path(account.profile_dir)
    browser = CamoufoxManager(
        headless=settings.camoufox_headless,
        os_name=settings.camoufox_os,
    )
    try:
        try:
            await browser.close_profile(profile_dir)
        except Exception:
            pass
    finally:
        await browser.close()
    store.delete_account(account.id)
    if args.purge_profile and profile_dir.exists() and profile_dir.is_dir():
        shutil.rmtree(profile_dir, ignore_errors=True)
    print(f"Deleted account #{account.id}: {account.name}")
    if args.purge_profile:
        print(f"Profile dir removed: {profile_dir}")
    return 0


async def _account_login(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    if account.id is None:
        raise SystemExit("Account has not been stored yet.")
    watcher = store.ensure_account_watcher(account.id)
    profile_dir = Path(account.profile_dir)
    browser = CamoufoxManager(
        headless=True,
        os_name=settings.camoufox_os,
    )
    state = "unknown"
    otp_submitted = False
    try:
        result = await browser.start_email_login(profile_dir, args.email.strip())
        if not result.get("ok"):
            failed_state = str(result.get("state") or "unknown")
            if failed_state == "logged_in":
                state = "logged_in"
            else:
                state = "unknown"
            if failed_state == "new_account_setup":
                reason = (
                    "New Claude accounts are currently blocked in CLI mode. "
                    "Onboarding-only accounts are not supported."
                )
                store.update_account_status(account.id, "new-account-blocked", reason)
                store.add_account_event(watcher.id, "warning", reason)
                print(reason)
                return 1
            if failed_state == "logged_in":
                # Some flows return an email-field error while already authenticated.
                # Continue with portal validation and optional Pro switch.
                pass
            else:
                reason = str(result.get("reason") or "login_start_failed")
                store.update_account_status(account.id, "login-incomplete", reason)
                store.add_account_event(watcher.id, "warning", f"CLI login start failed: {reason}")
                print(f"Login start failed: {reason}")
                return 1
        else:
            state = str(result.get("state") or "unknown")
        if state == "code_form":
            otp = (args.otp or input("OTP code: ")).strip()
            if not otp:
                print("Missing OTP code.")
                return 1
            otp_result = await browser.submit_otp(profile_dir, otp)
            otp_submitted = True
            if not otp_result.get("ok"):
                reason = str(otp_result.get("reason") or "otp_submit_failed")
                state = str(otp_result.get("state") or "unknown")
                store.update_account_status(account.id, "login-incomplete", reason)
                store.add_account_event(watcher.id, "warning", f"CLI OTP failed: {reason}")
                print(f"OTP submit failed ({state}): {reason}")
                return 1
            state = str(otp_result.get("state") or "unknown")
        if state != "logged_in":
            state = await _stabilize_login_state(browser, profile_dir, state)
        if state == "logged_in":
            try:
                await browser.session_key(profile_dir)
            except Exception as exc:  # noqa: BLE001
                reason = f"sessionKey validation failed: {exc}"
                store.update_account_status(account.id, "login-incomplete", reason)
                store.add_account_event(watcher.id, "warning", reason)
                print(reason)
                return 1
            # Match the web flow: verify Claude Code portal access and attempt
            # an automatic switch to the Pro-capable profile when needed.
            try:
                portal = await browser.code_portal_status(profile_dir)
            except Exception as exc:  # noqa: BLE001
                reason = f"Could not verify Claude Code portal state: {exc}"
                store.update_account_status(account.id, "login-incomplete", reason)
                store.add_account_event(watcher.id, "warning", reason)
                print(reason)
                return 1
            if portal.get("disabled"):
                store.add_account_event(
                    watcher.id,
                    "info",
                    "Claude Code disabled. Attempting automatic profile switch to Pro plan...",
                )
                try:
                    switch_result = await browser.ensure_pro_plan(profile_dir)
                except Exception as exc:  # noqa: BLE001
                    switch_result = {"ok": False, "reason": str(exc)}
                if switch_result.get("method"):
                    store.add_account_event(
                        watcher.id,
                        "info",
                        f"Pro switch method: {switch_result.get('method')}",
                    )
                if switch_result.get("reason"):
                    store.add_account_event(
                        watcher.id,
                        "info" if switch_result.get("ok") else "warning",
                        f"Pro switch detail: {switch_result.get('reason')}",
                    )
                try:
                    portal = await browser.code_portal_status(profile_dir)
                except Exception as exc:  # noqa: BLE001
                    reason = f"Could not re-check Claude Code portal state: {exc}"
                    store.update_account_status(account.id, "login-incomplete", reason)
                    store.add_account_event(watcher.id, "warning", reason)
                    print(reason)
                    return 1
                if portal.get("disabled"):
                    reason = str(
                        portal.get("message") or "Claude Code is disabled for this account/org."
                    )
                    store.update_account_status(account.id, "code-disabled", reason)
                    store.add_account_event(watcher.id, "warning", reason)
                    print(reason)
                    return 1
            store.update_account_status(account.id, "logged-in")
            store.add_account_event(watcher.id, "info", "CLI login finished")
            print(f"Login successful for account '{account.name}'.")
            return 0
        if state == "new_account_setup":
            reason = (
                "Claude account requires onboarding in web flow before automation can continue."
            )
            store.update_account_status(account.id, "login-incomplete", reason)
            store.add_account_event(watcher.id, "warning", reason)
            print(reason)
            return 1
        if otp_submitted and state == "email_form":
            reason = (
                "Login returned to email step after OTP. "
                "The OTP was likely invalid/expired; request a new code and retry."
            )
            store.update_account_status(account.id, "login-incomplete", reason)
            store.add_account_event(watcher.id, "warning", reason)
            print(reason)
            return 1
        if otp_submitted and state == "code_form":
            reason = (
                "OTP was not accepted. The code is likely invalid or expired; "
                "request a new code and retry."
            )
            store.update_account_status(account.id, "login-incomplete", reason)
            store.add_account_event(watcher.id, "warning", reason)
            print(reason)
            return 1
        reason = f"Unexpected login state: {state}"
        store.update_account_status(account.id, "login-incomplete", reason)
        store.add_account_event(watcher.id, "warning", reason)
        print(reason)
        return 1
    except BrowserError as exc:
        message = str(exc)
        store.update_account_status(account.id, "login-incomplete", message)
        store.add_account_event(watcher.id, "warning", f"CLI login failed: {message}")
        print(f"Login failed: {message}")
        print(
            "Hint: install full dependencies in this environment: "
            "python -m pip install -e \".[full]\" && camoufox fetch"
        )
        return 1
    finally:
        if not getattr(args, "no_close_browser", False):
            try:
                await browser.close_profile(profile_dir)
            except Exception:
                pass
        await browser.close()


async def _stabilize_login_state(
    browser: CamoufoxManager,
    profile_dir: Path,
    initial_state: str,
    *,
    timeout_seconds: int = 20,
) -> str:
    state = initial_state
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            await browser.session_key(profile_dir)
            return "logged_in"
        except Exception:
            pass
        try:
            state_payload = await browser.get_login_page_state(profile_dir)
            next_state = str(state_payload.get("state") or "unknown")
            if next_state != "unknown":
                state = next_state
            if state == "logged_in":
                return state
        except Exception:
            pass
        await asyncio.sleep(0.8)
    return state


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
    had_error = False
    try:
        if args.all:
            watchers = store.list_account_watchers(enabled_only=True)
        else:
            watchers = [_resolve_account_watcher(store, args.account)]
        for watcher in watchers:
            assert watcher.id is not None
            account = store.get_account(watcher.account_id)
            try:
                result = await service.check_account_now(watcher.id)
            except Exception as exc:  # noqa: BLE001
                had_error = True
                message = f"CLI check failed: {exc}"
                store.add_account_event(watcher.id, "error", message)
                print(f"{account.name}: failed ({exc})")
                continue
            store.add_account_event(watcher.id, "info", f"CLI check: {result}")
            print(f"{account.name}: {result}")
    finally:
        await browser.close()
    return 1 if had_error else 0


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
    print(
        f"{account.name}: discovered {result.found}, "
        f"updated {result.updated}, selected {result.selected}"
    )
    return 0


async def _probe(args, settings: Settings) -> int:
    store = _store(settings)
    account = _resolve_account(store, args.account)
    results = await probe_account(
        Path(account.profile_dir),
        session_id=getattr(args, "session_id", None),
        send_message=getattr(args, "send_message", None),
        include_oauth=not bool(getattr(args, "no_oauth", False)),
        oauth_credentials_path=(
            Path(args.oauth_credentials) if getattr(args, "oauth_credentials", None) else None
        ),
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


def _print_background_service_status(status, *, ok_when_stopped: bool) -> int:
    print(f"Backend: {status.backend}")
    print(f"Installed: {'yes' if status.installed else 'no'}")
    print(f"Running: {'yes' if status.running else 'no'}")
    if status.detail:
        print(f"Detail: {status.detail}")
    return 0 if status.running or ok_when_stopped else 1


def _print_native_runtime_status(settings: Settings) -> None:
    state = load_mode_state(settings)
    running, pid = agent_running(settings)
    task = desktop_task_status(settings)
    print(f"Desktop mode: {state.mode}")
    print(f"Desktop autostart: {'on' if state.autostart else 'off'}")
    print(
        f"Desktop agent: {'running' if running else 'stopped'}"
        + (f" (PID {pid})" if pid else "")
    )
    print(f"Desktop autostart entry: {'installed' if task.installed else 'missing'}")
    print(f"Desktop autostart agent state: {'running' if task.running else 'stopped'}")


def _doctor(args, settings: Settings) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("runtime", True, f"{sys.platform} / {platform.machine()}"))
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
        try:
            from camoufox.pkgman import OS_NAME, CamoufoxFetcher

            arch = CamoufoxFetcher.get_platform_arch()
            checks.append(("camoufox target", True, f"{OS_NAME}.{arch}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(("camoufox target", False, str(exc)))
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
