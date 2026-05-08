"""CLI entry point for PaperPilot-RAG retrieval evaluation.

Usage:
    python -m backend.evaluation.run_eval --dataset data/eval/paperpilot_eval.jsonl --user-id 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import SessionLocal, init_db
from evaluation.runner import DEFAULT_STRATEGIES, run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PaperPilot-RAG retrieval evaluation")
    parser.add_argument("--dataset", required=True, help="Path to JSONL evaluation dataset")
    parser.add_argument("--user-id", required=True, type=int, help="Current user id / owner_id to evaluate")
    parser.add_argument("--name", default="", help="Optional run name")
    parser.add_argument("--top-k", default=5, type=int, help="Top-k cutoff for metrics")
    parser.add_argument(
        "--strategies",
        default=",".join(DEFAULT_STRATEGIES),
        help="Comma-separated strategies: dense_only,bm25_only,hybrid,hybrid_rerank,hybrid_rerank_rewrite",
    )
    args = parser.parse_args()

    init_db()
    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    with SessionLocal() as db:
        run = run_evaluation(
            db,
            user_id=args.user_id,
            dataset_path=args.dataset,
            strategies=strategies,
            name=args.name,
            top_k=args.top_k,
        )
        print(f"Evaluation run {run.id} completed")
        print(f"JSON report: {run.report_path}")
        print(f"Markdown report: {run.markdown_report_path}")


if __name__ == "__main__":
    main()
