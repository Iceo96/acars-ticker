"""
SQLite storage for acars-ticker (WAL mode).

One table `messages` holding parsed acarsdec fields plus derived columns:
airline code, bucket, and an aircraft-meta JSON blob populated by enrichment.
Retention pruning drops rows older than N days.

Author: iceo96
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,              -- epoch seconds
    channel     INTEGER,                    -- acarsdec channel index
    freq        REAL,                       -- MHz
    level       REAL,                       -- signal level (dB)
    error       INTEGER,                    -- decode error count
    mode        TEXT,
    label       TEXT,
    block_id    TEXT,
    ack         TEXT,
    msgno       TEXT,
    tail        TEXT,                        -- registration
    flight      TEXT,
    text        TEXT,
    airline     TEXT,                        -- derived ICAO operator code
    bucket      TEXT,                        -- derived classification
    meta        TEXT,                        -- aircraft meta JSON (enrichment)
    raw         TEXT                         -- original acarsdec JSON
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_tail ON messages(tail);
CREATE INDEX IF NOT EXISTS idx_messages_bucket ON messages(bucket);
"""

COLUMNS = (
    "id", "ts", "channel", "freq", "level", "error", "mode", "label",
    "block_id", "ack", "msgno", "tail", "flight", "text", "airline",
    "bucket", "meta", "raw",
)


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + a lock: the collector and the web server
        # share one Storage instance across threads/tasks.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def insert(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Insert a parsed message dict; returns the stored row (with id)."""
        meta = msg.get("meta")
        row = (
            msg.get("ts", time.time()),
            msg.get("channel"),
            msg.get("freq"),
            msg.get("level"),
            msg.get("error"),
            msg.get("mode"),
            msg.get("label"),
            msg.get("block_id"),
            msg.get("ack"),
            msg.get("msgno"),
            msg.get("tail"),
            msg.get("flight"),
            msg.get("text"),
            msg.get("airline"),
            msg.get("bucket"),
            json.dumps(meta) if meta is not None else None,
            msg.get("raw"),
        )
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO messages
                   (ts, channel, freq, level, error, mode, label, block_id,
                    ack, msgno, tail, flight, text, airline, bucket, meta, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            self._conn.commit()
            new_id = cur.lastrowid
        out = dict(msg)
        out["id"] = new_id
        return out

    def update_meta(self, tail: str, meta: dict[str, Any], since_id: int = 0) -> int:
        """Attach enrichment meta to recent rows for a registration."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE messages SET meta=? WHERE tail=? AND id>=? AND meta IS NULL",
                (json.dumps(meta), tail, since_id),
            )
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        if d.get("meta"):
            try:
                d["meta"] = json.loads(d["meta"])
            except (ValueError, TypeError):
                d["meta"] = None
        return d

    def recent(self, limit: int = 200, bucket: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM messages"
        params: list[Any] = []
        if bucket:
            q += " WHERE bucket=?"
            params.append(bucket)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def thread_for_tail(self, tail: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE tail=? ORDER BY id DESC LIMIT ?",
                (tail, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def aircraft(self, window_seconds: int = 3600, limit: int = 300) -> list[dict[str, Any]]:
        """Latest message row per registration within the window (for the map).

        Returns one row per tail (its most recent message), newest first, so the
        UI can place a marker per aircraft and show its latest message.
        """
        cutoff = time.time() - window_seconds
        with self._lock:
            rows = self._conn.execute(
                """SELECT m.*, c.cnt FROM messages m
                   JOIN (
                       SELECT tail, MAX(id) AS max_id, COUNT(*) AS cnt
                       FROM messages
                       WHERE ts>=? AND tail IS NOT NULL
                       GROUP BY tail
                   ) c ON m.id = c.max_id
                   ORDER BY m.id DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self, window_seconds: int = 3600) -> dict[str, Any]:
        """Top-bar stats over the trailing window."""
        cutoff = time.time() - window_seconds
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE ts>=?", (cutoff,)
            ).fetchone()[0]
            uniq = self._conn.execute(
                "SELECT COUNT(DISTINCT tail) FROM messages WHERE ts>=? AND tail IS NOT NULL",
                (cutoff,),
            ).fetchone()[0]
            err_rows = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE ts>=? AND error>0", (cutoff,)
            ).fetchone()[0]
            per_channel = self._conn.execute(
                "SELECT freq, COUNT(*) c FROM messages WHERE ts>=? GROUP BY freq ORDER BY freq",
                (cutoff,),
            ).fetchall()
            buckets = self._conn.execute(
                "SELECT bucket, COUNT(*) c FROM messages WHERE ts>=? GROUP BY bucket",
                (cutoff,),
            ).fetchall()
        msgs_per_min = round(total / (window_seconds / 60.0), 2) if total else 0.0
        error_rate = round(err_rows / total, 4) if total else 0.0
        return {
            "window_seconds": window_seconds,
            "total": total,
            "unique_aircraft": uniq,
            "msgs_per_min": msgs_per_min,
            "error_rate": error_rate,
            "per_channel": {str(r["freq"]): r["c"] for r in per_channel},
            "buckets": {r["bucket"] or "misc": r["c"] for r in buckets},
        }

    def prune(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM messages WHERE ts<?", (cutoff,))
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
