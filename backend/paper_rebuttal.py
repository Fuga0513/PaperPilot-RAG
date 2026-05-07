"""Reviewer-comment analysis and rebuttal drafting for user-owned papers."""

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

ISSUE_TYPES = {
    "novelty",
    "method clarity",
    "experiment insufficiency",
    "baseline concern",
    "theoretical proof",
    "notation issue",
    "writing issue",
    "other",
}
SEVERITIES = {"high", "medium", "low"}

ARK_API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

_review_model = None

REVIEW_ANALYSIS_PROMPT = (
    "You are PaperPilot-RAG, helping authors analyze peer-review comments.\n"
    "Return strict JSON only, no markdown. The JSON must be an array of objects with keys:\n"
    "reviewer_original_comment, issue_type, severity, response_strategy, required_action, evidence_needed.\n"
    "Allowed issue_type values: novelty, method clarity, experiment insufficiency, baseline concern, "
    "theoretical proof, notation issue, writing issue, other.\n"
    "Allowed severity values: high, medium, low.\n"
    "Be conservative: do not invent paper results or claim a response will certainly satisfy reviewers.\n"
    "Split multi-point reviews into separate reviewer points when appropriate.\n\n"
    "Reviewer comments:\n{comments}"
)

REBUTTAL_DRAFT_PROMPT = (
    "You are PaperPilot-RAG drafting an academic rebuttal. Use only the provided evidence context "
    "for factual claims about the paper. Cite existing evidence with ids like [C1].\n"
    "Do not invent experiments, datasets, metrics, numbers, baselines, or results.\n"
    "For each reviewer point, clearly separate:\n"
    "1. Existing evidence\n"
    "2. Suggested experiments\n"
    "3. Suggested manuscript revisions\n"
    "4. Insufficient evidence\n"
    "Use a polite, specific, and cautious scholarly tone. Do not say the rebuttal will definitely convince reviewers.\n\n"
    "Reviewer analysis JSON:\n{points_json}\n\n"
    "Evidence Context:\n{evidence_context}"
)


@dataclass
class ReviewAnalysisResult:
    """Internal review-analysis result shared by API and LangChain tools."""

    points: list[dict]
    paper_id: int | None


@dataclass
class RebuttalDraftResult:
    """Internal rebuttal result shared by API and LangChain tools."""

    response: str
    points: list[dict]
    paper_id: int | None
    citations: list[dict]
    rag_trace: dict
    tool_calls: list[dict]


def _get_review_model():
    """Return the configured qwen-compatible chat model, if available."""
    global _review_model
    if not ARK_API_KEY or not MODEL:
        return None
    if _review_model is None:
        _review_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=ARK_API_KEY,
            base_url=BASE_URL,
            temperature=0.1,
        )
    return _review_model


def _coerce_paper_id(paper_id: Any) -> int | None:
    """Convert optional tool/API paper_id into an int."""
    if paper_id in (None, "", 0, "0"):
        return None
    try:
        return int(paper_id)
    except (TypeError, ValueError):
        raise ValueError("paper_id must be an integer") from None


def _get_owned_paper(db: Session, owner_id: int, paper_id: Any) -> Paper | None:
    """Return the selected current-user paper, or raise on invalid ownership."""
    clean_id = _coerce_paper_id(paper_id)
    if clean_id is None:
        return None
    paper = db.query(Paper).filter(Paper.id == clean_id, Paper.owner_id == owner_id).first()
    if not paper:
        raise PermissionError("Selected paper is not accessible to the current user.")
    return paper


def _extract_json_array(text: str) -> list[dict]:
    """Parse a JSON array from an LLM response."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    return data if isinstance(data, list) else []


def _normalize_point(item: dict, fallback_comment: str = "") -> dict:
    """Normalize one reviewer point into the public schema shape."""
    issue_type = str(item.get("issue_type") or "other").strip().lower()
    severity = str(item.get("severity") or "medium").strip().lower()
    return {
        "reviewer_original_comment": str(item.get("reviewer_original_comment") or fallback_comment).strip(),
        "issue_type": issue_type if issue_type in ISSUE_TYPES else "other",
        "severity": severity if severity in SEVERITIES else "medium",
        "response_strategy": str(item.get("response_strategy") or "Acknowledge the concern and answer with evidence if available.").strip(),
        "required_action": str(item.get("required_action") or "Identify supporting evidence or plan a manuscript revision.").strip(),
        "evidence_needed": str(item.get("evidence_needed") or "Relevant paper sections, experiments, or limitations.").strip(),
    }


def _fallback_review_points(comments: str) -> list[dict]:
    """Split comments conservatively when the LLM is unavailable."""
    parts = [part.strip(" \n\t-0123456789.()") for part in re.split(r"\n{2,}|\n(?=\s*(?:[-*]|\d+[.)]))", comments) if part.strip()]
    if not parts:
        parts = [comments.strip()]
    points = []
    for part in parts[:12]:
        lower = part.lower()
        if any(word in lower for word in ("baseline", "compare with", "comparison")):
            issue_type = "baseline concern"
        elif any(word in lower for word in ("experiment", "ablation", "dataset", "metric")):
            issue_type = "experiment insufficiency"
        elif any(word in lower for word in ("novel", "novelty", "incremental")):
            issue_type = "novelty"
        elif any(word in lower for word in ("proof", "theorem", "theoretical")):
            issue_type = "theoretical proof"
        elif any(word in lower for word in ("unclear", "clarify", "method")):
            issue_type = "method clarity"
        elif any(word in lower for word in ("notation", "symbol")):
            issue_type = "notation issue"
        elif any(word in lower for word in ("writing", "grammar", "typo")):
            issue_type = "writing issue"
        else:
            issue_type = "other"
        severity = "high" if issue_type in {"experiment insufficiency", "baseline concern", "novelty"} else "medium"
        points.append(_normalize_point({
            "reviewer_original_comment": part,
            "issue_type": issue_type,
            "severity": severity,
            "response_strategy": "Respond respectfully, identify what is already supported, and propose concrete revisions or experiments when evidence is missing.",
            "required_action": "Retrieve evidence from the current paper library and decide whether a manuscript revision or new experiment is needed.",
            "evidence_needed": "Paper sections that address the reviewer point, including method, experiments, results, limitations, or related work.",
        }))
    return points


def analyze_review_comments(
    db: Session,
    user: User,
    *,
    comments: str,
    paper_id: Any = None,
) -> ReviewAnalysisResult:
    """Analyze pasted reviewer comments with optional current-user paper scope."""
    clean_comments = (comments or "").strip()
    if not clean_comments:
        raise ValueError("Reviewer comments are required.")
    paper = _get_owned_paper(db, user.id, paper_id)

    model = _get_review_model()
    points: list[dict] = []
    if model:
        prompt = REVIEW_ANALYSIS_PROMPT.format(comments=clean_comments[:12000])
        try:
            content = model.invoke([{"role": "user", "content": prompt}]).content
            points = [_normalize_point(item) for item in _extract_json_array(str(content))]
        except Exception:
            logger.exception("Failed to analyze reviewer comments with LLM")

    if not points:
        points = _fallback_review_points(clean_comments)

    return ReviewAnalysisResult(points=points[:12], paper_id=paper.id if paper else None)


def _retrieve_point_evidence(owner_id: int, point: dict, paper_id: int | None = None) -> list[dict]:
    """Retrieve current-user evidence for one reviewer point."""
    query = (
        f"Reviewer concern: {point.get('reviewer_original_comment')}\n"
        f"Issue type: {point.get('issue_type')}\n"
        f"Evidence needed: {point.get('evidence_needed')}"
    )
    retrieved = retrieve_documents(
        query,
        top_k=3,
        owner_id=owner_id,
        paper_id=paper_id,
        source_type="paper",
    )
    docs = retrieved.get("docs", [])
    result = []
    for doc in docs:
        if int(doc.get("owner_id") or 0) != owner_id:
            continue
        if paper_id is not None and int(doc.get("paper_id") or 0) != paper_id:
            continue
        result.append(doc)
    return result


def _dedupe_docs(docs: list[dict]) -> list[dict]:
    """Dedupe retrieved chunks before assigning citations."""
    result = []
    seen = set()
    for doc in docs:
        key = doc.get("chunk_id") or (doc.get("paper_id"), doc.get("filename"), doc.get("page_number"), doc.get("text"))
        if key in seen:
            continue
        seen.add(key)
        result.append(doc)
    return result


def _fallback_rebuttal(points: list[dict], citations: list[dict]) -> str:
    """Build a conservative rebuttal draft when no LLM output is available."""
    cite_text = " ".join(f"[{item['citation_id']}]" for item in citations[:3])
    blocks = ["# Rebuttal Draft"]
    for idx, point in enumerate(points, 1):
        blocks.append(
            f"## Reviewer Point {idx}\n"
            f"**Comment:** {point.get('reviewer_original_comment')}\n\n"
            f"**Existing evidence:** {cite_text or 'Insufficient evidence in current retrieval.'}\n\n"
            "**Suggested experiments:** Add targeted experiments only if feasible; do not claim results before running them.\n\n"
            "**Suggested manuscript revisions:** Clarify the method, experimental setup, or limitation related to this point.\n\n"
            "**Insufficient evidence:** Any unsupported claim should be acknowledged and revised cautiously."
        )
    return "\n\n".join(blocks)


def _generate_rebuttal(points: list[dict], docs: list[dict], citations: list[dict]) -> str:
    """Ask the LLM for a rebuttal grounded in retrieved citation ids."""
    model = _get_review_model()
    if not model:
        return _fallback_rebuttal(points, citations)
    prompt = REBUTTAL_DRAFT_PROMPT.format(
        points_json=json.dumps(points, ensure_ascii=False, indent=2),
        evidence_context=build_evidence_context(docs, citations, max_chars=14000),
    )
    try:
        content = model.invoke([{"role": "user", "content": prompt}]).content
        return str(content or "").strip() or _fallback_rebuttal(points, citations)
    except Exception:
        logger.exception("Failed to draft rebuttal with LLM")
        return _fallback_rebuttal(points, citations)


def draft_rebuttal(
    db: Session,
    user: User,
    *,
    comments: str,
    paper_id: Any = None,
) -> RebuttalDraftResult:
    """Draft a citation-backed rebuttal for current-user reviewer comments."""
    analysis = analyze_review_comments(db, user, comments=comments, paper_id=paper_id)
    all_docs: list[dict] = []
    for point in analysis.points:
        all_docs.extend(_retrieve_point_evidence(user.id, point, paper_id=analysis.paper_id))

    docs = _dedupe_docs(all_docs)
    citations = build_citations(docs, owner_id=user.id)
    cited_ids = {item.get("citation_id") for item in citations}
    docs = [doc for doc in docs if doc.get("citation_id") in cited_ids]
    markdown = _generate_rebuttal(analysis.points, docs, citations)
    trace = {
        "tool_used": True,
        "tool_name": "draft_rebuttal",
        "original_query": "reviewer comments rebuttal",
        "query": comments[:500],
        "retrieval_stage": "rebuttal_evidence",
        "retrieval_mode": "hybrid_per_reviewer_point",
        "retrieval_scope": "paper",
        "owner_filter_applied": True,
        "user_filter_applied": True,
        "fallback_reason": None if citations else "no_rebuttal_evidence",
        "citations": citations,
        "retrieved_chunks": docs,
        "selected_context_chunks": docs,
        "first_retrieval_results": docs,
        "tool_calls": [{"name": "draft_rebuttal", "detail": f"{len(analysis.points)} reviewer points"}],
    }
    return RebuttalDraftResult(
        response=markdown,
        points=analysis.points,
        paper_id=analysis.paper_id,
        citations=citations,
        rag_trace=trace,
        tool_calls=trace["tool_calls"],
    )
