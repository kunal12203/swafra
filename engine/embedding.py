"""Embedding model (fastembed) and vector similarity."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re

log = logging.getLogger("scimap.engine")

_EMBED_MODEL = None
_EMBED_DIM = 384


def _get_embedder():
    global _EMBED_MODEL, _EMBED_DIM
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    from fastembed import TextEmbedding
    model_name = os.getenv("SCIMAP_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    _EMBED_MODEL = TextEmbedding(model_name=model_name)
    test = list(_EMBED_MODEL.embed(["test"]))[0]
    _EMBED_DIM = len(test)
    log.info("fastembed loaded: model=%s, dim=%d", model_name, _EMBED_DIM)
    return _EMBED_MODEL


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def local_vector(text: str) -> list[float]:
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
    embeddings = list(model.embed(texts))
    return [emb.tolist() for emb in embeddings]


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def init_embedder():
    _get_embedder()
