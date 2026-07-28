"""swafra — Leiden-chunked, graph-linked semantic memory.

SDK usage:
    import swafra
    swafra.add("I prefer dark mode and VS Code", title="prefs")
    swafra.search("editor")
    swafra.context("what editor do I prefer?")

    from swafra import Memory
    m = Memory()
    m.add("React is my frontend framework", title="tech")
"""
from __future__ import annotations

__version__ = "0.3.1"

from swafra.engine import (  # noqa: E402
    add_knowledge,
    search_knowledge,
    graph_walk,
    get_context,
    list_sources,
    delete_source,
)

__all__ = [
    "__version__",
    "add",
    "search",
    "context",
    "sources",
    "delete",
    "walk",
    "Memory",
    # full names also available
    "add_knowledge",
    "search_knowledge",
    "graph_walk",
    "get_context",
    "list_sources",
    "delete_source",
]


def add(text: str, title: str = "untitled") -> dict:
    """Store text — chunked, embedded, and graph-linked."""
    return add_knowledge(text, source_title=title)


def search(query: str, k: int = 8) -> list[dict]:
    """Find relevant chunks by natural language query."""
    return search_knowledge(query, k=k)


def context(query: str, k: int = 5, hops: int = 1) -> list[dict]:
    """Search + graph walk combined — recommended for most use cases."""
    return get_context(query, k=k, hops=hops)


def sources() -> list[dict]:
    """List all stored sources."""
    return list_sources()


def delete(source_id: str) -> dict:
    """Remove a source and all its chunks/edges."""
    return delete_source(source_id)


def walk(chunk_id: str, hops: int = 2, k: int = 10) -> list[dict]:
    """Explore connected chunks from a starting chunk."""
    return graph_walk(chunk_id, hops=hops, k=k)


class Memory:
    """Namespace-style SDK — identical to module-level functions.

    from swafra import Memory
    m = Memory()
    m.add("text", title="my-note")
    """

    def add(self, text: str, title: str = "untitled") -> dict:
        return add(text, title=title)

    def search(self, query: str, k: int = 8) -> list[dict]:
        return search(query, k=k)

    def context(self, query: str, k: int = 5, hops: int = 1) -> list[dict]:
        return context(query, k=k, hops=hops)

    def sources(self) -> list[dict]:
        return sources()

    def delete(self, source_id: str) -> dict:
        return delete(source_id)

    def walk(self, chunk_id: str, hops: int = 2, k: int = 10) -> list[dict]:
        return walk(chunk_id, hops=hops, k=k)
