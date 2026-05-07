"""Research-writing assistance grounded in current-user paper evidence."""

from __future__ import annotations

import json
import logging
import os
import re
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

TASK_TYPES = {
    "Generate Related Work",
    "Polish Contributions",
    "Rewrite Abstract",
    "Check Introduction Logic",
    "Polish Grant Scientific Question",
    "Summarize Experimental Settings",
}
EVIDENCE_REQUIRED_TASKS = {"Generate Related Work", "Summarize Experimental Settings"}

ARK_API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

_writing_model = None

RESEARCH_WRITING_PROMPT = (
    "You are PaperPilot-RAG, a research-writing assistant. Return strict JSON only with keys:\n"
    "evidence_based_facts: array of strings,\n"
    "suggested_writing: string,\n"
    "warnings: array of strings,\n"
    "revision_notes: array of strings.\n\n"
    "Rules:\n"
    "- Use only the provided evidence context for concrete claims about papers, datasets, metrics, methods, results, and limitations.\n"
    "- Every concrete paper fact in evidence_based_facts and related-work writing must cite provided ids like [C1].\n"
    "- Do not invent citations, papers, experiments, datasets, metrics, baselines, numbers, or results.\n"
    "- For polishing tasks, you may improve the user's wording without citations, but do not present writing suggestions as verified paper facts.\n"
    "- If evidence is insufficient, add a warning and avoid unsupported claims.\n"
    "- Match writing_style and language.\n\n"
    "Related Work special requirements: organize by method categories, representative papers, advantages, limitations, "
    "relation to the target topic, and a draft paragraph.\n\n"
    "Task type: {task_type}\n"
    "Topic: {topic}\n"
    "Writing style: {writing_style}\n"
    "Language: {language}\n"
    "User text:\n{user_text}\n\n"
    "Evidence Context:\n{evidence_context}"
)


@dataclass
class ResearchWritingResult:
    """Internal result shared by FastAPI APIs and LangChain tools."""

    evidence_based_facts: list[str]
    suggested_writing: str
    citations: list[dict]
    warnings: list[str]
    revision_notes: list[str]
    rag_trace: dict
    tool_calls: list[dict]


def _get_writing_model():
    """Return the configured qwen-compatible chat model, if available."""
    global _writing_model
    if not ARK_API_KEY or not MODEL:
        return None
    if _writing_model is None:
        _writing_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=ARK_API_KEY,
            base_url=BASE_URL,
            temperature=0.15,
        )
    return _writing_model


def _normalize_task_type(task_type: str) -> str:
    """Normalize frontend/tool task labels to the supported task list."""
    clean = (task_type or "").strip()
    if clean in TASK_TYPES:
        return clean
    lower = clean.lower()
    aliases = {
        "related work": "Generate Related Work",
        "generate related work": "Generate Related Work",
        "polish contributions": "Polish Contributions",
        "rewrite abstract": "Rewrite Abstract",
        "check introduction logic": "Check Introduction Logic",
        "polish grant scientific question": "Polish Grant Scientific Question",
        "summarize experimental settings": "Summarize Experimental Settings",
    }
    return aliases.get(lower, "Generate Related Work")


def _coerce_paper_ids(paper_ids: list[Any] | None) -> list[int]:
    """Convert optional paper ids into unique ints."""
    result: list[int] = []
    for item in paper_ids or []:
        try:
            paper_id = int(item)
        except (TypeError, ValueError):
            continue
        if paper_id and paper_id not in result:
            result.append(paper_id)
    return result


def _owned_papers_by_ids(db: Session, owner_id: int, paper_ids: list[int]) -> list[Paper]:
    """Load only current-user papers in requested order."""
    if not paper_ids:
        return []
    rows = db.query(Paper).filter(Paper.owner_id == owner_id, Paper.id.in_(paper_ids)).all()
    row_by_id = {row.id: row for row in rows}
    return [row_by_id[paper_id] for paper_id in paper_ids if paper_id in row_by_id]


def _resolve_requested_papers(db: Session, user: User, paper_ids: list[Any] | None) -> list[Paper]:
    """Resolve requested paper ids and reject cross-user access."""
    requested_ids = _coerce_paper_ids(paper_ids)
    papers = _owned_papers_by_ids(db, user.id, requested_ids)
    if requested_ids and len(papers) != len(requested_ids):
        raise PermissionError("One or more selected papers are not accessible to the current user.")
    return papers[:8]


def _retrieve_writing_evidence(
    owner_id: int,
    *,
    task_type: str,
    topic: str,
    user_text: str,
    papers: list[Paper],
) -> list[dict]:
    """Retrieve current-user evidence for the writing task."""
    base_query = (
        f"Task: {task_type}\n"
        f"Topic: {topic}\n"
        f"User text: {user_text[:1200]}\n"
        "Find paper evidence for methods, representative papers, advantages, limitations, datasets, metrics, experiments, and results."
    ).strip()
    docs: list[dict] = []
    if papers:
        for paper in papers:
            query = f"{base_query}\nPaper: {paper.title or paper.original_filename or paper.filename}"
            retrieved = retrieve_documents(query, top_k=3, owner_id=owner_id, paper_id=paper.id, source_type="paper")
            docs.extend(retrieved.get("docs", []))
    elif task_type in EVIDENCE_REQUIRED_TASKS or topic:
        retrieved = retrieve_documents(base_query, top_k=8, owner_id=owner_id, source_type="paper")
        docs.extend(retrieved.get("docs", []))

    filtered = []
    allowed_ids = {paper.id for paper in papers}
    for doc in docs:
        if int(doc.get("owner_id") or 0) != owner_id:
            continue
        if allowed_ids and int(doc.get("paper_id") or 0) not in allowed_ids:
            continue
        filtered.append(doc)
    return _dedupe_docs(filtered)


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


def _extract_json_object(text: str) -> dict:
    """Parse one JSON object from an LLM response."""
    cleaned = (text or "").strip()
    if not cleaned:
        return {}
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _as_string_list(value: Any) -> list[str]:
    """Normalize LLM JSON list fields."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _fallback_writing_result(task_type: str, user_text: str, citations: list[dict], needs_evidence: bool) -> dict:
    """Build a conservative result when no LLM output is available."""
    warnings = []
    if needs_evidence and not citations:
        warnings.append("Current evidence is insufficient. Upload or index more relevant papers before making paper-specific claims.")
    suggested = user_text.strip() or (
        "Draft cannot safely include paper-specific claims without retrieved evidence. "
        "Provide source text for polishing or select indexed papers for evidence-grounded writing."
    )
    if citations and task_type == "Generate Related Work":
        cited = " ".join(f"[{item['citation_id']}]" for item in citations[:5])
        suggested = (
            "Related work draft scaffold:\n\n"
            f"- Method categories: organize the retrieved works by their methodological assumptions and model families {cited}.\n"
            "- Representative papers: cite each concrete statement with the retrieved evidence ids.\n"
            "- Advantages and limitations: only describe limitations supported by the retrieved chunks.\n"
            "- Relation to target topic: connect the evidence to the target topic without adding unsupported claims."
        )
    cited_fact = ""
    if citations:
        cited_fact = "Retrieved evidence is available: " + " ".join(f"[{item['citation_id']}]" for item in citations[:5])
    return {
        "evidence_based_facts": [cited_fact] if cited_fact else [],
        "suggested_writing": suggested,
        "warnings": warnings,
        "revision_notes": ["Keep unverifiable claims as writing suggestions until evidence is retrieved."],
    }


def _generate_writing_json(
    *,
    task_type: str,
    topic: str,
    user_text: str,
    writing_style: str,
    language: str,
    docs: list[dict],
    citations: list[dict],
) -> dict:
    """Ask the LLM for structured writing output grounded in citations."""
    model = _get_writing_model()
    needs_evidence = task_type in EVIDENCE_REQUIRED_TASKS
    if not model:
        return _fallback_writing_result(task_type, user_text, citations, needs_evidence)

    prompt = RESEARCH_WRITING_PROMPT.format(
        task_type=task_type,
        topic=topic or "",
        writing_style=writing_style or "general academic",
        language=language or "en",
        user_text=user_text or "",
        evidence_context=build_evidence_context(docs, citations, max_chars=14000),
    )
    try:
        content = model.invoke([{"role": "user", "content": prompt}]).content
        data = _extract_json_object(str(content))
    except Exception:
        logger.exception("Failed to generate research writing output")
        data = {}

    if not data:
        data = _fallback_writing_result(task_type, user_text, citations, needs_evidence)

    warnings = _as_string_list(data.get("warnings"))
    if needs_evidence and not citations:
        warnings.append("No accessible evidence chunks were retrieved; upload or index more relevant papers.")
    return {
        "evidence_based_facts": _as_string_list(data.get("evidence_based_facts")),
        "suggested_writing": str(data.get("suggested_writing") or "").strip(),
        "warnings": warnings,
        "revision_notes": _as_string_list(data.get("revision_notes")),
    }


def run_research_writing_task(
    db: Session,
    user: User,
    *,
    task_type: str,
    topic: str = "",
    user_text: str = "",
    paper_ids: list[Any] | None = None,
    writing_style: str = "general academic",
    language: str = "en",
) -> ResearchWritingResult:
    """Run one current-user-scoped research writing task."""
    clean_task = _normalize_task_type(task_type)
    if not (topic or user_text or paper_ids):
        raise ValueError("Please provide a topic, user text, or selected papers.")
    papers = _resolve_requested_papers(db, user, paper_ids)
    docs = _retrieve_writing_evidence(
        user.id,
        task_type=clean_task,
        topic=topic or "",
        user_text=user_text or "",
        papers=papers,
    )
    citations = build_citations(docs, owner_id=user.id)
    cited_ids = {item.get("citation_id") for item in citations}
    docs = [doc for doc in docs if doc.get("citation_id") in cited_ids]
    output = _generate_writing_json(
        task_type=clean_task,
        topic=topic or "",
        user_text=user_text or "",
        writing_style=writing_style or "general academic",
        language=language or "en",
        docs=docs,
        citations=citations,
    )
    trace = {
        "tool_used": True,
        "tool_name": "research_writing",
        "original_query": topic or user_text[:500],
        "query": f"{clean_task}: {topic or user_text[:500]}",
        "retrieval_stage": "writing_evidence",
        "retrieval_mode": "hybrid_selected_papers" if papers else "hybrid_user_library",
        "retrieval_scope": "paper",
        "owner_filter_applied": True,
        "user_filter_applied": True,
        "fallback_reason": None if citations else "no_writing_evidence",
        "citations": citations,
        "retrieved_chunks": docs,
        "selected_context_chunks": docs,
        "first_retrieval_results": docs,
        "tool_calls": [{"name": "research_writing", "detail": clean_task}],
    }
    return ResearchWritingResult(
        evidence_based_facts=output["evidence_based_facts"],
        suggested_writing=output["suggested_writing"],
        citations=citations,
        warnings=output["warnings"],
        revision_notes=output["revision_notes"],
        rag_trace=trace,
        tool_calls=trace["tool_calls"],
    )
