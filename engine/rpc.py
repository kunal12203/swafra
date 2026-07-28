"""JSON-line RPC protocol — stdin/stdout communication with the TS bridge."""
from __future__ import annotations

import json
import logging
import sys

from engine.deps import ensure_deps
from engine.embedding import init_embedder
from engine.facts import get_active_facts, get_fact_history, invalidate_fact
from engine.graph import (
    add_knowledge, delete_source, get_context,
    graph_walk, list_sources, search_knowledge,
)
from engine.storage import DATA_DIR

log = logging.getLogger("scimap.engine")

METHODS = {
    "add_knowledge": lambda p: add_knowledge(p["text"], p.get("title", "untitled")),
    "search": lambda p: search_knowledge(p["query"], p.get("k", 8)),
    "graph_walk": lambda p: graph_walk(p["chunk_id"], p.get("hops", 2), p.get("k", 10)),
    "get_context": lambda p: get_context(p["query"], p.get("k", 5), p.get("hops", 1), p.get("min_source_pct", 0.15)),
    "list_sources": lambda p: list_sources(),
    "delete_source": lambda p: delete_source(p["source_id"]),
    "get_active_facts": lambda p: get_active_facts(p.get("subject"), p.get("relation")),
    "get_fact_history": lambda p: get_fact_history(p.get("subject"), p.get("relation")),
    "invalidate_fact": lambda p: invalidate_fact(p["fact_id"]),
    "ping": lambda p: {"status": "ok"},
}


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    ensure_deps()
    log.info("scimap engine started (data_dir=%s)", DATA_DIR)
    init_embedder()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps({"id": None, "error": f"invalid JSON: {e}"}) + "\n")
            sys.stdout.flush()
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method not in METHODS:
            resp = {"id": req_id, "error": f"unknown method: {method}"}
        else:
            try:
                result = METHODS[method](params)
                resp = {"id": req_id, "result": result}
            except Exception as e:
                log.exception("method %s failed", method)
                resp = {"id": req_id, "error": str(e)}

        sys.stdout.write(json.dumps(resp, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
