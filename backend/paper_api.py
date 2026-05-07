"""Authenticated PaperPilot paper-library APIs.

Stage 6 introduces PostgreSQL tables and user-scoped read/delete endpoints.
Upload parsing and Milvus deletion are intentionally deferred to later stages.
"""

import logging
import hashlib
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from auth import get_current_user, get_db
from models import Paper, PaperChunk, PaperMetadata, User
from schemas import (
    PaperChunkOut,
    PaperDeleteResponse,
    PaperDetailOut,
    PaperMetadataOut,
    PaperOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers", tags=["papers"])

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
PAPER_UPLOAD_ROOT = DATA_DIR / "uploads"
SUPPORTED_PAPER_SUFFIXES = {".pdf", ".docx", ".txt"}


def _dt(value) -> str:
    """Serialize nullable datetimes for API responses."""
    return value.isoformat() if value else ""


def _safe_filename(filename: str) -> str:
    """Return a path-safe basename while preserving a readable filename."""
    name = Path(filename or "").name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Filename is required")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe[:180]


def _validate_paper_file(filename: str) -> str:
    """Validate paper upload type and return the lowercase suffix."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_PAPER_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT files are supported")
    return suffix


def _build_user_paper_path(user_id: int, original_filename: str) -> tuple[Path, str]:
    """Build an isolated per-user paper path.

    Same-user same-name uploads are renamed with a short UUID instead of
    overwritten. This keeps upload history stable and avoids accidental data loss.
    """
    safe_name = _safe_filename(original_filename)
    suffix = _validate_paper_file(safe_name)
    stem = Path(safe_name).stem[:120] or "paper"
    stored_filename = f"{stem}_{uuid4().hex[:12]}{suffix}"
    user_dir = PAPER_UPLOAD_ROOT / str(user_id) / "papers"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / stored_filename, stored_filename


async def _save_upload_and_hash(file: UploadFile, file_path: Path) -> str:
    """Save an uploaded file in chunks and return its SHA256 hash."""
    digest = hashlib.sha256()
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                f.write(chunk)
    except Exception:
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass
        raise
    return digest.hexdigest()


def _paper_to_out(paper: Paper) -> PaperOut:
    """Convert a Paper ORM row to a public response without file_path."""
    return PaperOut(
        id=paper.id,
        filename=paper.filename,
        original_filename=paper.original_filename,
        title=paper.title,
        authors=paper.authors,
        year=paper.year,
        venue=paper.venue,
        abstract=paper.abstract,
        keywords=paper.keywords,
        file_hash=paper.file_hash,
        status=paper.status,
        created_at=_dt(paper.created_at),
        updated_at=_dt(paper.updated_at),
    )


def _metadata_to_out(metadata: PaperMetadata | None) -> PaperMetadataOut | None:
    """Convert extracted metadata to the response schema."""
    if metadata is None:
        return None
    return PaperMetadataOut(
        id=metadata.id,
        paper_id=metadata.paper_id,
        problem=metadata.problem,
        motivation=metadata.motivation,
        contributions=metadata.contributions,
        method_modules=metadata.method_modules,
        datasets=metadata.datasets,
        metrics=metadata.metrics,
        baselines=metadata.baselines,
        limitations=metadata.limitations,
        raw_json=metadata.raw_json,
        created_at=_dt(metadata.created_at),
        updated_at=_dt(metadata.updated_at),
    )


def _chunk_to_out(chunk: PaperChunk) -> PaperChunkOut:
    """Convert a PaperChunk ORM row to a user-safe response."""
    return PaperChunkOut(
        id=chunk.id,
        paper_id=chunk.paper_id,
        chunk_id=chunk.chunk_id,
        section_title=chunk.section_title,
        subsection_title=chunk.subsection_title,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        chunk_level=chunk.chunk_level,
        parent_chunk_id=chunk.parent_chunk_id,
        root_chunk_id=chunk.root_chunk_id,
        chunk_type=chunk.chunk_type,
        text=chunk.text,
        created_at=_dt(chunk.created_at),
    )


def _get_owned_paper(db: Session, paper_id: int, current_user: User) -> Paper:
    """Fetch one paper scoped to the logged-in user.

    Admins intentionally follow the same owner filter in stage 6. Global paper
    management can be added later under separate admin-only endpoints.
    """
    paper = (
        db.query(Paper)
        .filter(Paper.id == paper_id, Paper.owner_id == current_user.id)
        .first()
    )
    if not paper:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return paper


@router.get("", response_model=list[PaperOut])
async def list_papers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return papers owned by the current user only."""
    try:
        papers = (
            db.query(Paper)
            .filter(Paper.owner_id == current_user.id)
            .order_by(Paper.updated_at.desc(), Paper.id.desc())
            .all()
        )
        return [_paper_to_out(paper) for paper in papers]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list papers for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to list papers") from exc


@router.post("/upload", response_model=PaperDetailOut)
async def upload_paper(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a user-owned paper file and create its Paper row.

    Stage 7 intentionally stops at file persistence + PostgreSQL metadata.
    Parsing, chunking, embedding, and Milvus indexing are handled later.
    """
    original_filename = _safe_filename(file.filename or "")
    file_path, stored_filename = _build_user_paper_path(current_user.id, original_filename)
    try:
        file_hash = await _save_upload_and_hash(file, file_path)
        paper = Paper(
            owner_id=current_user.id,
            filename=stored_filename,
            original_filename=original_filename,
            title="",
            authors="",
            year=None,
            venue="",
            abstract="",
            keywords="",
            file_path=str(file_path),
            file_hash=file_hash,
            status="uploaded",
        )
        db.add(paper)
        db.commit()
        db.refresh(paper)
        base = _paper_to_out(paper).model_dump()
        return PaperDetailOut(**base, chunk_count=0, metadata=None)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to upload paper for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to upload paper") from exc


@router.get("/{paper_id}", response_model=PaperDetailOut)
async def get_paper(
    paper_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return one current-user-owned paper with metadata summary."""
    try:
        paper = _get_owned_paper(db, paper_id, current_user)
        chunk_count = db.query(PaperChunk).filter(
            PaperChunk.paper_id == paper.id,
            PaperChunk.owner_id == current_user.id,
        ).count()
        metadata = db.query(PaperMetadata).filter(
            PaperMetadata.paper_id == paper.id,
            PaperMetadata.owner_id == current_user.id,
        ).first()
        base = _paper_to_out(paper).model_dump()
        return PaperDetailOut(
            **base,
            chunk_count=chunk_count,
            metadata=_metadata_to_out(metadata),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get paper_id=%s for user_id=%s", paper_id, current_user.id)
        raise HTTPException(status_code=500, detail="Failed to get paper") from exc


@router.get("/{paper_id}/chunks", response_model=list[PaperChunkOut])
async def list_paper_chunks(
    paper_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return chunks for one current-user-owned paper only."""
    try:
        paper = _get_owned_paper(db, paper_id, current_user)
        chunks = (
            db.query(PaperChunk)
            .filter(PaperChunk.paper_id == paper.id, PaperChunk.owner_id == current_user.id)
            .order_by(PaperChunk.page_start.asc().nulls_last(), PaperChunk.id.asc())
            .all()
        )
        return [_chunk_to_out(chunk) for chunk in chunks]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list chunks for paper_id=%s user_id=%s", paper_id, current_user.id)
        raise HTTPException(status_code=500, detail="Failed to list paper chunks") from exc


@router.delete("/{paper_id}", response_model=PaperDeleteResponse)
async def delete_paper(
    paper_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one current-user-owned paper database record.

    Milvus vector deletion is deferred until paper metadata is written into
    Milvus in later stages.
    """
    try:
        paper = _get_owned_paper(db, paper_id, current_user)
        db.delete(paper)
        db.commit()
        return PaperDeleteResponse(
            paper_id=paper_id,
            message="Paper database record deleted. Vector cleanup will be added in a later stage.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to delete paper_id=%s for user_id=%s", paper_id, current_user.id)
        raise HTTPException(status_code=500, detail="Failed to delete paper") from exc
