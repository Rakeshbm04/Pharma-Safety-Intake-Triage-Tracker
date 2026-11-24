"""Build the retrieval corpus: fictional SOP markdown files + persisted (fictional) reports."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from app import config
from app.models import Report
from app.rag.chunking import Chunk, chunk_text

SOP_ID_RE = re.compile(r"^(SOP-\d{3})")


def load_sop_chunks(sop_dir: Path = config.SOP_DIR) -> list[Chunk]:
    """Each SOP is split by '## ' section headings, then windowed into 512/64 chunks."""
    chunks: list[Chunk] = []
    for path in sorted(Path(sop_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = SOP_ID_RE.match(path.stem.upper())
        sop_id = m.group(1) if m else path.stem.upper()
        lines = text.splitlines()
        doc_title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), sop_id)
        sections = re.split(r"(?m)^## ", text)
        for s_idx, section in enumerate(sections):
            body = section.strip()
            if not body:
                continue
            heading = body.splitlines()[0].lstrip("# ").strip() if s_idx else "Overview"
            if s_idx == 0:  # drop the H1 line from the overview section
                body = "\n".join(l for l in body.splitlines() if not l.startswith("# ")).strip()
                if not body:
                    continue
            for c in chunk_text(f"{doc_title} - {heading}. {body}", f"{sop_id}", "SOP",
                                f"{doc_title} / {heading}", metadata={"section": s_idx}):
                chunks.append(Chunk(f"{sop_id}§{s_idx}#{c.chunk_id.split('#')[1]}", c.source_id, c.source_type,
                                    c.title, c.text, c.metadata))
    return chunks


def render_report(r: Report) -> str:
    parts = [
        f"{r.ref_id}. {r.report_type.value}. Product: {r.product_name}. Batch: {r.batch_number}.",
        f"Status: {r.status.value}. Date received: {r.date_received.isoformat()}. Reporter type: {r.reporter_type.value}.",
        f"Description: {r.description}",
    ]
    if r.event_description:
        parts.append(f"Event: {r.event_description}")
    if r.event_onset_date:
        parts.append(f"Event onset date: {r.event_onset_date.isoformat()}.")
    return "\n".join(parts)


def report_chunks(reports: Iterable[Report]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for r in reports:
        chunks.extend(chunk_text(render_report(r), r.ref_id, "REPORT",
                                 f"{r.ref_id} · {r.report_type.value} · {r.product_name}",
                                 metadata={"status": r.status.value, "batch": r.batch_number}))
    return chunks
