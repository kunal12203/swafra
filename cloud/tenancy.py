"""Verified identity → isolated per-workspace store.

Backend selection (ADR-0002):
  - SWAFRA_CLOUD_DATABASE_URL set → shared Postgres pool, isolation via RLS
    (cloud/pgstore.py, cloud/schema.sql). PostgresStore instances are cheap
    views over one process-wide connection pool.
  - unset → silo-per-workspace SQLite files under
    <data_dir>/tenants/<tenant>/<workspace>/ws.db (dev / single node).

Both implement the same MemoryStore port, so tools and workers never care.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from typing import NamedTuple

from engine.store import MemoryStore, SqliteStore

from cloud.config import CloudConfig

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkspaceRef(NamedTuple):
    """The tenancy pair, always sourced from verified credentials or job rows."""
    tenant_id: str
    workspace_id: str


def safe_dir_name(identifier: str) -> str:
    """Map an identity-provider ID to a collision-free, filesystem-safe name.

    IDs that are already safe pass through (readable on disk); anything else
    (or anything path-traversal-shaped) becomes a hash — never sanitized in
    place, which could collide ("a/b" vs "a-b").
    """
    if _SAFE_ID.match(identifier) and ".." not in identifier:
        return identifier
    return "h-" + hashlib.sha256(identifier.encode()).hexdigest()[:24]


class StoreRegistry:
    """Hands out per-workspace stores; owns the shared Postgres pool if any."""

    def __init__(self, config: CloudConfig, max_open: int = 128):
        self._root = config.data_dir / "tenants"
        self._max_open = max_open
        self._stores: OrderedDict[tuple[str, str], MemoryStore] = OrderedDict()
        self._lock = threading.Lock()
        self.pool = None
        if config.database_url:
            from cloud.pgstore import create_pool
            self.pool = create_pool(config.database_url)

    def get(self, ref) -> MemoryStore:
        """`ref` is anything with tenant_id/workspace_id (token or WorkspaceRef)."""
        key = (ref.tenant_id, ref.workspace_id)
        with self._lock:
            store = self._stores.get(key)
            if store is not None:
                self._stores.move_to_end(key)
                return store
            store = self._create(key)
            self._stores[key] = store
            while len(self._stores) > self._max_open:
                # Drop the reference only — never close() here: an in-flight
                # request may still hold the evicted store. GC closes SQLite
                # connections once the last holder releases them.
                self._stores.popitem(last=False)
            return store

    def _create(self, key: tuple[str, str]) -> MemoryStore:
        tenant_id, workspace_id = key
        if self.pool is not None:
            from cloud.pgstore import PostgresStore
            return PostgresStore(self.pool, tenant_id, workspace_id)
        db_path = (self._root / safe_dir_name(tenant_id)
                   / safe_dir_name(workspace_id) / "ws.db")
        return SqliteStore(db_path)

    def close_all(self) -> None:
        with self._lock:
            for store in self._stores.values():
                store.close()
            self._stores.clear()
        if self.pool is not None:
            self.pool.close()
