from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    running: bool
    pid: int | None
    pid_path: Path


def read_pid(settings: Settings) -> int | None:
    try:
        return int(settings.pid_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _run_windows_hidden(command: list[str]) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, **kwargs)


def process_running(pid: int | None) -> bool:
    if pid is None:
        return False
    if os.name == "nt":
        result = _run_windows_hidden(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def service_status(settings: Settings) -> ServiceStatus:
    pid = read_pid(settings)
    return ServiceStatus(
        running=process_running(pid),
        pid=pid,
        pid_path=settings.pid_path,
    )


def start_service(settings: Settings) -> ServiceStatus:
    settings.ensure_dirs()
    current = service_status(settings)
    if current.running:
        return current

    env = os.environ.copy()
    env["CSW_DATA_DIR"] = str(settings.data_dir)

    stdout_path = settings.logs_dir / "windows-service.out.log"
    stderr_path = settings.logs_dir / "windows-service.err.log"
    command = [
        sys.executable,
        "-m",
        "claude_session_watcher.cli",
        "run",
    ]

    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        start_new_session = True

    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=Path.cwd(),
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
    settings.pid_path.write_text(str(process.pid), encoding="utf-8")
    time.sleep(1)
    return service_status(settings)


def stop_service(settings: Settings, *, timeout: float = 10.0) -> ServiceStatus:
    pid = read_pid(settings)
    if not process_running(pid):
        settings.pid_path.unlink(missing_ok=True)
        return service_status(settings)

    assert pid is not None
    if os.name == "nt":
        _run_windows_hidden(["taskkill", "/PID", str(pid), "/T", "/F"])
    else:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_running(pid):
            settings.pid_path.unlink(missing_ok=True)
            break
        time.sleep(0.2)
    return service_status(settings)
