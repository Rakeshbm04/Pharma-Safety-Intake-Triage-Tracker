"""Four-stage hybrid retrieval: BM25 -> bi-encoder -> RRF -> cross-encoder rerank.

The final answer is *extractive*: it only contains sentences copied verbatim from the
retained chunks, each with a [source_id] citation. If no chunk clears the relevance
threshold the pipeline says so instead of guessing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Sequence

from app import config
from app.rag.bm25 import BM25Index
from app.rag.chunking import Chunk
from app.rag.dense import DenseIndex, Encoder, NeuralBiEncoder, OfflineEncoder
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.rerank import CalibratedCrossScorer, CrossScorer, NeuralCrossEncoder, OfflineCrossScorer, rerank
from app.rag.text import split_sentences

log = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = (
    "No sufficiently relevant records or SOP passages were found (all candidates scored below the "
    f"{config.RERANK_THRESHOLD:.2f} relevance threshold). No answer has been generated."
)

PROMPT_TEMPLATE = """SYSTEM: You are a retrieval assistant for a FICTIONAL pharmacovigilance training prototype.
Answer ONLY with facts stated in the CONTEXT below and cite each fact as [source_id].
If the CONTEXT does not contain the answer, reply "Not found in records."
Never assess causality, recommend treatment, or determine regulatory reportability.

CONTEXT:
{context}

QUESTION: {query}
ANSWER:"""


@dataclass
class StageHit:
    rank: int
    chunk_id: str
    source_id: str
    title: str
    score: float
    extra: dict = field(default_factory=dict)


@dataclass
class RAGResult:
    query: str
    backend: str
    bm25: list[StageHit]
    dense: list[StageHit]
    fused: list[StageHit]
    reranked: list[StageHit]
    final: list[StageHit]
    final_chunks: list[Chunk]
    answer: str
    prompt: str

    @property
    def citations(self) -> list[str]:
        return list(dict.fromkeys(h.source_id for h in self.final))

    @property
    def found(self) -> bool:
        return bool(self.final)


@lru_cache(maxsize=1)
def _neural_models() -> tuple[NeuralBiEncoder, NeuralCrossEncoder]:
    return NeuralBiEncoder(), NeuralCrossEncoder()


def load_backend(mode: str = config.RAG_BACKEND) -> tuple[Encoder, CrossScorer, str]:
    """'neural' | 'offline' | 'auto' (neural when locally cached, otherwise offline)."""
    if mode in ("neural", "auto"):
        try:
            enc, cross = _neural_models()
            return enc, CalibratedCrossScorer(cross, OfflineCrossScorer()), "neural"
        except Exception as exc:  # missing package or model not in local cache
            if mode == "neural":
                raise RuntimeError(f"Neural RAG backend unavailable: {exc}") from exc
            log.info("Neural backend unavailable (%s); using offline encoders.", exc)
    return OfflineEncoder(), OfflineCrossScorer(), "offline"


class HybridRAGPipeline:
    def __init__(self, chunks: Sequence[Chunk], backend: str = config.RAG_BACKEND):
        self.chunks = list(chunks)
        encoder, self.cross, self.backend = load_backend(backend)
        texts = [c.text for c in self.chunks]
        self.bm25 = BM25Index(texts)
        self.dense = DenseIndex(encoder, texts)
        self.backend_detail = f"{self.backend} ({encoder.name} + {self.cross.name})"

    def _hit(self, rank: int, idx: int, score: float, **extra) -> StageHit:
        c = self.chunks[idx]
        return StageHit(rank, c.chunk_id, c.source_id, c.title, round(float(score), 4), extra)

    def search(
        self,
        query: str,
        top_n: int = config.RETRIEVER_TOP_N,
        threshold: float = config.RERANK_THRESHOLD,
        top_k: int = config.FINAL_TOP_K,
    ) -> RAGResult:
        query = query.strip()
        # Stage 1 + 2: independent candidate generation (top-50 each).
        bm25_hits = self.bm25.search(query, top_n)
        dense_hits = self.dense.search(query, top_n)
        # Stage 3: rank-only fusion.
        fused = reciprocal_rank_fusion(
            {"bm25": [i for i, _ in bm25_hits], "dense": [i for i, _ in dense_hits]}, k=config.RRF_K
        )
        pool = [int(c.key) for c in fused]
        # Stage 4: joint query-passage scoring, threshold, top-k.
        scored, kept = rerank(self.cross, query, [self.chunks[i].text for i in pool], threshold, top_k)

        final_idx = [pool[j] for j, _ in kept]
        final_chunks = [self.chunks[i] for i in final_idx]
        result = RAGResult(
            query=query,
            backend=self.backend_detail,
            bm25=[self._hit(r, i, s) for r, (i, s) in enumerate(bm25_hits, 1)],
            dense=[self._hit(r, i, s) for r, (i, s) in enumerate(dense_hits, 1)],
            fused=[self._hit(r, int(c.key), c.rrf_score, ranks=dict(c.ranks)) for r, c in enumerate(fused, 1)],
            reranked=[self._hit(r, pool[j], s, kept=s >= threshold) for r, (j, s) in enumerate(scored, 1)],
            final=[self._hit(r, pool[j], s) for r, (j, s) in enumerate(kept, 1)],
            final_chunks=final_chunks,
            answer="",
            prompt=build_prompt(query, final_chunks),
        )
        result.answer = compose_extractive_answer(query, final_chunks)
        return result


def build_prompt(query: str, chunks: Sequence[Chunk]) -> str:
    context = "\n\n".join(f"[{c.source_id}] ({c.title})\n{c.text}" for c in chunks) or "(no context retrieved)"
    return PROMPT_TEMPLATE.format(context=context, query=query)


def compose_extractive_answer(query: str, chunks: Sequence[Chunk], per_chunk: int = 2) -> str:
    """Grounded answer = verbatim best-matching sentences from each retained chunk + citations."""
    if not chunks:
        return NO_CONTEXT_ANSWER
    scorer = OfflineCrossScorer()
    lines = []
    for c in chunks:
        sentences = split_sentences(c.text)
        if not sentences:
            continue
        ranked = sorted(sentences, key=lambda s: -scorer.score_one(query, s))
        picked = [s for s in ranked[:per_chunk] if scorer.score_one(query, s) > 0] or ranked[:1]
        # keep original order for readability
        picked.sort(key=sentences.index)
        lines.append(f"- [{c.source_id}] " + " … ".join(picked))
    return "Relevant recorded information (verbatim excerpts):\n" + "\n".join(lines) + f"\n\n_{config.DISCLAIMER}_"
