from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import Account, Watcher, WatcherEvent, utc_now


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
                """
            )

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
    def _event_from_row(row: sqlite3.Row) -> WatcherEvent:
        return WatcherEvent(
            id=row["id"],
            watcher_id=row["watcher_id"],
            level=row["level"],
            message=row["message"],
            created_at=row["created_at"],
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
        return self.get_watcher(watcher_id)

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
