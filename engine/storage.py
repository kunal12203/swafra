"""Persistent JSON storage for chunks, edges, and sources."""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("SCIMAP_DATA_DIR", os.path.expanduser("~/.scimap")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHUNKS_FILE = DATA_DIR / "chunks.json"
EDGES_FILE = DATA_DIR / "edges.json"
SOURCES_FILE = DATA_DIR / "sources.json"


def load_json(path: Path) -> list | dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, default=str)
