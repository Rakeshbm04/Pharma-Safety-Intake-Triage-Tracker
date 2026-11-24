"""Sliding-window chunking: CHUNK_TOKENS-token windows with CHUNK_OVERLAP-token overlap."""
from __future__ import annotations

from dataclasses import dataclass, field

from app import config


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_id: str          # e.g. "REF-2026-0001" or "SOP-002"
    source_type: str        # "REPORT" | "SOP"
    title: str
    text: str
    metadata: dict = field(default_factory=dict, hash=False, compare=False)


def window_tokens(n_tokens: int, size: int = config.CHUNK_TOKENS, overlap: int = config.CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """Return [start, end) windows covering n_tokens; consecutive windows share `overlap` tokens."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be > overlap >= 0")
    if n_tokens <= 0:
        return []
    step = size - overlap
    windows, start = [], 0
    while True:
        end = min(start + size, n_tokens)
        windows.append((start, end))
        if end >= n_tokens:
            break
        start += step
    return windows


def chunk_text(
    text: str,
    source_id: str,
    source_type: str,
    title: str,
    size: int = config.CHUNK_TOKENS,
    overlap: int = config.CHUNK_OVERLAP,
    metadata: dict | None = None,
) -> list[Chunk]:
    """Split on whitespace tokens (a word-level proxy for model tokens)."""
    words = text.split()
    chunks = []
    for i, (s, e) in enumerate(window_tokens(len(words), size, overlap)):
        chunks.append(Chunk(f"{source_id}#{i}", source_id, source_type, title, " ".join(words[s:e]), dict(metadata or {})))
    return chunks
