from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Repository:
    """Small SQLite journal. JSON payloads keep the prototype easy to evolve."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS media (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE, request_hash TEXT, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    run_id TEXT NOT NULL, task_id TEXT NOT NULL, stage TEXT NOT NULL,
                    status TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY (run_id, task_id)
                );
                CREATE TABLE IF NOT EXISTS logs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS logs_run_seq ON logs(run_id, seq);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def create_media(self, data: dict) -> dict:
        data = {"id": new_id("media"), "created_at": now(), **data}
        with self.connection() as con:
            con.execute("INSERT INTO media VALUES (?,?)", (data["id"], json.dumps(data)))
        return data

    def update_media(self, media_id: str, **fields) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT data FROM media WHERE id=?", (media_id,)).fetchone()
            if not row:
                raise KeyError(media_id)
            data = {**json.loads(row["data"]), **fields}
            con.execute("UPDATE media SET data=? WHERE id=?", (json.dumps(data), media_id))
        return data

    def get_media(self, media_id: str) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT data FROM media WHERE id=?", (media_id,)).fetchone()
        if not row:
            raise KeyError(media_id)
        return json.loads(row["data"])

    def list_media(self) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT data FROM media ORDER BY rowid DESC").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def create_run(self, data: dict, idempotency_key: str | None = None,
                   request_hash: str | None = None) -> tuple[dict, bool]:
        stamp = now()
        data = {"id": new_id("run"), "status": "queued", "stage": "queued",
                "created_at": stamp, "updated_at": stamp, "progress": {},
                "query_spec": None, "results": None, "error": None, **data}
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                row = con.execute("SELECT data,request_hash FROM runs WHERE idempotency_key=?",
                                  (idempotency_key,)).fetchone()
                if row:
                    if row["request_hash"] != request_hash:
                        raise ValueError("idempotency_conflict")
                    return json.loads(row["data"]), False
            con.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                        (data["id"], data["status"], idempotency_key, request_hash,
                         json.dumps(data)))
        return data, True

    def get_run(self, run_id: str) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return json.loads(row["data"])

    def list_runs(self, limit: int = 30) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def update_run(self, run_id: str, **fields) -> dict:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            existing = json.loads(row["data"])
            # A late worker response cannot revive a cancelled run.
            if existing["status"] == "cancelled":
                fields["status"] = "cancelled"
                fields["stage"] = "cancelled"
                fields.pop("results", None)
            data = {**existing, **fields, "updated_at": now()}
            con.execute("UPDATE runs SET status=?, data=? WHERE id=?",
                        (data["status"], json.dumps(data), run_id))
        return data

    def claim_next_run(self) -> dict | None:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT id,data FROM runs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            data = json.loads(row["data"])
            data.update(status="running", stage="preparing", updated_at=now())
            con.execute("UPDATE runs SET status='running',data=? WHERE id=?",
                        (json.dumps(data), row["id"]))
        return data

    def is_cancelled(self, run_id: str) -> bool:
        return self.get_run(run_id)["status"] == "cancelled"

    def cancel_run(self, run_id: str) -> dict:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            data = json.loads(row["data"])
            if data["status"] in ("completed", "partial", "failed", "cancelled"):
                return data
            data.update(last_stage=data["stage"], status="cancelled", stage="cancelled", stop_reason="user_cancelled",
                        updated_at=now())
            con.execute("UPDATE runs SET status='cancelled',data=? WHERE id=?",
                        (json.dumps(data), run_id))
        return data

    def upsert_task(self, run_id: str, task_id: str, stage: str,
                    status: str = "pending", **fields) -> dict:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM tasks WHERE run_id=? AND task_id=?",
                              (run_id, task_id)).fetchone()
            prior = json.loads(row["data"]) if row else {}
            data = {**prior, "run_id": run_id, "task_id": task_id, "stage": stage,
                    "status": status, **fields, "updated_at": now()}
            con.execute("INSERT INTO tasks VALUES (?,?,?,?,?) ON CONFLICT(run_id,task_id) "
                        "DO UPDATE SET stage=excluded.stage,status=excluded.status,data=excluded.data",
                        (run_id, task_id, stage, status, json.dumps(data)))
        return data

    def list_tasks(self, run_id: str) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT data FROM tasks WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def log(self, run_id: str, stage: str, code: str, message: str,
            level: str = "info", details: dict | None = None) -> dict:
        data = {"run_id": run_id, "time": now(), "stage": stage, "level": level,
                "code": code, "message": message, "details": details or {}}
        with self.connection() as con:
            cur = con.execute("INSERT INTO logs(run_id,data) VALUES (?,?)", (run_id, json.dumps(data)))
            data["seq"] = cur.lastrowid
        return data

    def get_logs(self, run_id: str, after: int = 0, limit: int = 200) -> list[dict]:
        with self.connection() as con:
            rows = con.execute("SELECT seq,data FROM logs WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                               (run_id, after, limit)).fetchall()
        return [{**json.loads(row["data"]), "seq": row["seq"]} for row in rows]

    def set_meta(self, key: str, value: str) -> None:
        with self.connection() as con:
            con.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    def get_meta(self, key: str) -> str | None:
        with self.connection() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
