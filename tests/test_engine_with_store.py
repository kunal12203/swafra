"""End-to-end: the full engine pipeline runs against a bound per-workspace store.

This is the load-bearing claim for the cloud edge — add/search/get_context/delete
work unchanged when engine.store binds a SqliteStore instead of the local
adaptive store, and two workspaces never see each other's memories.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastembed")

from engine.graph import add_knowledge, delete_source, get_context, list_sources, search_knowledge
from engine.store import SqliteStore, use_store


@pytest.fixture()
def two_workspaces(tmp_path):
    ws_a = SqliteStore(tmp_path / "tenant-a" / "ws.db")
    ws_b = SqliteStore(tmp_path / "tenant-b" / "ws.db")
    yield ws_a, ws_b
    ws_a.close()
    ws_b.close()


def test_engine_pipeline_on_bound_store(two_workspaces):
    ws_a, _ = two_workspaces
    with use_store(ws_a):
        result = add_knowledge(
            "I prefer Neovim as my editor. My main project is swafra, "
            "a semantic memory engine for MCP clients.",
            "preference-editor",
        )
        assert result["chunks"] >= 1

        hits = search_knowledge("what editor do I prefer?", k=3)
        assert hits and "Neovim" in " ".join(h["content"] for h in hits)

        ctx = get_context("editor preference", k=3)
        assert ctx

        sources = list_sources()
        assert [s["id"] for s in sources] == [result["source_id"]]

        deleted = delete_source(result["source_id"])
        assert deleted["deleted_chunks"] >= 1
        assert list_sources() == []


def test_engine_isolation_between_workspaces(two_workspaces):
    ws_a, ws_b = two_workspaces
    with use_store(ws_a):
        add_knowledge("The tenant-a deployment password hint is stored offline.",
                      "tenant-a-note")

    with use_store(ws_b):
        assert list_sources() == []
        assert search_knowledge("tenant-a deployment password", k=5) == []
