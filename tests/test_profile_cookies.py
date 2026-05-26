import sqlite3

import pytest

from claude_session_watcher.profile_cookies import has_session_key, load_claude_cookies
from claude_session_watcher.usage import UsageLoginRequiredError


def _create_cookie_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE moz_cookies (
                host TEXT,
                name TEXT,
                value TEXT,
                path TEXT,
                expiry INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?)",
            (".claude.ai", "sessionKey", "secret", "/", 4_000_000_000),
        )
        conn.execute(
            "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?)",
            (".example.com", "ignored", "value", "/", 4_000_000_000),
        )


def test_load_claude_cookies_from_firefox_profile(tmp_path):
    _create_cookie_db(tmp_path / "cookies.sqlite")

    cookies = load_claude_cookies(tmp_path)

    assert [cookie.name for cookie in cookies] == ["sessionKey"]
    assert cookies[0].value == "secret"
    assert cookies[0].domain == ".claude.ai"


def test_load_claude_cookies_errors_when_missing(tmp_path):
    with pytest.raises(UsageLoginRequiredError):
        load_claude_cookies(tmp_path)


def test_has_session_key_reads_profile(tmp_path):
    _create_cookie_db(tmp_path / "cookies.sqlite")

    assert has_session_key(tmp_path) is True
    assert has_session_key(tmp_path / "missing") is False
