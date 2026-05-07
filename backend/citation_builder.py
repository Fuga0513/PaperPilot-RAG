"""Citation and evidence-context helpers for PaperPilot research QA."""

from __future__ import annotations

from typing import Any

MAX_CONTEXT_CHARS = 8000
MAX_CHUNK_CHARS = 1400
PREVIEW_CHARS = 360


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _as_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def build_citations(docs: list[dict], owner_id: int | None = None) -> list[dict]:
    """Build user-safe citations from retrieved chunks only.

    For private paper retrieval, owner_id must match the logged-in user. Legacy
    global documents have no owner_id and can still be cited by filename/page.
    """
    citations: list[dict] = []
    for idx, doc in enumerate(docs, 1):
        doc_owner = doc.get("owner_id")
        if owner_id is not None and doc.get("source_type") == "paper":
            if _as_int(doc_owner) != int(owner_id):
                continue

        text = _clean_text(doc.get("text"))
        citation_id = f"C{len(citations) + 1}"
        citations.append({
            "citation_id": citation_id,
            "owner_id": _as_int(doc_owner) if doc_owner is not None else None,
            "paper_id": _as_int(doc.get("paper_id")) or None,
            "paper_title": doc.get("paper_title") or doc.get("filename") or "Untitled paper",
            "filename": doc.get("filename") or "Unknown source",
            "section_title": doc.get("section_title") or "",
            "page_start": _as_int(doc.get("page_start") or doc.get("page_number")) or None,
            "page_end": _as_int(doc.get("page_end") or doc.get("page_number")) or None,
            "chunk_id": doc.get("chunk_id") or "",
            "score": _as_float(doc.get("score")),
            "rerank_score": _as_float(doc.get("rerank_score")),
            "preview_text": text[:PREVIEW_CHARS],
        })
        doc["citation_id"] = citation_id
        doc["preview_text"] = text[:PREVIEW_CHARS]
    return citations


def build_evidence_context(docs: list[dict], citations: list[dict], max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Build a bounded prompt context with [C1] style evidence anchors."""
    if not docs or not citations:
        return "No relevant evidence chunks were retrieved."

    citation_by_chunk = {item.get("chunk_id"): item for item in citations if item.get("chunk_id")}
    blocks: list[str] = []
    used = 0
    for doc in docs:
        citation = citation_by_chunk.get(doc.get("chunk_id"))
        if not citation:
            continue
        text = _clean_text(doc.get("text"))[:MAX_CHUNK_CHARS]
        pages = _format_pages(citation.get("page_start"), citation.get("page_end"))
        header = (
            f"[{citation['citation_id']}] "
            f"Title: {citation.get('paper_title') or citation.get('filename')}; "
            f"Section: {citation.get('section_title') or 'Unknown'}; "
            f"Pages: {pages}; "
            f"Chunk: {citation.get('chunk_id')}"
        )
        block = f"{header}\n{text}"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)

    return "\n\n---\n\n".join(blocks) if blocks else "No relevant evidence chunks were retrieved."


def _format_pages(start, end) -> str:
    if not start and not end:
        return "N/A"
    if start and end and start != end:
        return f"{start}-{end}"
    return str(start or end)
