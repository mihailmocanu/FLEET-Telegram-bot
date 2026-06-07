from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geofence_state (
    truck_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (truck_id, location_id)
);

CREATE TABLE IF NOT EXISTS pending_arrivals (
    truck_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    PRIMARY KEY (truck_id, location_id)
);

CREATE TABLE IF NOT EXISTS alert_cooldowns (
    alert_type TEXT NOT NULL,
    truck_id TEXT NOT NULL,
    location_id TEXT NOT NULL DEFAULT '',
    last_sent_at INTEGER NOT NULL,
    cycle_key TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (alert_type, truck_id, location_id)
);
"""


class StateStore:
    def __init__(self, path: str, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL")
        self.is_postgres = bool(self.database_url and self.database_url.startswith(("postgres://", "postgresql://")))
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("DATABASE_URL is set, but psycopg is not installed") from exc
            self.conn = psycopg.connect(self.database_url, row_factory=dict_row)
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        if self.is_postgres:
            with self.conn.cursor() as cur:
                for statement in SCHEMA.split(";"):
                    statement = statement.strip()
                    if statement:
                        cur.execute(statement)
        else:
            self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _placeholder(self) -> str:
        return "%s" if self.is_postgres else "?"

    def _execute(self, sql: str, params: tuple[Any, ...] = ()): 
        if self.is_postgres:
            sql = sql.replace("?", "%s")
            cur = self.conn.cursor()
            cur.execute(sql, params)
            return cur
        return self.conn.execute(sql, params)

    def get_setting(self, key: str) -> Optional[str]:
        row = self._execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_geofence_state(self, truck_id: str, location_id: str) -> str:
        row = self._execute(
            "SELECT state FROM geofence_state WHERE truck_id = ? AND location_id = ?",
            (truck_id, location_id),
        ).fetchone()
        return row["state"] if row else "OUTSIDE_LOCATION"

    def set_geofence_state(self, truck_id: str, location_id: str, state: str, now: int) -> None:
        self._execute(
            "INSERT INTO geofence_state(truck_id, location_id, state, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(truck_id, location_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
            (truck_id, location_id, state, now),
        )
        self.conn.commit()

    def get_pending_since(self, truck_id: str, location_id: str) -> Optional[int]:
        row = self._execute(
            "SELECT first_seen_at FROM pending_arrivals WHERE truck_id = ? AND location_id = ?",
            (truck_id, location_id),
        ).fetchone()
        return int(row["first_seen_at"]) if row else None

    def set_pending_since(self, truck_id: str, location_id: str, now: int) -> None:
        self._execute(
            "INSERT INTO pending_arrivals(truck_id, location_id, first_seen_at) VALUES(?, ?, ?) "
            "ON CONFLICT(truck_id, location_id) DO NOTHING",
            (truck_id, location_id, now),
        )
        self.conn.commit()

    def clear_pending(self, truck_id: str, location_id: str) -> None:
        self._execute(
            "DELETE FROM pending_arrivals WHERE truck_id = ? AND location_id = ?",
            (truck_id, location_id),
        )
        self.conn.commit()

    def get_cooldown(self, alert_type: str, truck_id: str, location_id: str = ""):
        return self._execute(
            "SELECT * FROM alert_cooldowns WHERE alert_type = ? AND truck_id = ? AND location_id = ?",
            (alert_type, truck_id, location_id),
        ).fetchone()

    def set_cooldown(
        self,
        alert_type: str,
        truck_id: str,
        location_id: str,
        last_sent_at: int,
        cycle_key: str = "",
    ) -> None:
        self._execute(
            "INSERT INTO alert_cooldowns(alert_type, truck_id, location_id, last_sent_at, cycle_key) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(alert_type, truck_id, location_id) DO UPDATE SET "
            "last_sent_at = excluded.last_sent_at, cycle_key = excluded.cycle_key",
            (alert_type, truck_id, location_id, last_sent_at, cycle_key),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
