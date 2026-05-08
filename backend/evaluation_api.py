"""FastAPI routes for user-scoped RAG evaluation runs."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user, get_db
from evaluation.runner import BASE_DIR, DEFAULT_STRATEGIES, REPORT_ROOT, load_run_report, run_evaluation
from models import EvaluationRun, User
from schemas import EvaluationRunDetail, EvaluationRunListResponse, EvaluationRunSummary

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def _serialize_run(run: EvaluationRun) -> EvaluationRunSummary:
    return EvaluationRunSummary(
        id=run.id,
        name=run.name,
        dataset_path=run.dataset_path,
        strategies=list(run.strategies or []),
        metrics_json=run.metrics_json or {},
        report_path=run.report_path,
        markdown_report_path=run.markdown_report_path or "",
        created_at=run.created_at.isoformat(),
    )


def _parse_strategy_form(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_STRATEGIES)
    return [item.strip() for item in value.split(",") if item.strip()]


def _save_user_dataset(file: UploadFile, owner_id: int) -> str:
    filename = Path(file.filename or "evaluation.jsonl").name
    if not filename.lower().endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="Evaluation dataset must be a JSONL file")
    dataset_dir = REPORT_ROOT / f"user_{owner_id}" / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = dataset_dir / filename
    with dataset_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return str(dataset_path)


def _resolve_allowed_dataset_path(dataset_path: str, owner_id: int) -> str:
    """Allow API path selection only from shared eval data or this user's dataset folder."""
    requested = Path(dataset_path).resolve()
    allowed_roots = [
        (BASE_DIR / "data" / "eval").resolve(),
        (REPORT_ROOT / f"user_{owner_id}" / "datasets").resolve(),
    ]
    if not any(str(requested).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Dataset path is outside the allowed evaluation folders")
    if not requested.exists() or requested.suffix.lower() != ".jsonl":
        raise HTTPException(status_code=400, detail="Dataset path must point to an existing JSONL file")
    return str(requested)


@router.post("/run", response_model=EvaluationRunDetail)
async def create_evaluation_run(
    file: UploadFile | None = File(None),
    dataset_path: str | None = Form(None),
    name: str = Form(""),
    strategies: str = Form(""),
    top_k: int = Form(5),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run an evaluation over only the current user's indexed paper chunks."""
    try:
        if file is not None:
            resolved_dataset = _save_user_dataset(file, current_user.id)
        elif dataset_path:
            resolved_dataset = _resolve_allowed_dataset_path(dataset_path, current_user.id)
        else:
            raise HTTPException(status_code=400, detail="Upload a JSONL file or provide dataset_path")

        run = run_evaluation(
            db,
            user_id=current_user.id,
            dataset_path=resolved_dataset,
            strategies=_parse_strategy_form(strategies),
            name=name,
            top_k=top_k,
        )
        report = load_run_report(run)
        summary = _serialize_run(run).dict()
        return EvaluationRunDetail(
            **summary,
            report=report,
            markdown_report=report.get("markdown_report", ""),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {exc}")


@router.get("/runs", response_model=EvaluationRunListResponse)
async def list_evaluation_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List evaluation runs owned by the current user."""
    runs = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.owner_id == current_user.id)
        .order_by(EvaluationRun.created_at.desc())
        .limit(50)
        .all()
    )
    return EvaluationRunListResponse(runs=[_serialize_run(run) for run in runs])


@router.get("/runs/{run_id}", response_model=EvaluationRunDetail)
async def get_evaluation_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Load one owned evaluation report; other users' reports are not visible."""
    run = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.id == run_id, EvaluationRun.owner_id == current_user.id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    report = load_run_report(run)
    summary = _serialize_run(run).dict()
    return EvaluationRunDetail(
        **summary,
        report=report,
        markdown_report=report.get("markdown_report", ""),
    )
