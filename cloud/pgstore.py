"""PostgresStore — MemoryStore adapter for shared Postgres with RLS (ADR-0002).

Isolation is defense in depth:
  1. RLS policies (cloud/schema.sql) — the database refuses cross-tenant rows
     even if application SQL is buggy. Fail-closed when context is unset.
  2. Every statement here still filters tenant_id/workspace_id explicitly.

Tenant context is bound with set_config(..., is_local=>true) — transaction
scoped, parameterizable (unlike SET LOCAL), and therefore safe with pooled
connections: the setting dies with the transaction (AWS RLS pattern).

Ops:
    python -m cloud.pgstore init                 # apply schema (owner DSN)
    python -m cloud.pgstore grant --role NAME    # grant app role, verify no BYPASSRLS
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

from psycopg_pool import ConnectionPool

from engine.storage import _decode_vec, _encode_vec  # same codec as SQLite tier

_CHUNK_COLS = ("id, source_id, source_title, content, embedding, token_count, "
               "chunk_index, community_id, entities, dates, preferences, "
               "type, span, created_at, superseded_by")


def create_pool(database_url: str, max_size: int = 10) -> ConnectionPool:
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        kwargs={"options": "-c search_path=swafra"},
        open=True,
    )


class PostgresStore:
    """One instance per (tenant, workspace); all instances share the pool."""

    def __init__(self, pool: ConnectionPool, tenant_id: str, workspace_id: str):
        self._pool = pool
        self._tenant = tenant_id
        self._workspace = workspace_id

    def close(self) -> None:  # pool is process-owned; nothing per-instance
        pass

    @contextmanager
    def _tx(self):
        """One transaction with tenant context bound for its duration."""
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT set_config('app.tenant_id', %s, true),"
                "       set_config('app.workspace_id', %s, true)",
                (self._tenant, self._workspace))
            yield conn

    # -- reads ---------------------------------------------------------------

    def load_active_chunks(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT {_CHUNK_COLS} FROM chunks"
                " WHERE tenant_id = %s AND workspace_id = %s"
                " AND superseded_by IS NULL",
                (self._tenant, self._workspace)).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def load_all_chunks(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT {_CHUNK_COLS} FROM chunks"
                " WHERE tenant_id = %s AND workspace_id = %s",
                (self._tenant, self._workspace)).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def load_edges(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT source_id, from_id, to_id, type, weight FROM edges"
                " WHERE tenant_id = %s AND workspace_id = %s",
                (self._tenant, self._workspace)).fetchall()
        return [{"source_id": r[0], "from": r[1], "to": r[2],
                 "type": r[3], "weight": r[4]} for r in rows]

    def load_sources(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, title, chunks FROM sources"
                " WHERE tenant_id = %s AND workspace_id = %s",
                (self._tenant, self._workspace)).fetchall()
        return [{"id": r[0], "title": r[1], "chunks": r[2]} for r in rows]

    def load_facts(self) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT data FROM facts_data"
                " WHERE tenant_id = %s AND workspace_id = %s",
                (self._tenant, self._workspace)).fetchall()
        return [r[0] for r in rows]

    # -- writes (each method = one transaction, matching SQLite-tier atomicity)

    def save_source_chunks(self, new_chunks: list[dict], source_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM chunks WHERE tenant_id = %s AND workspace_id = %s"
                " AND source_id = %s",
                (self._tenant, self._workspace, source_id))
            conn.cursor().executemany(
                f"INSERT INTO chunks (tenant_id, workspace_id, {_CHUNK_COLS})"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s)"
                " ON CONFLICT (tenant_id, workspace_id, id) DO NOTHING",
                [(self._tenant, self._workspace,
                  c["id"], c["source_id"], c.get("source_title", ""), c["content"],
                  _encode_vec(c["embedding"]) if c.get("embedding") else None,
                  c.get("token_count", 0), c.get("chunk_index", 0),
                  c.get("community_id", 0),
                  json.dumps(c.get("entities") or []),
                  json.dumps(c.get("dates") or []),
                  json.dumps(c.get("preferences") or []),
                  c.get("type", "unknown"), json.dumps(c.get("span") or []),
                  c.get("created_at", time.time()), c.get("superseded_by"))
                 for c in new_chunks])

    def save_source_edges(self, source_edges: list[dict], cross_edges: list[dict],
                          source_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM edges WHERE tenant_id = %s AND workspace_id = %s"
                " AND source_id = %s",
                (self._tenant, self._workspace, source_id))
            conn.cursor().executemany(
                "INSERT INTO edges (tenant_id, workspace_id, source_id, from_id,"
                " to_id, type, weight) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(self._tenant, self._workspace, e.get("source_id"),
                  e["from"], e["to"], e["type"], e["weight"])
                 for e in source_edges + cross_edges])

    def save_source_record(self, source_id: str, title: str, chunk_count: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sources (tenant_id, workspace_id, id, title, chunks)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (tenant_id, workspace_id, id)"
                " DO UPDATE SET title = EXCLUDED.title, chunks = EXCLUDED.chunks",
                (self._tenant, self._workspace, source_id, title, chunk_count))

    def delete_source(self, source_id: str) -> int:
        with self._tx() as conn:
            deleted = conn.execute(
                "DELETE FROM chunks WHERE tenant_id = %s AND workspace_id = %s"
                " AND source_id = %s",
                (self._tenant, self._workspace, source_id)).rowcount
            conn.execute(
                "DELETE FROM edges WHERE tenant_id = %s AND workspace_id = %s"
                " AND source_id = %s",
                (self._tenant, self._workspace, source_id))
            conn.execute(
                "DELETE FROM sources WHERE tenant_id = %s AND workspace_id = %s"
                " AND id = %s",
                (self._tenant, self._workspace, source_id))
        return deleted

    def supersede_chunk(self, chunk_id: str, superseded_by: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE chunks SET superseded_by = %s"
                " WHERE tenant_id = %s AND workspace_id = %s AND id = %s",
                (superseded_by, self._tenant, self._workspace, chunk_id))

    def save_facts(self, facts: list[dict]) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM facts_data WHERE tenant_id = %s AND workspace_id = %s",
                (self._tenant, self._workspace))
            conn.cursor().executemany(
                "INSERT INTO facts_data (tenant_id, workspace_id, id, chunk_id,"
                " source_id, data) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (tenant_id, workspace_id, id) DO NOTHING",
                [(self._tenant, self._workspace, f.get("id", ""), f.get("chunk_id"),
                  f.get("source_id"), json.dumps(f, default=str)) for f in facts])

    @staticmethod
    def _row_to_chunk(r) -> dict:
        return {
            "id": r[0], "source_id": r[1], "source_title": r[2], "content": r[3],
            "embedding": _decode_vec(bytes(r[4])) if r[4] else [],
            "token_count": r[5], "chunk_index": r[6], "community_id": r[7],
            "entities": r[8], "dates": r[9], "preferences": r[10],
            "type": r[11], "span": r[12], "created_at": r[13],
            "superseded_by": r[14],
        }


# ---------------------------------------------------------------------------
# Ops CLI — schema init (owner DSN) and app-role grants
# ---------------------------------------------------------------------------
def _main() -> None:
    import argparse

    import psycopg

    from cloud.config import get_config

    parser = argparse.ArgumentParser(prog="cloud.pgstore")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="apply cloud/schema.sql (run with owner DSN)")
    init.add_argument("--dsn", default=None, help="override SWAFRA_CLOUD_DATABASE_URL")
    grant = sub.add_parser("grant", help="grant DML to the app role and verify it cannot bypass RLS")
    grant.add_argument("--role", required=True)
    grant.add_argument("--dsn", default=None)
    args = parser.parse_args()

    dsn = args.dsn or get_config().database_url
    if not dsn:
        raise SystemExit("no DSN: pass --dsn or set SWAFRA_CLOUD_DATABASE_URL")

    with psycopg.connect(dsn) as conn:
        if args.cmd == "init":
            conn.execute((Path(__file__).parent / "schema.sql").read_text())
            print("schema applied")
        elif args.cmd == "grant":
            role = args.role
            if not role.replace("_", "").isalnum():
                raise SystemExit(f"suspicious role name: {role!r}")
            conn.execute(f'GRANT USAGE ON SCHEMA swafra TO "{role}"')
            conn.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA"
                f' swafra TO "{role}"')
            conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA swafra TO "{role}"')
            row = conn.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
                (role,)).fetchone()
            if row is None:
                raise SystemExit(f"role {role} does not exist — create it first")
            if row[0] or row[1]:
                raise SystemExit(
                    f"REFUSING: role {role} is superuser or has BYPASSRLS — "
                    "RLS would be silently disabled (ADR-0002)")
            print(f"granted; verified {role} cannot bypass RLS")


if __name__ == "__main__":
    _main()
