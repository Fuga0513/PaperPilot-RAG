"""Milvus indexing for user-owned PaperPilot papers.

This module is separate from the legacy /documents pipeline so private paper
retrieval can require owner_id filters without changing the global admin KB.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from embedding import embedding_service
from milvus_client import MilvusManager
from milvus_writer import MilvusWriter
from models import Paper, PaperChunk
from parent_chunk_store import ParentChunkStore

logger = logging.getLogger(__name__)

RESEARCH_MILVUS_FIELDS = {
    "source_type",
    "owner_id",
    "paper_id",
    "filename",
    "paper_title",
    "section_title",
    "subsection_title",
    "page_start",
    "page_end",
    "chunk_type",
    "year",
    "venue",
    "chunk_level",
    "chunk_id",
    "parent_chunk_id",
    "root_chunk_id",
}


class MilvusSchemaError(RuntimeError):
    """Raised when an existing collection cannot support private paper filters."""


def _file_type(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return "PDF"
    if suffix == ".docx":
        return "Word"
    if suffix == ".txt":
        return "Text"
    return "Paper"


def _safe_int(value) -> int:
    return int(value or 0)


def ensure_research_milvus_schema(milvus_manager: MilvusManager) -> None:
    """Ensure Milvus has explicit fields needed for owner-filtered paper search."""
    milvus_manager.init_collection()
    missing = RESEARCH_MILVUS_FIELDS - milvus_manager.get_field_names()
    if missing:
        fields = ", ".join(sorted(missing))
        raise MilvusSchemaError(
            "Milvus collection is missing fields required for PaperPilot paper "
            f"indexing/filtering: {fields}. Rebuild the collection with the new schema."
        )


def _chunk_to_document(paper: Paper, chunk: PaperChunk) -> dict:
    """Convert a PaperChunk ORM row to the shared Milvus/parent-store document shape."""
    page_number = _safe_int(chunk.page_start)
    return {
        "text": chunk.text,
        "filename": paper.filename,
        "file_type": _file_type(paper.filename),
        "file_path": paper.file_path,
        "page_number": page_number,
        "chunk_idx": chunk.id,
        "source_type": "paper",
        "owner_id": paper.owner_id,
        "paper_id": paper.id,
        "paper_title": chunk.paper_title or paper.title or paper.original_filename,
        "section_title": chunk.section_title,
        "subsection_title": chunk.subsection_title,
        "page_start": page_number,
        "page_end": _safe_int(chunk.page_end),
        "chunk_type": chunk.chunk_type,
        "year": _safe_int(paper.year),
        "venue": paper.venue or "",
        "chunk_id": chunk.chunk_id,
        "parent_chunk_id": chunk.parent_chunk_id or "",
        "root_chunk_id": chunk.root_chunk_id or "",
        "chunk_level": _safe_int(chunk.chunk_level),
    }


def _paper_filter(paper: Paper) -> str:
    return f'source_type == "paper" and owner_id == {paper.owner_id} and paper_id == {paper.id}'


def remove_paper_vectors(
    paper: Paper,
    milvus_manager: MilvusManager | None = None,
    parent_chunk_store: ParentChunkStore | None = None,
) -> int:
    """Delete one owned paper's vectors and parent chunks from storage."""
    milvus_manager = milvus_manager or MilvusManager()
    parent_chunk_store = parent_chunk_store or ParentChunkStore()
    ensure_research_milvus_schema(milvus_manager)
    filter_expr = _paper_filter(paper)
    rows = milvus_manager.query_all(filter_expr=filter_expr, output_fields=["text"])
    texts = [row.get("text") or "" for row in rows]
    if texts:
        embedding_service.increment_remove_documents(texts)
    result = milvus_manager.delete(filter_expr)
    parent_chunk_store.delete_by_filename(paper.filename)
    return result.get("delete_count", 0) if isinstance(result, dict) else 0


def index_paper_chunks(
    db: Session,
    paper: Paper,
    batch_size: int = 50,
    milvus_manager: MilvusManager | None = None,
    parent_chunk_store: ParentChunkStore | None = None,
) -> int:
    """Index searchable leaf chunks for a user-owned paper into Milvus.

    The existing dense embedding, BM25 sparse vector state, and parent chunk
    store are preserved. Only L3 leaf chunks are inserted into Milvus; L1/L2
    chunks are written to ParentChunkStore for auto-merging.
    """
    milvus_manager = milvus_manager or MilvusManager()
    parent_chunk_store = parent_chunk_store or ParentChunkStore()
    ensure_research_milvus_schema(milvus_manager)

    chunks = (
        db.query(PaperChunk)
        .filter(PaperChunk.paper_id == paper.id, PaperChunk.owner_id == paper.owner_id)
        .order_by(PaperChunk.id.asc())
        .all()
    )
    if not chunks:
        raise ValueError(f"No PaperChunk rows found for paper_id={paper.id}")

    parent_docs = [_chunk_to_document(paper, chunk) for chunk in chunks if int(chunk.chunk_level or 0) in (1, 2)]
    leaf_docs = [_chunk_to_document(paper, chunk) for chunk in chunks if int(chunk.chunk_level or 0) == 3]
    if not leaf_docs:
        raise ValueError(f"No leaf PaperChunk rows found for paper_id={paper.id}")

    try:
        remove_paper_vectors(paper, milvus_manager=milvus_manager, parent_chunk_store=parent_chunk_store)
    except Exception:
        logger.info("No existing vectors removed before indexing paper_id=%s", paper.id, exc_info=True)

    parent_chunk_store.upsert_documents(parent_docs)
    writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)
    writer.write_documents(leaf_docs, batch_size=batch_size)
    return len(leaf_docs)
