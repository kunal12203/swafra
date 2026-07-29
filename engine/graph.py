"""Knowledge graph operations — add, search, walk, get_context, list, delete."""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict

from engine.bm25 import BM25Index
from engine.chunking import chunk_conversation, leiden_chunk
from engine.embedding import cosine_sim, embed
from engine.extractors import (
    clear_llm_cache, extract_dates, extract_entities,
    _regex_extract_entities,
)
from engine.facts import get_chunk_fact_signals, ingest_facts
from engine.llm import is_llm_available, llm_call, llm_check_duplicate
from engine.store import get_store
from engine.tokenizer import tokenize


def _gen_id(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Storage dispatch — all persistence goes through the MemoryStore port
# (engine/store.py). Locally that is the adaptive JSON→SQLite store; a cloud
# edge binds a per-workspace store via engine.store.use_store().
# ---------------------------------------------------------------------------

def _load_all_chunks() -> list[dict]:
    return get_store().load_active_chunks()


def _load_all_chunks_including_superseded() -> list[dict]:
    return get_store().load_all_chunks()


def _load_all_edges() -> list[dict]:
    return get_store().load_edges()


def _load_all_sources() -> list[dict]:
    return get_store().load_sources()


def _save_source_chunks(new_chunks: list[dict], source_id: str):
    """Persist chunks for a source (replaces the source's previous chunks)."""
    get_store().save_source_chunks(new_chunks, source_id)


def _save_source_edges(source_edges: list[dict], cross_edges: list[dict], source_id: str):
    """Persist intra-source edges and cross-session edges."""
    get_store().save_source_edges(source_edges, cross_edges, source_id)


def _save_source_record(source_id: str, title: str, chunk_count: int):
    get_store().save_source_record(source_id, title, chunk_count)


def _delete_source_data(source_id: str) -> int:
    return get_store().delete_source(source_id)


def _supersede_chunk_record(chunk_id: str, superseded_by: str):
    get_store().supersede_chunk(chunk_id, superseded_by)


# ---------------------------------------------------------------------------
# LLM reranking
# ---------------------------------------------------------------------------

def _llm_rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Reorder candidates using a single batched LLM relevance-scoring call.

    Falls back to original order on any failure.
    """
    if len(candidates) <= 1:
        return candidates

    numbered = "\n".join(
        f"[{i}] {c['content'][:300]}" for i, c in enumerate(candidates)
    )
    prompt = (
        f'Query: "{query}"\n\n'
        f"Rate each chunk's relevance to the query (0-10):\n{numbered}\n\n"
        f"Return ONLY valid JSON: {{\"scores\": [<score for [0]>, <score for [1]>, ...]}}\n"
        f"Scores array must have exactly {len(candidates)} numbers."
    )
    system = (
        "You are a relevance scorer. Score how well each text chunk answers the query. "
        "Return ONLY valid JSON with a 'scores' array, no markdown, no explanation."
    )
    resp = llm_call(prompt, system)
    if not resp:
        return candidates

    resp = resp.strip()
    if resp.startswith("```"):
        resp = resp.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        parsed = json.loads(resp)
        scores = parsed.get("scores", [])
        if not isinstance(scores, list) or len(scores) != len(candidates):
            return candidates
        scored = sorted(
            zip(scores, candidates),
            key=lambda x: float(x[0]),
            reverse=True,
        )
        return [c for _, c in scored]
    except (json.JSONDecodeError, TypeError, ValueError):
        return candidates


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def add_knowledge(text: str, source_title: str = "untitled") -> dict:
    # Load all existing data upfront (needed for dedup, cross-session edges,
    # supersession checks, and JSON save-back).
    all_chunks = _load_all_chunks_including_superseded()
    all_sources = _load_all_sources()

    source_id = _gen_id(f"{source_title}:{text[:100]}")

    # Semantic dedup: ask LLM if this duplicates existing knowledge
    if is_llm_available() and all_sources:
        existing_summaries = []
        for src in all_sources:
            src_chunks = [c for c in all_chunks if c.get("source_id") == src["id"]]
            if src_chunks:
                preview = src_chunks[0]["content"][:200]
                existing_summaries.append(f"{src['title']}: {preview}")

        if existing_summaries:
            dedup_result = llm_check_duplicate(text, existing_summaries)
            if dedup_result and dedup_result.get("is_duplicate"):
                return {
                    "source_id": source_id,
                    "chunks": 0,
                    "edges": 0,
                    "skipped": True,
                    "reason": dedup_result.get("reason", "duplicate content"),
                    "duplicate_of": existing_summaries[dedup_result.get("duplicate_of_index", 0)][:80]
                        if dedup_result.get("duplicate_of_index") is not None else None,
                }

    clear_llm_cache()

    # Save old chunks for this source BEFORE stripping them — needed for
    # intra-source supersession check below.
    old_source_chunks = [c for c in all_chunks if c.get("source_id") == source_id]

    # Build new chunks
    pieces = leiden_chunk(text)
    if not pieces:
        pieces = chunk_conversation(text, source_title)
    if not pieces:
        return {"source_id": source_id, "chunks": 0, "edges": 0}

    vectors = embed([p["content"] for p in pieces])
    new_chunks: list[dict] = []
    chunk_ids = []
    for piece, vec in zip(pieces, vectors):
        cid = _gen_id(f"{source_id}:{piece['chunk_index']}")
        chunk_ids.append(cid)
        new_chunks.append({
            "id": cid,
            "source_id": source_id,
            "source_title": source_title,
            "content": piece["content"],
            "embedding": vec,
            "token_count": piece["token_count"],
            "chunk_index": piece["chunk_index"],
            "community_id": piece["community_id"],
            "entities": piece.get("entities", []),
            "dates": piece.get("dates", []),
            "preferences": piece.get("preferences", []),
            "type": piece.get("type", "unknown"),
            "span": piece["span"],
            "created_at": time.time(),
            "superseded_by": None,
        })

    # Intra-source supersession: mark old chunks superseded if very similar
    # to new ones from the same source (re-ingestion of updated content).
    superseded_old_ids: list[tuple[str, str]] = []  # (old_chunk_id, new_chunk_id)
    for new_chunk, new_vec in zip(chunk_ids, vectors):
        new_entities = set(pieces[chunk_ids.index(new_chunk)].get("entities", []))
        if len(new_entities) < 3:
            continue
        for old_c in old_source_chunks:
            if old_c.get("superseded_by"):
                continue
            old_entities = set(old_c.get("entities", []))
            if len(new_entities & old_entities) >= 3:
                sim = cosine_sim(new_vec, old_c["embedding"])
                if sim >= 0.85:
                    superseded_old_ids.append((old_c["id"], new_chunk))

    # Build intra-source edges
    source_edges = []
    for i in range(len(chunk_ids) - 1):
        source_edges.append({"source_id": source_id, "from": chunk_ids[i], "to": chunk_ids[i+1], "type": "next", "weight": 1.0})
        source_edges.append({"source_id": source_id, "from": chunk_ids[i+1], "to": chunk_ids[i], "type": "prev", "weight": 1.0})

    for i in range(len(vectors)):
        sims = []
        for j in range(len(vectors)):
            if i == j or abs(i - j) == 1:
                continue
            s = cosine_sim(vectors[i], vectors[j])
            if s >= 0.7:
                sims.append((j, s))
        sims.sort(key=lambda x: x[1], reverse=True)
        for j, score in sims[:5]:
            source_edges.append({"source_id": source_id, "from": chunk_ids[i], "to": chunk_ids[j], "type": "similar", "weight": score})

    entity_to_chunks: dict[str, list[str]] = defaultdict(list)
    for cid, piece in zip(chunk_ids, pieces):
        for ent in piece.get("entities", []):
            entity_to_chunks[ent].append(cid)
    for ent, cids in entity_to_chunks.items():
        if len(cids) > 1 and len(cids) <= 10:
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    source_edges.append({"source_id": source_id, "from": cids[i], "to": cids[j], "type": "entity", "weight": 0.6})

    # Cross-session edges: link new chunks to best matching chunk per other source
    other_active = [c for c in all_chunks
                    if c.get("source_id") != source_id and not c.get("superseded_by")]
    cross_edges = []
    if other_active:
        other_by_source: dict[str, list] = defaultdict(list)
        for c in other_active:
            other_by_source[c["source_id"]].append(c)

        for new_cid, new_vec in zip(chunk_ids, vectors):
            candidates = []
            for src_id, src_chunks in other_by_source.items():
                best = max(src_chunks, key=lambda c: cosine_sim(new_vec, c["embedding"]))
                sim = cosine_sim(new_vec, best["embedding"])
                if sim >= 0.45:
                    candidates.append((sim, best))
            candidates.sort(key=lambda x: x[0], reverse=True)
            for sim, other_c in candidates[:3]:
                cross_edges.append({
                    "source_id": None,
                    "from": new_cid,
                    "to": other_c["id"],
                    "type": "cross_session",
                    "weight": round(sim, 4),
                })

    # Extract structured facts for lifecycle tracking
    facts_summary = {"extracted": 0, "conflicts": 0, "superseded": 0}
    for cid, piece in zip(chunk_ids, pieces):
        r = ingest_facts(piece["content"], cid, source_id)
        facts_summary["extracted"] += r["extracted"]
        facts_summary["conflicts"] += r["conflicts"]
        facts_summary["superseded"] += r["superseded"]

    # Persist — order matters: supersede old chunks first, then save new ones
    for old_cid, new_cid in superseded_old_ids:
        _supersede_chunk_record(old_cid, new_cid)

    _save_source_chunks(new_chunks, source_id)
    _save_source_edges(source_edges, cross_edges, source_id)
    _save_source_record(source_id, source_title, len(pieces))

    return {
        "source_id": source_id,
        "chunks": len(pieces),
        "edges": len(source_edges) + len(cross_edges),
        "facts": facts_summary,
    }


def search_knowledge(query: str, k: int = 8, rerank: bool = False) -> list[dict]:
    chunks_store = _load_all_chunks()  # already filters superseded
    if not chunks_store:
        return []

    bm25 = BM25Index()
    for c in chunks_store:
        bm25.add(tokenize(c["content"]))

    bm25_results = bm25.search(query, k=k * 3)
    bm25_scores = {i: s for i, s in bm25_results}

    qvec = embed([query])[0]
    vec_scores = {}
    for i, c in enumerate(chunks_store):
        vec_scores[i] = cosine_sim(qvec, c["embedding"])

    query_entities = _regex_extract_entities(query)
    for m in re.finditer(r"['\"]([^'\"]{2,40})['\"]", query):
        query_entities.append(m.group(1).lower())
    query_dates = extract_dates(query)
    query_lower = query.lower()
    query_keywords = [w for w in tokenize(query) if len(w) > 3]

    entity_scores = {}
    for i, c in enumerate(chunks_store):
        bonus = 0.0
        chunk_entities = c.get("entities", [])
        chunk_dates = c.get("dates", [])
        chunk_prefs = c.get("preferences", [])
        chunk_content_lower = c["content"].lower()

        for qe in query_entities:
            if qe in chunk_entities or qe in chunk_content_lower:
                bonus += 0.3

        for qd in query_dates:
            if qd in chunk_dates or qd in chunk_content_lower:
                bonus += 0.2

        is_pref_query = any(w in query_lower for w in ("prefer", "favorite", "like", "go-to", "usually", "recommend", "what do i", "what's my", "what is my"))
        if is_pref_query:
            if chunk_prefs:
                bonus += 0.35
            for pref in chunk_prefs:
                for kw in query_keywords:
                    if kw in pref:
                        bonus += 0.2
            if "user:" in chunk_content_lower:
                bonus += 0.1

        for kw in query_keywords:
            if kw in chunk_content_lower:
                bonus += 0.05

        entity_scores[i] = bonus

    def _char_ngrams(text: str, n: int = 3) -> set[str]:
        text = text.lower()
        return {text[i:i+n] for i in range(len(text) - n + 1)}

    query_ngrams = _char_ngrams(query)
    ngram_scores = {}
    for i, c in enumerate(chunks_store):
        chunk_ngrams = _char_ngrams(c["content"])
        if query_ngrams and chunk_ngrams:
            overlap = len(query_ngrams & chunk_ngrams)
            ngram_scores[i] = overlap / len(query_ngrams)
        else:
            ngram_scores[i] = 0.0

    max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
    max_vec = max(vec_scores.values()) if vec_scores else 1.0
    max_ngram = max(ngram_scores.values()) if ngram_scores else 1.0

    # Fact-lifecycle: penalize stale chunks, boost chunks with query-relevant active facts
    stale_penalties, chunk_active_values = get_chunk_fact_signals()

    now = time.time()
    fused = {}
    for i in range(len(chunks_store)):
        bm25_norm = bm25_scores.get(i, 0) / max(max_bm25, 0.001)
        vec_norm = vec_scores.get(i, 0) / max(max_vec, 0.001)
        ent_score = entity_scores.get(i, 0)
        ngram_norm = ngram_scores.get(i, 0) / max(max_ngram, 0.001)
        base = 0.4 * bm25_norm + 0.15 * vec_norm + 0.25 * ent_score + 0.2 * ngram_norm
        created_at = chunks_store[i].get("created_at")
        if created_at:
            days_old = (now - created_at) / 86400
            decay = math.exp(-0.005 * days_old)
        else:
            decay = 1.0
        chunk_id = chunks_store[i].get("id")
        if chunk_id in stale_penalties:
            penalty = stale_penalties[chunk_id]
            decay *= (1.0 - penalty * 0.85)
        if chunk_id in chunk_active_values:
            for val in chunk_active_values[chunk_id]:
                if any(kw in val for kw in query_keywords) or any(kw in query_lower for kw in val.split() if len(kw) > 3):
                    decay *= 1.3
                    break

        fused[i] = base * decay

    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)

    results = []
    seen_content = set()
    for i, score in ranked:
        if score <= 0:
            continue
        c = chunks_store[i]
        content_key = c["content"][:100].lower()
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        results.append({
            "chunk_id": c["id"],
            "content": c["content"],
            "source_title": c["source_title"],
            "score": round(score, 4),
            "community_id": c["community_id"],
            "entities": c.get("entities", []),
            "type": c.get("type", "unknown"),
        })
        if len(results) >= k:
            break

    if rerank and is_llm_available() and len(results) > 1:
        results = _llm_rerank(query, results)

    return results


def graph_walk(chunk_id: str, hops: int = 2, k: int = 10) -> list[dict]:
    all_chunks = _load_all_chunks_including_superseded()
    edges_store = _load_all_edges()

    chunk_map = {c["id"]: c for c in all_chunks}
    if chunk_id not in chunk_map:
        return []

    visited = {chunk_id}
    frontier = [(chunk_id, 0, 1.0, "origin")]
    results = []

    while frontier:
        current, depth, path_weight, edge_type = frontier.pop(0)
        if current != chunk_id:
            c = chunk_map.get(current)
            if c:
                results.append({
                    "chunk_id": current,
                    "content": c["content"],
                    "source_title": c["source_title"],
                    "distance": depth,
                    "path_type": edge_type,
                    "score": round(path_weight, 4),
                    "community_id": c["community_id"],
                })

        if depth < hops:
            for e in edges_store:
                if e["from"] == current and e["to"] not in visited:
                    visited.add(e["to"])
                    frontier.append((e["to"], depth + 1, path_weight * e["weight"], e["type"]))

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def get_context(query: str, k: int = 5, hops: int = 1, min_source_pct: float = 0.15) -> list[dict]:
    all_sources = _load_all_sources()
    total_sources = len(all_sources)

    # target_sources: at least cover min_source_pct of sources, but never exceed k
    if total_sources > 0:
        pct_target = max(1, int(total_sources * min_source_pct))
        target_sources = min(k, pct_target) if pct_target <= k else k
    else:
        target_sources = k

    total_chunks = len(_load_all_chunks())
    all_hits = search_knowledge(query, k=max(total_chunks, 1))
    if not all_hits:
        return []

    seen_ids = {h["chunk_id"] for h in all_hits}
    for top_hit in all_hits[:3]:
        walked = graph_walk(top_hit["chunk_id"], hops=hops, k=k)
        for w in walked:
            if w["chunk_id"] not in seen_ids:
                w["score"] = w["score"] * 0.5
                all_hits.append(w)
                seen_ids.add(w["chunk_id"])

    source_best: dict[str, dict] = {}
    for h in all_hits:
        src = h.get("source_title", "")
        if src not in source_best or h["score"] > source_best[src]["score"]:
            source_best[src] = h

    sources_ranked = sorted(source_best.keys(), key=lambda s: source_best[s]["score"], reverse=True)

    selected = []
    for src in sources_ranked:
        if len(selected) >= target_sources:
            break
        selected.append(source_best[src])

    if is_llm_available() and len(selected) > 1:
        selected = _llm_rerank(query, selected)

    return selected


def list_sources() -> list[dict]:
    return _load_all_sources()


def delete_source(source_id: str) -> dict:
    deleted = _delete_source_data(source_id)
    return {"deleted_chunks": deleted}
