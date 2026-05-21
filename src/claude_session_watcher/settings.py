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
    check_jitter_seconds: int = 5

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

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
