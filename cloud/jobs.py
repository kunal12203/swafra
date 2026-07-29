"""Ingest job queue — cloud ingest is a job, never a tool body (ADR-0004).

Status machine: pending → processing → ready | failed
('failed' after max_attempts is the dead-letter state: kept, inspectable,
manually retryable — never auto-retried again.)

Claim protocol (the SKIP LOCKED pattern used by Solid Queue / pg-boss / Oban):
  * one atomic UPDATE claims the oldest runnable job — either pending and due,
    or processing with an EXPIRED lease (crashed worker; lease lapse is the
    recovery signal — no heartbeat table, no coordinator)
  * the claim transaction is short; the actual work runs outside any lock
  * completion writes are guarded by locked_by, so a worker that lost its
    lease cannot clobber a reclaiming worker's outcome
  * delivery is therefore at-least-once; the handler is idempotent
    (add_knowledge with identical content replaces the same source_id)
  * retries back off exponentially via run_at; attempts are bounded

Backends share one SQL text ('?' placeholders): SQLite for single-node dev,
Postgres (FOR UPDATE SKIP LOCKED, shared with the store pool) for real fleets
where any number of `python -m cloud.worker` processes drain the same queue.

Quotas (per-tenant fairness, enforced at submit): max text size per job and
max concurrently active jobs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from engine.store import use_store

log = logging.getLogger("swafra.cloud.jobs")

# Dedup covers in-flight jobs only: once a job is done, identical content may
# be resubmitted (e.g. after delete_source) — engine ingest is idempotent by
# source_id, so a re-run replaces rather than duplicates.
_DEDUP_STATUSES = ("pending", "processing")

_SQLITE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS jobs (
        id            TEXT PRIMARY KEY,
        tenant_id     TEXT NOT NULL,
        workspace_id  TEXT NOT NULL,
        content_hash  TEXT NOT NULL,
        title         TEXT NOT NULL,
        text          TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'pending',
        attempts      INTEGER NOT NULL DEFAULT 0,
        max_attempts  INTEGER NOT NULL DEFAULT 3,
        run_at        REAL NOT NULL,
        locked_until  REAL,
        locked_by     TEXT,
        result        TEXT,
        error         TEXT,
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_runnable
        ON jobs (run_at) WHERE status IN ('pending', 'processing');
    CREATE INDEX IF NOT EXISTS idx_jobs_dedup
        ON jobs (tenant_id, workspace_id, content_hash);
"""


class _SqliteJobs:
    """Single-node backend. One connection, lock-serialized (dev / small deploys)."""

    lock_clause = ""  # single writer; BEGIN semantics serialize claims

    def __init__(self, db_path: Path | str):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SQLITE_SCHEMA)
            self._conn.commit()

    def run(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            self._conn.commit()
            return rows


class _PostgresJobs:
    """Fleet backend: shared pool, FOR UPDATE SKIP LOCKED claims."""

    lock_clause = "FOR UPDATE SKIP LOCKED"

    def __init__(self, pool):
        self._pool = pool

    def run(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._pool.connection() as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            return cur.fetchall() if cur.description else []


class JobQueue:
    def __init__(self, config, registry):
        self._cfg = config
        self._registry = registry
        if registry.pool is not None:
            self._db = _PostgresJobs(registry.pool)
        else:
            self._db = _SqliteJobs(config.data_dir / "jobs.db")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- submit / status ------------------------------------------------------

    def submit(self, token, title: str, text: str) -> dict:
        """Enqueue for the authenticated workspace. Idempotent; quota-checked."""
        if len(text.encode()) > self._cfg.quota_max_text_bytes:
            return {"status": "rejected",
                    "reason": f"text exceeds {self._cfg.quota_max_text_bytes} bytes"}

        (active,), = self._db.run(
            "SELECT COUNT(*) FROM jobs WHERE tenant_id = ?"
            " AND status IN ('pending', 'processing')", (token.tenant_id,))
        if active >= self._cfg.quota_max_active_jobs:
            return {"status": "rejected",
                    "reason": f"tenant has {active} active jobs (limit "
                              f"{self._cfg.quota_max_active_jobs}); retry later"}

        content_hash = hashlib.sha256(f"{title}\x00{text}".encode()).hexdigest()
        existing = self._db.run(
            "SELECT id, status FROM jobs WHERE tenant_id = ? AND workspace_id = ?"
            " AND content_hash = ? AND status IN (?, ?)"
            " ORDER BY created_at DESC LIMIT 1",
            (token.tenant_id, token.workspace_id, content_hash, *_DEDUP_STATUSES))
        if existing:
            return {"job_id": existing[0][0], "status": existing[0][1],
                    "deduplicated": True}

        job_id, now = uuid.uuid4().hex, time.time()
        self._db.run(
            "INSERT INTO jobs (id, tenant_id, workspace_id, content_hash, title,"
            " text, status, max_attempts, run_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (job_id, token.tenant_id, token.workspace_id, content_hash, title,
             text, self._cfg.job_max_attempts, now, now, now))
        return {"job_id": job_id, "status": "pending", "deduplicated": False}

    def get(self, token, job_id: str) -> dict | None:
        """Job status — scoped to the caller's tenant/workspace, no cross reads."""
        rows = self._db.run(
            "SELECT id, status, title, attempts, result, error, created_at,"
            " updated_at FROM jobs"
            " WHERE id = ? AND tenant_id = ? AND workspace_id = ?",
            (job_id, token.tenant_id, token.workspace_id))
        if not rows:
            return None
        r = rows[0]
        out = {"job_id": r[0], "status": r[1], "title": r[2], "attempts": r[3],
               "created_at": r[6], "updated_at": r[7]}
        if r[4]:
            out["result"] = json.loads(r[4])
        if r[5]:
            out["error"] = r[5]
        return out

    # -- claim protocol --------------------------------------------------------

    def claim(self, worker_id: str) -> dict | None:
        """Atomically claim the oldest runnable job (due pending, or expired lease)."""
        now = time.time()
        # Reap poison jobs first: an expired lease with exhausted attempts means
        # the job repeatedly killed its worker (so _fail never ran) — dead-letter
        # it instead of reclaiming it forever.
        self._db.run(
            "UPDATE jobs SET status = 'failed',"
            " error = COALESCE(error, 'worker died; attempts exhausted'),"
            " locked_until = NULL, locked_by = NULL, updated_at = ?"
            " WHERE status = 'processing' AND locked_until < ?"
            " AND attempts >= max_attempts",
            (now, now))
        rows = self._db.run(
            "UPDATE jobs SET status = 'processing', attempts = attempts + 1,"
            " locked_until = ?, locked_by = ?, updated_at = ?"
            " WHERE id = ("
            "   SELECT id FROM jobs"
            "   WHERE ((status = 'pending' AND run_at <= ?)"
            "      OR (status = 'processing' AND locked_until < ?))"
            "   AND attempts < max_attempts"
            f"  ORDER BY run_at LIMIT 1 {self._db.lock_clause})"
            " RETURNING id, tenant_id, workspace_id, title, text, attempts,"
            " max_attempts",
            (now + self._cfg.job_lease_seconds, worker_id, now, now, now))
        if not rows:
            return None
        r = rows[0]
        return {"id": r[0], "tenant_id": r[1], "workspace_id": r[2], "title": r[3],
                "text": r[4], "attempts": r[5], "max_attempts": r[6]}

    def _complete(self, job_id: str, worker_id: str, result: dict) -> None:
        self._db.run(
            "UPDATE jobs SET status = 'ready', result = ?, locked_until = NULL,"
            " locked_by = NULL, updated_at = ?"
            " WHERE id = ? AND locked_by = ? AND status = 'processing'",
            (json.dumps(result), time.time(), job_id, worker_id))

    def _fail(self, job: dict, worker_id: str, error: str) -> None:
        """Retry with exponential backoff + jitter; dead-letter after max attempts."""
        exhausted = job["attempts"] >= job["max_attempts"]
        backoff = min(300.0, (2 ** job["attempts"]) * 2 + random.uniform(0, 1))
        self._db.run(
            "UPDATE jobs SET status = ?, error = ?, run_at = ?,"
            " locked_until = NULL, locked_by = NULL, updated_at = ?"
            " WHERE id = ? AND locked_by = ? AND status = 'processing'",
            ("failed" if exhausted else "pending", error[:2000],
             time.time() + backoff, time.time(), job["id"], worker_id))
        if exhausted:
            log.error("job %s dead-lettered after %d attempts: %s",
                      job["id"], job["attempts"], error)

    # -- workers ----------------------------------------------------------------

    def start_workers(self, count: int) -> None:
        for i in range(count):
            t = threading.Thread(target=self._worker_loop,
                                 name=f"ingest-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("started %d ingest workers", count)

    def stop_workers(self, timeout: float = 30.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)

    def _worker_loop(self) -> None:
        worker_id = uuid.uuid4().hex
        while not self._stop.is_set():
            job = self.claim(worker_id)
            if job is None:
                self._stop.wait(self._cfg.job_poll_seconds + random.uniform(0, 0.2))
                continue
            self._process(job, worker_id)

    def _process(self, job: dict, worker_id: str) -> None:
        try:
            # Imported here so the queue stays importable without ML deps.
            from engine.graph import add_knowledge

            from cloud.tenancy import WorkspaceRef

            store = self._registry.get(
                WorkspaceRef(job["tenant_id"], job["workspace_id"]))
            with use_store(store):
                result = add_knowledge(job["text"], job["title"])
            self._complete(job["id"], worker_id, result)
            log.info("job %s ready (%s chunks)", job["id"], result.get("chunks"))
        except Exception as e:
            log.exception("job %s attempt %d failed", job["id"], job["attempts"])
            self._fail(job, worker_id, str(e))
