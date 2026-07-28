"""BM25 index for keyword-based retrieval."""
from __future__ import annotations

import math
from collections import Counter

from engine.tokenizer import tokenize


class BM25Index:
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
        query_tokens = tokenize(query)
        scores = [(i, self.score(query_tokens, i)) for i in range(self.n_docs)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(i, s) for i, s in scores[:k] if s > 0]
