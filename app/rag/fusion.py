"""Stage 3: Reciprocal Rank Fusion. RRF(d) = sum_r 1 / (k + rank_r(d)), ranks are 1-based.

Uses ranks only, so BM25 scores and cosine similarities never need scale normalisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable, Sequence

from app import config


@dataclass
class FusedCandidate:
    key: Hashable
    rrf_score: float
    ranks: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(
    rankings: dict[str, Sequence[Hashable]], k: int = config.RRF_K
) -> list[FusedCandidate]:
    """`rankings` maps retriever name -> ordered list of candidate keys (best first)."""
    pool: dict[Hashable, FusedCandidate] = {}
    for name, ranked in rankings.items():
        for rank, key in enumerate(ranked, start=1):
            cand = pool.setdefault(key, FusedCandidate(key, 0.0))
            cand.rrf_score += 1.0 / (k + rank)
            cand.ranks[name] = rank
    fused = list(pool.values())
    fused.sort(key=lambda c: (-c.rrf_score, min(c.ranks.values()), str(c.key)))
    return fused
