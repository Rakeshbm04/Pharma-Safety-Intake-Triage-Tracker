"""Stage 4: precision cross-encoder reranking with a relevance threshold.

* NeuralCrossEncoder  - ms-marco MiniLM cross-encoder (joint attention over query+chunk),
                        logits mapped to [0, 1] with a sigmoid.
* OfflineCrossScorer  - deterministic fallback that scores each (query, chunk) *pair* jointly:
                        synonym-aware query-term coverage, phrase (bigram) matches and an
                        exact-identifier boost / mismatch penalty, clipped to [0, 1].
"""
from __future__ import annotations

import math
import os
from typing import Protocol, Sequence

import numpy as np

from app import config
from app.rag.text import QUERY_FILLER, concept_tokens, extract_ids


class CrossScorer(Protocol):
    name: str

    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


def _query_terms(query: str) -> list[str]:
    return [t for t in concept_tokens(query) if t not in QUERY_FILLER]


class OfflineCrossScorer:
    name = "offline-cross-scorer"

    def score_one(self, query: str, passage: str) -> float:
        q_terms = _query_terms(query)
        q_ids = extract_ids(query)
        p_tokens = concept_tokens(passage)
        p_set = set(p_tokens)
        p_ids = extract_ids(passage)
        if not q_terms and not q_ids:
            return 0.0
        id_match = len(q_ids & p_ids) / len(q_ids) if q_ids else 0.0

        # Coverage of the *content* terms (identifiers are scored separately below so that an
        # ID-only match cannot masquerade as topical relevance). Medical concepts weigh double.
        content = {t for t in q_terms if t.upper() not in q_ids}
        if content:
            weights = {t: (2.0 if t.startswith("c_") else 1.0) for t in content}
            coverage = sum(w for t, w in weights.items() if t in p_set) / sum(weights.values())
        else:  # pure identifier lookup, e.g. "LOT-45127"
            coverage = id_match

        q_bigrams = set(zip(q_terms, q_terms[1:]))
        p_bigrams = set(zip(p_tokens, p_tokens[1:]))
        phrase = len(q_bigrams & p_bigrams) / len(q_bigrams) if q_bigrams else 0.0

        score = 0.75 * coverage + 0.15 * phrase
        if q_ids:  # reward the requested ID; penalise passages about a different batch/report
            score += 0.25 * id_match if id_match else -0.25
        return float(min(1.0, max(0.0, score)))

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [self.score_one(query, p) for p in passages]


class NeuralCrossEncoder:
    def __init__(self, model_name: str = config.CROSS_ENCODER_MODEL):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import CrossEncoder  # optional dependency

        self.model = CrossEncoder(model_name, device="cpu", max_length=config.MODEL_MAX_SEQ_TOKENS, local_files_only=True)
        self.name = f"neural:{model_name.split('/')[-1]}"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        raw = np.asarray(self.model.predict([(query, p) for p in passages], show_progress_bar=False), dtype=float)
        if raw.min() < 0.0 or raw.max() > 1.0:  # logits -> probability
            raw = 1.0 / (1.0 + np.exp(-raw))
        return [float(x) for x in raw]


class CalibratedCrossScorer:
    """Domain calibration for an out-of-domain neural cross-encoder.

    ms-marco models are trained on web search and under-score short pharmacovigilance records
    (and cannot know that LOT-45127 != LOT-88392 matters). The final relevance is a convex blend
    of the neural probability and the domain-aware joint scorer (synonyms + exact-ID consistency).
    """

    def __init__(self, neural: CrossScorer, domain: CrossScorer, neural_weight: float = config.RERANK_NEURAL_WEIGHT):
        if not 0.0 <= neural_weight <= 1.0:
            raise ValueError("neural_weight must be within [0, 1]")
        self.neural, self.domain, self.w = neural, domain, neural_weight
        self.name = f"{neural.name} calibrated-with {domain.name} (w={neural_weight})"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        n = self.neural.score(query, passages)
        d = self.domain.score(query, passages)
        return [self.w * a + (1 - self.w) * b for a, b in zip(n, d)]


def rerank(
    scorer: CrossScorer,
    query: str,
    passages: Sequence[str],
    threshold: float = config.RERANK_THRESHOLD,
    top_k: int = config.FINAL_TOP_K,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (all_scored, kept): kept = score >= threshold, best first, capped at top_k."""
    scores = scorer.score(query, passages)
    scored = sorted(enumerate(scores), key=lambda x: (-x[1], x[0]))
    kept = [(i, s) for i, s in scored if s >= threshold and not math.isnan(s)][:top_k]
    return scored, kept
