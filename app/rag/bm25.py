"""Stage 1: lexical sparse retrieval with Okapi BM25.

score(q, d) = sum_t IDF(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
IDF(t)      = ln(1 + (N - df + 0.5) / (df + 0.5))
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from app import config
from app.rag.text import tokenize


class BM25Index:
    def __init__(self, documents: Sequence[str], k1: float = config.BM25_K1, b: float = config.BM25_B):
        lo, hi = config.BM25_K1_RANGE
        if not lo <= k1 <= hi:
            raise ValueError(f"BM25 k1 must be within [{lo}, {hi}]")
        if not 0.0 <= b <= 1.0:
            raise ValueError("BM25 b must be within [0, 1]")
        self.k1, self.b = k1, b
        self.doc_tokens = [tokenize(d) for d in documents]
        self.tf = [Counter(toks) for toks in self.doc_tokens]
        self.doc_len = [len(toks) for toks in self.doc_tokens]
        self.n_docs = len(documents)
        self.avgdl = (sum(self.doc_len) / self.n_docs) if self.n_docs else 0.0
        df: Counter[str] = Counter()
        for toks in self.doc_tokens:
            df.update(set(toks))
        self.idf = {t: math.log(1 + (self.n_docs - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def score(self, query: str) -> list[float]:
        q_terms = tokenize(query)
        scores = [0.0] * self.n_docs
        if not self.avgdl:
            return scores
        for i, tf in enumerate(self.tf):
            norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
            s = 0.0
            for t in q_terms:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            scores[i] = s
        return scores

    def search(self, query: str, top_n: int = config.RETRIEVER_TOP_N) -> list[tuple[int, float]]:
        """Return (doc_index, score) for documents with score > 0, best first."""
        scored = [(i, s) for i, s in enumerate(self.score(query)) if s > 0]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_n]
