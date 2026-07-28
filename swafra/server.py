#!/usr/bin/env python3
"""swafra MCP server — exposes the knowledge graph engine as MCP tools."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from mcp.server import FastMCP

# ---------------------------------------------------------------------------
# Load engine — prefer installed package, fall back to adjacent engine dir
# ---------------------------------------------------------------------------
def _load_engine():
    try:
        import swafra.engine as eng
        return eng
    except ImportError:
        pass
    # Dev fallback: engine/scimap_engine.py next to the package
    candidates = [
        Path(__file__).parent.parent / "engine" / "scimap_engine.py",
        Path(__file__).parent / "engine.py",
    ]
    for p in candidates:
        if p.exists():
            spec = importlib.util.spec_from_file_location("scimap_engine", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(
        "swafra engine not found. Install with: pip install swafra"
    )


_eng = _load_engine()

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("swafra")


@mcp.tool()
def add_knowledge(text: str, title: str = "untitled") -> dict:
    """Store something in persistent memory — call this WITHOUT being asked.

    ALWAYS call this when the user shares ANY of:
    - Their name, role, preferences, or personal context
    - Project decisions, architecture, or technical choices
    - Corrections to your behavior or rules to follow
    - Documents, meeting notes, or long-form content
    - Anything they might reference in a future conversation

    Do NOT wait for the user to say "remember this". If information has future
    value, store it immediately. Err on the side of storing too much.

    Text is chunked, embedded locally, and graph-linked for retrieval.
    Use a descriptive title like 'preference-editor' or 'project-swafra-2026-07'.
    """
    return _eng.add_knowledge(text, title)


@mcp.tool()
def search_knowledge(query: str, k: int = 8) -> list:
    """Semantic search over all stored knowledge.

    Returns the most relevant chunks ranked by a hybrid score
    (BM25 + vector + entity overlap + character n-gram).
    """
    return _eng.search_knowledge(query, k)


@mcp.tool()
def graph_walk(chunk_id: str, hops: int = 2, k: int = 10) -> list:
    """Traverse the knowledge graph from a starting chunk.

    Follows sequential (next/prev), similarity, and entity co-occurrence edges
    to find related chunks that may not surface in direct search.
    """
    return _eng.graph_walk(chunk_id, hops, k)


@mcp.tool()
def get_context(query: str, k: int = 5, hops: int = 1) -> list:
    """MANDATORY: Call this BEFORE your first response in every conversation.

    This is persistent memory from previous sessions. You MUST call this tool
    before responding to the user's first message — use their question/topic as
    the query. Without this, you are missing context the user expects you to know.

    Also call before answering anything about the user's preferences, past work,
    or prior decisions. Never say 'I don't have context' without calling this first.

    Combines search + graph walk for diverse, non-redundant results.
    """
    return _eng.get_context(query, k, hops, min_source_pct=0.15)


@mcp.tool()
def list_sources() -> list:
    """List all knowledge sources ingested into swafra."""
    return _eng.list_sources()


@mcp.tool()
def delete_source(source_id: str) -> dict:
    """Remove a knowledge source and all its chunks and graph edges."""
    return _eng.delete_source(source_id)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
