"""Authenticated PaperPilot paper-library APIs.

Stage 6 introduces PostgreSQL tables and user-scoped read/delete endpoints.
Upload parsing and Milvus deletion are intentionally deferred to later stages.
"""

import logging
import hashlib
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from auth import get_current_user, get_db
from models import Paper, PaperChunk, PaperMetadata, User
from paper_metadata_extractor import extract_and_store_metadata
from paper_parser import ResearchPaperParser
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
paper_parser = ResearchPaperParser()


def _dt(value) -> str:
    """Serialize nullable datetimes for API responses."""
    return value.isoformat() if value else ""


def _db_text(value) -> str:
    """Normalize text before inserting into PostgreSQL Text/Varchar columns."""
    text = str(value or "")
    text = text.replace("\x00", "")
    return "".join(char for char in text if char in ("\n", "\t") or ord(char) >= 32)


def _safe_filename(filename: str) -> str:
    """Return a path-safe basename while preserving Unicode names.

    Path traversal is blocked by taking only the basename. We keep Chinese and
    other Unicode word characters for readability, while replacing characters
    that are unsafe on Windows/Linux filesystems.
    """
    name = Path(filename or "").name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Filename is required")
    stem = Path(name).stem.strip()
    suffix = Path(name).suffix.lower()
    if not suffix:
        raise HTTPException(status_code=400, detail="Filename extension is required")
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem, flags=re.UNICODE).strip(" ._")
    safe_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix)
    safe = f"{safe_stem or 'paper'}{safe_suffix}"
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
        paper_title=chunk.paper_title,
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


def _store_parsed_chunks(db: Session, paper: Paper, chunks: list[dict]) -> int:
    """Replace a paper's parsed chunks with newly generated PaperChunk rows."""
    db.query(PaperChunk).filter(
        PaperChunk.paper_id == paper.id,
        PaperChunk.owner_id == paper.owner_id,
    ).delete(synchronize_session=False)
    rows = [
        PaperChunk(
            paper_id=paper.id,
            owner_id=paper.owner_id,
            chunk_id=_db_text(item["chunk_id"]),
            paper_title=_db_text(item.get("paper_title", "")),
            section_title=_db_text(item.get("section_title") or "Unknown"),
            subsection_title=_db_text(item.get("subsection_title") or ""),
            page_start=item.get("page_start"),
            page_end=item.get("page_end"),
            chunk_level=int(item.get("chunk_level") or 1),
            parent_chunk_id=_db_text(item.get("parent_chunk_id") or ""),
            root_chunk_id=_db_text(item.get("root_chunk_id") or ""),
            chunk_type=_db_text(item.get("chunk_type") or "unknown"),
            text=_db_text(item.get("text") or ""),
        )
        for item in chunks
        if item.get("text")
    ]
    db.add_all(rows)
    return len(rows)


def _parse_and_index_paper_chunks(db: Session, paper: Paper) -> int:
    """Parse the uploaded file and write section-aware chunks to PostgreSQL."""
    chunks = paper_parser.parse_file(
        file_path=paper.file_path,
        filename=paper.filename,
        paper_id=paper.id,
        owner_id=paper.owner_id,
        paper_title=paper.title,
    )
    if not chunks:
        raise ValueError("No chunks were generated from the uploaded paper")
    return _store_parsed_chunks(db, paper, chunks)


def _extract_metadata_after_parse(db: Session, paper: Paper) -> bool:
    """Try metadata extraction without invalidating parsed chunks on failure."""
    try:
        extract_and_store_metadata(db, paper)
        paper.status = "parsed"
        paper.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(paper)
        return True
    except Exception:
        db.rollback()
        paper = db.query(Paper).filter(Paper.id == paper.id, Paper.owner_id == paper.owner_id).first()
        if paper:
            paper.status = "metadata_failed"
            paper.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(paper)
        logger.exception("Failed to extract metadata for paper_id=%s", paper.id if paper else None)
        return False


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
            status="parsing",
        )
        db.add(paper)
        db.commit()
        db.refresh(paper)
        chunk_count = 0
        try:
            chunk_count = _parse_and_index_paper_chunks(db, paper)
            paper.status = "parsed"
            paper.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(paper)
            _extract_metadata_after_parse(db, paper)
            db.refresh(paper)
        except Exception as parse_exc:
            db.rollback()
            paper = db.query(Paper).filter(Paper.id == paper.id, Paper.owner_id == current_user.id).first()
            if paper:
                paper.status = "failed"
                paper.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(paper)
            logger.exception("Failed to parse uploaded paper_id=%s user_id=%s", paper.id if paper else None, current_user.id)

        metadata = db.query(PaperMetadata).filter(
            PaperMetadata.paper_id == paper.id,
            PaperMetadata.owner_id == current_user.id,
        ).first()
        base = _paper_to_out(paper).model_dump()
        return PaperDetailOut(**base, chunk_count=chunk_count, metadata=_metadata_to_out(metadata))
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


@router.post("/{paper_id}/parse", response_model=PaperDetailOut)
async def parse_paper(
    paper_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-parse one current-user-owned paper and replace its PaperChunk rows.

    This is useful for older uploads or failed parses after local schema changes.
    Milvus indexing is still intentionally deferred to later stages.
    """
    try:
        paper = _get_owned_paper(db, paper_id, current_user)
        paper.status = "parsing"
        paper.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(paper)

        chunk_count = _parse_and_index_paper_chunks(db, paper)
        paper.status = "parsed"
        paper.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(paper)
        _extract_metadata_after_parse(db, paper)
        db.refresh(paper)

        base = _paper_to_out(paper).model_dump()
        metadata = db.query(PaperMetadata).filter(
            PaperMetadata.paper_id == paper.id,
            PaperMetadata.owner_id == current_user.id,
        ).first()
        return PaperDetailOut(**base, chunk_count=chunk_count, metadata=_metadata_to_out(metadata))
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        try:
            paper = _get_owned_paper(db, paper_id, current_user)
            paper.status = "failed"
            paper.updated_at = datetime.utcnow()
            db.commit()
        except Exception:
            db.rollback()
        logger.exception("Failed to parse paper_id=%s for user_id=%s", paper_id, current_user.id)
        raise HTTPException(status_code=500, detail="Failed to parse paper") from exc


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
