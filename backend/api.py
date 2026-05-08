import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from agent import chat_with_agent, chat_with_agent_stream, storage
from auth import authenticate_user, create_access_token, get_current_user, get_db, get_password_hash, require_admin, resolve_role
from document_loader import DocumentLoader
from embedding import embedding_service
from milvus_client import MilvusManager
from milvus_writer import MilvusWriter
from models import User
from parent_chunk_store import ParentChunkStore
from schemas import (
    AuthResponse,
    ChatRequest,
    ChatResponse,
    CurrentUserResponse,
    DocumentDeleteJobResponse,
    DocumentDeleteResponse,
    DocumentDeleteStartResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUploadJobResponse,
    DocumentUploadResponse,
    DocumentUploadStartResponse,
    LoginRequest,
    MessageInfo,
    RegisterRequest,
    SessionDeleteResponse,
    SessionInfo,
    SessionListResponse,
    SessionMessagesResponse,
)
from upload_jobs import DELETE_STEPS, delete_job_manager, upload_job_manager

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "documents"

loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
milvus_manager = MilvusManager()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)

router = APIRouter()


def _chat_retrieval_scope(request: ChatRequest) -> str:
    """Normalize chat retrieval scope while keeping the old boolean compatible."""
    scope = (request.retrieval_scope or "private").strip().lower()
    if request.use_global_knowledge and scope == "private":
        scope = "private_plus_global"
    if scope not in {"private", "global", "private_plus_global"}:
        raise HTTPException(status_code=400, detail="Invalid retrieval_scope")
    return scope


def _remove_bm25_stats_for_filename(filename: str) -> None:
    """Remove matching document chunks from persistent BM25 state before vector deletion."""
    rows = milvus_manager.query_all(
        filter_expr=f'source_type == "document" and filename == "{filename}"',
        output_fields=["text"],
    )
    texts = [r.get("text") or "" for r in rows]
    embedding_service.increment_remove_documents(texts)


@router.post("/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    username = (request.username or "").strip()
    password = (request.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")

    role = resolve_role(request.role, request.admin_code)
    user = User(username=username, password_hash=get_password_hash(password), role=role)
    db.add(user)
    db.commit()

    token = create_access_token(username=username, role=role)
    return AuthResponse(access_token=token, username=username, role=role)


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(username=user.username, role=user.role)
    return AuthResponse(access_token=token, username=user.username, role=user.role)


@router.get("/auth/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return CurrentUserResponse(username=current_user.username, role=current_user.role)


@router.get("/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str, current_user: User = Depends(get_current_user)):
    """Return all messages for one current-user session."""
    try:
        messages = [
            MessageInfo(
                type=msg["type"],
                content=msg["content"],
                timestamp=msg["timestamp"],
                rag_trace=msg.get("rag_trace"),
            )
            for msg in storage.get_session_messages(current_user.username, session_id)
        ]
        return SessionMessagesResponse(messages=messages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(current_user: User = Depends(get_current_user)):
    """Return the current user session list."""
    try:
        sessions = [SessionInfo(**item) for item in storage.list_session_infos(current_user.username)]
        sessions.sort(key=lambda x: x.updated_at, reverse=True)
        return SessionListResponse(sessions=sessions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    """Delete one current-user session."""
    try:
        deleted = storage.delete_session(current_user.username, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse(session_id=session_id, message="Session deleted")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    try:
        session_id = request.session_id or "default_session"
        retrieval_scope = _chat_retrieval_scope(request)
        resp = chat_with_agent(
            request.message,
            current_user.username,
            session_id,
            owner_id=current_user.id,
            role=current_user.role,
            retrieval_scope=retrieval_scope,
        )
        if isinstance(resp, dict):
            return ChatResponse(**resp)
        return ChatResponse(response=resp)
    except Exception as e:
        message = str(e)
        match = re.search(r"Error code:\s*(\d{3})", message)
        if match:
            code = int(match.group(1))
            if code == 429:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "The upstream model service returned HTTP 429. Check account quota, rate limits, or model status.\n"
                        f"Original error: {message}"
                    ),
                )
            if code in (401, 403):
                raise HTTPException(status_code=code, detail=message)
            raise HTTPException(status_code=code, detail=message)
        raise HTTPException(status_code=500, detail=message)


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """Stream an Agent chat response over SSE."""

    async def event_generator():
        try:
            session_id = request.session_id or "default_session"
            retrieval_scope = _chat_retrieval_scope(request)
            async for chunk in chat_with_agent_stream(
                request.message,
                current_user.username,
                session_id,
                owner_id=current_user.id,
                role=current_user.role,
                retrieval_scope=retrieval_scope,
            ):
                yield chunk
        except Exception as e:
            error_data = {"type": "error", "content": str(e)}
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _is_supported_document(filename: str) -> bool:
    file_lower = filename.lower()
    return (
        file_lower.endswith(".pdf")
        or file_lower.endswith((".docx", ".doc"))
        or file_lower.endswith((".xlsx", ".xls"))
    )


async def _save_upload_file(file: UploadFile, file_path: Path) -> None:
    """Save an uploaded file in chunks to avoid loading large files into memory."""
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def _process_upload_job(job_id: str, file_path: str, filename: str) -> None:
    """Run global document parsing, chunking, embedding, and job progress updates."""
    failed_step = "cleanup"
    try:
        upload_job_manager.complete_step(job_id, "upload", "File saved on server")

        failed_step = "cleanup"
        upload_job_manager.update_step(job_id, "cleanup", 10, "running", "Cleaning old chunks for the same filename")
        milvus_manager.init_collection()
        delete_expr = f'source_type == "document" and filename == "{filename}"'
        try:
            _remove_bm25_stats_for_filename(filename)
        except Exception:
            pass
        try:
            milvus_manager.delete(delete_expr)
        except Exception:
            pass
        try:
            parent_chunk_store.delete_by_filename(filename)
        except Exception:
            pass
        upload_job_manager.complete_step(job_id, "cleanup", "Old chunks cleaned")

        failed_step = "parse"
        upload_job_manager.update_step(job_id, "parse", 5, "running", "Parsing document and building three-level chunks")
        new_docs = loader.load_document(file_path, filename)
        if not new_docs:
            raise ValueError("Document processing failed: no content extracted")

        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise ValueError("Document processing failed: no searchable leaf chunks were generated")
        upload_job_manager.complete_step(
            job_id,
            "parse",
            f"Parsing complete: {len(parent_docs)} parent chunks, {len(leaf_docs)} leaf chunks",
        )

        failed_step = "parent_store"
        upload_job_manager.update_step(job_id, "parent_store", 20, "running", "Writing parent chunks")
        parent_chunk_store.upsert_documents(parent_docs)
        upload_job_manager.complete_step(job_id, "parent_store", f"Parent chunks stored: {len(parent_docs)}")

        failed_step = "vector_store"
        total_leaf = len(leaf_docs)
        upload_job_manager.update_step(
            job_id,
            "vector_store",
            0,
            "running",
            f"Embedding and indexing: 0 / {total_leaf}",
            total_chunks=total_leaf,
            processed_chunks=0,
        )

        def _on_vector_progress(processed: int, total: int) -> None:
            percent = round(processed * 100 / total) if total else 100
            upload_job_manager.update_step(
                job_id,
                "vector_store",
                percent,
                "running",
                f"Embedding and indexing: {processed} / {total}",
                total_chunks=total,
                processed_chunks=processed,
            )

        milvus_writer.write_documents(leaf_docs, progress_callback=_on_vector_progress)
        upload_job_manager.complete_step(job_id, "vector_store", f"Vector indexing complete: {total_leaf} leaf chunks")
        upload_job_manager.complete_job(job_id, f"Uploaded and processed {filename}")
    except Exception as e:
        upload_job_manager.fail_job(job_id, failed_step, str(e))


def _process_delete_job(job_id: str, filename: str) -> None:
    """Run async global document deletion and report progress to the UI."""
    failed_step = "prepare"
    try:
        failed_step = "prepare"
        delete_job_manager.update_step(job_id, "prepare", 20, "running", "Initializing Milvus collection")
        milvus_manager.init_collection()
        delete_expr = f'source_type == "document" and filename == "{filename}"'
        delete_job_manager.complete_step(job_id, "prepare", "Delete job created")

        failed_step = "bm25"
        delete_job_manager.update_step(job_id, "bm25", 20, "running", "Synchronizing BM25 state")
        _remove_bm25_stats_for_filename(filename)
        delete_job_manager.complete_step(job_id, "bm25", "BM25 state synchronized")

        failed_step = "milvus"
        delete_job_manager.update_step(job_id, "milvus", 30, "running", "Deleting Milvus vectors")
        result = milvus_manager.delete(delete_expr)
        deleted_count = result.get("delete_count", 0) if isinstance(result, dict) else 0
        delete_job_manager.complete_step(job_id, "milvus", f"Milvus vectors deleted: {deleted_count}")

        failed_step = "parent_store"
        delete_job_manager.update_step(job_id, "parent_store", 30, "running", "Deleting PostgreSQL parent chunks")
        parent_chunk_store.delete_by_filename(filename)
        delete_job_manager.complete_step(job_id, "parent_store", "Parent chunks deleted")

        # The frontend briefly keeps completed rows visible before refreshing the list.
        delete_job_manager.complete_job(job_id, f"Deleted {filename}; vectors removed: {deleted_count}")
    except Exception as e:
        delete_job_manager.fail_job(job_id, failed_step, str(e))


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(_: User = Depends(require_admin)):
    """Return the admin-managed global document list."""
    try:
        milvus_manager.init_collection()

        results = milvus_manager.query(
            filter_expr='source_type == "document"',
            output_fields=["filename", "file_type"],
            limit=10000,
        )

        file_stats = {}
        for item in results:
            filename = item.get("filename", "")
            file_type = item.get("file_type", "")
            if filename not in file_stats:
                file_stats[filename] = {
                    "filename": filename,
                    "file_type": file_type,
                    "chunk_count": 0,
                }
            file_stats[filename]["chunk_count"] += 1

        documents = [DocumentInfo(**stats) for stats in file_stats.values()]
        return DocumentListResponse(documents=documents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")

@router.post("/documents/upload/async", response_model=DocumentUploadStartResponse)
async def upload_document_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
):
    """Start async global document upload and indexing."""
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if not _is_supported_document(filename):
        raise HTTPException(status_code=400, detail="Only PDF, Word, and Excel documents are supported")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    job = upload_job_manager.create_job(filename)
    file_path = UPLOAD_DIR / filename

    try:
        upload_job_manager.update_step(job["job_id"], "upload", 1, "running", "Saving file on server")
        await _save_upload_file(file, file_path)
        upload_job_manager.complete_step(job["job_id"], "upload", "File uploaded; waiting for background processing")
    except Exception as e:
        upload_job_manager.fail_job(job["job_id"], "upload", f"File save failed: {e}")
        raise HTTPException(status_code=500, detail=f"File save failed: {e}")

    background_tasks.add_task(_process_upload_job, job["job_id"], str(file_path), filename)
    return DocumentUploadStartResponse(
        job_id=job["job_id"],
        filename=filename,
        message="File uploaded; parsing and indexing in the background",
    )


@router.get("/documents/upload/jobs/{job_id}", response_model=DocumentUploadJobResponse)
async def get_upload_job(job_id: str, _: User = Depends(require_admin)):
    job = upload_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found or expired")
    return DocumentUploadJobResponse(**job)


@router.get("/documents/upload/jobs", response_model=list[DocumentUploadJobResponse])
async def list_upload_jobs(_: User = Depends(require_admin)):
    jobs = upload_job_manager.list_jobs()
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return [DocumentUploadJobResponse(**job) for job in jobs]


@router.delete("/documents/delete/async/{filename}", response_model=DocumentDeleteStartResponse)
async def delete_document_async(
    filename: str,
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
):
    """Start async deletion for one global document."""
    job = delete_job_manager.create_job(
        filename,
        steps=DELETE_STEPS,
        current_step="prepare",
        message="Waiting to delete",
        completion_step="parent_store",
    )
    delete_job_manager.update_step(job["job_id"], "prepare", 1, "running", "Delete job submitted")
    background_tasks.add_task(_process_delete_job, job["job_id"], filename)
    return DocumentDeleteStartResponse(
        job_id=job["job_id"],
        filename=filename,
        message=f"Deleting {filename}",
    )


@router.get("/documents/delete/jobs/{job_id}", response_model=DocumentDeleteJobResponse)
async def get_delete_job(job_id: str, _: User = Depends(require_admin)):
    job = delete_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Delete job not found or expired")
    return DocumentDeleteJobResponse(**job)


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...), _: User = Depends(require_admin)):
    """Upload and embed one global document synchronously. Admin only."""
    try:
        filename = file.filename or ""
        file_lower = filename.lower()
        if not filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        if not (
            file_lower.endswith(".pdf")
            or file_lower.endswith((".docx", ".doc"))
            or file_lower.endswith((".xlsx", ".xls"))
        ):
            raise HTTPException(status_code=400, detail="Only PDF, Word, and Excel documents are supported")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        milvus_manager.init_collection()

        delete_expr = f'source_type == "document" and filename == "{filename}"'
        try:
            _remove_bm25_stats_for_filename(filename)
        except Exception:
            pass
        try:
            milvus_manager.delete(delete_expr)
        except Exception:
            pass
        try:
            parent_chunk_store.delete_by_filename(filename)
        except Exception:
            pass

        file_path = UPLOAD_DIR / filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        try:
            new_docs = loader.load_document(str(file_path), filename)
        except Exception as doc_err:
            raise HTTPException(status_code=500, detail=f"Document processing failed: {doc_err}")

        if not new_docs:
            raise HTTPException(status_code=500, detail="Document processing failed: no content extracted")

        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise HTTPException(status_code=500, detail="Document processing failed: no searchable leaf chunks were generated")

        parent_chunk_store.upsert_documents(parent_docs)
        milvus_writer.write_documents(leaf_docs)

        return DocumentUploadResponse(
            filename=filename,
            chunks_processed=len(leaf_docs),
            message=(
                f"Uploaded and processed {filename}: {len(leaf_docs)} leaf chunks, "
                f"{len(parent_docs)} parent chunks stored in PostgreSQL"
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document upload failed: {str(e)}")


@router.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
async def delete_document(filename: str, _: User = Depends(require_admin)):
    """Delete one global document from Milvus while keeping the local file."""
    try:
        milvus_manager.init_collection()

        delete_expr = f'source_type == "document" and filename == "{filename}"'
        _remove_bm25_stats_for_filename(filename)
        result = milvus_manager.delete(delete_expr)
        parent_chunk_store.delete_by_filename(filename)

        return DocumentDeleteResponse(
            filename=filename,
            chunks_deleted=result.get("delete_count", 0) if isinstance(result, dict) else 0,
            message=f"Deleted vectors for {filename}; local file was kept.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document deletion failed: {str(e)}")
