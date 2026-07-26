"""scimap MCP engine — the Python subprocess that handles embeddings, chunking,
and knowledge graph operations.

v2: Improved retrieval with BM25 + vector hybrid, turn-level conversation chunking,
temporal indexing, and entity-aware search.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scimap.engine")

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
_DATA_DIR = Path(os.getenv("SCIMAP_DATA_DIR", os.path.expanduser("~/.scimap")))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_CHUNKS_FILE = _DATA_DIR / "chunks.json"
_EDGES_FILE = _DATA_DIR / "edges.json"
_SOURCES_FILE = _DATA_DIR / "sources.json"


def _load_json(path: Path) -> list | dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def _save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, default=str)


# ---------------------------------------------------------------------------
# BM25 (pure Python, no deps)
# ---------------------------------------------------------------------------
_STOP_WORDS = frozenset({
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she", "her",
    "hers", "herself", "it", "its", "itself", "they", "them", "their", "theirs",
    "themselves", "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "having", "do", "does", "did", "doing", "a", "an", "the", "and", "but", "if",
    "or", "because", "as", "until", "while", "of", "at", "by", "for", "with",
    "about", "against", "between", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s",
    "t", "can", "will", "just", "don", "should", "now", "d", "ll", "m", "o", "re",
    "ve", "y", "ain", "aren", "couldn", "didn", "doesn", "hadn", "hasn", "haven",
    "isn", "ma", "mightn", "mustn", "needn", "shan", "shouldn", "wasn", "weren",
    "won", "wouldn",
})

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _stem(word: str) -> str:
    """Minimal suffix-stripping stemmer. Handles common English inflections."""
    if len(word) <= 3:
        return word
    # Handle common suffixes (order matters — longest first)
    suffixes = [
        ("ational", "ate"), ("tional", "tion"), ("encies", "ence"),
        ("ancies", "ance"), ("izers", "ize"), ("ously", "ous"),
        ("ively", "ive"), ("ments", "ment"), ("ities", ""),
        ("ness", ""), ("ings", ""), ("ment", ""), ("ence", ""),
        ("ance", ""), ("ible", ""), ("able", ""), ("tion", ""),
        ("ling", ""), ("ally", ""), ("ized", "ize"), ("ised", "ise"),
        ("ful", ""), ("ing", ""), ("ers", ""), ("ies", "y"),
        ("ess", ""), ("est", ""), ("ous", ""), ("ive", ""),
        ("ize", ""), ("ise", ""), ("ion", ""), ("ed", ""),
        ("er", ""), ("ly", ""), ("es", ""), ("'s", ""), ("s", ""),
    ]
    for suffix, replacement in suffixes:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[:-len(suffix)] + replacement
    return word


def _tokenize(text: str) -> list[str]:
    """Tokenize, remove stop words, and stem."""
    return [_stem(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS and len(w) > 1]


def _tokenize_raw(text: str) -> list[str]:
    """Tokenize without stemming (for entity matching)."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS and len(w) > 1]


class BM25Index:
    """In-memory BM25 index. Rebuilt per-question (since we reset between questions in bench)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: Counter = Counter()
        self.doc_lens: list[int] = []
        self.doc_tokens: list[list[str]] = []
        self.avg_dl: float = 0.0
        self.n_docs: int = 0

    def add(self, tokens: list[str]):
        self.doc_tokens.append(tokens)
        self.doc_lens.append(len(tokens))
        self.n_docs += 1
        seen = set(tokens)
        for t in seen:
            self.doc_freqs[t] += 1
        self.avg_dl = sum(self.doc_lens) / max(1, self.n_docs)

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        doc_toks = self.doc_tokens[doc_idx]
        dl = self.doc_lens[doc_idx]
        tf_map = Counter(doc_toks)
        score = 0.0
        for qt in query_tokens:
            if qt not in tf_map:
                continue
            tf = tf_map[qt]
            df = self.doc_freqs.get(qt, 0)
            idf = math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * dl / max(1, self.avg_dl))
            score += idf * numerator / denominator
        return score

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        query_tokens = _tokenize(query)
        scores = [(i, self.score(query_tokens, i)) for i in range(self.n_docs)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(i, s) for i, s in scores[:k] if s > 0]


# ---------------------------------------------------------------------------
# Embedding (vector similarity — hash-based fallback or fastembed)
# ---------------------------------------------------------------------------
_EMBED_MODEL = None
_EMBED_DIM = 384


def _get_embedder():
    global _EMBED_MODEL, _EMBED_DIM
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from fastembed import TextEmbedding
        model_name = os.getenv("SCIMAP_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        _EMBED_MODEL = TextEmbedding(model_name=model_name)
        test = list(_EMBED_MODEL.embed(["test"]))[0]
        _EMBED_DIM = len(test)
        log.info("fastembed loaded: model=%s, dim=%d", model_name, _EMBED_DIM)
        return _EMBED_MODEL
    except Exception as e:
        log.warning("fastembed unavailable (%s), using local fallback", e)
        return None


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _local_vector(text: str) -> list[float]:
    vec = [0.0] * _EMBED_DIM
    toks = _TOKEN_RE.findall((text or "").lower())
    if not toks:
        vec[0] = 1.0
        return vec
    for tok in toks:
        padded = f"#{tok}#"
        ngrams = [padded[i:i+3] for i in range(max(1, len(padded) - 2))]
        for feat in (tok, *ngrams):
            h = hashlib.sha1(feat.encode()).digest()
            bucket = int.from_bytes(h[:4], "big") % _EMBED_DIM
            sign = 1.0 if (h[4] & 1) else -1.0
            vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def embed(texts: list[str]) -> list[list[float]]:
    model = _get_embedder()
    if model is not None:
        embeddings = list(model.embed(texts))
        return [emb.tolist() for emb in embeddings]
    return [_local_vector(t) for t in texts]


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Date/temporal extraction
# ---------------------------------------------------------------------------
_DATE_PATTERNS = [
    # "March 15th", "April 3rd", "June 10"
    re.compile(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?\b', re.I),
    # "03/15/2023", "2023-03-15"
    re.compile(r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b'),
    re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b'),
    # Relative: "last week", "two weeks ago", "3 months"
    re.compile(r'\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(days?|weeks?|months?|years?)\s*(ago|before|after|later)?\b', re.I),
    # Days of week
    re.compile(r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b', re.I),
    # Times
    re.compile(r'\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?\b'),
    re.compile(r'\b(\d{1,2})\s*(AM|PM|am|pm)\b'),
]


def _extract_dates(text: str) -> list[str]:
    """Extract date/time mentions from text."""
    dates = []
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            dates.append(m.group(0).lower())
    return dates


# ---------------------------------------------------------------------------
# Entity extraction (names, proper nouns, key terms)
# ---------------------------------------------------------------------------
_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
_NAME_STOPWORDS = frozenset({
    "The", "This", "That", "These", "Those", "It", "Its", "They", "Their",
    "We", "Our", "He", "She", "His", "Her", "You", "Your", "My", "In",
    "On", "At", "By", "For", "With", "From", "To", "And", "But", "Or",
    "If", "When", "Where", "What", "How", "Why", "Who", "Which", "Each",
    "Every", "Some", "Any", "All", "Most", "Many", "Few", "No", "Not",
    "Also", "However", "Therefore", "Furthermore", "Moreover", "Thus",
    "Here", "There", "Now", "Then", "After", "Before", "While", "Since",
    "Sure", "Yes", "No", "Well", "Great", "Good", "Thanks", "Thank",
    "Hi", "Hello", "Hey", "Okay", "Ok", "Right", "Let", "Can", "Could",
    "Would", "Should", "May", "Might", "Will", "Shall", "Do", "Does",
    "Did", "Have", "Has", "Had", "Are", "Is", "Was", "Were", "Be",
    "Been", "Being", "First", "Last", "Next", "New", "Old", "Just",
    "Really", "Actually", "Definitely", "Absolutely", "Recently",
})


def _extract_entities(text: str) -> list[str]:
    """Extract proper nouns and key entities."""
    entities = []
    for m in _ENTITY_RE.finditer(text):
        ent = m.group(1)
        if ent not in _NAME_STOPWORDS and len(ent) > 1:
            entities.append(ent.lower())
    # Also extract quoted terms
    for m in re.finditer(r"['\"]([^'\"]{2,40})['\"]", text):
        entities.append(m.group(1).lower())
    return list(set(entities))


# ---------------------------------------------------------------------------
# Preference/opinion extraction
# ---------------------------------------------------------------------------
_PREF_PATTERNS = [
    re.compile(r"\b(?:i|we)\s+(?:prefer|like|love|enjoy|hate|dislike|want|need|use|chose|picked|switched to|started using|recommend|always|usually|typically)\s+(.{3,80}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:my|our)\s+(?:favorite|preferred|go-to|usual|regular|default)\s+(.{3,60}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:i'm|i am)\s+(?:a fan of|into|interested in|passionate about|obsessed with|addicted to)\s+(.{3,50}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:i|we)\s+(?:always|usually|typically|normally|generally)\s+(?:go with|choose|pick|get|order|buy|use|watch|listen to|read|play|eat|drink|wear)\s+(.{3,60}?)(?:\.|,|!|\?|$)", re.I),
    re.compile(r"\b(?:i've been|i have been|i started)\s+(?:using|reading|watching|playing|eating|drinking|wearing|going to|listening to)\s+(.{3,60}?)(?:\.|,|!|\?|$)", re.I),
]


def _extract_preferences(text: str) -> list[str]:
    """Extract preference/opinion statements."""
    prefs = []
    for pat in _PREF_PATTERNS:
        for m in pat.finditer(text):
            prefs.append(m.group(0).lower().strip())
    return prefs


# ---------------------------------------------------------------------------
# Conversation-aware chunking
# ---------------------------------------------------------------------------
def chunk_conversation(text: str, source_title: str = "") -> list[dict]:
    """Chunk a conversation into meaningful units.

    Strategy:
      - If the text looks like a conversation (has "User:" / "Assistant:" markers),
        chunk by exchange (user message + assistant response = 1 chunk).
      - Additionally create a "facts" chunk that extracts key facts, dates, entities,
        and preferences from the entire conversation.
      - For non-conversation text, use paragraph-level chunking.
    """
    chunks = []
    chunk_idx = 0

    # Detect if this is a conversation
    is_conversation = bool(re.search(r'^(User|Assistant|Human|AI):', text, re.M))

    if is_conversation:
        # Split into turns
        turn_pattern = re.compile(r'^(User|Assistant|Human|AI):\s*', re.M)
        parts = turn_pattern.split(text)
        # parts = ['', 'User', content, 'Assistant', content, ...]

        current_exchange = []
        exchanges = []
        i = 1  # skip empty first part
        while i < len(parts) - 1:
            role = parts[i].lower()
            content = parts[i + 1].strip()
            current_exchange.append(f"{role}: {content}")
            # End exchange after assistant response
            if role in ("assistant", "ai") and current_exchange:
                exchanges.append("\n".join(current_exchange))
                current_exchange = []
            i += 2
        if current_exchange:
            exchanges.append("\n".join(current_exchange))

        # Create chunks from exchanges (group 2-3 exchanges per chunk for context)
        group_size = 2
        for gi in range(0, len(exchanges), group_size):
            group = exchanges[gi:gi + group_size]
            content = "\n\n".join(group)
            words = content.split()
            entities = _extract_entities(content)
            dates = _extract_dates(content)
            prefs = _extract_preferences(content)

            chunks.append({
                "content": content,
                "token_count": len(words),
                "span": [0, len(words)],
                "chunk_index": chunk_idx,
                "community_id": chunk_idx,
                "entities": entities,
                "dates": dates,
                "preferences": prefs,
                "type": "exchange",
            })
            chunk_idx += 1

        # Create a "facts summary" chunk with all extracted metadata
        all_entities = []
        all_dates = []
        all_prefs = []
        for c in chunks:
            all_entities.extend(c["entities"])
            all_dates.extend(c["dates"])
            all_prefs.extend(c["preferences"])

        if all_entities or all_dates or all_prefs:
            facts_parts = []
            if all_entities:
                facts_parts.append("Entities mentioned: " + ", ".join(set(all_entities)))
            if all_dates:
                facts_parts.append("Dates/times: " + ", ".join(set(all_dates)))
            if all_prefs:
                facts_parts.append("Preferences: " + "; ".join(set(all_prefs)))
            facts_content = f"[Session facts for {source_title}] " + ". ".join(facts_parts)
            chunks.append({
                "content": facts_content,
                "token_count": len(facts_content.split()),
                "span": [0, 0],
                "chunk_index": chunk_idx,
                "community_id": chunk_idx,
                "entities": list(set(all_entities)),
                "dates": list(set(all_dates)),
                "preferences": list(set(all_prefs)),
                "type": "facts",
            })
            chunk_idx += 1
    else:
        # Non-conversation: paragraph-level chunking
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        # Group small paragraphs
        current = []
        current_words = 0
        for para in paragraphs:
            pw = len(para.split())
            if current_words + pw > 256 and current:
                content = "\n\n".join(current)
                chunks.append({
                    "content": content,
                    "token_count": current_words,
                    "span": [0, current_words],
                    "chunk_index": chunk_idx,
                    "community_id": chunk_idx,
                    "entities": _extract_entities(content),
                    "dates": _extract_dates(content),
                    "preferences": _extract_preferences(content),
                    "type": "paragraph",
                })
                chunk_idx += 1
                current = []
                current_words = 0
            current.append(para)
            current_words += pw
        if current:
            content = "\n\n".join(current)
            chunks.append({
                "content": content,
                "token_count": current_words,
                "span": [0, current_words],
                "chunk_index": chunk_idx,
                "community_id": chunk_idx,
                "entities": _extract_entities(content),
                "dates": _extract_dates(content),
                "preferences": _extract_preferences(content),
                "type": "paragraph",
            })
            chunk_idx += 1

    return chunks


# ---------------------------------------------------------------------------
# Leiden chunker (when deps available)
# ---------------------------------------------------------------------------
_SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])|(?<=\n)\s*(?=\S)|(?<=\.)\s*\n')
_MIN_SENTENCES = 8


def leiden_chunk(text: str) -> list[dict]:
    """Leiden community-detection chunking. Falls back to conversation chunking."""
    try:
        import igraph as ig
        import leidenalg
        import numpy as np
    except ImportError:
        return []  # Signal that Leiden is unavailable

    sentences = _SENT_RE.split(text.strip())
    sentences = [s.strip() for s in sentences if s and s.strip()]
    if len(sentences) < _MIN_SENTENCES:
        return []

    # Build sentence nodes with embeddings
    embeddings = embed(sentences)
    emb_array = np.array(embeddings, dtype=np.float32)
    norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    emb_array = emb_array / norms
    sim_matrix = (emb_array @ emb_array.T).tolist()

    n = len(sentences)
    g = ig.Graph(n=n, directed=False)
    edges, weights = [], []

    for i in range(n):
        for j in range(i + 1, n):
            sim = sim_matrix[i][j]
            dist = abs(i - j)
            if dist <= 3:
                sim += 0.2 * (1 - dist / 4)
            if sim >= 0.3:
                edges.append((i, j))
                weights.append(sim)

    if not edges:
        return []

    g.add_edges(edges)
    g.es["weight"] = weights

    total_words = sum(len(s.split()) for s in sentences)
    expected = max(1, total_words // 256)
    resolution = max(0.5, min(3.0, expected / max(1, n / 10)))

    partition = leidenalg.find_partition(
        g, leidenalg.RBConfigurationVertexPartition,
        weights="weight", resolution_parameter=resolution,
        n_iterations=-1, seed=42)

    chunks = []
    for ci, comm in enumerate(partition):
        if not comm:
            continue
        comm_sorted = sorted(comm)
        content = " ".join(sentences[i] for i in comm_sorted)
        wc = sum(len(sentences[i].split()) for i in comm_sorted)
        chunks.append({
            "content": content,
            "token_count": wc,
            "span": [0, wc],
            "chunk_index": ci,
            "community_id": ci,
            "entities": _extract_entities(content),
            "dates": _extract_dates(content),
            "preferences": _extract_preferences(content),
            "type": "leiden",
        })

    return chunks


# ---------------------------------------------------------------------------
# Knowledge graph operations
# ---------------------------------------------------------------------------
def _gen_id(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def add_knowledge(text: str, source_title: str = "untitled") -> dict:
    """Ingest text: chunk, embed, build graph edges, persist."""
    chunks_store = _load_json(_CHUNKS_FILE) or []
    edges_store = _load_json(_EDGES_FILE) or []
    sources_store = _load_json(_SOURCES_FILE) or []

    source_id = _gen_id(f"{source_title}:{text[:100]}")

    # Remove existing data for this source (idempotent re-index)
    chunks_store = [c for c in chunks_store if c.get("source_id") != source_id]
    edges_store = [e for e in edges_store if e.get("source_id") != source_id]

    # Try Leiden first, fall back to conversation-aware chunking
    pieces = leiden_chunk(text)
    if not pieces:
        pieces = chunk_conversation(text, source_title)

    if not pieces:
        return {"source_id": source_id, "chunks": 0, "edges": 0}

    # Embed
    vectors = embed([p["content"] for p in pieces])
    chunk_ids = []
    for piece, vec in zip(pieces, vectors):
        cid = _gen_id(f"{source_id}:{piece['chunk_index']}")
        chunk_ids.append(cid)
        chunks_store.append({
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
        })

    # Build edges
    new_edges = []
    for i in range(len(chunk_ids) - 1):
        new_edges.append({"source_id": source_id, "from": chunk_ids[i], "to": chunk_ids[i+1], "type": "next", "weight": 1.0})
        new_edges.append({"source_id": source_id, "from": chunk_ids[i+1], "to": chunk_ids[i], "type": "prev", "weight": 1.0})

    # Similarity edges
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
            new_edges.append({"source_id": source_id, "from": chunk_ids[i], "to": chunk_ids[j], "type": "similar", "weight": score})

    # Entity co-occurrence edges (chunks sharing entities)
    entity_to_chunks: dict[str, list[str]] = defaultdict(list)
    for cid, piece in zip(chunk_ids, pieces):
        for ent in piece.get("entities", []):
            entity_to_chunks[ent].append(cid)
    for ent, cids in entity_to_chunks.items():
        if len(cids) > 1 and len(cids) <= 10:
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    new_edges.append({"source_id": source_id, "from": cids[i], "to": cids[j], "type": "entity", "weight": 0.6})

    edges_store.extend(new_edges)
    sources_store = [s for s in sources_store if s["id"] != source_id]
    sources_store.append({"id": source_id, "title": source_title, "chunks": len(pieces)})

    _save_json(_CHUNKS_FILE, chunks_store)
    _save_json(_EDGES_FILE, edges_store)
    _save_json(_SOURCES_FILE, sources_store)

    return {"source_id": source_id, "chunks": len(pieces), "edges": len(new_edges)}


def search_knowledge(query: str, k: int = 8) -> list[dict]:
    """Hybrid search: BM25 + vector + entity/date matching."""
    chunks_store = _load_json(_CHUNKS_FILE) or []
    if not chunks_store:
        return []

    # Build BM25 index
    bm25 = BM25Index()
    for c in chunks_store:
        bm25.add(_tokenize(c["content"]))

    # BM25 scores
    bm25_results = bm25.search(query, k=k * 3)
    bm25_scores = {i: s for i, s in bm25_results}

    # Vector scores
    qvec = embed([query])[0]
    vec_scores = {}
    for i, c in enumerate(chunks_store):
        vec_scores[i] = cosine_sim(qvec, c["embedding"])

    # Entity/date bonus: if query mentions an entity/date, boost chunks containing it
    query_entities = _extract_entities(query)
    # Also extract quoted terms from query
    for m in re.finditer(r"['\"]([^'\"]{2,40})['\"]", query):
        query_entities.append(m.group(1).lower())
    query_dates = _extract_dates(query)
    query_lower = query.lower()

    # Also extract key nouns from query that might be entity matches
    query_keywords = [w for w in _tokenize(query) if len(w) > 3]

    entity_scores = {}
    for i, c in enumerate(chunks_store):
        bonus = 0.0
        chunk_entities = c.get("entities", [])
        chunk_dates = c.get("dates", [])
        chunk_prefs = c.get("preferences", [])
        chunk_content_lower = c["content"].lower()

        # Entity overlap
        for qe in query_entities:
            if qe in chunk_entities or qe in chunk_content_lower:
                bonus += 0.3

        # Date overlap
        for qd in query_dates:
            if qd in chunk_dates or qd in chunk_content_lower:
                bonus += 0.2

        # Preference match: if query asks about preferences and chunk has them
        is_pref_query = any(w in query_lower for w in ("prefer", "favorite", "like", "go-to", "usually", "recommend", "what do i", "what's my", "what is my"))
        if is_pref_query:
            if chunk_prefs:
                bonus += 0.35
            # Extra: check if the preference content matches query keywords
            for pref in chunk_prefs:
                for kw in query_keywords:
                    if kw in pref:
                        bonus += 0.2
            # Boost chunks containing user statements (preferences come from user, not assistant)
            if "user:" in chunk_content_lower:
                bonus += 0.1

        # Keyword presence bonus (exact term matching beyond BM25)
        for kw in query_keywords:
            if kw in chunk_content_lower:
                bonus += 0.05

        entity_scores[i] = bonus

    # Character n-gram overlap (fuzzy matching — catches morphological variants)
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

    # Fuse scores: weighted combination of 4 signals
    max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
    max_vec = max(vec_scores.values()) if vec_scores else 1.0
    max_ngram = max(ngram_scores.values()) if ngram_scores else 1.0

    fused = {}
    for i in range(len(chunks_store)):
        bm25_norm = bm25_scores.get(i, 0) / max(max_bm25, 0.001)
        vec_norm = vec_scores.get(i, 0) / max(max_vec, 0.001)
        ent_score = entity_scores.get(i, 0)
        ngram_norm = ngram_scores.get(i, 0) / max(max_ngram, 0.001)
        fused[i] = 0.4 * bm25_norm + 0.15 * vec_norm + 0.25 * ent_score + 0.2 * ngram_norm

    # Sort and return top-k
    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)

    results = []
    seen_content = set()
    for i, score in ranked:
        if score <= 0:
            continue
        c = chunks_store[i]
        # Dedup by content similarity
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

    return results


def graph_walk(chunk_id: str, hops: int = 2, k: int = 10) -> list[dict]:
    """BFS graph traversal from a starting chunk."""
    chunks_store = _load_json(_CHUNKS_FILE) or []
    edges_store = _load_json(_EDGES_FILE) or []

    chunk_map = {c["id"]: c for c in chunks_store}
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


def get_context(query: str, k: int = 5, hops: int = 1, min_source_pct: float = 0.75) -> list[dict]:
    """Combined search + graph walk. Returns best chunk per source until coverage target met.

    Retrieves all scored chunks, then picks best chunk from each source (session).
    Keeps adding sources until min_source_pct of total sources are covered.
    """
    sources_store = _load_json(_SOURCES_FILE) or []
    total_sources = len(sources_store)
    target_sources = max(k, int(total_sources * min_source_pct)) if total_sources > 0 else k

    # Score ALL chunks
    chunks_store = _load_json(_CHUNKS_FILE) or []
    all_hits = search_knowledge(query, k=len(chunks_store))
    if not all_hits:
        return []

    # Graph walk from top hits
    seen_ids = {h["chunk_id"] for h in all_hits}
    for top_hit in all_hits[:3]:
        walked = graph_walk(top_hit["chunk_id"], hops=hops, k=k)
        for w in walked:
            if w["chunk_id"] not in seen_ids:
                w["score"] = w["score"] * 0.5
                all_hits.append(w)
                seen_ids.add(w["chunk_id"])

    # Source-diverse: best chunk per source
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

    return selected


def list_sources() -> list[dict]:
    return _load_json(_SOURCES_FILE) or []


def delete_source(source_id: str) -> dict:
    chunks_store = _load_json(_CHUNKS_FILE) or []
    edges_store = _load_json(_EDGES_FILE) or []
    sources_store = _load_json(_SOURCES_FILE) or []

    before = len(chunks_store)
    chunks_store = [c for c in chunks_store if c.get("source_id") != source_id]
    edges_store = [e for e in edges_store if e.get("source_id") != source_id]
    sources_store = [s for s in sources_store if s.get("id") != source_id]

    _save_json(_CHUNKS_FILE, chunks_store)
    _save_json(_EDGES_FILE, edges_store)
    _save_json(_SOURCES_FILE, sources_store)

    return {"deleted_chunks": before - len(chunks_store)}


# ---------------------------------------------------------------------------
# JSON-line RPC protocol
# ---------------------------------------------------------------------------
METHODS = {
    "add_knowledge": lambda p: add_knowledge(p["text"], p.get("title", "untitled")),
    "search": lambda p: search_knowledge(p["query"], p.get("k", 8)),
    "graph_walk": lambda p: graph_walk(p["chunk_id"], p.get("hops", 2), p.get("k", 10)),
    "get_context": lambda p: get_context(p["query"], p.get("k", 5), p.get("hops", 1), p.get("min_source_pct", 0.15)),
    "list_sources": lambda p: list_sources(),
    "delete_source": lambda p: delete_source(p["source_id"]),
    "ping": lambda p: {"status": "ok"},
}


def main():
    log.info("scimap engine started (data_dir=%s)", _DATA_DIR)
    _get_embedder()

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
