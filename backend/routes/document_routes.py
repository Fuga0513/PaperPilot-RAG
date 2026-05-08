import os

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from auth import require_admin
from config import DOCUMENT_UPLOAD_DIR
from models import User
from schemas import (
    DocumentDeleteJobResponse,
    DocumentDeleteResponse,
    DocumentDeleteStartResponse,
    DocumentListResponse,
    DocumentUploadJobResponse,
    DocumentUploadResponse,
    DocumentUploadStartResponse,
)
from services.document_service import (
    delete_global_document,
    is_supported_document,
    list_global_documents,
    process_delete_job,
    process_upload_job,
    save_upload_file,
    upload_global_document_sync,
)
from upload_jobs import DELETE_STEPS, delete_job_manager, upload_job_manager

router = APIRouter()


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(_: User = Depends(require_admin)):
    try:
        return list_global_documents()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


@router.post("/documents/upload/async", response_model=DocumentUploadStartResponse)
async def upload_document_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
):
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    if not is_supported_document(filename):
        raise HTTPException(status_code=400, detail="Only PDF, Word, and Excel documents are supported")

    os.makedirs(DOCUMENT_UPLOAD_DIR, exist_ok=True)
    job = upload_job_manager.create_job(filename)
    file_path = DOCUMENT_UPLOAD_DIR / filename

    try:
        upload_job_manager.update_step(job["job_id"], "upload", 1, "running", "Saving file on server")
        await save_upload_file(file, file_path)
        upload_job_manager.complete_step(job["job_id"], "upload", "File uploaded; waiting for background processing")
    except Exception as e:
        upload_job_manager.fail_job(job["job_id"], "upload", f"File save failed: {e}")
        raise HTTPException(status_code=500, detail=f"File save failed: {e}")

    background_tasks.add_task(process_upload_job, job["job_id"], str(file_path), filename)
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
    job = delete_job_manager.create_job(
        filename,
        steps=DELETE_STEPS,
        current_step="prepare",
        message="Waiting to delete",
        completion_step="parent_store",
    )
    delete_job_manager.update_step(job["job_id"], "prepare", 1, "running", "Delete job submitted")
    background_tasks.add_task(process_delete_job, job["job_id"], filename)
    return DocumentDeleteStartResponse(job_id=job["job_id"], filename=filename, message=f"Deleting {filename}")


@router.get("/documents/delete/jobs/{job_id}", response_model=DocumentDeleteJobResponse)
async def get_delete_job(job_id: str, _: User = Depends(require_admin)):
    job = delete_job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Delete job not found or expired")
    return DocumentDeleteJobResponse(**job)


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...), _: User = Depends(require_admin)):
    try:
        return await upload_global_document_sync(file)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document upload failed: {str(e)}")


@router.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
async def delete_document(filename: str, _: User = Depends(require_admin)):
    try:
        return delete_global_document(filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document deletion failed: {str(e)}")
