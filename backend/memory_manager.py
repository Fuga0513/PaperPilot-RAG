"""User-scoped memory manager for PaperPilot-RAG.

Memory is intentionally kept in PostgreSQL text records for stage 16. It may
help prompts with user preferences and project context, but it is never treated
as citation evidence; citations must still come from retrieved paper chunks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import ChatMessage, ChatSession, MemoryItem, Paper, ProjectMemory, ResearchProject

MEMORY_SCOPES = {"global", "project", "paper", "session"}
MEMORY_TYPES = {"preference", "fact", "task", "style", "reviewer_issue"}
MAX_MEMORY_CONTEXT_CHARS = 1800


def _clean_text(value: Any, max_chars: int = 4000) -> str:
    """Normalize user-provided memory content."""
    text = " ".join(str(value or "").split())
    return text[:max_chars]


def _dt(value) -> str:
    """Serialize datetimes for API responses."""
    return value.isoformat() if value else ""


class MemoryManager:
    """Read, write, and inject owner-scoped memory records."""

    def __init__(self, db: Session):
        self.db = db

    def read_short_term_context(self, user_id: int, session_id: str, limit: int = 8) -> dict:
        """Return recent messages and session metadata for one user/session."""
        session = self._get_session(user_id, session_id)
        if not session:
            return {"session_summary": "", "current_paper_id": None, "current_project_id": None, "recent_messages": []}
        rows = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_ref_id == session.id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        metadata = session.metadata_json or {}
        return {
            "session_summary": metadata.get("session_summary", ""),
            "current_paper_id": metadata.get("current_paper_id"),
            "current_project_id": metadata.get("current_project_id"),
            "recent_messages": [
                {"type": row.message_type, "content": row.content[:600], "timestamp": _dt(row.timestamp)}
                for row in reversed(rows)
            ],
        }

    def update_session_summary(
        self,
        user_id: int,
        session_id: str,
        *,
        current_paper_id: int | None = None,
        current_project_id: int | None = None,
    ) -> str:
        """Update session metadata with a compact non-citation summary."""
        session = self._get_session(user_id, session_id)
        if not session:
            return ""
        rows = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_ref_id == session.id)
            .order_by(ChatMessage.id.desc())
            .limit(6)
            .all()
        )
        snippets = []
        for row in reversed(rows):
            label = "User" if row.message_type == "human" else "Assistant"
            snippets.append(f"{label}: {_clean_text(row.content, 300)}")
        summary = "\n".join(snippets)[-1600:]
        metadata = dict(session.metadata_json or {})
        metadata["session_summary"] = summary
        if current_paper_id is not None:
            metadata["current_paper_id"] = current_paper_id
        if current_project_id is not None:
            metadata["current_project_id"] = current_project_id
        session.metadata_json = metadata
        session.updated_at = datetime.utcnow()
        self.db.commit()
        return summary

    def save_memory_item(
        self,
        user_id: int,
        *,
        scope: str,
        memory_type: str,
        content: str,
        metadata_json: dict | None = None,
        source_session_id: str = "",
        project_id: int | None = None,
        paper_id: int | None = None,
    ) -> MemoryItem:
        """Save one explicit owner-scoped memory item."""
        clean_scope = self._validate_scope(scope)
        clean_type = self._validate_memory_type(memory_type)
        clean_content = _clean_text(content)
        if not clean_content:
            raise ValueError("Memory content is required.")
        if project_id is not None:
            self._get_owned_project(user_id, project_id)
        if paper_id is not None:
            self._get_owned_paper(user_id, paper_id)
        item = MemoryItem(
            owner_id=user_id,
            scope=clean_scope,
            memory_type=clean_type,
            content=clean_content,
            metadata_json=metadata_json or {},
            source_session_id=source_session_id or "",
            project_id=project_id,
            paper_id=paper_id,
        )
        self.db.add(item)
        self.db.flush()
        if project_id is not None:
            self.db.add(ProjectMemory(
                owner_id=user_id,
                project_id=project_id,
                memory_type=clean_type,
                content=clean_content,
                source=source_session_id or clean_scope,
            ))
        self.db.commit()
        self.db.refresh(item)
        return item

    def retrieve_memory_items(
        self,
        user_id: int,
        *,
        scope: str | None = None,
        query: str = "",
        project_id: int | None = None,
        paper_id: int | None = None,
        limit: int = 20,
    ) -> list[MemoryItem]:
        """Retrieve owner-scoped memory with simple PostgreSQL text filters."""
        q = self.db.query(MemoryItem).filter(MemoryItem.owner_id == user_id)
        if scope:
            q = q.filter(MemoryItem.scope == self._validate_scope(scope))
        if project_id is not None:
            self._get_owned_project(user_id, project_id)
            q = q.filter(MemoryItem.project_id == project_id)
        if paper_id is not None:
            self._get_owned_paper(user_id, paper_id)
            q = q.filter(MemoryItem.paper_id == paper_id)
        clean_query = _clean_text(query, 200)
        if clean_query:
            pattern = f"%{clean_query}%"
            q = q.filter(or_(
                MemoryItem.content.ilike(pattern),
                MemoryItem.memory_type.ilike(pattern),
                MemoryItem.source_session_id.ilike(pattern),
            ))
        return q.order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc()).limit(limit).all()

    def inject_relevant_memory_into_prompt(
        self,
        user_id: int,
        session_id: str,
        paper_id: int | None = None,
        project_id: int | None = None,
    ) -> str:
        """Build a bounded prompt section from user preferences and project context."""
        short_term = self.read_short_term_context(user_id, session_id)
        items: list[MemoryItem] = []
        items.extend(self.retrieve_memory_items(user_id, scope="global", limit=6))
        if project_id:
            items.extend(self.retrieve_memory_items(user_id, scope="project", project_id=project_id, limit=6))
        if paper_id:
            items.extend(self.retrieve_memory_items(user_id, scope="paper", paper_id=paper_id, limit=4))
        items.extend(self.retrieve_memory_items(user_id, scope="session", query=session_id, limit=4))

        lines = [
            "Memory is user/project context only. It is not citation evidence; paper facts still require RAG citations."
        ]
        if short_term.get("session_summary"):
            lines.append(f"Session summary: {short_term['session_summary']}")
        seen = set()
        for item in items:
            key = (item.scope, item.memory_type, item.content)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{item.scope}/{item.memory_type}] {item.content}")
        context = "\n".join(lines)
        return context[:MAX_MEMORY_CONTEXT_CHARS] if len(lines) > 1 else ""

    def create_project(self, user_id: int, name: str, description: str = "") -> ResearchProject:
        """Create one current-user research project."""
        clean_name = _clean_text(name, 255)
        if not clean_name:
            raise ValueError("Project name is required.")
        project = ResearchProject(owner_id=user_id, name=clean_name, description=_clean_text(description))
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def list_projects(self, user_id: int) -> list[ResearchProject]:
        """List projects owned by the current user."""
        return (
            self.db.query(ResearchProject)
            .filter(ResearchProject.owner_id == user_id)
            .order_by(ResearchProject.updated_at.desc(), ResearchProject.id.desc())
            .all()
        )

    def delete_memory_item(self, user_id: int, memory_id: int) -> bool:
        """Delete one memory item owned by the current user."""
        item = self.db.query(MemoryItem).filter(MemoryItem.id == memory_id, MemoryItem.owner_id == user_id).first()
        if not item:
            return False
        self.db.delete(item)
        self.db.commit()
        return True

    def _get_session(self, user_id: int, session_id: str) -> ChatSession | None:
        return (
            self.db.query(ChatSession)
            .filter(ChatSession.user_id == user_id, ChatSession.session_id == session_id)
            .first()
        )

    def _get_owned_project(self, user_id: int, project_id: int) -> ResearchProject:
        project = (
            self.db.query(ResearchProject)
            .filter(ResearchProject.id == project_id, ResearchProject.owner_id == user_id)
            .first()
        )
        if not project:
            raise PermissionError("Project not found")
        return project

    def _get_owned_paper(self, user_id: int, paper_id: int) -> Paper:
        paper = self.db.query(Paper).filter(Paper.id == paper_id, Paper.owner_id == user_id).first()
        if not paper:
            raise PermissionError("Paper not found")
        return paper

    def _validate_scope(self, scope: str) -> str:
        clean = (scope or "global").strip().lower()
        if clean not in MEMORY_SCOPES:
            raise ValueError(f"Invalid memory scope: {scope}")
        return clean

    def _validate_memory_type(self, memory_type: str) -> str:
        clean = (memory_type or "preference").strip().lower()
        if clean not in MEMORY_TYPES:
            raise ValueError(f"Invalid memory type: {memory_type}")
        return clean


def memory_item_to_dict(item: MemoryItem) -> dict:
    """Serialize a MemoryItem for API responses."""
    return {
        "id": item.id,
        "owner_id": item.owner_id,
        "scope": item.scope,
        "memory_type": item.memory_type,
        "content": item.content,
        "metadata_json": item.metadata_json or {},
        "source_session_id": item.source_session_id,
        "project_id": item.project_id,
        "paper_id": item.paper_id,
        "created_at": _dt(item.created_at),
        "updated_at": _dt(item.updated_at),
    }


def project_to_dict(project: ResearchProject) -> dict:
    """Serialize a ResearchProject for API responses."""
    return {
        "id": project.id,
        "owner_id": project.owner_id,
        "name": project.name,
        "description": project.description,
        "created_at": _dt(project.created_at),
        "updated_at": _dt(project.updated_at),
    }
