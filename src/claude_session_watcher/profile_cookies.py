from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from .usage import ClaudeCookie, UsageLoginRequiredError


def load_claude_cookies(profile_dir: Path) -> list[ClaudeCookie]:
    cookie_db = profile_dir / "cookies.sqlite"
    if not cookie_db.exists():
        raise UsageLoginRequiredError(f"No Firefox cookie store found at {cookie_db}")

    with tempfile.TemporaryDirectory(prefix="csw-cookies-") as tmp:
        tmp_path = Path(tmp)
        copied_db = tmp_path / "cookies.sqlite"
        shutil.copy2(cookie_db, copied_db)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(cookie_db) + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(copied_db) + suffix))

        conn = sqlite3.connect(copied_db)
        try:
            rows = conn.execute(
                """
                SELECT host, name, value, path, expiry
                FROM moz_cookies
                WHERE value IS NOT NULL AND value != ''
                """
            ).fetchall()
        finally:
            conn.close()

    now = int(time.time())
    cookies: list[ClaudeCookie] = []
    for host, name, value, path, expiry in rows:
        host = str(host or "")
        normalized_host = host.lstrip(".")
        if normalized_host != "claude.ai" and not normalized_host.endswith(".claude.ai"):
            continue
        if expiry and int(expiry) <= now:
            continue
        cookies.append(
            ClaudeCookie(
                name=str(name),
                value=str(value),
                domain=host or "claude.ai",
                path=str(path or "/"),
            )
        )

    if not cookies:
        raise UsageLoginRequiredError("No usable claude.ai cookies found in the browser profile")
    return cookies


def has_session_key(profile_dir: Path) -> bool:
    try:
        return any(
            cookie.name == "sessionKey" and cookie.value
            for cookie in load_claude_cookies(profile_dir)
        )
    except UsageLoginRequiredError:
        return False
