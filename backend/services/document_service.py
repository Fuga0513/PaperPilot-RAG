"""Global document upload, indexing, listing, and deletion service."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, UploadFile

from config import DOCUMENT_UPLOAD_DIR
from document_loader import DocumentLoader
from embedding import embedding_service
from milvus_client import MilvusManager
from milvus_writer import MilvusWriter
from parent_chunk_store import ParentChunkStore
from schemas import DocumentDeleteResponse, DocumentInfo, DocumentListResponse, DocumentUploadResponse
from services.indexing_service import IndexingService
from upload_jobs import delete_job_manager, upload_job_manager

loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
milvus_manager = MilvusManager()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)
indexing_service = IndexingService(
    loader=loader,
    milvus_manager=milvus_manager,
    parent_chunk_store=parent_chunk_store,
    writer=milvus_writer,
)


def is_supported_document(filename: str) -> bool:
    file_lower = filename.lower()
    return (
        file_lower.endswith(".pdf")
        or file_lower.endswith((".docx", ".doc"))
        or file_lower.endswith((".xlsx", ".xls"))
    )


async def save_upload_file(file: UploadFile, file_path: Path) -> None:
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def remove_bm25_stats_for_filename(filename: str) -> None:
    rows = milvus_manager.query_all(
        filter_expr=f'source_type == "document" and filename == "{filename}"',
        output_fields=["text"],
    )
    texts = [r.get("text") or "" for r in rows]
    embedding_service.increment_remove_documents(texts)


def cleanup_global_document(filename: str) -> None:
    milvus_manager.init_collection()
    delete_expr = f'source_type == "document" and filename == "{filename}"'
    try:
        remove_bm25_stats_for_filename(filename)
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


def index_global_document(file_path: str, filename: str, progress_callback=None) -> tuple[int, int]:
    result = indexing_service.index_global_document(
        file_path=file_path,
        filename=filename,
        progress_callback=progress_callback,
    )
    return result.parent_chunks, result.leaf_chunks


def process_upload_job(job_id: str, file_path: str, filename: str) -> None:
    failed_step = "cleanup"
    try:
        upload_job_manager.complete_step(job_id, "upload", "File saved on server")

        failed_step = "cleanup"
        upload_job_manager.update_step(job_id, "cleanup", 10, "running", "Cleaning old chunks for the same filename")
        cleanup_global_document(filename)
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


def process_delete_job(job_id: str, filename: str) -> None:
    failed_step = "prepare"
    try:
        failed_step = "prepare"
        delete_job_manager.update_step(job_id, "prepare", 20, "running", "Initializing Milvus collection")
        milvus_manager.init_collection()
        delete_expr = f'source_type == "document" and filename == "{filename}"'
        delete_job_manager.complete_step(job_id, "prepare", "Delete job created")

        failed_step = "bm25"
        delete_job_manager.update_step(job_id, "bm25", 20, "running", "Synchronizing BM25 state")
        remove_bm25_stats_for_filename(filename)
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
        delete_job_manager.complete_job(job_id, f"Deleted {filename}; vectors removed: {deleted_count}")
    except Exception as e:
        delete_job_manager.fail_job(job_id, failed_step, str(e))


def list_global_documents() -> DocumentListResponse:
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
            file_stats[filename] = {"filename": filename, "file_type": file_type, "chunk_count": 0}
        file_stats[filename]["chunk_count"] += 1
    return DocumentListResponse(documents=[DocumentInfo(**stats) for stats in file_stats.values()])


async def upload_global_document_sync(file: UploadFile) -> DocumentUploadResponse:
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if not is_supported_document(filename):
        raise HTTPException(status_code=400, detail="Only PDF, Word, and Excel documents are supported")

    os.makedirs(DOCUMENT_UPLOAD_DIR, exist_ok=True)
    cleanup_global_document(filename)

    file_path = DOCUMENT_UPLOAD_DIR / filename
    with open(file_path, "wb") as f:
        f.write(await file.read())

    parent_count, leaf_count = index_global_document(str(file_path), filename)
    return DocumentUploadResponse(
        filename=filename,
        chunks_processed=leaf_count,
        message=(
            f"Uploaded and processed {filename}: {leaf_count} leaf chunks, "
            f"{parent_count} parent chunks stored in PostgreSQL"
        ),
    )


def delete_global_document(filename: str) -> DocumentDeleteResponse:
    milvus_manager.init_collection()
    delete_expr = f'source_type == "document" and filename == "{filename}"'
    remove_bm25_stats_for_filename(filename)
    result = milvus_manager.delete(delete_expr)
    parent_chunk_store.delete_by_filename(filename)
    return DocumentDeleteResponse(
        filename=filename,
        chunks_deleted=result.get("delete_count", 0) if isinstance(result, dict) else 0,
        message=f"Deleted vectors for {filename}; local file was kept.",
    )
