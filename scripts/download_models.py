"""One-time setup: fetch the optional neural RAG models into the local HuggingFace cache.

This is the ONLY step that touches the network. At runtime the app loads models with
`local_files_only=True` / HF_HUB_OFFLINE=1 and falls back to offline encoders if absent.

    python scripts/download_models.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402


def main() -> int:
    try:
        from sentence_transformers import CrossEncoder, SentenceTransformer
    except ImportError:
        print("sentence-transformers not installed: pip install -r requirements-ml.txt")
        return 1
    print(f"Downloading bi-encoder {config.BI_ENCODER_MODEL} ...")
    SentenceTransformer(config.BI_ENCODER_MODEL, device="cpu")
    print(f"Downloading cross-encoder {config.CROSS_ENCODER_MODEL} ...")
    CrossEncoder(config.CROSS_ENCODER_MODEL, device="cpu")
    print("Models cached locally. The app will now use the neural RAG backend offline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
