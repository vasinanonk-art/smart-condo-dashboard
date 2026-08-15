"""Bounded, fail-safe event history for household presence decisions."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


RETENTION_DAYS = 30
BACKUP_RETENTION_DAYS = 90
BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
MAINTENANCE_INTERVAL_SECONDS = 60 * 60


class PresenceEventStore:
    def __init__(
        self,
        path: Path,
        *,
        clock=time.time,
        retention_days: int = RETENTION_DAYS,
        backup_dir: Path | None = None,
        backup_retention_days: int = BACKUP_RETENTION_DAYS,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.retention_days = max(1, int(retention_days))
        self.backup_dir = Path(backup_dir) if backup_dir else None
        self.backup_retention_days = max(1, int(backup_retention_days))
        self.last_error: str | None = None
        self.last_backup_error: str | None = None
        self._next_maintenance_at = 0
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=1.0)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=1000")
        return connection

    def _ensure_schema(self) -> bool:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS presence_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        person TEXT,
                        from_state TEXT,
                        to_state TEXT,
                        reason TEXT,
                        transition_id TEXT,
                        mode TEXT NOT NULL,
                        details_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_presence_events_ts
                        ON presence_events(ts);
                    CREATE TABLE IF NOT EXISTS presence_event_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
            os.chmod(self.path, 0o600)
            if self.path.stat().st_mode & 0o077:
                raise PermissionError("presence_event_database_permissions")
            self.last_error = None
            return True
        except (OSError, sqlite3.Error) as exc:
            self.last_error = type(exc).__name__
            return False

    def record(
        self,
        event_type: str,
        *,
        ts: int | None = None,
        person: str | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        reason: str | None = None,
        transition_id: str | None = None,
        mode: str = "shadow",
        details: Mapping[str, Any] | None = None,
    ) -> bool:
        now = int(self.clock() if ts is None else ts)
        payload = json.dumps(dict(details or {}), sort_keys=True, separators=(",", ":"), default=str)
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO presence_events
                            (ts, event_type, person, from_state, to_state, reason, transition_id, mode, details_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (now, event_type, person, from_state, to_state, reason, transition_id, mode, payload),
                    )
                    connection.execute(
                        "DELETE FROM presence_events WHERE ts < ?",
                        (now - self.retention_days * 24 * 60 * 60,),
                    )
                os.chmod(self.path, 0o600)
                self.last_error = None
            except (OSError, sqlite3.Error) as exc:
                self.last_error = type(exc).__name__
                return False
        self.maintenance(now=now)
        return True

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(500, max(1, int(limit)))
        with self._lock:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        """
                        SELECT ts, event_type, person, from_state, to_state, reason,
                               transition_id, mode, details_json
                        FROM presence_events
                        ORDER BY ts DESC, id DESC
                        LIMIT ?
                        """,
                        (safe_limit,),
                    ).fetchall()
                self.last_error = None
            except (OSError, sqlite3.Error) as exc:
                self.last_error = type(exc).__name__
                return []
        events = []
        for row in rows:
            try:
                details = json.loads(row[8])
            except (TypeError, json.JSONDecodeError):
                details = {}
            events.append({
                "ts": row[0],
                "event_type": row[1],
                "person": row[2],
                "from_state": row[3],
                "to_state": row[4],
                "reason": row[5],
                "transition_id": row[6],
                "mode": row[7],
                "details": details,
            })
        return events

    def status(self) -> dict[str, Any]:
        return {
            "available": self.last_error is None,
            "retention_days": self.retention_days,
            "backup_enabled": self.backup_dir is not None,
            "last_error": self.last_error,
            "last_backup_error": self.last_backup_error,
        }

    def maintenance(self, *, now: int | None = None, force: bool = False) -> None:
        current = int(self.clock() if now is None else now)
        with self._lock:
            if not force and current < self._next_maintenance_at:
                return
            self._next_maintenance_at = current + MAINTENANCE_INTERVAL_SECONDS
            try:
                with self._connect() as connection:
                    connection.execute(
                        "DELETE FROM presence_events WHERE ts < ?",
                        (current - self.retention_days * 24 * 60 * 60,),
                    )
                    row = connection.execute(
                        "SELECT value FROM presence_event_metadata WHERE key='last_backup_at'"
                    ).fetchone()
                    last_backup_at = int(row[0]) if row else 0
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.last_error = type(exc).__name__
                return

            if self.backup_dir is None or current - last_backup_at < BACKUP_INTERVAL_SECONDS:
                return
            self._backup(current)

    def _backup(self, now: int) -> None:
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
            target = self.backup_dir / f"presence_events-{stamp}.sqlite3"
            with self._connect() as source, sqlite3.connect(target, timeout=2.0) as destination:
                source.backup(destination)
            os.chmod(target, 0o600)
            if target.stat().st_mode & 0o077:
                target.unlink(missing_ok=True)
                raise PermissionError("presence_event_backup_permissions")
            cutoff = now - self.backup_retention_days * 24 * 60 * 60
            for candidate in self.backup_dir.glob("presence_events-*.sqlite3"):
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO presence_event_metadata(key, value) VALUES('last_backup_at', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (str(now),),
                )
            self.last_backup_error = None
        except (OSError, sqlite3.Error) as exc:
            self.last_backup_error = type(exc).__name__
