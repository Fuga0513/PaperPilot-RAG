"""Run PaperPilot-RAG retrieval evaluations and write user-scoped reports."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from evaluation.metrics import average_metrics, evaluate_retrieval
from models import EvaluationItemResult, EvaluationRun, User
from rag_utils import retrieve_documents_for_evaluation

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
REPORT_ROOT = BASE_DIR / "data" / "evaluation"
DEFAULT_STRATEGIES = [
    "dense_only",
    "bm25_only",
    "hybrid",
    "hybrid_rerank",
    "hybrid_rerank_rewrite",
]


def load_jsonl_dataset(path: str | Path) -> list[dict]:
    """Load evaluation items from the supported JSONL format."""
    dataset_path = Path(path)
    items: list[dict] = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
            if not item.get("question"):
                raise ValueError(f"Missing question at line {line_no}")
            items.append(item)
    if not items:
        raise ValueError("Evaluation dataset is empty")
    return items


def normalize_strategies(values: Iterable[str] | None) -> list[str]:
    """Validate strategy names and preserve request order."""
    requested = [str(item).strip() for item in (values or DEFAULT_STRATEGIES) if str(item).strip()]
    invalid = [item for item in requested if item not in DEFAULT_STRATEGIES]
    if invalid:
        raise ValueError(f"Unsupported strategies: {', '.join(invalid)}")
    return requested or list(DEFAULT_STRATEGIES)


def _safe_chunk(chunk: dict) -> dict:
    return {
        "chunk_id": chunk.get("chunk_id"),
        "paper_id": chunk.get("paper_id"),
        "paper_title": chunk.get("paper_title"),
        "filename": chunk.get("filename"),
        "section_title": chunk.get("section_title"),
        "subsection_title": chunk.get("subsection_title"),
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "score": chunk.get("score"),
        "rrf_rank": chunk.get("rrf_rank"),
        "rerank_score": chunk.get("rerank_score"),
        "preview_text": (chunk.get("text") or "")[:500],
    }


def _write_reports(report_base: Path, payload: dict) -> tuple[Path, Path]:
    report_base.mkdir(parents=True, exist_ok=True)
    json_path = report_base / "report.json"
    md_path = report_base / "report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(payload), encoding="utf-8")
    return json_path, md_path


def render_markdown_report(payload: dict) -> str:
    """Render a compact Markdown report for the frontend preview."""
    lines = [
        f"# {payload.get('name') or 'PaperPilot Evaluation'}",
        "",
        f"- Run ID: {payload.get('run_id')}",
        f"- Owner ID: {payload.get('owner_id')}",
        f"- Dataset: `{payload.get('dataset_path')}`",
        f"- Top K: {payload.get('top_k')}",
        "",
        "## Strategy Metrics",
        "",
        "| Strategy | Hit@k | Recall@k | MRR | Citation Hit Rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for strategy, metrics in payload.get("metrics", {}).items():
        lines.append(
            "| {strategy} | {hit:.3f} | {recall:.3f} | {mrr:.3f} | {citation:.3f} |".format(
                strategy=strategy,
                hit=float(metrics.get("hit_at_k", 0)),
                recall=float(metrics.get("recall_at_k", 0)),
                mrr=float(metrics.get("mrr", 0)),
                citation=float(metrics.get("citation_hit_rate", 0)),
            )
        )
    lines.extend(["", "## Question Results", ""])
    for row in payload.get("items", []):
        lines.extend([
            f"### {row.get('question')}",
            "",
            f"- Strategy: `{row.get('strategy')}`",
            f"- Hit: {row.get('hit')}  Recall: {float(row.get('recall', 0)):.3f}  MRR: {float(row.get('mrr', 0)):.3f}  Citation Hit: {row.get('citation_hit')}",
            "",
        ])
        for idx, chunk in enumerate(row.get("retrieved_chunks", [])[:5], 1):
            lines.append(
                f"{idx}. {chunk.get('paper_title') or chunk.get('filename') or 'Unknown'}"
                f" / {chunk.get('section_title') or '-'}"
                f" / score={chunk.get('score')}"
            )
        lines.append("")
    return "\n".join(lines)


def run_evaluation(
    db: Session,
    *,
    user_id: int,
    dataset_path: str,
    strategies: Iterable[str] | None = None,
    name: str = "",
    top_k: int = 5,
) -> EvaluationRun:
    """Run all requested strategies against one user's private paper library."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"user_id does not exist: {user_id}")

    selected_strategies = normalize_strategies(strategies)
    dataset = load_jsonl_dataset(dataset_path)
    display_name = name or f"Evaluation {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"

    run = EvaluationRun(
        owner_id=user_id,
        name=display_name,
        dataset_path=str(dataset_path),
        strategies=selected_strategies,
        metrics_json={},
        report_path="",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    rows: list[dict] = []
    grouped: dict[str, list[dict]] = {strategy: [] for strategy in selected_strategies}
    for item in dataset:
        for strategy in selected_strategies:
            try:
                retrieved = retrieve_documents_for_evaluation(
                    item["question"],
                    top_k=top_k,
                    owner_id=user_id,
                    strategy=strategy,
                )
                chunks = [_safe_chunk(chunk) for chunk in retrieved.get("docs", [])]
                metrics = evaluate_retrieval(item, chunks, top_k)
            except Exception as exc:
                logger.exception("Evaluation failed for strategy=%s question=%s", strategy, item.get("question"))
                chunks = []
                metrics = {"hit": 0, "recall": 0.0, "mrr": 0.0, "citation_hit": 0, "error": str(exc)}

            row = {
                "question": item["question"],
                "gold_paper": item.get("gold_paper", ""),
                "gold_sections": item.get("gold_sections", []),
                "gold_keywords": item.get("gold_keywords", []),
                "strategy": strategy,
                **metrics,
                "retrieved_chunks": chunks,
            }
            rows.append(row)
            grouped[strategy].append(row)
            db.add(EvaluationItemResult(
                run_id=run.id,
                question=item["question"],
                strategy=strategy,
                hit=int(metrics.get("hit", 0)),
                recall=f"{float(metrics.get('recall', 0)):.6f}",
                mrr=f"{float(metrics.get('mrr', 0)):.6f}",
                citation_hit=int(metrics.get("citation_hit", 0)),
                retrieved_chunks_json=chunks,
            ))

    metrics_json = {strategy: average_metrics(items) for strategy, items in grouped.items()}
    report_payload = {
        "run_id": run.id,
        "owner_id": user_id,
        "name": display_name,
        "dataset_path": str(dataset_path),
        "strategies": selected_strategies,
        "top_k": top_k,
        "metrics": metrics_json,
        "items": rows,
        "created_at": run.created_at.isoformat(),
    }
    report_dir = REPORT_ROOT / f"user_{user_id}" / f"run_{run.id}"
    json_path, md_path = _write_reports(report_dir, report_payload)

    run.metrics_json = metrics_json
    run.report_path = str(json_path)
    run.markdown_report_path = str(md_path)
    db.commit()
    db.refresh(run)
    return run


def load_run_report(run: EvaluationRun) -> dict:
    """Read the JSON and Markdown reports recorded on an owned run."""
    payload = {}
    markdown = ""
    if run.report_path and Path(run.report_path).exists():
        payload = json.loads(Path(run.report_path).read_text(encoding="utf-8"))
    if run.markdown_report_path and Path(run.markdown_report_path).exists():
        markdown = Path(run.markdown_report_path).read_text(encoding="utf-8")
    payload.setdefault("metrics", run.metrics_json or {})
    payload["markdown_report"] = markdown
    return payload
