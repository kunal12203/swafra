"""Job queue semantics — claim protocol, lease recovery, DLQ, quotas, dedup.

Runs on the SQLite backend (no ML deps, no network); the SQL is shared with
the Postgres backend except for the FOR UPDATE SKIP LOCKED clause.
"""
from __future__ import annotations

import time

import pytest

from cloud.config import CloudConfig
from cloud.jobs import JobQueue
from cloud.tenancy import StoreRegistry, WorkspaceRef


class Token(WorkspaceRef):
    pass


@pytest.fixture()
def queue(tmp_path):
    cfg = CloudConfig(data_dir=tmp_path, quota_max_active_jobs=3,
                      quota_max_text_bytes=1000, job_max_attempts=3,
                      job_lease_seconds=60.0)
    return JobQueue(cfg, StoreRegistry(cfg))


A = Token("tenant-a", "default")
B = Token("tenant-b", "default")


def test_submit_claim_complete(queue):
    job = queue.submit(A, "note", "some text")
    assert job["status"] == "pending"

    claimed = queue.claim("w1")
    assert claimed["id"] == job["job_id"]
    assert claimed["attempts"] == 1
    assert queue.claim("w2") is None  # nothing else runnable

    queue._complete(claimed["id"], "w1", {"chunks": 2})
    status = queue.get(A, job["job_id"])
    assert status["status"] == "ready"
    assert status["result"] == {"chunks": 2}


def test_dedup_and_tenant_scoped_reads(queue):
    job = queue.submit(A, "note", "same text")
    dup = queue.submit(A, "note", "same text")
    assert dup["deduplicated"] and dup["job_id"] == job["job_id"]

    # Same content from another tenant is NOT deduplicated across tenants.
    other = queue.submit(B, "note", "same text")
    assert other["deduplicated"] is False

    # And B cannot read A's job.
    assert queue.get(B, job["job_id"]) is None


def test_retry_backoff_then_dead_letter(queue):
    job = queue.submit(A, "note", "will fail")
    for attempt in (1, 2, 3):
        # make any backoff run_at due immediately
        queue._db.run("UPDATE jobs SET run_at = 0 WHERE id = ?", (job["job_id"],))
        claimed = queue.claim("w1")
        assert claimed is not None and claimed["attempts"] == attempt
        queue._fail(claimed, "w1", "boom")

    status = queue.get(A, job["job_id"])
    assert status["status"] == "failed"  # dead-letter: kept, not retried
    assert status["error"] == "boom"
    queue._db.run("UPDATE jobs SET run_at = 0 WHERE id = ?", (job["job_id"],))
    assert queue.claim("w1") is None


def test_completed_content_can_be_resubmitted(queue):
    """Dedup covers in-flight jobs only — after completion (or delete_source),
    identical content must be ingestable again (engine re-ingest is idempotent)."""
    job = queue.submit(A, "note", "re-ingest me")
    claimed = queue.claim("w1")
    queue._complete(claimed["id"], "w1", {"chunks": 1})

    again = queue.submit(A, "note", "re-ingest me")
    assert again["deduplicated"] is False
    assert again["job_id"] != job["job_id"]


def test_poison_job_is_dead_lettered_not_reclaimed_forever(queue):
    """A job that kills its worker (lease expires, _fail never runs) must hit
    the dead-letter state once attempts are exhausted — not loop forever."""
    job = queue.submit(A, "note", "poison")
    for _ in range(3):  # max_attempts = 3
        claimed = queue.claim("w1")
        assert claimed is not None
        queue._db.run("UPDATE jobs SET locked_until = ? WHERE id = ?",
                      (time.time() - 1, claimed["id"]))
    # Attempts exhausted with an expired lease: next claim reaps it and finds
    # no runnable work.
    assert queue.claim("w1") is None
    status = queue.get(A, job["job_id"])
    assert status["status"] == "failed"
    assert "worker died" in status["error"]


def test_expired_lease_is_reclaimed(queue):
    queue.submit(A, "note", "crash recovery")
    first = queue.claim("w1")
    assert first is not None

    # Worker w1 "crashes": lease expires, another worker claims the same job.
    queue._db.run("UPDATE jobs SET locked_until = ? WHERE id = ?",
                  (time.time() - 1, first["id"]))
    second = queue.claim("w2")
    assert second is not None and second["id"] == first["id"]
    assert second["attempts"] == 2

    # The zombie (w1) finishing late must not clobber w2's ownership.
    queue._complete(first["id"], "w1", {"chunks": 99})
    assert queue.get(A, first["id"])["status"] == "processing"

    queue._complete(second["id"], "w2", {"chunks": 1})
    assert queue.get(A, first["id"])["status"] == "ready"


def test_quota_text_size(queue):
    out = queue.submit(A, "big", "x" * 2000)
    assert out["status"] == "rejected"
    assert "bytes" in out["reason"]


def test_quota_active_jobs_per_tenant(queue):
    for i in range(3):
        assert queue.submit(A, f"n{i}", f"text {i}")["status"] == "pending"
    out = queue.submit(A, "n3", "text 3")
    assert out["status"] == "rejected"
    # Other tenants are unaffected (fairness, not a global cap).
    assert queue.submit(B, "n0", "text 0")["status"] == "pending"


def test_worker_processes_job_end_to_end(queue, monkeypatch):
    """Worker loop drives pending → processing → ready with a stub engine."""
    import engine.graph

    monkeypatch.setattr(engine.graph, "add_knowledge",
                        lambda text, title: {"chunks": 1, "source_id": "s"})
    job = queue.submit(A, "note", "hello worker")
    queue.start_workers(1)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if queue.get(A, job["job_id"])["status"] == "ready":
                break
            time.sleep(0.05)
        assert queue.get(A, job["job_id"])["status"] == "ready"
    finally:
        queue.stop_workers()
