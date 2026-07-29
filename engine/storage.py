"""Adaptive persistent storage — JSON → SQLite → sqlite-vec.

Tier 1: JSON files (default, zero deps, works for 0-5k chunks)
Tier 2: SQLite (stdlib, auto-migrates when chunks exceed threshold)
Tier 3: sqlite-vec (optional, accelerates vector search if installed)

Data is always readable. Migration is automatic and one-way up.
Downgrade path: `swafra export` can dump SQLite back to JSON.
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import time
from pathlib import Path

DATA_DIR = Path(os.getenv("SCIMAP_DATA_DIR", os.path.expanduser("~/.scimap")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHUNKS_FILE = DATA_DIR / "chunks.json"
EDGES_FILE = DATA_DIR / "edges.json"
SOURCES_FILE = DATA_DIR / "sources.json"
FACTS_FILE = DATA_DIR / "facts.json"
DB_FILE = DATA_DIR / "swafra.db"

_MIGRATION_THRESHOLD = 5000

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
_backend: str | None = None


def _detect_backend() -> str:
    """Determine storage backend: 'sqlite' if DB exists or chunks exceed threshold, else 'json'."""
    global _backend
    if _backend is not None:
        return _backend

    if DB_FILE.exists():
        _backend = "sqlite"
        return _backend

    if CHUNKS_FILE.exists():
        try:
            size = CHUNKS_FILE.stat().st_size
            if size > 500_000:
                count = _count_json_items(CHUNKS_FILE)
                if count >= _MIGRATION_THRESHOLD:
                    _migrate_json_to_sqlite()
                    _backend = "sqlite"
                    return _backend
        except (OSError, json.JSONDecodeError):
            pass

    _backend = "json"
    return _backend


def _count_json_items(path: Path) -> int:
    with open(path) as f:
        data = json.load(f)
    return len(data) if isinstance(data, list) else 0


def get_backend() -> str:
    return _detect_backend()


# ---------------------------------------------------------------------------
# JSON backend (Tier 1)
# ---------------------------------------------------------------------------
def load_json(path: Path) -> list | dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, default=str)


# ---------------------------------------------------------------------------
# SQLite backend (Tier 2)
# ---------------------------------------------------------------------------
_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn

    _conn = sqlite3.connect(str(DB_FILE), timeout=10)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    _conn.row_factory = sqlite3.Row
    _init_schema(_conn)
    return _conn


def open_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a standalone SQLite connection with the swafra schema and pragmas.

    Used by instance-based stores (engine/store.py) that manage their own
    database file — e.g. one file per cloud workspace. The module-global
    connection (`_get_db`) remains the local single-user path.
    """
    conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_title TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding BLOB,
            token_count INTEGER,
            chunk_index INTEGER,
            community_id INTEGER,
            entities TEXT,
            dates TEXT,
            preferences TEXT,
            type TEXT,
            span TEXT,
            created_at REAL,
            superseded_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_superseded ON chunks(superseded_by);

        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
        CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);

        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            chunks INTEGER
        );

        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            subject TEXT,
            predicate TEXT,
            object TEXT,
            value TEXT,
            chunk_id TEXT,
            source_id TEXT,
            confidence REAL,
            valid_from REAL,
            valid_to REAL,
            superseded_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_facts_chunk ON facts(chunk_id);
        CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);
        CREATE INDEX IF NOT EXISTS idx_facts_valid ON facts(valid_to);

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS facts_data (
            id TEXT PRIMARY KEY,
            chunk_id TEXT,
            source_id TEXT,
            data TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fdata_chunk ON facts_data(chunk_id);
        CREATE INDEX IF NOT EXISTS idx_fdata_source ON facts_data(source_id);
    """)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '1')")
    conn.commit()


# ---------------------------------------------------------------------------
# Vector encoding (store as compact binary blobs)
# ---------------------------------------------------------------------------
def _encode_vec(vec: list[float]) -> bytes:
    return struct.pack(f'{len(vec)}f', *vec)


def _decode_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f'{n}f', blob))


def _encode_json_field(data) -> str:
    return json.dumps(data) if data else "[]"


def _decode_json_field(text: str | None) -> list:
    if not text:
        return []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# SQLite CRUD operations
# ---------------------------------------------------------------------------
def db_load_chunks(source_id: str | None = None,
                   conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn if conn is not None else _get_db()
    if source_id:
        rows = conn.execute("SELECT * FROM chunks WHERE source_id = ?", (source_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM chunks").fetchall()
    return [_row_to_chunk(r) for r in rows]


def db_load_active_chunks(conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn if conn is not None else _get_db()
    rows = conn.execute("SELECT * FROM chunks WHERE superseded_by IS NULL").fetchall()
    return [_row_to_chunk(r) for r in rows]


def db_save_chunks(chunks: list[dict], source_id: str,
                   conn: sqlite3.Connection | None = None):
    conn = conn if conn is not None else _get_db()
    conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
    for c in chunks:
        conn.execute("""
            INSERT OR REPLACE INTO chunks (id, source_id, source_title, content, embedding,
                token_count, chunk_index, community_id, entities, dates, preferences,
                type, span, created_at, superseded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c["id"], c["source_id"], c.get("source_title", ""),
            c["content"], _encode_vec(c["embedding"]) if c.get("embedding") else None,
            c.get("token_count", 0), c.get("chunk_index", 0), c.get("community_id", 0),
            _encode_json_field(c.get("entities")),
            _encode_json_field(c.get("dates")),
            _encode_json_field(c.get("preferences")),
            c.get("type", "unknown"), _encode_json_field(c.get("span")),
            c.get("created_at", time.time()), c.get("superseded_by"),
        ))
    conn.commit()


def db_load_edges(source_id: str | None = None,
                  conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn if conn is not None else _get_db()
    if source_id:
        rows = conn.execute("SELECT * FROM edges WHERE source_id = ?", (source_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM edges").fetchall()
    return [{"source_id": r["source_id"], "from": r["from_id"], "to": r["to_id"],
             "type": r["type"], "weight": r["weight"]} for r in rows]


def db_save_edges(edges: list[dict], source_id: str,
                  conn: sqlite3.Connection | None = None):
    conn = conn if conn is not None else _get_db()
    conn.execute("DELETE FROM edges WHERE source_id = ?", (source_id,))
    for e in edges:
        conn.execute(
            "INSERT INTO edges (source_id, from_id, to_id, type, weight) VALUES (?, ?, ?, ?, ?)",
            (e.get("source_id"), e["from"], e["to"], e["type"], e["weight"]))
    conn.commit()


def db_add_edges(edges: list[dict], conn: sqlite3.Connection | None = None):
    conn = conn if conn is not None else _get_db()
    for e in edges:
        conn.execute(
            "INSERT INTO edges (source_id, from_id, to_id, type, weight) VALUES (?, ?, ?, ?, ?)",
            (e.get("source_id"), e["from"], e["to"], e["type"], e["weight"]))
    conn.commit()


def db_load_sources(conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn if conn is not None else _get_db()
    rows = conn.execute("SELECT * FROM sources").fetchall()
    return [{"id": r["id"], "title": r["title"], "chunks": r["chunks"]} for r in rows]


def db_save_source(source_id: str, title: str, chunk_count: int,
                   conn: sqlite3.Connection | None = None):
    conn = conn if conn is not None else _get_db()
    conn.execute("INSERT OR REPLACE INTO sources (id, title, chunks) VALUES (?, ?, ?)",
                 (source_id, title, chunk_count))
    conn.commit()


def db_delete_source(source_id: str,
                     conn: sqlite3.Connection | None = None) -> int:
    conn = conn if conn is not None else _get_db()
    count = conn.execute("SELECT COUNT(*) FROM chunks WHERE source_id = ?", (source_id,)).fetchone()[0]
    conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM edges WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    return count


def db_supersede_chunk(chunk_id: str, superseded_by: str,
                       conn: sqlite3.Connection | None = None):
    conn = conn if conn is not None else _get_db()
    conn.execute("UPDATE chunks SET superseded_by = ? WHERE id = ?", (superseded_by, chunk_id))
    conn.commit()


def db_chunk_count(conn: sqlite3.Connection | None = None) -> int:
    conn = conn if conn is not None else _get_db()
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def db_get_all_vectors(conn: sqlite3.Connection | None = None) -> list[tuple[str, list[float]]]:
    """Return (chunk_id, embedding) for all active chunks. Used for vector search."""
    conn = conn if conn is not None else _get_db()
    rows = conn.execute(
        "SELECT id, embedding FROM chunks WHERE superseded_by IS NULL AND embedding IS NOT NULL"
    ).fetchall()
    return [(r["id"], _decode_vec(r["embedding"])) for r in rows]


def _row_to_chunk(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "source_id": r["source_id"],
        "source_title": r["source_title"],
        "content": r["content"],
        "embedding": _decode_vec(r["embedding"]) if r["embedding"] else [],
        "token_count": r["token_count"],
        "chunk_index": r["chunk_index"],
        "community_id": r["community_id"],
        "entities": _decode_json_field(r["entities"]),
        "dates": _decode_json_field(r["dates"]),
        "preferences": _decode_json_field(r["preferences"]),
        "type": r["type"],
        "span": _decode_json_field(r["span"]),
        "created_at": r["created_at"],
        "superseded_by": r["superseded_by"],
    }


# ---------------------------------------------------------------------------
# Migration: JSON → SQLite (one-time, automatic)
# ---------------------------------------------------------------------------
def _migrate_json_to_sqlite():
    """Migrate all JSON data to SQLite. Keeps JSON files as backup."""
    conn = _get_db()

    if CHUNKS_FILE.exists():
        chunks = load_json(CHUNKS_FILE)
        for c in chunks:
            conn.execute("""
                INSERT OR IGNORE INTO chunks (id, source_id, source_title, content, embedding,
                    token_count, chunk_index, community_id, entities, dates, preferences,
                    type, span, created_at, superseded_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c["id"], c["source_id"], c.get("source_title", ""),
                c["content"], _encode_vec(c["embedding"]) if c.get("embedding") else None,
                c.get("token_count", 0), c.get("chunk_index", 0), c.get("community_id", 0),
                _encode_json_field(c.get("entities")),
                _encode_json_field(c.get("dates")),
                _encode_json_field(c.get("preferences")),
                c.get("type", "unknown"), _encode_json_field(c.get("span")),
                c.get("created_at"), c.get("superseded_by"),
            ))

    if EDGES_FILE.exists():
        edges = load_json(EDGES_FILE)
        for e in edges:
            conn.execute(
                "INSERT INTO edges (source_id, from_id, to_id, type, weight) VALUES (?, ?, ?, ?, ?)",
                (e.get("source_id"), e["from"], e["to"], e["type"], e["weight"]))

    if SOURCES_FILE.exists():
        sources = load_json(SOURCES_FILE)
        for s in sources:
            conn.execute("INSERT OR IGNORE INTO sources (id, title, chunks) VALUES (?, ?, ?)",
                         (s["id"], s["title"], s.get("chunks", 0)))

    if FACTS_FILE.exists():
        facts = load_json(FACTS_FILE)
        for f in facts:
            # Legacy structured table (keep for backward compat)
            conn.execute("""
                INSERT OR IGNORE INTO facts (id, subject, predicate, object, value,
                    chunk_id, source_id, confidence, valid_from, valid_to, superseded_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f.get("id", ""), f.get("subject"), f.get("predicate"), f.get("object"),
                f.get("value"), f.get("chunk_id"), f.get("source_id"),
                f.get("confidence"), f.get("valid_from"), f.get("valid_to"),
                f.get("superseded_by"),
            ))
            # Full-fidelity blob table (used by current code)
            conn.execute(
                "INSERT OR IGNORE INTO facts_data (id, chunk_id, source_id, data) VALUES (?, ?, ?, ?)",
                (f.get("id", ""), f.get("chunk_id"), f.get("source_id"), json.dumps(f, default=str)),
            )

    conn.commit()

    # Rename JSON files as backups (don't delete)
    for p in [CHUNKS_FILE, EDGES_FILE, SOURCES_FILE, FACTS_FILE]:
        if p.exists():
            backup = p.with_suffix(".json.bak")
            p.rename(backup)


# ---------------------------------------------------------------------------
# SQLite facts CRUD (full-fidelity JSON blob per fact)
# ---------------------------------------------------------------------------

def db_load_facts(conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn if conn is not None else _get_db()
    rows = conn.execute("SELECT data FROM facts_data").fetchall()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["data"]))
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def db_save_facts(facts: list[dict], conn: sqlite3.Connection | None = None):
    """Replace the entire facts store (same semantics as save_json for facts)."""
    conn = conn if conn is not None else _get_db()
    conn.execute("DELETE FROM facts_data")
    for f in facts:
        conn.execute(
            "INSERT INTO facts_data (id, chunk_id, source_id, data) VALUES (?, ?, ?, ?)",
            (f.get("id", ""), f.get("chunk_id"), f.get("source_id"), json.dumps(f, default=str)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Tier 3: sqlite-vec detection (optional acceleration)
# ---------------------------------------------------------------------------
_has_sqlite_vec: bool | None = None


def has_sqlite_vec() -> bool:
    global _has_sqlite_vec
    if _has_sqlite_vec is not None:
        return _has_sqlite_vec
    try:
        import sqlite_vec
        conn = _get_db()
        sqlite_vec.load(conn)
        _has_sqlite_vec = True
    except (ImportError, Exception):
        _has_sqlite_vec = False
    return _has_sqlite_vec


def force_migrate():
    """Force migration from JSON to SQLite regardless of chunk count."""
    global _backend
    if DB_FILE.exists():
        return "already_sqlite"
    _migrate_json_to_sqlite()
    _backend = "sqlite"
    return "migrated"
