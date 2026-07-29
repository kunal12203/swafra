"""M4 canaries — PostgresStore + RLS against a real Postgres (docker).

The critical tests are the raw-SQL ones: they prove the DATABASE blocks
cross-tenant access even when application code is taken out of the picture
entirely (ADR-0002: RLS is the guarantee, app filters are defense in depth).

Skipped automatically when docker is unavailable. Set SWAFRA_TEST_PG_KEEP=1
to keep the container for inspection.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.test_store import make_chunk  # noqa: E402

APP_ROLE, APP_PASSWORD = "swafra_app", "app-secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg_dsn():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    port = _free_port()
    name = f"swafra-pg-test-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "POSTGRES_PASSWORD=admin", "-p", f"127.0.0.1:{port}:5432",
         "postgres:16-alpine"],
        capture_output=True, text=True)
    if run.returncode != 0:
        pytest.skip(f"cannot start postgres container: {run.stderr.strip()}")

    admin_dsn = f"postgresql://postgres:admin@127.0.0.1:{port}/postgres"
    try:
        deadline = time.time() + 60
        while True:
            try:
                with psycopg.connect(admin_dsn, connect_timeout=2):
                    break
            except psycopg.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)

        # Owner applies schema; app role is a plain login role (no BYPASSRLS).
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE")
        env = dict(os.environ, SWAFRA_CLOUD_DATA_DIR="/tmp/swafra-pg-test")
        for cmd in (["init"], ["grant", "--role", APP_ROLE]):
            out = subprocess.run(
                ["python3", "-m", "cloud.pgstore", *cmd, "--dsn", admin_dsn],
                capture_output=True, text=True, env=env,
                cwd=os.path.dirname(os.path.dirname(__file__)))
            assert out.returncode == 0, out.stderr

        yield f"postgresql://{APP_ROLE}:{APP_PASSWORD}@127.0.0.1:{port}/postgres"
    finally:
        if not os.environ.get("SWAFRA_TEST_PG_KEEP"):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture()
def pool(pg_dsn):
    from cloud.pgstore import create_pool
    p = create_pool(pg_dsn, max_size=4)
    yield p
    p.close()


@pytest.fixture(autouse=True)
def clean_tables(pg_dsn):
    yield
    with psycopg.connect(pg_dsn) as conn:
        # app role sees only its own rows; cleanup per tenant used in tests
        for tenant in ("tenant-a", "tenant-b"):
            conn.execute("SELECT set_config('app.tenant_id', %s, true),"
                         " set_config('app.workspace_id', 'default', true)", (tenant,))
            for table in ("chunks", "edges", "sources", "facts_data"):
                conn.execute(f"DELETE FROM swafra.{table}")
            conn.commit()


def _store(pool, tenant):
    from cloud.pgstore import PostgresStore
    return PostgresStore(pool, tenant, "default")


# ---------------------------------------------------------------------------
# Port fidelity
# ---------------------------------------------------------------------------

def test_postgres_store_roundtrip(pool):
    store = _store(pool, "tenant-a")
    store.save_source_chunks([make_chunk("c1", "s1", "hello pg")], "s1")
    store.save_source_edges(
        [{"source_id": "s1", "from": "c1", "to": "c1", "type": "next", "weight": 1.0}],
        [{"source_id": None, "from": "c1", "to": "c1", "type": "cross_session", "weight": 0.5}],
        "s1")
    store.save_source_record("s1", "title", 1)
    store.save_facts([{"id": "f1", "chunk_id": "c1", "source_id": "s1", "value": "x"}])

    chunks = store.load_active_chunks()
    assert len(chunks) == 1
    assert chunks[0]["content"] == "hello pg"
    assert chunks[0]["embedding"] == pytest.approx([0.1, 0.2, 0.3])
    assert chunks[0]["entities"] == ["alpha"]
    assert len(store.load_edges()) == 2
    assert store.load_sources() == [{"id": "s1", "title": "title", "chunks": 1}]
    assert store.load_facts()[0]["id"] == "f1"

    store.supersede_chunk("c1", "c2")
    assert store.load_active_chunks() == []
    assert store.delete_source("s1") == 1
    assert store.load_all_chunks() == []


# ---------------------------------------------------------------------------
# Isolation canaries
# ---------------------------------------------------------------------------

def test_isolation_through_port(pool):
    _store(pool, "tenant-a").save_source_chunks(
        [make_chunk("c1", "s1", "tenant-a secret")], "s1")
    store_b = _store(pool, "tenant-b")
    assert store_b.load_all_chunks() == []
    assert store_b.load_sources() == []


def test_rls_blocks_raw_sql_without_app_filters(pg_dsn, pool):
    """The database itself must refuse cross-tenant rows — no WHERE clauses."""
    _store(pool, "tenant-a").save_source_chunks(
        [make_chunk("c1", "s1", "tenant-a secret")], "s1")

    with psycopg.connect(pg_dsn) as conn:
        # No tenant context bound → fail closed: zero rows, not an error.
        assert conn.execute("SELECT * FROM swafra.chunks").fetchall() == []

        # Bound to tenant-b → tenant-a's rows are invisible to unfiltered SQL.
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-b', true),"
                     " set_config('app.workspace_id', 'default', true)")
        assert conn.execute("SELECT * FROM swafra.chunks").fetchall() == []

        # Bound to tenant-a in a fresh transaction → rows appear.
        conn.commit()
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-a', true),"
                     " set_config('app.workspace_id', 'default', true)")
        rows = conn.execute("SELECT content FROM swafra.chunks").fetchall()
        assert rows == [("tenant-a secret",)]


def test_rls_blocks_cross_tenant_writes(pg_dsn):
    """WITH CHECK (implied by USING): cannot insert rows for another tenant."""
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-b', true),"
                     " set_config('app.workspace_id', 'default', true)")
        with pytest.raises(psycopg.errors.Error):
            conn.execute(
                "INSERT INTO swafra.sources (tenant_id, workspace_id, id, title, chunks)"
                " VALUES ('tenant-a', 'default', 'sx', 'forged', 0)")


def test_tenant_context_dies_with_transaction(pg_dsn):
    """set_config(..., true) must not leak across transactions on a pooled conn."""
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
        conn.commit()  # transaction ends — context must be gone
        val = conn.execute(
            "SELECT current_setting('app.tenant_id', true)").fetchone()[0]
        assert val in (None, "")


# ---------------------------------------------------------------------------
# Full engine + job queue on Postgres (M4 + M5 together)
# ---------------------------------------------------------------------------

def test_engine_and_jobs_on_postgres(pg_dsn, tmp_path):
    pytest.importorskip("fastembed")
    from engine.graph import search_knowledge
    from engine.store import use_store

    from cloud.config import CloudConfig
    from cloud.jobs import JobQueue
    from cloud.tenancy import StoreRegistry, WorkspaceRef

    cfg = CloudConfig(data_dir=tmp_path, database_url=pg_dsn)
    registry = StoreRegistry(cfg)
    queue = JobQueue(cfg, registry)
    token = WorkspaceRef("tenant-a", "default")

    job = queue.submit(token, "infra-notes",
                       "Our staging cluster runs Kubernetes 1.31 on Graviton nodes.")
    queue.start_workers(1)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            status = queue.get(token, job["job_id"])
            if status["status"] in ("ready", "failed"):
                break
            time.sleep(0.3)
        assert status["status"] == "ready", status

        with use_store(registry.get(token)):
            hits = search_knowledge("what does staging run on?", k=3)
        assert hits and "Kubernetes" in " ".join(h["content"] for h in hits)

        # Cross-tenant read of the same Postgres queue and store: nothing.
        other = WorkspaceRef("tenant-b", "default")
        assert queue.get(other, job["job_id"]) is None
        with use_store(registry.get(other)):
            assert search_knowledge("Kubernetes staging", k=3) == []
    finally:
        queue.stop_workers()
        registry.close_all()
