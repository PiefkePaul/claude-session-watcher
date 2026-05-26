from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
from asyncio.subprocess import DEVNULL, Process
from dataclasses import dataclass
from pathlib import Path


class DisplayError(Exception):
    pass


@dataclass(slots=True)
class DisplayState:
    enabled: bool
    running: bool
    ready: bool
    display: str
    vnc_port: int
    console_url: str | None


class DisplayManager:
    def __init__(
        self,
        *,
        enabled: bool,
        display: str = ":99",
        screen: str = "1920x1080x24",
        vnc_port: int = 6080,
        rfb_port: int = 5900,
        web_root: str = "/usr/share/novnc/",
        console_url: str | None = None,
    ):
        self.enabled = enabled
        self.display = display
        self.screen = screen
        self.vnc_port = vnc_port
        self.rfb_port = rfb_port
        self.web_root = web_root
        self.console_url = console_url
        self._processes: list[Process] = []
        self._lock = asyncio.Lock()

    async def ensure_started(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            if self._is_running_locked():
                # If something crashed, restart the stack.
                if self.is_ready():
                    return
                await self._stop_locked()
            self._prepare_display_socket()
            try:
                await self._start_locked()
            except Exception:
                await self._stop_locked()
                raise

    async def stop(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            await self._stop_locked()

    def state(self) -> DisplayState:
        running = self.is_running()
        return DisplayState(
            enabled=self.enabled,
            running=running,
            ready=running and self.is_ready(),
            display=self.display,
            vnc_port=self.vnc_port,
            console_url=self.console_url if running else None,
        )

    def is_running(self) -> bool:
        return bool(self._processes) and all(
            process.returncode is None for process in self._processes
        )

    def is_ready(self) -> bool:
        if not self.enabled:
            return False
        return self._tcp_ready("127.0.0.1", self.vnc_port)

    def browser_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.enabled:
            env["DISPLAY"] = self.display
        return env

    async def _start_locked(self) -> None:
        self._processes = [
            await self._start(
                "Xvfb",
                self.display,
                "-screen",
                "0",
                self.screen,
                "-nolisten",
                "tcp",
                "-ac",
            ),
        ]
        await self._wait_for_display()
        self._processes.extend(
            [
                await self._start("fluxbox"),
                await self._start(
                    "x11vnc",
                    "-display",
                    self.display,
                    "-forever",
                    "-shared",
                    "-nopw",
                    "-listen",
                    "127.0.0.1",
                    "-rfbport",
                    str(self.rfb_port),
                    "-quiet",
                ),
                await self._start(
                    "websockify",
                    f"--web={self.web_root}",
                    f"0.0.0.0:{self.vnc_port}",
                    f"127.0.0.1:{self.rfb_port}",
                ),
            ]
        )
        await self._wait_for_vnc()

    async def _start(self, *args: str) -> Process:
        return await asyncio.create_subprocess_exec(
            *args,
            stdout=DEVNULL,
            stderr=DEVNULL,
            start_new_session=True,
        )

    async def _stop_locked(self) -> None:
        processes = list(reversed(self._processes))
        self._processes.clear()
        for process in processes:
            if process.returncode is None:
                self._terminate(process)
        if processes:
            await asyncio.gather(*(self._wait_stopped(process) for process in processes))
        # Best-effort cleanup: in rare crash/teardown scenarios, Xvfb/x11vnc/websockify
        # can outlive our tracked Process objects. Kill strays for our configured display/port.
        # Also ensure the VNC port is actually closed before returning.
        for _ in range(3):
            self._force_kill_strays()
            if not self._tcp_ready("127.0.0.1", self.vnc_port):
                break
            await asyncio.sleep(0.2)

    def _is_running_locked(self) -> bool:
        self._processes = [process for process in self._processes if process.returncode is None]
        return bool(self._processes)

    def _terminate(self, process: Process) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            try:
                process.terminate()
            except ProcessLookupError:
                return

        # Some processes might not be in their own process group despite start_new_session
        # depending on platform/runtime; also send the signal to the pid directly.
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            pass

    async def _wait_stopped(self, process: Process) -> None:
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except Exception:
                process.kill()
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except Exception:
                pass
            await process.wait()

    async def _wait_for_display(self) -> None:
        display_number = self.display.removeprefix(":").split(".", 1)[0]
        socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
        for _ in range(100):
            if socket_path.exists():
                return
            await asyncio.sleep(0.1)
        raise DisplayError(f"Timed out waiting for X display {self.display}")

    async def _wait_for_vnc(self) -> None:
        for _ in range(100):
            if self._tcp_ready("127.0.0.1", self.vnc_port):
                return
            await asyncio.sleep(0.1)
        raise DisplayError(f"Timed out waiting for noVNC on port {self.vnc_port}")

    def _prepare_display_socket(self) -> None:
        display_number = self.display.removeprefix(":").split(".", 1)[0]
        lock_file = Path(f"/tmp/.X{display_number}-lock")
        lock_file.unlink(missing_ok=True)
        Path("/tmp/.X11-unix").mkdir(parents=True, exist_ok=True)

    def _force_kill_strays(self) -> None:
        if os.name == "nt":
            return
        display = self.display
        vnc_port = str(self.vnc_port)
        rfb_port = str(self.rfb_port)
        patterns = [
            f"Xvfb {display}",
            f"x11vnc .* -display {display}",
            f"websockify .*:{vnc_port}",
            f"websockify .*:{rfb_port}",
            "fluxbox",
        ]
        for pattern in patterns:
            try:
                subprocess.run(
                    ["pkill", "-f", pattern],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                continue

    @staticmethod
    def _tcp_ready(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            return False
