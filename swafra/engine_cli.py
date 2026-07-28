"""swafra engine CLI — direct access to engine functions for skill/script use.

Usage:
    python3 -m swafra.engine_cli get_context "query"
    python3 -m swafra.engine_cli add "text" "title"
    python3 -m swafra.engine_cli search "query" [k]
    python3 -m swafra.engine_cli walk "chunk_id" [hops] [k]
    python3 -m swafra.engine_cli list
    python3 -m swafra.engine_cli delete "source_id"

All output is JSON to stdout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure engine is importable
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.graph import (
    add_knowledge,
    search_knowledge,
    graph_walk,
    get_context,
    list_sources,
    delete_source,
)


def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "No command. Use: get_context, add, search, walk, list, delete"}))
        sys.exit(1)

    cmd = args[0]

    try:
        if cmd == "get_context":
            query = args[1] if len(args) > 1 else ""
            k = int(args[2]) if len(args) > 2 else 5
            result = get_context(query, k=k)
            print(json.dumps(result, default=str))

        elif cmd == "add":
            text = args[1] if len(args) > 1 else ""
            title = args[2] if len(args) > 2 else "untitled"
            if not text:
                print(json.dumps({"error": "No text provided"}))
                sys.exit(1)
            result = add_knowledge(text, title)
            print(json.dumps(result, default=str))

        elif cmd == "search":
            query = args[1] if len(args) > 1 else ""
            k = int(args[2]) if len(args) > 2 else 8
            result = search_knowledge(query, k=k)
            print(json.dumps(result, default=str))

        elif cmd == "walk":
            chunk_id = args[1] if len(args) > 1 else ""
            hops = int(args[2]) if len(args) > 2 else 2
            k = int(args[3]) if len(args) > 3 else 10
            result = graph_walk(chunk_id, hops=hops, k=k)
            print(json.dumps(result, default=str))

        elif cmd == "list":
            result = list_sources()
            print(json.dumps(result, default=str))

        elif cmd == "delete":
            source_id = args[1] if len(args) > 1 else ""
            if not source_id:
                print(json.dumps({"error": "No source_id provided"}))
                sys.exit(1)
            result = delete_source(source_id)
            print(json.dumps(result, default=str))

        else:
            print(json.dumps({"error": f"Unknown command: {cmd}. Use: get_context, add, search, walk, list, delete"}))
            sys.exit(1)

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
