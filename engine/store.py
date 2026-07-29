"""MemoryStore port — the single persistence seam for graph.py / facts.py.

The protocol is the CRUD surface the engine actually uses; how a backend
persists (whole-file JSON, SQLite rows, Postgres+RLS) is adapter-private.

Adapters:
  - AdaptiveLocalStore: today's local behavior (JSON tier 1 → auto-migrated
    module-global SQLite tier 2). The default; local users see zero change.
  - SqliteStore(path): an instance-owned SQLite file. Cloud edges create one
    per workspace so tenants never share a database handle or file.
  - PostgresStore (cloud/pgstore.py): shared Postgres, isolation via RLS.

Binding:
  - get_store() returns the ContextVar-bound store, falling back to the
    local adaptive store. Local code paths never set the ContextVar.
  - use_store(store) binds a store for the current (async) context only,
    so concurrent requests for different workspaces cannot bleed into
    each other the way a module global would.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from engine import storage


@runtime_checkable
class MemoryStore(Protocol):
    """Persistence surface actually used by engine.graph / engine.facts."""

    def load_active_chunks(self) -> list[dict]: ...
    def load_all_chunks(self) -> list[dict]: ...
    def load_edges(self) -> list[dict]: ...
    def load_sources(self) -> list[dict]: ...
    def save_source_chunks(self, new_chunks: list[dict], source_id: str) -> None: ...
    def save_source_edges(self, source_edges: list[dict], cross_edges: list[dict],
                          source_id: str) -> None: ...
    def save_source_record(self, source_id: str, title: str, chunk_count: int) -> None: ...
    def delete_source(self, source_id: str) -> int: ...
    def supersede_chunk(self, chunk_id: str, superseded_by: str) -> None: ...
    def load_facts(self) -> list[dict]: ...
    def save_facts(self, facts: list[dict]) -> None: ...


# ---------------------------------------------------------------------------
# Adapter 1: local adaptive JSON → SQLite (existing behavior)
# ---------------------------------------------------------------------------
class AdaptiveLocalStore:
    """Delegates to engine.storage module state — JSON tier or global SQLite.

    The JSON tier persists whole files, so its writes are load→modify→save
    inside the adapter. That tier is capped (~5k chunks) by the automatic
    SQLite migration, so the extra read per write is bounded and local-only.
    """

    @staticmethod
    def _sqlite() -> bool:
        return storage.get_backend() == "sqlite"

    def load_active_chunks(self) -> list[dict]:
        if self._sqlite():
            return storage.db_load_active_chunks()
        chunks = storage.load_json(storage.CHUNKS_FILE) or []
        return [c for c in chunks if not c.get("superseded_by")]

    def load_all_chunks(self) -> list[dict]:
        if self._sqlite():
            return storage.db_load_chunks()
        return storage.load_json(storage.CHUNKS_FILE) or []

    def load_edges(self) -> list[dict]:
        if self._sqlite():
            return storage.db_load_edges()
        return storage.load_json(storage.EDGES_FILE) or []

    def load_sources(self) -> list[dict]:
        if self._sqlite():
            return storage.db_load_sources()
        return storage.load_json(storage.SOURCES_FILE) or []

    def save_source_chunks(self, new_chunks: list[dict], source_id: str) -> None:
        if self._sqlite():
            storage.db_save_chunks(new_chunks, source_id)
        else:
            chunks = storage.load_json(storage.CHUNKS_FILE) or []
            other = [c for c in chunks if c.get("source_id") != source_id]
            storage.save_json(storage.CHUNKS_FILE, other + new_chunks)

    def save_source_edges(self, source_edges: list[dict], cross_edges: list[dict],
                          source_id: str) -> None:
        if self._sqlite():
            storage.db_save_edges(source_edges, source_id)
            if cross_edges:
                storage.db_add_edges(cross_edges)
        else:
            edges = storage.load_json(storage.EDGES_FILE) or []
            other = [e for e in edges if e.get("source_id") != source_id]
            storage.save_json(storage.EDGES_FILE, other + source_edges + cross_edges)

    def save_source_record(self, source_id: str, title: str, chunk_count: int) -> None:
        if self._sqlite():
            storage.db_save_source(source_id, title, chunk_count)
        else:
            sources = storage.load_json(storage.SOURCES_FILE) or []
            others = [s for s in sources if s.get("id") != source_id]
            storage.save_json(storage.SOURCES_FILE,
                              others + [{"id": source_id, "title": title, "chunks": chunk_count}])

    def delete_source(self, source_id: str) -> int:
        if self._sqlite():
            return storage.db_delete_source(source_id)
        chunks = storage.load_json(storage.CHUNKS_FILE) or []
        edges = storage.load_json(storage.EDGES_FILE) or []
        sources = storage.load_json(storage.SOURCES_FILE) or []
        remaining = [c for c in chunks if c.get("source_id") != source_id]
        storage.save_json(storage.CHUNKS_FILE, remaining)
        storage.save_json(storage.EDGES_FILE,
                          [e for e in edges if e.get("source_id") != source_id])
        storage.save_json(storage.SOURCES_FILE,
                          [s for s in sources if s.get("id") != source_id])
        return len(chunks) - len(remaining)

    def supersede_chunk(self, chunk_id: str, superseded_by: str) -> None:
        if self._sqlite():
            storage.db_supersede_chunk(chunk_id, superseded_by)
        else:
            chunks = storage.load_json(storage.CHUNKS_FILE) or []
            for c in chunks:
                if c.get("id") == chunk_id:
                    c["superseded_by"] = superseded_by
                    break
            storage.save_json(storage.CHUNKS_FILE, chunks)

    def load_facts(self) -> list[dict]:
        if self._sqlite():
            return storage.db_load_facts()
        return storage.load_json(storage.FACTS_FILE) or []

    def save_facts(self, facts: list[dict]) -> None:
        if self._sqlite():
            storage.db_save_facts(facts)
        else:
            storage.save_json(storage.FACTS_FILE, facts)


# ---------------------------------------------------------------------------
# Adapter 2: instance-owned SQLite file (one per cloud workspace)
# ---------------------------------------------------------------------------
class SqliteStore:
    """SQLite store bound to an explicit file, isolated from module globals.

    Reuses the exact SQL surface of engine.storage (schema, codecs, queries)
    by passing its own connection — no duplicated persistence logic.
    A lock serializes access: sqlite3 connections are not thread-safe and
    HTTP servers may call from multiple worker threads.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = storage.open_connection(self.db_path)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def load_active_chunks(self) -> list[dict]:
        with self._lock:
            return storage.db_load_active_chunks(conn=self._conn)

    def load_all_chunks(self) -> list[dict]:
        with self._lock:
            return storage.db_load_chunks(conn=self._conn)

    def load_edges(self) -> list[dict]:
        with self._lock:
            return storage.db_load_edges(conn=self._conn)

    def load_sources(self) -> list[dict]:
        with self._lock:
            return storage.db_load_sources(conn=self._conn)

    def save_source_chunks(self, new_chunks: list[dict], source_id: str) -> None:
        with self._lock:
            storage.db_save_chunks(new_chunks, source_id, conn=self._conn)

    def save_source_edges(self, source_edges: list[dict], cross_edges: list[dict],
                          source_id: str) -> None:
        with self._lock:
            storage.db_save_edges(source_edges, source_id, conn=self._conn)
            if cross_edges:
                storage.db_add_edges(cross_edges, conn=self._conn)

    def save_source_record(self, source_id: str, title: str, chunk_count: int) -> None:
        with self._lock:
            storage.db_save_source(source_id, title, chunk_count, conn=self._conn)

    def delete_source(self, source_id: str) -> int:
        with self._lock:
            return storage.db_delete_source(source_id, conn=self._conn)

    def supersede_chunk(self, chunk_id: str, superseded_by: str) -> None:
        with self._lock:
            storage.db_supersede_chunk(chunk_id, superseded_by, conn=self._conn)

    def load_facts(self) -> list[dict]:
        with self._lock:
            return storage.db_load_facts(conn=self._conn)

    def save_facts(self, facts: list[dict]) -> None:
        with self._lock:
            storage.db_save_facts(facts, conn=self._conn)


# ---------------------------------------------------------------------------
# Store binding — ContextVar so concurrent requests stay isolated
# ---------------------------------------------------------------------------
_active_store: ContextVar[MemoryStore | None] = ContextVar("swafra_active_store", default=None)
_local_store: AdaptiveLocalStore | None = None


def get_store() -> MemoryStore:
    """Current store: ContextVar binding if set, else the local adaptive store."""
    store = _active_store.get()
    if store is not None:
        return store
    global _local_store
    if _local_store is None:
        _local_store = AdaptiveLocalStore()
    return _local_store


@contextmanager
def use_store(store: MemoryStore) -> Iterator[MemoryStore]:
    """Bind a store for the current context (e.g. one authenticated request)."""
    token = _active_store.set(store)
    try:
        yield store
    finally:
        _active_store.reset(token)
