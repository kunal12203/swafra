"""Text chunking — conversation-aware and Leiden community detection."""
from __future__ import annotations

import re

from engine.embedding import embed
from engine.extractors import extract_dates, extract_entities, extract_preferences

# ---------------------------------------------------------------------------
# Conversation-aware chunking
# ---------------------------------------------------------------------------


def chunk_conversation(text: str, source_title: str = "") -> list[dict]:
    chunks = []
    chunk_idx = 0

    is_conversation = bool(re.search(r'^(User|Assistant|Human|AI):', text, re.M))

    if is_conversation:
        turn_pattern = re.compile(r'^(User|Assistant|Human|AI):\s*', re.M)
        parts = turn_pattern.split(text)

        current_exchange = []
        exchanges = []
        i = 1
        while i < len(parts) - 1:
            role = parts[i].lower()
            content = parts[i + 1].strip()
            current_exchange.append(f"{role}: {content}")
            if role in ("assistant", "ai") and current_exchange:
                exchanges.append("\n".join(current_exchange))
                current_exchange = []
            i += 2
        if current_exchange:
            exchanges.append("\n".join(current_exchange))

        group_size = 2
        for gi in range(0, len(exchanges), group_size):
            group = exchanges[gi:gi + group_size]
            content = "\n\n".join(group)
            words = content.split()
            entities = extract_entities(content)
            dates = extract_dates(content)
            prefs = extract_preferences(content)

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
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
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
                    "entities": extract_entities(content),
                    "dates": extract_dates(content),
                    "preferences": extract_preferences(content),
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
                "entities": extract_entities(content),
                "dates": extract_dates(content),
                "preferences": extract_preferences(content),
                "type": "paragraph",
            })
            chunk_idx += 1

    return chunks


# ---------------------------------------------------------------------------
# Community-detection chunking (tiered: Leiden → Louvain → ungrouped)
# ---------------------------------------------------------------------------
_SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])|(?<=\n)\s*(?=\S)|(?<=\.)\s*\n')
_MIN_SENTENCES = 8

_CLUSTER_BACKEND = None  # cached: "leiden", "louvain", or "none"


def _detect_backend() -> str:
    global _CLUSTER_BACKEND
    if _CLUSTER_BACKEND is not None:
        return _CLUSTER_BACKEND
    try:
        import igraph  # noqa: F401
        import leidenalg  # noqa: F401
        _CLUSTER_BACKEND = "leiden"
    except ImportError:
        try:
            import networkx  # noqa: F401
            _CLUSTER_BACKEND = "louvain"
        except ImportError:
            _CLUSTER_BACKEND = "none"
    return _CLUSTER_BACKEND


def _build_sim_matrix(sentences: list[str]) -> tuple:
    """Embed sentences and return (numpy sim_matrix, n)."""
    import numpy as np
    embeddings = embed(sentences)
    emb_array = np.array(embeddings, dtype=np.float32)
    norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    emb_array = emb_array / norms
    sim_matrix = emb_array @ emb_array.T
    return sim_matrix, len(sentences)


def _build_edges(sim_matrix, n: int) -> tuple[list, list]:
    """Build edge list with proximity boost and adaptive threshold."""
    import numpy as np

    all_sims = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(sim_matrix[i][j])
            dist = abs(i - j)
            if dist <= 3:
                sim += 0.2 * (1 - dist / 4)
            all_sims.append(sim)

    # Adaptive threshold: median of boosted similarities, floored at 0.5
    # Keeps ~50% of strongest edges — enough structure for community detection
    # without drowning signal in weak cross-topic connections
    threshold = max(0.5, float(np.median(all_sims))) if all_sims else 0.5

    edges, weights = [], []
    idx = 0
    for i in range(n):
        for j in range(i + 1, n):
            if all_sims[idx] >= threshold:
                edges.append((i, j))
                weights.append(all_sims[idx])
            idx += 1
    return edges, weights


def _cluster_leiden(sentences: list[str], sim_matrix, n: int, edges, weights) -> list[list[int]]:
    import igraph as ig
    import leidenalg

    g = ig.Graph(n=n, directed=False)
    g.add_edges(edges)
    g.es["weight"] = weights

    partition = leidenalg.find_partition(
        g, leidenalg.RBConfigurationVertexPartition,
        weights="weight", resolution_parameter=1.0,
        n_iterations=-1, seed=42)
    return [list(comm) for comm in partition if comm]


def _cluster_louvain(sentences: list[str], sim_matrix, n: int, edges, weights) -> list[list[int]]:
    import networkx as nx

    G = nx.Graph()
    G.add_nodes_from(range(n))
    for (i, j), w in zip(edges, weights):
        G.add_edge(i, j, weight=w)

    communities = nx.community.louvain_communities(
        G, weight="weight", resolution=1.0, seed=42)
    return [list(comm) for comm in communities if comm]


def _cluster_none(sentences: list[str], sim_matrix, n: int, edges, weights) -> list[list[int]]:
    """Connected-components fallback — no optimization, just groups connected nodes."""
    adj: dict[int, set[int]] = {i: set() for i in range(n)}
    for i, j in edges:
        adj[i].add(j)
        adj[j].add(i)

    visited = set()
    communities = []
    for start in range(n):
        if start in visited:
            continue
        comp = []
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            comp.append(node)
            stack.extend(adj[node] - visited)
        communities.append(comp)
    return communities


def leiden_chunk(text: str) -> list[dict]:
    sentences = _SENT_RE.split(text.strip())
    sentences = [s.strip() for s in sentences if s and s.strip()]
    if len(sentences) < _MIN_SENTENCES:
        return []

    sim_matrix, n = _build_sim_matrix(sentences)
    edges, weights = _build_edges(sim_matrix, n)
    if not edges:
        return []

    backend = _detect_backend()
    if backend == "leiden":
        communities = _cluster_leiden(sentences, sim_matrix, n, edges, weights)
    elif backend == "louvain":
        communities = _cluster_louvain(sentences, sim_matrix, n, edges, weights)
    else:
        communities = _cluster_none(sentences, sim_matrix, n, edges, weights)

    chunks = []
    for ci, comm in enumerate(communities):
        comm_sorted = sorted(comm)
        content = " ".join(sentences[i] for i in comm_sorted)
        wc = sum(len(sentences[i].split()) for i in comm_sorted)
        chunks.append({
            "content": content,
            "token_count": wc,
            "span": [0, wc],
            "chunk_index": ci,
            "community_id": ci,
            "entities": extract_entities(content),
            "dates": extract_dates(content),
            "preferences": extract_preferences(content),
            "type": "leiden" if backend == "leiden" else "louvain" if backend == "louvain" else "component",
        })

    return chunks
