"""Single source of truth for paths and tunable constants."""
from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SOP_DIR = DATA_DIR / "sops"
DB_PATH = Path(os.environ.get("PHARMA_DB_PATH", DATA_DIR / "pharma_intake.db"))

# ---- Validation -----------------------------------------------------------
BATCH_PATTERN = r"^LOT-\d{5}$"
REF_PATTERN = r"^REF-\d{4}-\d{4}$"
PRODUCT_MIN, PRODUCT_MAX = 2, 100
DESCRIPTION_MIN, DESCRIPTION_MAX = 20, 2000
EVENT_DESC_MIN, EVENT_DESC_MAX = 10, 2000

# ---- RAG pipeline ---------------------------------------------------------
# "auto" -> neural if sentence-transformers + cached models are available, else offline.
RAG_BACKEND = os.environ.get("PHARMA_RAG_BACKEND", "auto").lower()
BI_ENCODER_MODEL = os.environ.get("PHARMA_BI_ENCODER", "sentence-transformers/all-MiniLM-L6-v2")
CROSS_ENCODER_MODEL = os.environ.get("PHARMA_CROSS_ENCODER", "cross-encoder/ms-marco-MiniLM-L-6-v2")

BM25_K1 = 1.5            # term-frequency saturation (spec range 1.2 - 1.5)
BM25_K1_RANGE = (1.2, 1.5)
BM25_B = 0.75            # document length normalisation
CHUNK_TOKENS = 512
CHUNK_OVERLAP = 64
RETRIEVER_TOP_N = 50     # candidates taken from each of BM25 and the bi-encoder
RRF_K = 60
RERANK_THRESHOLD = 0.40
RERANK_NEURAL_WEIGHT = 0.5   # neural cross-encoder share of the calibrated rerank score
FINAL_TOP_K = 5          # 3-5 chunks go to the prompt; the threshold wins over padding
OFFLINE_LSA_DIM = 128
MODEL_MAX_SEQ_TOKENS = 512    # positional limit of the MiniLM encoders

# ---- Agent control plane --------------------------------------------------
CB_MAX_CONSECUTIVE_FAILURES = 2   # the 3rd identical invalid call trips the breaker
SHORT_TERM_MEMORY_MAX_STEPS = 50

DISCLAIMER = (
    "Training prototype using fictional data. This assistant only retrieves recorded "
    "information; it does not assess causality, recommend treatment, or determine "
    "regulatory reportability."
)
