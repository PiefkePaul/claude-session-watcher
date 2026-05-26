from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "claude-session-watcher"


def default_headless() -> str | bool:
    return True if os.name == "nt" else "virtual"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CSW_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 47831
    camoufox_headless: str | bool = Field(default_factory=default_headless)
    camoufox_os: str | None = None
    browser_keepalive: bool = False
    check_jitter_seconds: int = 5
    resume_safety_margin_seconds: int = 120
    ui_token: str | None = None
    local_port_bind_only: bool = False
    browser_console_url: str | None = None
    enable_vnc: bool = False
    vnc_port: int = 6080
    vnc_screen: str = "1920x1080x24"
    vnc_display: str = ":99"
    vnc_web_root: str = "/usr/share/novnc/"
    auto_finish_login: bool = True
    auto_switch_to_pro_plan: bool = True
    notify_ntfy_url: str | None = None
    notify_ntfy_token: str | None = None

    @field_validator("camoufox_headless", mode="before")
    @classmethod
    def parse_headless(cls, value):
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"false", "0", "no", "off"}:
                return False
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered == "virtual":
                return "virtual"
        return value

    @property
    def db_path(self) -> Path:
        return self.data_dir / "watcher.sqlite3"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def pid_path(self) -> Path:
        return self.data_dir / "csw.pid"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def validate_web_security(self) -> None:
        if not self.is_public_bind():
            return
        if self.ui_token or self.local_port_bind_only:
            return
        raise ValueError(
            "Refusing to bind the web UI to a non-local host without CSW_UI_TOKEN. "
            "Set CSW_UI_TOKEN or bind to 127.0.0.1."
        )

    def is_public_bind(self) -> bool:
        host = self.host.strip().lower()
        return host not in {"127.0.0.1", "localhost", "::1"}
