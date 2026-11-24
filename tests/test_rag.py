"""Four-stage hybrid RAG: each stage in isolation plus the full pipeline (offline backend)."""
from __future__ import annotations

import pytest

from app import config
from app.rag.bm25 import BM25Index
from app.rag.chunking import chunk_text, window_tokens
from app.rag.corpus import load_sop_chunks
from app.rag.dense import DenseIndex, OfflineEncoder
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.pipeline import NO_CONTEXT_ANSWER, HybridRAGPipeline, load_backend
from app.rag.rerank import CalibratedCrossScorer, OfflineCrossScorer, rerank
from app.rag.service import KnowledgeBase
from app.rag.text import normalize_medical, tokenize

DOCS = [
    "REF-2026-0001 Suspected Adverse Event. Batch LOT-88392. Event: dyspnea after the second dose.",
    "REF-2026-0002 Product Quality Complaint. Batch LOT-45127. Chipped tablets in bottle.",
    "REF-2026-0003 Product Quality Complaint. Batch LOT-60218. Floating particles in a vial.",
    "SOP text about reference numbers and the triage workflow for intake operators.",
]


# ---- text + chunking --------------------------------------------------------
def test_tokenizer_keeps_identifiers():
    toks = tokenize("Batch LOT-88392 and REF-2026-0001 noted")
    assert "lot-88392" in toks and "ref-2026-0001" in toks


def test_synonym_normalisation():
    assert "c_dyspnea" in normalize_medical("difficulty breathing")
    assert "c_dyspnea" in normalize_medical("Dyspnea noted")
    assert "c_urticaria" in normalize_medical("hives on arms")


def test_chunk_windows_512_with_64_overlap():
    w = window_tokens(1200)
    assert w[0] == (0, 512) and w[1] == (448, 960) and w[-1][1] == 1200
    assert all(b[0] == a[1] - config.CHUNK_OVERLAP for a, b in zip(w, w[1:]))
    chunks = chunk_text(" ".join(f"w{i}" for i in range(1200)), "SOP-X", "SOP", "t")
    assert len(chunks) == 3 and len(chunks[0].text.split()) == 512
    assert chunks[0].text.split()[-64:] == chunks[1].text.split()[:64]


def test_invalid_chunk_params():
    with pytest.raises(ValueError):
        window_tokens(10, size=64, overlap=64)


def test_sop_corpus_loaded():
    chunks = load_sop_chunks()
    assert {c.source_id for c in chunks} >= {f"SOP-00{i}" for i in range(1, 7)}
    assert all(c.source_type == "SOP" and c.text for c in chunks)


# ---- stage 1: BM25 -----------------------------------------------------------
def test_bm25_exact_lot_match_ranks_first():
    idx = BM25Index(DOCS)
    hits = idx.search("LOT-45127")
    assert hits[0][0] == 1 and len(hits) == 1


def test_bm25_parameters_validated_and_length_normalisation():
    with pytest.raises(ValueError):
        BM25Index(DOCS, k1=2.0)
    short, long = "anaphylaxis reported", "anaphylaxis reported " + "filler words " * 60
    with_b = BM25Index([short, long], b=0.75).score("anaphylaxis")
    no_b = BM25Index([short, long], b=0.0).score("anaphylaxis")
    assert with_b[0] > with_b[1]           # long document penalised
    assert no_b[0] == pytest.approx(no_b[1])


def test_bm25_tf_saturation():
    idx = BM25Index(["rash", "rash rash rash rash rash rash rash rash", "other text here"], b=0.0)
    s = idx.score("rash")
    assert s[1] > s[0] and s[1] < s[0] * (config.BM25_K1 + 1)  # bounded by (k1+1)


# ---- stage 2: dense -----------------------------------------------------------
def test_dense_semantic_synonym_match():
    idx = DenseIndex(OfflineEncoder(), DOCS)
    hits = idx.search("patient had difficulty breathing")
    assert hits[0][0] == 0  # matches the "dyspnea" document with no shared surface word


def test_dense_cosine_bounds():
    idx = DenseIndex(OfflineEncoder(), DOCS)
    assert all(-1.0001 <= s <= 1.0001 for _, s in idx.search("vial particles"))


# ---- stage 3: RRF --------------------------------------------------------------
def test_rrf_formula():
    fused = reciprocal_rank_fusion({"bm25": ["a", "b"], "dense": ["b", "c"]}, k=60)
    scores = {c.key: c.rrf_score for c in fused}
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["c"] == pytest.approx(1 / 62)
    assert fused[0].key == "b" and fused[0].ranks == {"bm25": 2, "dense": 1}


def test_rrf_uses_ranks_not_scores():
    # identical rankings -> identical fusion regardless of raw score scales
    assert [c.key for c in reciprocal_rank_fusion({"x": [3, 1, 2]})] == [3, 1, 2]


# ---- stage 4: rerank -----------------------------------------------------------
def test_offline_cross_scorer_threshold_and_top_k():
    passages = DOCS * 3
    scored, kept = rerank(OfflineCrossScorer(), "difficulty breathing LOT-88392", passages, 0.40, 2)
    assert len(scored) == len(passages) and len(kept) == 2
    assert all(s >= 0.40 for _, s in kept)
    assert all(passages[i] == DOCS[0] for i, _ in kept)


def test_cross_scorer_penalises_wrong_identifier():
    s = OfflineCrossScorer()
    assert s.score_one("chipped tablets LOT-88392", DOCS[1]) < s.score_one("chipped tablets LOT-45127", DOCS[1])


def test_irrelevant_query_scores_zero():
    assert OfflineCrossScorer().score_one("weather in Paris", DOCS[0]) == 0.0


def test_calibrated_blend():
    class Fixed:
        name = "fixed"

        def __init__(self, v):
            self.v = v

        def score(self, q, ps):
            return [self.v] * len(ps)

    blend = CalibratedCrossScorer(Fixed(1.0), Fixed(0.0), neural_weight=0.5)
    assert blend.score("q", ["p"]) == [0.5]


# ---- full pipeline ---------------------------------------------------------------
@pytest.fixture
def kb(make_report, ae_payload, pqc_payload, db_path):
    make_report(ae_payload)          # REF-...-0001 shortness of breath, LOT-88392
    make_report(pqc_payload)         # REF-...-0002 chipped tablets, LOT-45127
    return KnowledgeBase(db_path, backend="offline")


def test_pipeline_end_to_end(kb):
    r = kb.pipeline().search("difficulty breathing reported for LOT-88392")
    assert r.bm25 and r.dense and r.fused
    assert len(r.fused) >= max(len(r.bm25), len(r.dense))
    assert 1 <= len(r.final) <= config.FINAL_TOP_K
    assert all(h.score >= config.RERANK_THRESHOLD for h in r.final)
    assert r.final[0].source_id.endswith("0001")
    assert f"[{r.final[0].source_id}]" in r.answer and config.DISCLAIMER in r.answer
    assert "CONTEXT" in r.prompt and r.final_chunks[0].text in r.prompt


def test_answer_is_extractive(kb):
    r = kb.pipeline().search("chipped tablets LOT-45127")
    corpus = " ".join(c.text for c in r.final_chunks)
    for line in r.answer.splitlines():
        if line.startswith("- ["):
            for piece in line.split("] ", 1)[1].split(" … "):
                assert piece in corpus  # every sentence is copied verbatim


def test_irrelevant_query_returns_no_answer(kb):
    r = kb.pipeline().search("weather forecast for Paris tomorrow")
    assert not r.found and r.answer == NO_CONTEXT_ANSWER


def test_index_refreshes_when_reports_change(kb, make_report, pqc_payload):
    first = kb.pipeline()
    make_report({**pqc_payload, "batch_number": "LOT-12345"})
    second = kb.pipeline()
    assert second is not first
    assert second.search("LOT-12345").final[0].source_id.endswith("0003")


def test_offline_backend_selected():
    _, _, name = load_backend("offline")
    assert name == "offline"


def test_pipeline_with_empty_corpus():
    r = HybridRAGPipeline([], backend="offline").search("anything")
    assert not r.found


@pytest.mark.neural
def test_neural_backend_if_available(kb):
    pytest.importorskip("sentence_transformers")
    try:
        _, scorer, name = load_backend("neural")
    except RuntimeError:
        pytest.skip("neural models not cached locally")
    assert name == "neural"
    p = HybridRAGPipeline(kb.pipeline().chunks, backend="neural")
    r = p.search("difficulty breathing reported for LOT-88392")
    assert r.found and r.final[0].source_id.endswith("0001")
