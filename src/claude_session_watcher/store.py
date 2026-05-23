from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from .models import (
    Account,
    AccountWatcher,
    AccountWatcherEvent,
    ClaudeSession,
    Watcher,
    WatcherEvent,
    utc_now,
)


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    profile_dir TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watchers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    remote_url TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'active',
                    five_hour_threshold REAL NOT NULL DEFAULT 95.0,
                    seven_day_threshold REAL NOT NULL DEFAULT 98.0,
                    resume_threshold REAL NOT NULL DEFAULT 5.0,
                    check_interval_seconds INTEGER NOT NULL DEFAULT 60,
                    pause_message TEXT NOT NULL,
                    continue_message TEXT NOT NULL DEFAULT 'continue',
                    last_usage_json TEXT,
                    last_reason TEXT,
                    last_error TEXT,
                    last_checked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watcher_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watcher_id INTEGER NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_watchers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'active',
                    five_hour_threshold REAL NOT NULL DEFAULT 95.0,
                    seven_day_threshold REAL NOT NULL DEFAULT 98.0,
                    resume_threshold REAL NOT NULL DEFAULT 5.0,
                    check_interval_seconds INTEGER NOT NULL DEFAULT 60,
                    pause_message TEXT NOT NULL,
                    continue_message TEXT NOT NULL DEFAULT 'continue',
                    last_usage_json TEXT,
                    last_reason TEXT,
                    last_error TEXT,
                    last_checked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claude_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    session_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'unknown',
                    status TEXT NOT NULL DEFAULT 'unknown',
                    watch_enabled INTEGER NOT NULL DEFAULT 0,
                    control_supported INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT,
                    last_seen_at TEXT,
                    last_checked_at TEXT,
                    last_control_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, session_key)
                );

                CREATE TABLE IF NOT EXISTS account_watcher_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_watcher_id INTEGER NOT NULL
                        REFERENCES account_watchers(id) ON DELETE CASCADE,
                    session_id INTEGER REFERENCES claude_sessions(id) ON DELETE SET NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
        self._migrate_legacy_watchers()

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            name=row["name"],
            profile_dir=row["profile_dir"],
            status=row["status"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _watcher_from_row(row: sqlite3.Row) -> Watcher:
        return Watcher(
            id=row["id"],
            name=row["name"],
            account_id=row["account_id"],
            remote_url=row["remote_url"],
            enabled=bool(row["enabled"]),
            state=row["state"],
            five_hour_threshold=row["five_hour_threshold"],
            seven_day_threshold=row["seven_day_threshold"],
            resume_threshold=row["resume_threshold"],
            check_interval_seconds=row["check_interval_seconds"],
            pause_message=row["pause_message"],
            continue_message=row["continue_message"],
            last_usage_json=row["last_usage_json"],
            last_reason=row["last_reason"],
            last_error=row["last_error"],
            last_checked_at=row["last_checked_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _account_watcher_from_row(row: sqlite3.Row) -> AccountWatcher:
        return AccountWatcher(
            id=row["id"],
            account_id=row["account_id"],
            enabled=bool(row["enabled"]),
            state=row["state"],
            five_hour_threshold=row["five_hour_threshold"],
            seven_day_threshold=row["seven_day_threshold"],
            resume_threshold=row["resume_threshold"],
            check_interval_seconds=row["check_interval_seconds"],
            pause_message=row["pause_message"],
            continue_message=row["continue_message"],
            last_usage_json=row["last_usage_json"],
            last_reason=row["last_reason"],
            last_error=row["last_error"],
            last_checked_at=row["last_checked_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> ClaudeSession:
        return ClaudeSession(
            id=row["id"],
            account_id=row["account_id"],
            session_key=row["session_key"],
            title=row["title"],
            url=row["url"],
            kind=row["kind"],
            status=row["status"],
            watch_enabled=bool(row["watch_enabled"]),
            control_supported=bool(row["control_supported"]),
            raw_json=row["raw_json"],
            last_seen_at=row["last_seen_at"],
            last_checked_at=row["last_checked_at"],
            last_control_error=row["last_control_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> WatcherEvent:
        return WatcherEvent(
            id=row["id"],
            watcher_id=row["watcher_id"],
            level=row["level"],
            message=row["message"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _account_event_from_row(row: sqlite3.Row) -> AccountWatcherEvent:
        return AccountWatcherEvent(
            id=row["id"],
            account_watcher_id=row["account_watcher_id"],
            session_id=row["session_id"],
            level=row["level"],
            message=row["message"],
            created_at=row["created_at"],
        )

    @staticmethod
    def session_key_from_url(url: str) -> str:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        for part in reversed(parts):
            if part.startswith("session_") or len(part) >= 12:
                return part
        return "url-" + sha256(url.encode("utf-8")).hexdigest()[:24]

    def _migrate_legacy_watchers(self) -> None:
        now = utc_now()
        with self._connect() as conn:
            legacy_rows = conn.execute("SELECT * FROM watchers ORDER BY id").fetchall()
            for row in legacy_rows:
                existing_account_watcher = conn.execute(
                    "SELECT id FROM account_watchers WHERE account_id = ?",
                    (row["account_id"],),
                ).fetchone()
                if existing_account_watcher is None:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO account_watchers (
                            account_id, enabled, state,
                            five_hour_threshold, seven_day_threshold, resume_threshold,
                            check_interval_seconds, pause_message, continue_message,
                            last_usage_json, last_reason, last_error, last_checked_at,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["account_id"],
                            row["enabled"],
                            row["state"],
                            row["five_hour_threshold"],
                            row["seven_day_threshold"],
                            row["resume_threshold"],
                            row["check_interval_seconds"],
                            row["pause_message"],
                            row["continue_message"],
                            row["last_usage_json"],
                            row["last_reason"],
                            row["last_error"],
                            row["last_checked_at"],
                            row["created_at"] or now,
                            now,
                        ),
                    )
                remote_url = row["remote_url"]
                session_key = self.session_key_from_url(remote_url)
                title = row["name"]
                conn.execute(
                    """
                    INSERT INTO claude_sessions (
                        account_id, session_key, title, url, kind, status,
                        watch_enabled, control_supported, last_seen_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, session_key) DO UPDATE SET
                        title = excluded.title,
                        url = excluded.url,
                        watch_enabled = CASE
                            WHEN claude_sessions.watch_enabled = 1 THEN 1
                            ELSE excluded.watch_enabled
                        END,
                        control_supported = excluded.control_supported,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["account_id"],
                        session_key,
                        title,
                        remote_url,
                        "remote",
                        "unknown",
                        row["enabled"],
                        1,
                        row["last_checked_at"],
                        row["created_at"] or now,
                        now,
                    ),
                )

    def create_account(self, name: str, profile_dir: str) -> Account:
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO accounts (name, profile_dir, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, profile_dir, now, now),
            )
            account_id = cur.lastrowid
        self.ensure_account_watcher(account_id)
        return self.get_account(account_id)

    def list_accounts(self) -> list[Account]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY name").fetchall()
            return [self._account_from_row(row) for row in rows]

    def get_account(self, account_id: int) -> Account:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise KeyError(f"Account {account_id} not found")
            return self._account_from_row(row)

    def update_account_status(
        self, account_id: int, status: str, last_error: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, last_error, utc_now(), account_id),
            )

    def ensure_account_watcher(self, account_id: int) -> AccountWatcher:
        existing = self.get_account_watcher_by_account(account_id)
        if existing:
            return existing
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO account_watchers (
                    account_id, created_at, updated_at, pause_message
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    account_id,
                    now,
                    now,
                    AccountWatcher(id=None, account_id=account_id).pause_message,
                ),
            )
        existing = self.get_account_watcher_by_account(account_id)
        if existing is None:
            raise KeyError(f"Account watcher for account {account_id} not found")
        return existing

    def list_account_watchers(self, enabled_only: bool = False) -> list[AccountWatcher]:
        sql = "SELECT * FROM account_watchers"
        params: Iterable[object] = ()
        if enabled_only:
            sql += " WHERE enabled = ?"
            params = (1,)
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._account_watcher_from_row(row) for row in rows]

    def get_account_watcher(self, account_watcher_id: int) -> AccountWatcher:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_watchers WHERE id = ?",
                (account_watcher_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Account watcher {account_watcher_id} not found")
            return self._account_watcher_from_row(row)

    def get_account_watcher_by_account(self, account_id: int) -> AccountWatcher | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_watchers WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            return self._account_watcher_from_row(row) if row else None

    def update_account_watcher_config(
        self,
        account_watcher_id: int,
        watcher: AccountWatcher,
    ) -> AccountWatcher:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE account_watchers
                SET enabled = ?, state = ?,
                    five_hour_threshold = ?, seven_day_threshold = ?,
                    resume_threshold = ?, check_interval_seconds = ?,
                    pause_message = ?, continue_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    int(watcher.enabled),
                    watcher.state,
                    watcher.five_hour_threshold,
                    watcher.seven_day_threshold,
                    watcher.resume_threshold,
                    watcher.check_interval_seconds,
                    watcher.pause_message,
                    watcher.continue_message,
                    utc_now(),
                    account_watcher_id,
                ),
            )
        return self.get_account_watcher(account_watcher_id)

    def set_account_watcher_enabled(self, account_watcher_id: int, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE account_watchers SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), utc_now(), account_watcher_id),
            )

    def update_account_watcher_runtime(
        self,
        account_watcher_id: int,
        *,
        state: str | None = None,
        last_usage_json: str | None = None,
        last_reason: str | None = None,
        last_error: str | None = None,
    ) -> None:
        watcher = self.get_account_watcher(account_watcher_id)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE account_watchers
                SET state = ?, last_usage_json = ?, last_reason = ?, last_error = ?,
                    last_checked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    state if state is not None else watcher.state,
                    last_usage_json if last_usage_json is not None else watcher.last_usage_json,
                    last_reason,
                    last_error,
                    utc_now(),
                    utc_now(),
                    account_watcher_id,
                ),
            )

    def upsert_session(self, session: ClaudeSession) -> ClaudeSession:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO claude_sessions (
                    account_id, session_key, title, url, kind, status,
                    watch_enabled, control_supported, raw_json, last_seen_at,
                    last_checked_at, last_control_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, session_key) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    kind = excluded.kind,
                    status = excluded.status,
                    watch_enabled = CASE
                        WHEN excluded.watch_enabled = 1 THEN 1
                        ELSE claude_sessions.watch_enabled
                    END,
                    control_supported = excluded.control_supported,
                    raw_json = excluded.raw_json,
                    last_seen_at = excluded.last_seen_at,
                    last_checked_at = excluded.last_checked_at,
                    updated_at = excluded.updated_at
                """,
                (
                    session.account_id,
                    session.session_key,
                    session.title,
                    session.url,
                    session.kind,
                    session.status,
                    int(session.watch_enabled),
                    int(session.control_supported),
                    session.raw_json,
                    session.last_seen_at or now,
                    session.last_checked_at,
                    session.last_control_error,
                    now,
                    now,
                ),
            )
        return self.get_session_by_key(session.account_id, session.session_key)

    def list_sessions(self, account_id: int | None = None) -> list[ClaudeSession]:
        sql = "SELECT * FROM claude_sessions"
        params: list[object] = []
        if account_id is not None:
            sql += " WHERE account_id = ?"
            params.append(account_id)
        sql += " ORDER BY account_id, watch_enabled DESC, updated_at DESC, title"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._session_from_row(row) for row in rows]

    def list_watched_sessions(self, account_id: int) -> list[ClaudeSession]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM claude_sessions
                WHERE account_id = ? AND watch_enabled = 1
                ORDER BY id
                """,
                (account_id,),
            ).fetchall()
            return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: int) -> ClaudeSession:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM claude_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Claude session {session_id} not found")
            return self._session_from_row(row)

    def get_session_by_key(self, account_id: int, session_key: str) -> ClaudeSession:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM claude_sessions WHERE account_id = ? AND session_key = ?",
                (account_id, session_key),
            ).fetchone()
            if row is None:
                raise KeyError(f"Claude session {session_key} not found")
            return self._session_from_row(row)

    def set_session_watch_enabled(self, session_id: int, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE claude_sessions SET watch_enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), utc_now(), session_id),
            )

    def update_session_control_error(self, session_id: int, error: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE claude_sessions
                SET last_control_error = ?, last_checked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (error, utc_now(), utc_now(), session_id),
            )

    def add_account_event(
        self,
        account_watcher_id: int,
        level: str,
        message: str,
        *,
        session_id: int | None = None,
    ) -> AccountWatcherEvent:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO account_watcher_events (
                    account_watcher_id, session_id, level, message, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (account_watcher_id, session_id, level, message, utc_now()),
            )
            event_id = cur.lastrowid
        return self.get_account_event(event_id)

    def get_account_event(self, event_id: int) -> AccountWatcherEvent:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_watcher_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Account watcher event {event_id} not found")
            return self._account_event_from_row(row)

    def list_account_events(
        self,
        account_watcher_id: int | None = None,
        limit: int = 100,
    ) -> list[AccountWatcherEvent]:
        sql = "SELECT * FROM account_watcher_events"
        params: list[object] = []
        if account_watcher_id is not None:
            sql += " WHERE account_watcher_id = ?"
            params.append(account_watcher_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._account_event_from_row(row) for row in rows]

    def mark_missing_sessions(self, account_id: int, seen_keys: set[str]) -> None:
        sessions = self.list_sessions(account_id)
        now = utc_now()
        with self._connect() as conn:
            for session in sessions:
                if session.session_key in seen_keys:
                    continue
                if session.status == "archived":
                    continue
                conn.execute(
                    """
                    UPDATE claude_sessions
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("archived", now, session.id),
                )

    def create_watcher(self, watcher: Watcher) -> Watcher:
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO watchers (
                    name, account_id, remote_url, enabled, state,
                    five_hour_threshold, seven_day_threshold, resume_threshold,
                    check_interval_seconds, pause_message, continue_message,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    watcher.name,
                    watcher.account_id,
                    watcher.remote_url,
                    int(watcher.enabled),
                    watcher.state,
                    watcher.five_hour_threshold,
                    watcher.seven_day_threshold,
                    watcher.resume_threshold,
                    watcher.check_interval_seconds,
                    watcher.pause_message,
                    watcher.continue_message,
                    now,
                    now,
                ),
            )
            watcher_id = cur.lastrowid
        saved = self.get_watcher(watcher_id)
        account_watcher = self.ensure_account_watcher(saved.account_id)
        self.update_account_watcher_config(
            account_watcher.id,
            AccountWatcher(
                id=account_watcher.id,
                account_id=saved.account_id,
                enabled=saved.enabled,
                state=saved.state,
                five_hour_threshold=saved.five_hour_threshold,
                seven_day_threshold=saved.seven_day_threshold,
                resume_threshold=saved.resume_threshold,
                check_interval_seconds=saved.check_interval_seconds,
                pause_message=saved.pause_message,
                continue_message=saved.continue_message,
            ),
        )
        self.upsert_session(
            ClaudeSession(
                id=None,
                account_id=saved.account_id,
                session_key=self.session_key_from_url(saved.remote_url),
                title=saved.name,
                url=saved.remote_url,
                kind="remote",
                status="unknown",
                watch_enabled=saved.enabled,
                control_supported=True,
                last_seen_at=saved.last_checked_at,
            )
        )
        return saved

    def list_watchers(self, enabled_only: bool = False) -> list[Watcher]:
        sql = "SELECT * FROM watchers"
        params: Iterable[object] = ()
        if enabled_only:
            sql += " WHERE enabled = ?"
            params = (1,)
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._watcher_from_row(row) for row in rows]

    def get_watcher(self, watcher_id: int) -> Watcher:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM watchers WHERE id = ?", (watcher_id,)).fetchone()
            if row is None:
                raise KeyError(f"Watcher {watcher_id} not found")
            return self._watcher_from_row(row)

    def set_watcher_enabled(self, watcher_id: int, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE watchers SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), utc_now(), watcher_id),
            )

    def update_watcher_config(self, watcher_id: int, watcher: Watcher) -> Watcher:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE watchers
                SET name = ?, account_id = ?, remote_url = ?, enabled = ?,
                    five_hour_threshold = ?, seven_day_threshold = ?,
                    resume_threshold = ?, check_interval_seconds = ?,
                    pause_message = ?, continue_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    watcher.name,
                    watcher.account_id,
                    watcher.remote_url,
                    int(watcher.enabled),
                    watcher.five_hour_threshold,
                    watcher.seven_day_threshold,
                    watcher.resume_threshold,
                    watcher.check_interval_seconds,
                    watcher.pause_message,
                    watcher.continue_message,
                    utc_now(),
                    watcher_id,
                ),
            )
        return self.get_watcher(watcher_id)

    def update_watcher_runtime(
        self,
        watcher_id: int,
        *,
        state: str | None = None,
        last_usage_json: str | None = None,
        last_reason: str | None = None,
        last_error: str | None = None,
    ) -> None:
        watcher = self.get_watcher(watcher_id)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE watchers
                SET state = ?, last_usage_json = ?, last_reason = ?, last_error = ?,
                    last_checked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    state if state is not None else watcher.state,
                    last_usage_json if last_usage_json is not None else watcher.last_usage_json,
                    last_reason,
                    last_error,
                    utc_now(),
                    utc_now(),
                    watcher_id,
                ),
            )

    def add_event(self, watcher_id: int, level: str, message: str) -> WatcherEvent:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO watcher_events (watcher_id, level, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (watcher_id, level, message, utc_now()),
            )
            event_id = cur.lastrowid
        return self.get_event(event_id)

    def get_event(self, event_id: int) -> WatcherEvent:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM watcher_events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(f"Event {event_id} not found")
            return self._event_from_row(row)

    def list_events(self, watcher_id: int | None = None, limit: int = 100) -> list[WatcherEvent]:
        sql = "SELECT * FROM watcher_events"
        params: list[object] = []
        if watcher_id is not None:
            sql += " WHERE watcher_id = ?"
            params.append(watcher_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._event_from_row(row) for row in rows]
