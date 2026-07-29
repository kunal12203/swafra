"""MemoryStore port tests — adapter behavior, context binding, isolation."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from engine.store import AdaptiveLocalStore, SqliteStore, get_store, use_store

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_chunk(cid: str, source_id: str, content: str) -> dict:
    return {
        "id": cid,
        "source_id": source_id,
        "source_title": f"title-{source_id}",
        "content": content,
        "embedding": [0.1, 0.2, 0.3],
        "token_count": 3,
        "chunk_index": 0,
        "community_id": 0,
        "entities": ["alpha"],
        "dates": [],
        "preferences": [],
        "type": "statement",
        "span": [0, len(content)],
        "created_at": time.time(),
        "superseded_by": None,
    }


# ---------------------------------------------------------------------------
# SqliteStore — CRUD round-trip and per-file isolation
# ---------------------------------------------------------------------------

def test_sqlite_store_roundtrip(tmp_path):
    store = SqliteStore(tmp_path / "ws.db")
    chunk = make_chunk("c1", "s1", "hello world")
    store.save_source_chunks([chunk], "s1")
    store.save_source_edges(
        [{"source_id": "s1", "from": "c1", "to": "c1", "type": "next", "weight": 1.0}],
        [], "s1")
    store.save_source_record("s1", "title-s1", 1)
    store.save_facts([{"id": "f1", "chunk_id": "c1", "source_id": "s1", "value": "x"}])

    loaded = store.load_active_chunks()
    assert len(loaded) == 1
    assert loaded[0]["content"] == "hello world"
    assert loaded[0]["embedding"] == pytest.approx([0.1, 0.2, 0.3])
    assert store.load_sources() == [{"id": "s1", "title": "title-s1", "chunks": 1}]
    assert len(store.load_edges()) == 1
    assert store.load_facts()[0]["id"] == "f1"
    store.close()


def test_sqlite_store_supersede_and_delete(tmp_path):
    store = SqliteStore(tmp_path / "ws.db")
    store.save_source_chunks([make_chunk("c1", "s1", "old"), make_chunk("c2", "s1", "new")], "s1")

    store.supersede_chunk("c1", "c2")
    active = store.load_active_chunks()
    assert [c["id"] for c in active] == ["c2"]
    assert len(store.load_all_chunks()) == 2

    deleted = store.delete_source("s1")
    assert deleted == 2
    assert store.load_all_chunks() == []
    store.close()


def test_workspace_isolation(tmp_path):
    """Tenant canary: data written to workspace A must be invisible in B."""
    store_a = SqliteStore(tmp_path / "tenant-a" / "ws.db")
    store_b = SqliteStore(tmp_path / "tenant-b" / "ws.db")

    store_a.save_source_chunks([make_chunk("c1", "s1", "tenant-a secret")], "s1")
    store_a.save_facts([{"id": "f1", "chunk_id": "c1", "source_id": "s1", "value": "secret"}])

    assert store_b.load_all_chunks() == []
    assert store_b.load_facts() == []
    assert store_b.load_sources() == []

    # And the reverse: B's writes never appear in A.
    store_b.save_source_chunks([make_chunk("c9", "s9", "tenant-b data")], "s9")
    ids_in_a = {c["id"] for c in store_a.load_all_chunks()}
    assert ids_in_a == {"c1"}
    store_a.close()
    store_b.close()


# ---------------------------------------------------------------------------
# Context binding — get_store() / use_store()
# ---------------------------------------------------------------------------

def test_default_store_is_adaptive_local():
    assert isinstance(get_store(), AdaptiveLocalStore)


def test_use_store_binds_and_resets(tmp_path):
    store = SqliteStore(tmp_path / "ws.db")
    with use_store(store):
        assert get_store() is store
        inner = SqliteStore(tmp_path / "inner.db")
        with use_store(inner):
            assert get_store() is inner
        assert get_store() is store
    assert isinstance(get_store(), AdaptiveLocalStore)
    store.close()


def test_use_store_isolated_across_async_tasks(tmp_path):
    """Two concurrent requests bound to different workspaces must not bleed."""
    store_a = SqliteStore(tmp_path / "a.db")
    store_b = SqliteStore(tmp_path / "b.db")
    seen: dict[str, object] = {}

    async def request(name: str, store: SqliteStore):
        with use_store(store):
            await asyncio.sleep(0.01)  # force interleaving
            seen[name] = get_store()

    async def main():
        await asyncio.gather(request("a", store_a), request("b", store_b))

    asyncio.run(main())
    assert seen["a"] is store_a
    assert seen["b"] is store_b
    store_a.close()
    store_b.close()


# ---------------------------------------------------------------------------
# AdaptiveLocalStore JSON tier — subprocess so SCIMAP_DATA_DIR applies at import
# ---------------------------------------------------------------------------

_JSON_TIER_SCRIPT = """
import json
from engine.store import AdaptiveLocalStore
from engine import storage

assert storage.get_backend() == "json", storage.get_backend()
store = AdaptiveLocalStore()

chunk = {"id": "c1", "source_id": "s1", "source_title": "t", "content": "hi",
         "embedding": [0.1], "token_count": 1, "chunk_index": 0, "community_id": 0,
         "entities": [], "dates": [], "preferences": [], "type": "statement",
         "span": [0, 2], "created_at": 1.0, "superseded_by": None}

store.save_source_chunks([chunk], "s1")
store.save_source_record("s1", "t", 1)
store.save_facts([{"id": "f1", "chunk_id": "c1", "source_id": "s1"}])

assert [c["id"] for c in store.load_active_chunks()] == ["c1"]
assert store.load_sources() == [{"id": "s1", "title": "t", "chunks": 1}]
assert store.load_facts()[0]["id"] == "f1"

store.supersede_chunk("c1", "c2")
assert store.load_active_chunks() == []
assert len(store.load_all_chunks()) == 1

assert store.delete_source("s1") == 1
assert store.load_all_chunks() == []
print("JSON_TIER_OK")
"""


def test_adaptive_local_store_json_tier(tmp_path):
    env = dict(os.environ, SCIMAP_DATA_DIR=str(tmp_path / "data"))
    result = subprocess.run(
        [sys.executable, "-c", _JSON_TIER_SCRIPT],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "JSON_TIER_OK" in result.stdout
    # Data landed in the sandboxed dir, not ~/.scimap
    assert (tmp_path / "data" / "chunks.json").exists()
