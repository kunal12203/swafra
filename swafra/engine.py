"""swafra.engine — re-exports from the engine package for pip-installed usage."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure engine package is importable when installed as swafra
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.graph import (  # noqa: E402
    add_knowledge,
    search_knowledge,
    graph_walk,
    get_context,
    list_sources,
    delete_source,
)

__all__ = [
    "add_knowledge",
    "search_knowledge",
    "graph_walk",
    "get_context",
    "list_sources",
    "delete_source",
]
