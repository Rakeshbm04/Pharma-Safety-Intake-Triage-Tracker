"""Stage 2: dense semantic retrieval (bi-encoder + cosine similarity).

* NeuralBiEncoder  - sentence-transformers MiniLM, loaded from the local HF cache only.
* OfflineEncoder   - deterministic NumPy fallback: medical-synonym normalisation ->
                     sublinear TF-IDF -> LSA (truncated SVD). Captures lay/clinical synonymy
                     such as "difficulty breathing" ~ "dyspnea" through shared concept tokens.
"""
from __future__ import annotations

import math
import os
from collections import Counter
from typing import Protocol, Sequence

import numpy as np

from app import config
from app.rag.text import concept_tokens


class Encoder(Protocol):
    name: str

    def fit(self, documents: Sequence[str]) -> None: ...
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2_normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class OfflineEncoder:
    name = "offline-lsa"

    def __init__(self, dim: int = config.OFFLINE_LSA_DIM):
        self.dim = dim
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray = np.zeros(0)
        self.components: np.ndarray | None = None

    def _tfidf(self, texts: Sequence[str]) -> np.ndarray:
        mat = np.zeros((len(texts), len(self.vocab)), dtype=np.float64)
        for i, text in enumerate(texts):
            for tok, cnt in Counter(concept_tokens(text)).items():
                j = self.vocab.get(tok)
                if j is not None:
                    mat[i, j] = 1.0 + math.log(cnt)
        return _l2_normalize(mat * self.idf)

    def fit(self, documents: Sequence[str]) -> None:
        df: Counter[str] = Counter()
        for doc in documents:
            df.update(set(concept_tokens(doc)))
        self.vocab = {t: i for i, t in enumerate(sorted(df))}
        n = max(len(documents), 1)
        self.idf = np.array([math.log((1 + n) / (1 + df[t])) + 1.0 for t in sorted(df)])
        if not documents or not self.vocab:
            self.components = None
            return
        x = self._tfidf(documents)
        _, s, vt = np.linalg.svd(x, full_matrices=False)
        k = int(min(self.dim, int((s > 1e-10).sum()) or 1))
        self.components = vt[:k].T  # (vocab, k)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self.components is None:
            return np.zeros((len(texts), 1))
        return _l2_normalize(self._tfidf(texts) @ self.components)


class NeuralBiEncoder:
    def __init__(self, model_name: str = config.BI_ENCODER_MODEL):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")          # never reach the network at runtime
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model = SentenceTransformer(model_name, device="cpu", local_files_only=True)
        self.model.max_seq_length = min(config.CHUNK_TOKENS, config.MODEL_MAX_SEQ_TOKENS)
        self.name = f"neural:{model_name.split('/')[-1]}"

    def fit(self, documents: Sequence[str]) -> None:  # pre-trained, nothing to fit
        return None

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1))
        return np.asarray(self.model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False))


class DenseIndex:
    def __init__(self, encoder: Encoder, documents: Sequence[str]):
        self.encoder = encoder
        encoder.fit(documents)
        self.matrix = encoder.encode(documents) if documents else np.zeros((0, 1))

    def search(self, query: str, top_n: int = config.RETRIEVER_TOP_N) -> list[tuple[int, float]]:
        if self.matrix.shape[0] == 0:
            return []
        q = self.encoder.encode([query])[0]
        sims = self.matrix @ q  # rows are L2-normalised -> cosine similarity
        order = sorted(range(len(sims)), key=lambda i: (-float(sims[i]), i))
        return [(i, float(sims[i])) for i in order[:top_n] if sims[i] > 0]
