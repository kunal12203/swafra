"""scimap engine — Leiden-chunked, graph-linked semantic memory."""
from engine.graph import (
    add_knowledge,
    search_knowledge,
    graph_walk,
    get_context,
    list_sources,
    delete_source,
)
from engine.facts import (
    get_active_facts,
    get_fact_history,
    invalidate_fact,
)
from engine.rpc import main

__all__ = [
    "add_knowledge",
    "search_knowledge",
    "graph_walk",
    "get_context",
    "list_sources",
    "delete_source",
    "get_active_facts",
    "get_fact_history",
    "invalidate_fact",
    "main",
]
