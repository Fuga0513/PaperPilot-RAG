"""Metric helpers for PaperPilot-RAG retrieval evaluation."""

from __future__ import annotations

from typing import Any


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def chunk_matches_gold(chunk: dict, item: dict) -> bool:
    """Return true when a retrieved chunk matches gold paper plus any gold cue."""
    haystack = " ".join(
        [
            normalize_text(chunk.get("paper_title")),
            normalize_text(chunk.get("filename")),
            normalize_text(chunk.get("section_title")),
            normalize_text(chunk.get("subsection_title")),
            normalize_text(chunk.get("text")),
        ]
    )
    gold_paper = normalize_text(item.get("gold_paper"))
    if gold_paper and gold_paper not in haystack:
        return False

    sections = [normalize_text(v) for v in item.get("gold_sections", []) if normalize_text(v)]
    keywords = [normalize_text(v) for v in item.get("gold_keywords", []) if normalize_text(v)]
    cues = sections + keywords
    if not cues:
        return bool(gold_paper)
    return any(cue in haystack for cue in cues)


def evaluate_retrieval(item: dict, chunks: list[dict], k: int) -> dict:
    """Compute Hit@k, Recall@k, MRR, and citation-hit for one question."""
    top_chunks = chunks[:k]
    relevant_positions = [
        index
        for index, chunk in enumerate(top_chunks, 1)
        if chunk_matches_gold(chunk, item)
    ]
    gold_units = max(
        1,
        len(item.get("gold_sections") or []) + len(item.get("gold_keywords") or []),
    )
    matched_units = min(len(relevant_positions), gold_units)
    first_rank = relevant_positions[0] if relevant_positions else 0
    return {
        "hit": 1 if relevant_positions else 0,
        "recall": matched_units / gold_units,
        "mrr": 1 / first_rank if first_rank else 0.0,
        "citation_hit": 1 if any(chunk.get("chunk_id") for chunk in top_chunks if chunk_matches_gold(chunk, item)) else 0,
    }


def average_metrics(rows: list[dict]) -> dict:
    """Average metric values for one strategy."""
    if not rows:
        return {"hit_at_k": 0.0, "recall_at_k": 0.0, "mrr": 0.0, "citation_hit_rate": 0.0}
    count = len(rows)
    return {
        "hit_at_k": sum(float(row.get("hit", 0)) for row in rows) / count,
        "recall_at_k": sum(float(row.get("recall", 0)) for row in rows) / count,
        "mrr": sum(float(row.get("mrr", 0)) for row in rows) / count,
        "citation_hit_rate": sum(float(row.get("citation_hit", 0)) for row in rows) / count,
    }
