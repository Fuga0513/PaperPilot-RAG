"""LLM-based structured metadata extraction for user-owned papers."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from models import Paper, PaperChunk, PaperMetadata

load_dotenv()

logger = logging.getLogger(__name__)

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

_metadata_model = None


class ExtractedPaperMetadata(BaseModel):
    """Validated JSON payload returned by the metadata extraction LLM."""

    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    problem: str | None = None
    motivation: str | None = None
    contributions: list[str] = Field(default_factory=list)
    method_modules: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @field_validator(
        "authors",
        "contributions",
        "method_modules",
        "datasets",
        "metrics",
        "baselines",
        "limitations",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        return []

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, value):
        if value in (None, ""):
            return None
        match = re.search(r"\b(19|20)\d{2}\b", str(value))
        return int(match.group(0)) if match else None


def _get_metadata_model():
    """Create the OpenAI-compatible chat model configured by .env."""
    global _metadata_model
    if not API_KEY or not MODEL:
        raise RuntimeError("Metadata extraction LLM is not configured")
    if _metadata_model is None:
        _metadata_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0,
        )
    return _metadata_model


def _json_array(values: list[str]) -> str:
    """Persist list fields as JSON strings in Text columns."""
    return json.dumps([item for item in values if item], ensure_ascii=False)


def _clean_scalar(value: str | None) -> str:
    """Normalize nullable scalar metadata for DB storage."""
    return (value or "").strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a strict JSON object, allowing fenced-code wrappers."""
    content = (text or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(content[start:end + 1])


def collect_metadata_context(db: Session, paper: Paper, max_chars: int = 18000) -> str:
    """Collect high-signal paper text for metadata extraction.

    Prefer Abstract/Introduction/Method/Conclusion sections, then fill with the
    earliest level-1 chunks. This keeps synchronous extraction bounded.
    """
    chunks = (
        db.query(PaperChunk)
        .filter(
            PaperChunk.paper_id == paper.id,
            PaperChunk.owner_id == paper.owner_id,
            PaperChunk.chunk_level == 1,
        )
        .order_by(PaperChunk.page_start.asc().nulls_last(), PaperChunk.id.asc())
        .all()
    )
    if not chunks:
        return ""

    preferred_sections = (
        "abstract",
        "introduction",
        "method",
        "methodology",
        "approach",
        "framework",
        "experiments",
        "evaluation",
        "discussion",
        "conclusion",
    )
    selected: list[PaperChunk] = []
    for chunk in chunks:
        section = (chunk.section_title or "").lower()
        if any(key in section for key in preferred_sections):
            selected.append(chunk)
    for chunk in chunks[:8]:
        if chunk not in selected:
            selected.append(chunk)

    parts = []
    total = 0
    for chunk in selected:
        header = (
            f"[section={chunk.section_title or 'Unknown'}; "
            f"subsection={chunk.subsection_title or '-'}; "
            f"pages={chunk.page_start or '-'}-{chunk.page_end or '-'}]\n"
        )
        block = header + (chunk.text or "").strip()
        if not block.strip():
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        block = block[:remaining]
        parts.append(block)
        total += len(block)
    return "\n\n---\n\n".join(parts)


def build_metadata_prompt(context: str) -> str:
    """Build a bilingual strict-JSON extraction prompt."""
    return f"""
You are extracting metadata from a research paper or technical project document.
Only use the provided paper text. Do not invent authors, year, venue, datasets,
metrics, baselines, results, or limitations.

If a field is not clearly stated, use null for scalar fields or [] for list
fields. Support both Chinese and English papers. Return strict JSON only: no
markdown, no code fences, no explanation.

JSON schema:
{{
  "title": string | null,
  "authors": string[],
  "year": number | null,
  "venue": string | null,
  "abstract": string | null,
  "problem": string | null,
  "motivation": string | null,
  "contributions": string[],
  "method_modules": string[],
  "datasets": string[],
  "metrics": string[],
  "baselines": string[],
  "limitations": string[]
}}

Paper text:
{context}
""".strip()


def extract_metadata_from_text(context: str) -> ExtractedPaperMetadata:
    """Call the configured LLM and validate its strict JSON response."""
    if not context.strip():
        raise ValueError("No paper text available for metadata extraction")
    model = _get_metadata_model()
    response = model.invoke(build_metadata_prompt(context))
    raw_content = getattr(response, "content", response)
    payload = _extract_json_object(str(raw_content))
    try:
        return ExtractedPaperMetadata.model_validate(payload)
    except ValidationError as exc:
        logger.exception("Metadata JSON validation failed")
        raise ValueError(f"Metadata JSON validation failed: {exc}") from exc


def upsert_paper_metadata(db: Session, paper: Paper, extracted: ExtractedPaperMetadata) -> PaperMetadata:
    """Write extracted metadata to Paper and PaperMetadata rows."""
    paper.title = _clean_scalar(extracted.title)
    paper.authors = "; ".join(extracted.authors)
    paper.year = extracted.year
    paper.venue = _clean_scalar(extracted.venue)
    paper.abstract = _clean_scalar(extracted.abstract)
    paper.updated_at = datetime.utcnow()

    metadata = (
        db.query(PaperMetadata)
        .filter(PaperMetadata.paper_id == paper.id, PaperMetadata.owner_id == paper.owner_id)
        .first()
    )
    if metadata is None:
        metadata = PaperMetadata(paper_id=paper.id, owner_id=paper.owner_id)
        db.add(metadata)

    metadata.problem = _clean_scalar(extracted.problem)
    metadata.motivation = _clean_scalar(extracted.motivation)
    metadata.contributions = _json_array(extracted.contributions)
    metadata.method_modules = _json_array(extracted.method_modules)
    metadata.datasets = _json_array(extracted.datasets)
    metadata.metrics = _json_array(extracted.metrics)
    metadata.baselines = _json_array(extracted.baselines)
    metadata.limitations = _json_array(extracted.limitations)
    metadata.raw_json = extracted.model_dump_json()
    metadata.updated_at = datetime.utcnow()
    return metadata


def extract_and_store_metadata(db: Session, paper: Paper) -> PaperMetadata:
    """Collect context, call the LLM, and persist validated metadata."""
    context = collect_metadata_context(db, paper)
    extracted = extract_metadata_from_text(context)
    return upsert_paper_metadata(db, paper, extracted)
