"""swafra CLI — `swafra` in terminal shows knowledge graph stats."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


DATA_DIR = Path(os.getenv("SCIMAP_DATA_DIR", os.path.expanduser("~/.scimap")))

CHUNKS_FILE = DATA_DIR / "chunks.json"
EDGES_FILE = DATA_DIR / "edges.json"
SOURCES_FILE = DATA_DIR / "sources.json"
FACTS_FILE = DATA_DIR / "facts.json"


def _load(path: Path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def _format_size(nbytes: int) -> str:
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    else:
        return f"{nbytes / (1024 * 1024):.1f} MB"


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def stats():
    """Print detailed knowledge graph statistics."""
    if not DATA_DIR.exists():
        print("  No data found. Start by adding knowledge through an MCP client.")
        print(f"  Data directory: {DATA_DIR}")
        return

    chunks = _load(CHUNKS_FILE)
    edges = _load(EDGES_FILE)
    sources = _load(SOURCES_FILE)
    facts = _load(FACTS_FILE)

    active_chunks = [c for c in chunks if not c.get("superseded_by")]
    superseded_chunks = [c for c in chunks if c.get("superseded_by")]

    # Edge type breakdown
    edge_types = Counter(e.get("type", "unknown") for e in edges)

    # Community stats
    communities = Counter(c.get("community_id") for c in active_chunks)

    # Entity stats
    all_entities = []
    for c in active_chunks:
        all_entities.extend(c.get("entities", []))
    entity_counts = Counter(all_entities)

    # Token stats
    total_tokens = sum(c.get("token_count", 0) for c in active_chunks)

    # Chunk type breakdown
    chunk_types = Counter(c.get("type", "unknown") for c in active_chunks)

    # Fact stats
    active_facts = [f for f in facts if f.get("valid_to") is None]
    superseded_facts = [f for f in facts if f.get("valid_to") is not None]

    # Cross-session edges
    cross_session = sum(1 for e in edges if e.get("type") == "cross_session")

    # Storage size
    total_size = sum(_file_size(p) for p in [CHUNKS_FILE, EDGES_FILE, SOURCES_FILE, FACTS_FILE])

    # Source breakdown
    source_chunks = defaultdict(int)
    for c in active_chunks:
        source_chunks[c.get("source_title", "unknown")] += 1

    # Print
    print()
    print("  \033[1;36m╭─────────────────────────────────────────╮\033[0m")
    print("  \033[1;36m│\033[0m       \033[1;37mswafra\033[0m knowledge graph stats       \033[1;36m│\033[0m")
    print("  \033[1;36m╰─────────────────────────────────────────╯\033[0m")
    print()

    # Overview
    print("  \033[1;33m⚡ Overview\033[0m")
    print(f"     Sources:          {len(sources)}")
    print(f"     Active chunks:    {len(active_chunks)}")
    print(f"     Superseded:       {len(superseded_chunks)}")
    print(f"     Edges:            {len(edges)}")
    print(f"     Total tokens:     {total_tokens:,}")
    print(f"     Storage:          {_format_size(total_size)}")
    print(f"     Data dir:         {DATA_DIR}")
    print()

    # Graph topology
    print("  \033[1;33m🔗 Graph Topology\033[0m")
    print(f"     Communities:      {len(communities)}")
    print(f"     Cross-session:    {cross_session} edges")
    if edge_types:
        print("     Edge types:")
        for etype, count in edge_types.most_common():
            print(f"       {etype:<15} {count}")
    print()

    # Facts
    print("  \033[1;33m📋 Facts\033[0m")
    print(f"     Active:           {len(active_facts)}")
    print(f"     Superseded:       {len(superseded_facts)}")
    print(f"     Total:            {len(facts)}")
    print()

    # Entities
    print("  \033[1;33m🏷️  Entities\033[0m")
    print(f"     Unique:           {len(entity_counts)}")
    if entity_counts:
        print("     Top entities:")
        for ent, count in entity_counts.most_common(10):
            print(f"       {ent:<20} ({count} chunks)")
    print()

    # Chunk types
    if chunk_types:
        print("  \033[1;33m📦 Chunk Types\033[0m")
        for ctype, count in chunk_types.most_common():
            print(f"     {ctype:<18} {count}")
        print()

    # Sources
    if sources:
        print("  \033[1;33m📚 Sources\033[0m")
        sorted_sources = sorted(source_chunks.items(), key=lambda x: x[1], reverse=True)
        for title, count in sorted_sources[:15]:
            display = title[:35] + "…" if len(title) > 35 else title
            print(f"     {display:<37} {count} chunks")
        if len(sorted_sources) > 15:
            print(f"     ... and {len(sorted_sources) - 15} more")
        print()


def main():
    args = sys.argv[1:]

    if not args or args[0] == "stats":
        print()
        print("  \033[1;37mswafra\033[0m v" + _get_version())
        print("  Leiden-chunked, graph-linked semantic memory")
        stats()
    elif args[0] == "serve":
        from swafra.server import main as serve_main
        serve_main()
    elif args[0] in ("-h", "--help", "help"):
        print()
        print("  \033[1;37mswafra\033[0m — semantic memory for AI")
        print()
        print("  Usage:")
        print("    swafra              Show knowledge graph stats")
        print("    swafra stats        Same as above")
        print("    swafra serve        Start the MCP server")
        print("    swafra help         Show this help")
        print()
    else:
        print(f"  Unknown command: {args[0]}")
        print("  Run `swafra help` for usage.")
        sys.exit(1)


def _get_version():
    try:
        from swafra import __version__
        return __version__
    except Exception:
        return "0.2.1"


if __name__ == "__main__":
    main()
