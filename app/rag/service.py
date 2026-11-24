"""Knowledge base = SOP chunks + report chunks, re-indexed whenever persisted reports change."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app import config
from app.rag.corpus import load_sop_chunks, report_chunks
from app.rag.pipeline import HybridRAGPipeline
from app.repository import ReportRepository


class KnowledgeBase:
    def __init__(self, db_path: Path | str, backend: str = config.RAG_BACKEND, sop_dir: Path = config.SOP_DIR):
        self.repo = ReportRepository(db_path)
        self.backend = backend
        self.sop_chunks = load_sop_chunks(sop_dir)
        self._fingerprint: Optional[tuple[int, str]] = None
        self._pipeline: Optional[HybridRAGPipeline] = None

    def pipeline(self) -> HybridRAGPipeline:
        fp = self.repo.fingerprint()
        if self._pipeline is None or fp != self._fingerprint:
            chunks = self.sop_chunks + report_chunks(self.repo.list())
            self._pipeline = HybridRAGPipeline(chunks, backend=self.backend)
            self._fingerprint = fp
        return self._pipeline
