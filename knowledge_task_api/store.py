"""SQLite transactions implement durable jobs and publication fencing."""

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

TERMINAL = {"succeeded", "failed", "cancelled"}
PUBLIC_TASK_FIELDS = (
    "id", "state", "attempt", "max_attempts", "next_run", "result_document_id",
    "error_code", "created_at", "updated_at", "request_id",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL, tenant TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL, payload TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
    lease_token INTEGER NOT NULL DEFAULT 0, lease_until REAL,
    next_run REAL NOT NULL, result_document_id TEXT, error_code TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, request_id TEXT NOT NULL,
    UNIQUE(tenant, idempotency_key)
);
CREATE INDEX IF NOT EXISTS tasks_ready ON tasks(state,next_run,seq);
CREATE INDEX IF NOT EXISTS tasks_expired ON tasks(state,lease_until);
CREATE TABLE IF NOT EXISTS documents (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    tenant TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
    source_task TEXT UNIQUE NOT NULL REFERENCES tasks(id), created_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(title,body);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL,
    data TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
PRAGMA user_version=1;
"""


class Conflict(ValueError):
    pass


class Missing(LookupError):
    pass


@dataclass(frozen=True)
class Lease:
    id: str
    tenant: str
    token: int
    payload: dict
    attempt: int


class Store:
    def __init__(self, database: Path, *, clock=time.time, lease_seconds=30):
        if not isinstance(lease_seconds, (int, float)) or isinstance(lease_seconds, bool) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lease_seconds = lease_seconds
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unsupported database schema version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self, *, write=False):
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def public(row):
        return {field: row[field] for field in PUBLIC_TASK_FIELDS}

    @staticmethod
    def event(connection, task_id, kind, data, now):
        connection.execute(
            "INSERT INTO events(task_id,kind,data,created_at) VALUES(?,?,?,?)",
            (task_id, kind, json.dumps(data, separators=(",", ":")), now),
        )

    def enqueue(self, tenant, key, payload, request_id):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE tenant=? AND idempotency_key=?", (tenant, key)
            ).fetchone()
            if row:
                if row["request_hash"] != digest:
                    raise Conflict("idempotency key already used for a different request")
                return self.public(row), True
            now = self.clock()
            task_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO tasks(id,tenant,idempotency_key,request_hash,payload,state,max_attempts,next_run,created_at,updated_at,request_id) VALUES(?,?,?,?,?,'queued',?,?,?,?,?)",
                (task_id, tenant, key, digest, encoded, payload["max_attempts"], now, now, now, request_id),
            )
            self.event(connection, task_id, "queued", {"attempt": 0}, now)
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self.public(row), False

    def get_task(self, tenant, task_id):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE tenant=? AND id=?", (tenant, task_id)).fetchone()
        if row is None:
            raise Missing("task not found")
        return self.public(row)

    def task_events(self, tenant, task_id, after=0, limit=100):
        with self.connection() as connection:
            exists = connection.execute("SELECT 1 FROM tasks WHERE tenant=? AND id=?", (tenant, task_id)).fetchone()
            if not exists:
                raise Missing("task not found")
            rows = connection.execute(
                "SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?", (task_id, after, limit)
            ).fetchall()
        return [{"id": row["id"], "kind": row["kind"], "data": json.loads(row["data"]), "created_at": row["created_at"]} for row in rows]

    def claim(self):
        with self.connection(write=True) as connection:
            now = self.clock()
            expired = connection.execute(
                "SELECT * FROM tasks WHERE state='running' AND lease_until<=? ORDER BY seq LIMIT 100", (now,)
            ).fetchall()
            for row in expired:
                state = "failed" if row["attempt"] >= row["max_attempts"] else "queued"
                connection.execute(
                    "UPDATE tasks SET state=?,lease_token=lease_token+1,lease_until=NULL,next_run=?,error_code='lease_expired',updated_at=? WHERE id=?",
                    (state, now, now, row["id"]),
                )
                self.event(connection, row["id"], "lease_expired", {"state": state, "attempt": row["attempt"]}, now)
            row = connection.execute(
                "SELECT * FROM tasks WHERE state='queued' AND next_run<=? ORDER BY next_run,seq LIMIT 1", (now,)
            ).fetchone()
            if row is None:
                return None
            token, attempt = row["lease_token"] + 1, row["attempt"] + 1
            connection.execute(
                "UPDATE tasks SET state='running',attempt=?,lease_token=?,lease_until=?,error_code=NULL,updated_at=? WHERE id=?",
                (attempt, token, now + self.lease_seconds, now, row["id"]),
            )
            self.event(connection, row["id"], "running", {"attempt": attempt}, now)
            return Lease(row["id"], row["tenant"], token, json.loads(row["payload"]), attempt)

    @staticmethod
    def current_lease(connection, lease, now):
        return connection.execute(
            "SELECT * FROM tasks WHERE id=? AND tenant=? AND state='running' AND lease_token=? AND lease_until>?",
            (lease.id, lease.tenant, lease.token, now),
        ).fetchone()

    def complete(self, lease):
        with self.connection(write=True) as connection:
            now = self.clock()
            if not self.current_lease(connection, lease, now):
                return False
            document_id = uuid.uuid4().hex
            cursor = connection.execute(
                "INSERT INTO documents(id,tenant,title,body,source_task,created_at) VALUES(?,?,?,?,?,?)",
                (document_id, lease.tenant, lease.payload["title"], lease.payload["text"], lease.id, now),
            )
            connection.execute(
                "INSERT INTO document_fts(rowid,title,body) VALUES(?,?,?)",
                (cursor.lastrowid, lease.payload["title"], lease.payload["text"]),
            )
            connection.execute(
                "UPDATE tasks SET state='succeeded',result_document_id=?,lease_until=NULL,updated_at=? WHERE id=?",
                (document_id, now, lease.id),
            )
            self.event(connection, lease.id, "succeeded", {"document_id": document_id}, now)
            return True

    def renew(self, lease):
        """Extend only a live, current lease; never revive an expired owner."""
        with self.connection(write=True) as connection:
            now = self.clock()
            row = self.current_lease(connection, lease, now)
            if row is None:
                return False
            deadline = max(row["lease_until"], now + self.lease_seconds)
            connection.execute(
                "UPDATE tasks SET lease_until=?,updated_at=? WHERE id=?",
                (deadline, now, lease.id),
            )
            self.event(connection, lease.id, "lease_renewed", {"attempt": row["attempt"], "lease_until": deadline}, now)
            return True

    def fail(self, lease, *, retryable=True):
        with self.connection(write=True) as connection:
            now = self.clock()
            row = self.current_lease(connection, lease, now)
            if row is None:
                return False
            retry = retryable and row["attempt"] < row["max_attempts"]
            state = "queued" if retry else "failed"
            delay = min(60, 2 ** (row["attempt"] - 1)) if retry else 0
            code = "temporary_index_failure" if retryable else "index_failure"
            connection.execute(
                "UPDATE tasks SET state=?,lease_until=NULL,lease_token=lease_token+1,next_run=?,error_code=?,updated_at=? WHERE id=?",
                (state, now + delay, code, now, lease.id),
            )
            self.event(connection, lease.id, "retry_scheduled" if retry else "failed", {"attempt": row["attempt"], "retry_after_seconds": delay, "error_code": code}, now)
            return True

    def cancel(self, tenant, task_id):
        with self.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE tenant=? AND id=?", (tenant, task_id)).fetchone()
            if row is None:
                raise Missing("task not found")
            if row["state"] in {"succeeded", "failed"}:
                raise Conflict("task is already terminal")
            if row["state"] != "cancelled":
                now = self.clock()
                connection.execute(
                    "UPDATE tasks SET state='cancelled',lease_token=lease_token+1,lease_until=NULL,updated_at=? WHERE id=?", (now, task_id)
                )
                self.event(connection, task_id, "cancelled", {}, now)
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self.public(row)

    def get_document(self, tenant, document_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id,title,body,source_task,created_at FROM documents WHERE tenant=? AND id=?", (tenant, document_id)
            ).fetchone()
        if row is None:
            raise Missing("document not found")
        return dict(row)

    def search(self, tenant, query, limit=10):
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms or len(terms) > 12:
            raise ValueError("search requires 1 to 12 word tokens")
        expression = " AND ".join('"' + word.replace('"', '""') + '"' for word in terms)
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT d.id,d.title,d.source_task,snippet(document_fts,1,'[',']','...',24) AS excerpt,bm25(document_fts,2.0,1.0) AS rank FROM document_fts JOIN documents d ON d.seq=document_fts.rowid WHERE document_fts MATCH ? AND d.tenant=? ORDER BY rank,d.id LIMIT ?",
                (expression, tenant, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def statistics(self, tenant=None):
        with self.connection() as connection:
            where, parameters = (" WHERE tenant=?", (tenant,)) if tenant is not None else ("", ())
            states = connection.execute("SELECT state,count(*) AS count FROM tasks" + where + " GROUP BY state ORDER BY state", parameters).fetchall()
            documents = connection.execute("SELECT count(*) FROM documents" + where, parameters).fetchone()[0]
        return {"documents": documents, "tasks": {row["state"]: row["count"] for row in states}}
