"""Authenticated project and memory APIs for PaperPilot-RAG."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user, get_db
from memory_manager import MemoryManager, memory_item_to_dict, project_to_dict
from models import User
from schemas import (
    MemoryContextResponse,
    MemoryDeleteResponse,
    MemoryItemCreate,
    MemoryItemOut,
    MemoryListResponse,
    ProjectListResponse,
    ResearchProjectCreate,
    ResearchProjectOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List research projects owned by the current user only."""
    try:
        manager = MemoryManager(db)
        return ProjectListResponse(projects=[ResearchProjectOut(**project_to_dict(item)) for item in manager.list_projects(current_user.id)])
    except Exception as exc:
        logger.exception("Failed to list projects for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to list projects") from exc


@router.post("/projects", response_model=ResearchProjectOut)
async def create_project(
    request: ResearchProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create one current-user research project."""
    try:
        manager = MemoryManager(db)
        return ResearchProjectOut(**project_to_dict(manager.create_project(current_user.id, request.name, request.description or "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create project for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to create project") from exc


@router.get("/memory", response_model=MemoryListResponse)
async def list_memory_items(
    scope: str | None = Query(default=None),
    query: str = Query(default=""),
    project_id: int | None = Query(default=None),
    paper_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List current-user memory items with simple text filters."""
    try:
        manager = MemoryManager(db)
        items = manager.retrieve_memory_items(
            current_user.id,
            scope=scope,
            query=query,
            project_id=project_id,
            paper_id=paper_id,
        )
        return MemoryListResponse(memories=[MemoryItemOut(**memory_item_to_dict(item)) for item in items])
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to list memory for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to list memory") from exc


@router.post("/memory", response_model=MemoryItemOut)
async def create_memory_item(
    request: MemoryItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save one explicit current-user memory item."""
    try:
        manager = MemoryManager(db)
        item = manager.save_memory_item(
            current_user.id,
            scope=request.scope,
            memory_type=request.memory_type,
            content=request.content,
            metadata_json=request.metadata_json,
            source_session_id=request.source_session_id or "",
            project_id=request.project_id,
            paper_id=request.paper_id,
        )
        return MemoryItemOut(**memory_item_to_dict(item))
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create memory for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to create memory") from exc


@router.delete("/memory/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory_item(
    memory_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one memory item owned by the current user."""
    try:
        manager = MemoryManager(db)
        if not manager.delete_memory_item(current_user.id, memory_id):
            raise HTTPException(status_code=404, detail="Memory item not found")
        return MemoryDeleteResponse(memory_id=memory_id, message="Memory item deleted")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete memory_id=%s user_id=%s", memory_id, current_user.id)
        raise HTTPException(status_code=500, detail="Failed to delete memory") from exc


@router.get("/memory/context", response_model=MemoryContextResponse)
async def get_memory_context(
    session_id: str = Query(default="default_session"),
    project_id: int | None = Query(default=None),
    paper_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview the prompt memory context for the current user."""
    try:
        manager = MemoryManager(db)
        short_term = manager.read_short_term_context(current_user.id, session_id)
        context = manager.inject_relevant_memory_into_prompt(
            current_user.id,
            session_id,
            paper_id=paper_id,
            project_id=project_id,
        )
        return MemoryContextResponse(context=context, session_summary=short_term.get("session_summary", ""))
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to build memory context for user_id=%s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to build memory context") from exc
