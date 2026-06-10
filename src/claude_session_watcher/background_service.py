from __future__ import annotations

import os
import platform
import plistlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings

SERVICE_NAME = "claude-session-watcher"
MACOS_LABEL = "com.claude-session-watcher.agent"
WINDOWS_TASK_NAME = "ClaudeSessionWatcher"


class BackgroundServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackgroundServiceStatus:
    installed: bool
    running: bool
    backend: str
    detail: str


def background_backend() -> str:
    system = platform.system().lower()
    if system == "linux":
        return "systemd-user"
    if system == "darwin":
        return "launchd-agent"
    if system == "windows":
        return "task-scheduler"
    raise BackgroundServiceError(f"Unsupported OS for background service management: {system}")


def install_background_service(settings: Settings) -> BackgroundServiceStatus:
    settings.ensure_dirs()
    backend = background_backend()
    if backend == "systemd-user":
        _install_systemd_user(settings)
    elif backend == "launchd-agent":
        _install_launchd_agent(settings)
    elif backend == "task-scheduler":
        _install_windows_task(settings)
    return background_service_status(settings)


def uninstall_background_service(settings: Settings) -> BackgroundServiceStatus:
    backend = background_backend()
    if backend == "systemd-user":
        _uninstall_systemd_user()
    elif backend == "launchd-agent":
        _uninstall_launchd_agent()
    elif backend == "task-scheduler":
        _uninstall_windows_task(settings)
    return background_service_status(settings)


def start_background_service(settings: Settings) -> BackgroundServiceStatus:
    backend = background_backend()
    if backend == "systemd-user":
        _run(["systemctl", "--user", "start", _systemd_service_name()])
    elif backend == "launchd-agent":
        plist_path = _launchd_plist_path()
        if not plist_path.exists():
            raise BackgroundServiceError(
                f"LaunchAgent is not installed. Install first: {plist_path}"
            )
        _run(["launchctl", "bootstrap", _launchd_domain(), str(plist_path)], check=False)
        _run(["launchctl", "kickstart", "-k", f"{_launchd_domain()}/{MACOS_LABEL}"])
    elif backend == "task-scheduler":
        _run(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME])
    return background_service_status(settings)


def stop_background_service(settings: Settings) -> BackgroundServiceStatus:
    backend = background_backend()
    if backend == "systemd-user":
        _run(["systemctl", "--user", "stop", _systemd_service_name()], check=False)
    elif backend == "launchd-agent":
        _run(["launchctl", "bootout", _launchd_domain(), MACOS_LABEL], check=False)
    elif backend == "task-scheduler":
        _run(["schtasks", "/End", "/TN", WINDOWS_TASK_NAME], check=False)
    return background_service_status(settings)


def restart_background_service(settings: Settings) -> BackgroundServiceStatus:
    backend = background_backend()
    if backend == "systemd-user":
        _run(["systemctl", "--user", "restart", _systemd_service_name()])
    elif backend == "launchd-agent":
        stop_background_service(settings)
        start_background_service(settings)
    elif backend == "task-scheduler":
        _run(["schtasks", "/End", "/TN", WINDOWS_TASK_NAME], check=False)
        _run(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME], check=False)
    return background_service_status(settings)


def background_service_status(settings: Settings) -> BackgroundServiceStatus:
    backend = background_backend()
    if backend == "systemd-user":
        unit_path = _systemd_unit_path()
        installed = unit_path.exists()
        result = _run(["systemctl", "--user", "is-active", _systemd_service_name()], check=False)
        running = result.returncode == 0 and result.stdout.strip() == "active"
        detail = result.stdout.strip() or result.stderr.strip() or str(unit_path)
        return BackgroundServiceStatus(
            installed=installed,
            running=running,
            backend=backend,
            detail=detail,
        )
    if backend == "launchd-agent":
        plist_path = _launchd_plist_path()
        installed = plist_path.exists()
        target = f"{_launchd_domain()}/{MACOS_LABEL}"
        result = _run(["launchctl", "print", target], check=False)
        output = (result.stdout + "\n" + result.stderr).strip()
        running = "state = running" in output
        detail = output or str(plist_path)
        return BackgroundServiceStatus(
            installed=installed,
            running=running,
            backend=backend,
            detail=detail,
        )
    result = _run(
        ["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME, "/FO", "LIST", "/V"],
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    installed = result.returncode == 0
    low = output.lower()
    running = installed and ("status: running" in low or "status: wird ausgef" in low)
    detail = output or WINDOWS_TASK_NAME
    return BackgroundServiceStatus(
        installed=installed,
        running=running,
        backend=backend,
        detail=detail,
    )


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    run_kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **run_kwargs)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise BackgroundServiceError(f"Command failed ({' '.join(command)}): {detail}")
    return result


def _command_for_run() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "run"]
    return [sys.executable, "-m", "claude_session_watcher.cli", "run"]


def _systemd_service_name() -> str:
    return f"{SERVICE_NAME}.service"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _systemd_service_name()


def _install_systemd_user(settings: Settings) -> None:
    _run(["systemctl", "--user", "--version"])
    command = shlex.join(_command_for_run())
    unit = (
        "[Unit]\n"
        "Description=Claude Session Watcher\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"Environment=CSW_DATA_DIR={shlex.quote(str(settings.data_dir))}\n"
        f"ExecStart={command}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit, encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", _systemd_service_name()])


def _uninstall_systemd_user() -> None:
    _run(["systemctl", "--user", "disable", "--now", _systemd_service_name()], check=False)
    path = _systemd_unit_path()
    path.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"


def _install_launchd_agent(settings: Settings) -> None:
    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    settings.ensure_dirs()
    out_log = settings.logs_dir / "launchd.out.log"
    err_log = settings.logs_dir / "launchd.err.log"
    payload = {
        "Label": MACOS_LABEL,
        "ProgramArguments": _command_for_run(),
        "EnvironmentVariables": {"CSW_DATA_DIR": str(settings.data_dir)},
        "WorkingDirectory": str(Path.cwd()),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)
    _run(["launchctl", "bootout", _launchd_domain(), MACOS_LABEL], check=False)
    _run(["launchctl", "bootstrap", _launchd_domain(), str(plist_path)])
    _run(["launchctl", "kickstart", "-k", f"{_launchd_domain()}/{MACOS_LABEL}"])


def _uninstall_launchd_agent() -> None:
    _run(["launchctl", "bootout", _launchd_domain(), MACOS_LABEL], check=False)
    _launchd_plist_path().unlink(missing_ok=True)


def _windows_task_script_path(settings: Settings) -> Path:
    return settings.data_dir / "windows-task-run.cmd"


def _install_windows_task(settings: Settings) -> None:
    settings.ensure_dirs()
    script_path = _windows_task_script_path(settings)
    out_log = settings.logs_dir / "task-scheduler.out.log"
    err_log = settings.logs_dir / "task-scheduler.err.log"
    command = _command_for_run()
    quoted = subprocess.list2cmdline(command)
    script = (
        "@echo off\r\n"
        f"set CSW_DATA_DIR={settings.data_dir}\r\n"
        f"{quoted} >> \"{out_log}\" 2>> \"{err_log}\"\r\n"
    )
    script_path.write_text(script, encoding="utf-8")
    user_result = _run(["whoami"], check=False)
    current_user = user_result.stdout.strip() or os.getenv("USERNAME", "")
    task_command = f"\"{script_path}\""
    command = [
        "schtasks",
        "/Create",
        "/TN",
        WINDOWS_TASK_NAME,
        "/SC",
        "ONLOGON",
        "/TR",
        task_command,
        "/RL",
        "LIMITED",
        "/F",
    ]
    if current_user:
        command.extend(["/RU", current_user])
    try:
        _run(command)
    except BackgroundServiceError as exc:
        if current_user and "Zugriff verweigert" in str(exc):
            fallback = [part for part in command if part not in {"/RU", current_user}]
            _run(fallback)
            return
        raise


def _uninstall_windows_task(settings: Settings) -> None:
    _run(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"], check=False)
    _windows_task_script_path(settings).unlink(missing_ok=True)
