from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings

MODE_TEMPORARY = "temporary"
MODE_INSTALLED = "installed"
SUPPORTED_MODES = {MODE_TEMPORARY, MODE_INSTALLED}

DESKTOP_STATE_FILENAME = "desktop_mode.json"
AGENT_PID_FILENAME = "native_agent.pid"
AGENT_LOCK_FILENAME = "native_agent.lock"
AGENT_CONTROL_FILENAME = "native_agent.control.json"
AGENT_TASK_NAME = "ClaudeSessionWatcherDesktopAgent"
AUTOSTART_VALUE_NAME = "ClaudeSessionWatcherDesktopAgent"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class DesktopRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DesktopModeState:
    mode: str
    autostart: bool


@dataclass(frozen=True, slots=True)
class DesktopTaskStatus:
    installed: bool
    running: bool
    detail: str


def state_path(settings: Settings) -> Path:
    settings.ensure_dirs()
    return settings.data_dir / DESKTOP_STATE_FILENAME


def agent_pid_path(settings: Settings) -> Path:
    settings.ensure_dirs()
    return settings.data_dir / AGENT_PID_FILENAME


def agent_lock_path(settings: Settings) -> Path:
    settings.ensure_dirs()
    return settings.data_dir / AGENT_LOCK_FILENAME


def agent_control_path(settings: Settings) -> Path:
    settings.ensure_dirs()
    return settings.data_dir / AGENT_CONTROL_FILENAME


def load_mode_state(settings: Settings) -> DesktopModeState:
    path = state_path(settings)
    if not path.exists():
        return DesktopModeState(mode=MODE_TEMPORARY, autostart=False)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return DesktopModeState(mode=MODE_TEMPORARY, autostart=False)
    mode = str(raw.get("mode") or MODE_TEMPORARY).strip().lower()
    if mode not in SUPPORTED_MODES:
        mode = MODE_TEMPORARY
    autostart = bool(raw.get("autostart", False))
    return DesktopModeState(mode=mode, autostart=autostart)


def save_mode_state(settings: Settings, state: DesktopModeState) -> None:
    payload = {"mode": state.mode, "autostart": state.autostart}
    state_path(settings).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def set_mode(settings: Settings, mode: str) -> DesktopModeState:
    normalized = mode.strip().lower()
    if normalized not in SUPPORTED_MODES:
        choices = ", ".join(sorted(SUPPORTED_MODES))
        raise DesktopRuntimeError(f"Unsupported mode '{mode}'. Use one of: {choices}")
    current = load_mode_state(settings)
    updated = DesktopModeState(mode=normalized, autostart=current.autostart)
    save_mode_state(settings, updated)
    return updated


def set_autostart(settings: Settings, enabled: bool) -> DesktopModeState:
    current = load_mode_state(settings)
    if platform.system().lower() == "windows":
        if enabled:
            _enable_windows_run_autostart()
        else:
            _disable_windows_run_autostart()
    updated = DesktopModeState(mode=current.mode, autostart=enabled)
    save_mode_state(settings, updated)
    return updated


def desktop_task_status(settings: Settings | None = None) -> DesktopTaskStatus:
    if platform.system().lower() != "windows":
        return DesktopTaskStatus(installed=False, running=False, detail="not-windows")
    installed, detail = _windows_run_autostart_status()
    pid_running = False
    runtime_settings = settings or Settings()
    running, _pid = agent_running(runtime_settings)
    if running:
        pid_running = True
    return DesktopTaskStatus(installed=installed, running=pid_running, detail=detail)


def read_agent_pid(settings: Settings) -> int | None:
    path = agent_pid_path(settings)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def process_running(pid: int | None) -> bool:
    if pid is None:
        return False
    if os.name == "nt":
        result = _run_windows(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def agent_running(settings: Settings) -> tuple[bool, int | None]:
    pid = read_agent_pid(settings)
    if process_running(pid):
        return True, pid
    return False, None


def write_agent_pid(settings: Settings, pid: int) -> None:
    agent_pid_path(settings).write_text(str(pid), encoding="utf-8")


def clear_agent_pid(settings: Settings) -> None:
    agent_pid_path(settings).unlink(missing_ok=True)


def send_agent_command(settings: Settings, command: str) -> None:
    payload = {"command": command, "ts": time.time()}
    agent_control_path(settings).write_text(json.dumps(payload), encoding="utf-8")


def start_agent_process(*, show_window: bool) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["CSW_NATIVE_CHILD"] = "1"
    if show_window:
        env["CSW_NATIVE_SHOW_WINDOW"] = "1"
    if getattr(sys, "frozen", False):
        command = [sys.executable, "native", "launch"]
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(sys.executable))
        command = [executable, "-m", "claude_session_watcher.cli", "native", "launch"]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess,
            "DETACHED_PROCESS",
            0,
        )
    return subprocess.Popen(
        command,
        cwd=Path.cwd(),
        env=env,
        creationflags=flags,
        start_new_session=True,
    )


def request_agent_quit(settings: Settings) -> bool:
    running, pid = agent_running(settings)
    if not running:
        return False
    send_agent_command(settings, "quit")
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not process_running(pid):
            clear_agent_pid(settings)
            return True
        time.sleep(0.25)
    if pid is not None and os.name == "nt":
        _run_windows(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        clear_agent_pid(settings)
    return True


def _install_windows_agent_task() -> None:
    if os.name != "nt":
        return
    if getattr(sys, "frozen", False):
        task_run = f"\"{sys.executable}\" native launch"
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(sys.executable))
        task_run = f"\"{executable}\" -m claude_session_watcher.cli native launch"
    _enable_windows_run_autostart(task_run)


def _uninstall_windows_agent_task() -> None:
    if os.name != "nt":
        return
    _disable_windows_run_autostart()


def _enable_windows_run_autostart(task_run: str | None = None) -> None:
    if os.name != "nt":
        return
    if task_run is None:
        if getattr(sys, "frozen", False):
            task_run = f"\"{sys.executable}\" native launch"
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            executable = str(pythonw if pythonw.exists() else Path(sys.executable))
            task_run = f"\"{executable}\" -m claude_session_watcher.cli native launch"
    try:
        import winreg

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY)
        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, task_run)
        winreg.CloseKey(key)
    except Exception as exc:  # noqa: BLE001
        raise DesktopRuntimeError(f"Could not enable desktop autostart: {exc}") from exc


def _disable_windows_run_autostart() -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY)
        try:
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except Exception as exc:  # noqa: BLE001
        raise DesktopRuntimeError(f"Could not disable desktop autostart: {exc}") from exc


def _windows_run_autostart_status() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "not-windows"
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY)
        value, _reg_type = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        winreg.CloseKey(key)
        return True, str(value)
    except FileNotFoundError:
        return False, "missing"
    except Exception as exc:  # noqa: BLE001
        return False, f"error: {exc}"


def _run_windows(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **kwargs)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise DesktopRuntimeError(f"Command failed ({' '.join(command)}): {detail}")
    return result
