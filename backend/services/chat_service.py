"""Chat/session service wrappers used by HTTP routes."""

from __future__ import annotations

import re

from fastapi import HTTPException

from agent import chat_with_agent, chat_with_agent_stream, storage
from schemas import ChatRequest


def normalize_chat_retrieval_scope(request: ChatRequest) -> str:
    """Normalize chat retrieval scope while keeping the old boolean compatible."""
    scope = (request.retrieval_scope or "private").strip().lower()
    if request.use_global_knowledge and scope == "private":
        scope = "private_plus_global"
    if scope not in {"private", "global", "private_plus_global"}:
        raise HTTPException(status_code=400, detail="Invalid retrieval_scope")
    return scope


def get_session_messages_for_user(username: str, session_id: str) -> list[dict]:
    return storage.get_session_messages(username, session_id)


def list_session_infos_for_user(username: str) -> list[dict]:
    sessions = storage.list_session_infos(username)
    sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return sessions


def delete_session_for_user(username: str, session_id: str) -> bool:
    return storage.delete_session(username, session_id)


def run_chat(request: ChatRequest, current_user):
    session_id = request.session_id or "default_session"
    retrieval_scope = normalize_chat_retrieval_scope(request)
    resp = chat_with_agent(
        request.message,
        current_user.username,
        session_id,
        owner_id=current_user.id,
        role=current_user.role,
        retrieval_scope=retrieval_scope,
    )
    if isinstance(resp, dict):
        return resp
    return {"response": resp}


def map_model_exception(exc: Exception) -> HTTPException:
    message = str(exc)
    match = re.search(r"Error code:\s*(\d{3})", message)
    if match:
        code = int(match.group(1))
        if code == 429:
            return HTTPException(
                status_code=429,
                detail=(
                    "The upstream model service returned HTTP 429. Check account quota, rate limits, or model status.\n"
                    f"Original error: {message}"
                ),
            )
        return HTTPException(status_code=code, detail=message)
    return HTTPException(status_code=500, detail=message)


async def stream_chat_events(request: ChatRequest, current_user):
    session_id = request.session_id or "default_session"
    retrieval_scope = normalize_chat_retrieval_scope(request)
    async for chunk in chat_with_agent_stream(
        request.message,
        current_user.username,
        session_id,
        owner_id=current_user.id,
        role=current_user.role,
        retrieval_scope=retrieval_scope,
    ):
        yield chunk
