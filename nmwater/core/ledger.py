"""SQLite fetch ledger: every request, its outcome, and where the raw bytes live.

The ledger makes runs resumable and incremental: a request whose key already has
status 'ok' (and whose raw file still exists) is served from the archive.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    command TEXT,
    args TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    n_requests INTEGER DEFAULT 0,
    n_rows INTEGER DEFAULT 0,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS fetches (
    request_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT,
    url TEXT NOT NULL,
    params TEXT,
    site_uid TEXT,
    variable TEXT,
    window_start TEXT,
    window_end TEXT,
    status TEXT NOT NULL,
    http_status INTEGER,
    sha256 TEXT,
    bytes INTEGER,
    raw_path TEXT,
    content_type TEXT,
    fetched_at TEXT NOT NULL,
    n_rows INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_fetches_source ON fetches(source);
CREATE INDEX IF NOT EXISTS ix_fetches_site ON fetches(source, site_uid, variable);
CREATE INDEX IF NOT EXISTS ix_fetches_run ON fetches(run_id);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


@dataclass
class FetchRecord:
    request_key: str
    run_id: str
    source: str
    kind: str | None
    url: str
    params: dict[str, Any] | None
    site_uid: str | None
    variable: str | None
    window_start: str | None
    window_end: str | None
    status: str
    http_status: int | None
    sha256: str | None
    bytes: int | None
    raw_path: str | None
    content_type: str | None
    fetched_at: str
    n_rows: int | None = None
    error: str | None = None


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=60)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Runs -------------------------------------------------------------
    def start_run(self, source: str, command: str, args: dict[str, Any] | None = None) -> str:
        run_id = new_run_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs(run_id, source, command, args, started_at, status) VALUES (?,?,?,?,?,?)",
                (run_id, source, command, json.dumps(args or {}, default=str), now_iso(), "running"),
            )
            self._conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str = "ok", notes: str | None = None) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(n_rows),0) AS r FROM fetches WHERE run_id=?", (run_id,)
            ).fetchone()
            self._conn.execute(
                "UPDATE runs SET finished_at=?, status=?, n_requests=?, n_rows=?, notes=? WHERE run_id=?",
                (now_iso(), status, row["n"], row["r"], notes, run_id),
            )
            self._conn.commit()

    # Fetches ----------------------------------------------------------
    def get(self, request_key: str) -> FetchRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM fetches WHERE request_key=?", (request_key,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["params"] = json.loads(d["params"]) if d.get("params") else None
        return FetchRecord(**d)

    def record(self, rec: FetchRecord) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO fetches(request_key, run_id, source, kind, url, params, site_uid, variable,
                       window_start, window_end, status, http_status, sha256, bytes, raw_path, content_type,
                       fetched_at, n_rows, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(request_key) DO UPDATE SET
                       run_id=excluded.run_id, status=excluded.status, http_status=excluded.http_status,
                       sha256=excluded.sha256, bytes=excluded.bytes, raw_path=excluded.raw_path,
                       content_type=excluded.content_type, fetched_at=excluded.fetched_at,
                       n_rows=excluded.n_rows, error=excluded.error""",
                (
                    rec.request_key,
                    rec.run_id,
                    rec.source,
                    rec.kind,
                    rec.url,
                    json.dumps(rec.params, default=str) if rec.params is not None else None,
                    rec.site_uid,
                    rec.variable,
                    rec.window_start,
                    rec.window_end,
                    rec.status,
                    rec.http_status,
                    rec.sha256,
                    rec.bytes,
                    rec.raw_path,
                    rec.content_type,
                    rec.fetched_at,
                    rec.n_rows,
                    rec.error,
                ),
            )
            self._conn.commit()

    def set_rows(self, request_key: str, n_rows: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE fetches SET n_rows=? WHERE request_key=?", (n_rows, request_key))
            self._conn.commit()

    def iter_fetches(self, source: str, kind: str | None = None, status: str = "ok"):
        q = "SELECT * FROM fetches WHERE source=? AND status=?"
        args: list[Any] = [source, status]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY fetched_at"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"]) if d.get("params") else None
            yield FetchRecord(**d)

    def last_window_end(self, source: str, site_uid: str, variable: str | None = None) -> str | None:
        q = "SELECT MAX(window_end) AS m FROM fetches WHERE source=? AND site_uid=? AND status='ok'"
        args: list[Any] = [source, site_uid]
        if variable:
            q += " AND variable=?"
            args.append(variable)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        return row["m"] if row else None

    def stats(self, source: str | None = None) -> list[dict[str, Any]]:
        q = """SELECT source, status, COUNT(*) AS n, COALESCE(SUM(bytes),0) AS bytes,
                      COALESCE(SUM(n_rows),0) AS rows, MIN(fetched_at) AS first, MAX(fetched_at) AS last
               FROM fetches"""
        args: list[Any] = []
        if source:
            q += " WHERE source=?"
            args.append(source)
        q += " GROUP BY source, status ORDER BY source, status"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def runs(self, source: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        q = "SELECT * FROM runs"
        args: list[Any] = []
        if source:
            q += " WHERE source=?"
            args.append(source)
        q += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]
