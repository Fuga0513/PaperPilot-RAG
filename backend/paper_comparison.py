"""Citation-backed multi-paper comparison for user-owned papers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from sqlalchemy.orm import Session

from citation_builder import build_citations, build_evidence_context
from models import Paper, User
from rag_utils import retrieve_documents

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_COMPARE_ASPECTS = ["problem", "method", "contribution", "dataset", "metric", "limitation"]
MAX_COMPARE_PAPERS = 5
ASPECT_QUERY_HINTS = {
    "problem": "research problem motivation challenge task objective",
    "method": "method architecture framework model algorithm module approach",
    "contribution": "contribution novelty key findings proposed main contributions",
    "dataset": "dataset data benchmark corpus experimental setup",
    "metric": "metric evaluation measure result performance score",
    "limitation": "limitation weakness future work failure assumption threat",
}

ARK_API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

_comparison_model = None


@dataclass
class PaperComparisonResult:
    """Internal result object shared by FastAPI and LangChain tools."""

    response: str
    paper_ids: list[int]
    compare_aspects: list[str]
    citations: list[dict]
    rag_trace: dict
    tool_calls: list[dict]


def _get_comparison_model():
    """Return the configured qwen-compatible chat model, if available."""
    global _comparison_model
    if not ARK_API_KEY or not MODEL:
        return None
    if _comparison_model is None:
        _comparison_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=ARK_API_KEY,
            base_url=BASE_URL,
            temperature=0.1,
        )
    return _comparison_model


def _normalize_aspects(compare_aspects: list[str] | None) -> list[str]:
    """Keep a bounded, readable aspect list for the comparison table."""
    aspects = [str(item).strip().lower() for item in (compare_aspects or []) if str(item).strip()]
    return aspects[:8] or DEFAULT_COMPARE_ASPECTS


def _coerce_paper_ids(paper_ids: list[Any] | None) -> list[int]:
    """Convert tool/API ids to integers and ignore empty values."""
    result: list[int] = []
    for item in paper_ids or []:
        try:
            paper_id = int(item)
        except (TypeError, ValueError):
            continue
        if paper_id not in result:
            result.append(paper_id)
    return result


def _owned_papers_by_ids(db: Session, owner_id: int, paper_ids: list[int]) -> list[Paper]:
    """Load only papers owned by the current user."""
    if not paper_ids:
        return []
    rows = (
        db.query(Paper)
        .filter(Paper.owner_id == owner_id, Paper.id.in_(paper_ids))
        .order_by(Paper.updated_at.desc(), Paper.id.desc())
        .all()
    )
    row_by_id = {row.id: row for row in rows}
    return [row_by_id[paper_id] for paper_id in paper_ids if paper_id in row_by_id]


def _owned_papers_by_filenames(db: Session, owner_id: int, filenames: list[str] | None) -> list[Paper]:
    """Load current-user papers matching stored or original filenames."""
    names = [str(item).strip() for item in (filenames or []) if str(item).strip()]
    if not names:
        return []
    rows = (
        db.query(Paper)
        .filter(
            Paper.owner_id == owner_id,
            (Paper.filename.in_(names)) | (Paper.original_filename.in_(names)) | (Paper.title.in_(names)),
        )
        .order_by(Paper.updated_at.desc(), Paper.id.desc())
        .all()
    )
    return rows


def _candidate_papers_from_query(db: Session, owner_id: int, query: str) -> list[Paper]:
    """Retrieve candidate papers from the current user's indexed paper library."""
    retrieved = retrieve_documents(query, top_k=10, owner_id=owner_id, source_type="paper")
    candidate_ids: list[int] = []
    for doc in retrieved.get("docs", []):
        try:
            paper_id = int(doc.get("paper_id") or 0)
        except (TypeError, ValueError):
            continue
        if paper_id and paper_id not in candidate_ids:
            candidate_ids.append(paper_id)
    return _owned_papers_by_ids(db, owner_id, candidate_ids[:MAX_COMPARE_PAPERS])


def resolve_comparison_papers(
    db: Session,
    user: User,
    *,
    query: str,
    paper_ids: list[Any] | None = None,
    filenames: list[str] | None = None,
) -> list[Paper]:
    """Resolve requested papers while enforcing current-user ownership."""
    requested_ids = _coerce_paper_ids(paper_ids)
    papers = _owned_papers_by_ids(db, user.id, requested_ids)
    if requested_ids and len(papers) != len(requested_ids):
        raise PermissionError("One or more selected papers are not accessible to the current user.")

    seen = {paper.id for paper in papers}
    for paper in _owned_papers_by_filenames(db, user.id, filenames):
        if paper.id not in seen:
            papers.append(paper)
            seen.add(paper.id)

    if not papers:
        papers = _candidate_papers_from_query(db, user.id, query)

    return papers[:MAX_COMPARE_PAPERS]


def _retrieve_aspect_evidence(owner_id: int, paper: Paper, query: str, aspect: str) -> list[dict]:
    """Retrieve evidence for one paper/aspect with owner_id and paper_id filters."""
    hint = ASPECT_QUERY_HINTS.get(aspect, aspect)
    title = paper.title or paper.original_filename or paper.filename
    aspect_query = f"{query}\nPaper: {title}\nAspect: {aspect}. Find evidence about {hint}."
    retrieved = retrieve_documents(
        aspect_query,
        top_k=2,
        owner_id=owner_id,
        paper_id=paper.id,
        source_type="paper",
    )
    docs = retrieved.get("docs", [])
    return [doc for doc in docs if int(doc.get("owner_id") or 0) == owner_id and int(doc.get("paper_id") or 0) == paper.id]


def _dedupe_docs(docs: list[dict]) -> list[dict]:
    """Dedupe retrieved chunks before citation ids are assigned."""
    result = []
    seen = set()
    for doc in docs:
        key = doc.get("chunk_id") or (doc.get("paper_id"), doc.get("filename"), doc.get("page_number"), doc.get("text"))
        if key in seen:
            continue
        seen.add(key)
        result.append(doc)
    return result


def _fallback_table(papers: list[Paper], aspects: list[str], aspect_docs: dict[tuple[int, str], list[dict]]) -> str:
    """Build a conservative Markdown table directly from retrieved citations."""
    headers = ["Paper", *[aspect.title() for aspect in aspects]]
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for paper in papers:
        title = paper.title or paper.original_filename or paper.filename
        cells = [title]
        for aspect in aspects:
            docs = aspect_docs.get((paper.id, aspect), [])
            cite_ids = [doc.get("citation_id") for doc in docs if doc.get("citation_id")]
            cells.append(" ".join(f"[{cid}]" for cid in cite_ids[:2]) or "Not found in current evidence")
        rows.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(rows)


def _generate_comparison_markdown(
    query: str,
    papers: list[Paper],
    aspects: list[str],
    docs: list[dict],
    citations: list[dict],
    aspect_docs: dict[tuple[int, str], list[dict]],
) -> str:
    """Ask the LLM for a compact comparison table grounded in citation ids."""
    model = _get_comparison_model()
    if not model or not citations:
        return _fallback_table(papers, aspects, aspect_docs)

    paper_lines = [
        f"- paper_id={paper.id}: {paper.title or paper.original_filename or paper.filename}"
        for paper in papers
    ]
    prompt = (
        "You are PaperPilot-RAG. Create a Markdown comparison table for the selected user-owned papers.\n"
        "Use only the evidence context. Cite every concrete fact with provided ids like [C1].\n"
        "If an aspect is not supported by evidence for a paper, write exactly: Not found in current evidence.\n"
        "Do not invent datasets, metrics, contributions, limitations, citations, or paper facts.\n\n"
        f"User query: {query}\n"
        f"Papers:\n{chr(10).join(paper_lines)}\n"
        f"Aspects / columns: {', '.join(aspects)}\n\n"
        f"Evidence Context:\n{build_evidence_context(docs, citations, max_chars=12000)}"
    )
    try:
        content = model.invoke([{"role": "user", "content": prompt}]).content
        return str(content or "").strip() or _fallback_table(papers, aspects, aspect_docs)
    except Exception:
        logger.exception("Failed to generate paper comparison table")
        return _fallback_table(papers, aspects, aspect_docs)


def compare_user_papers(
    db: Session,
    user: User,
    *,
    query: str = "Compare the selected papers",
    paper_ids: list[Any] | None = None,
    filenames: list[str] | None = None,
    compare_aspects: list[str] | None = None,
) -> PaperComparisonResult:
    """Compare current-user papers and return Markdown, citations, and trace."""
    clean_query = (query or "Compare the selected papers").strip()
    aspects = _normalize_aspects(compare_aspects)
    papers = resolve_comparison_papers(
        db,
        user,
        query=clean_query,
        paper_ids=paper_ids,
        filenames=filenames,
    )
    if len(papers) < 2:
        raise ValueError("Please select at least two accessible papers to compare.")

    aspect_docs: dict[tuple[int, str], list[dict]] = {}
    all_docs: list[dict] = []
    for paper in papers:
        for aspect in aspects:
            docs = _retrieve_aspect_evidence(user.id, paper, clean_query, aspect)
            aspect_docs[(paper.id, aspect)] = docs
            all_docs.extend(docs)

    docs = _dedupe_docs(all_docs)
    citations = build_citations(docs, owner_id=user.id)
    markdown = _generate_comparison_markdown(clean_query, papers, aspects, docs, citations, aspect_docs)
    trace = {
        "tool_used": True,
        "tool_name": "compare_papers",
        "original_query": clean_query,
        "query": clean_query,
        "retrieval_stage": "comparison",
        "retrieval_mode": "hybrid_per_paper_aspect",
        "retrieval_scope": "paper",
        "owner_filter_applied": True,
        "user_filter_applied": True,
        "fallback_reason": None if citations else "no_comparison_evidence",
        "citations": citations,
        "retrieved_chunks": docs,
        "selected_context_chunks": docs,
        "first_retrieval_results": docs,
        "tool_calls": [{"name": "compare_papers", "detail": f"{len(papers)} papers x {len(aspects)} aspects"}],
    }
    return PaperComparisonResult(
        response=markdown,
        paper_ids=[paper.id for paper in papers],
        compare_aspects=aspects,
        citations=citations,
        rag_trace=trace,
        tool_calls=trace["tool_calls"],
    )
