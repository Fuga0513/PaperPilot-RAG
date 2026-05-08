"""Request-local-ish state used by LangChain tools during one Agent turn."""

from __future__ import annotations

from typing import Optional

_LAST_RAG_CONTEXT = None
_KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0
_RAG_STEP_QUEUE = None
_RAG_STEP_LOOP = None
_CURRENT_TOOL_USER_CONTEXT = None


def set_tool_user_context(
    user_id: str | None,
    role: str | None = None,
    owner_id: int | None = None,
    use_global_knowledge: bool = False,
    retrieval_scope: str = "private",
) -> None:
    """Set the current request user context for future retrieval filters."""
    global _CURRENT_TOOL_USER_CONTEXT
    scope = (retrieval_scope or "private").strip().lower()
    if use_global_knowledge and scope == "private":
        scope = "private_plus_global"
    if scope not in {"private", "global", "private_plus_global"}:
        scope = "private"
    _CURRENT_TOOL_USER_CONTEXT = {
        "user_id": user_id,
        "role": role,
        "owner_id": owner_id,
        "use_global_knowledge": scope == "private_plus_global",
        "retrieval_scope": scope,
    }


def get_tool_user_context() -> Optional[dict]:
    return _CURRENT_TOOL_USER_CONTEXT


def set_last_rag_context(context: dict) -> None:
    global _LAST_RAG_CONTEXT
    _LAST_RAG_CONTEXT = context


def get_last_rag_context(clear: bool = True) -> Optional[dict]:
    global _LAST_RAG_CONTEXT
    context = _LAST_RAG_CONTEXT
    if clear:
        _LAST_RAG_CONTEXT = None
    return context


def reset_tool_call_guards() -> None:
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0


def acquire_research_search_slot(tool_name: str) -> Optional[str]:
    """Allow at most one retrieval-style tool call in a single Agent turn."""
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    if _KNOWLEDGE_TOOL_CALLS_THIS_TURN >= 1:
        return (
            f"TOOL_CALL_LIMIT_REACHED: {tool_name} or another retrieval tool has already "
            "been called once in this turn. Use the existing retrieval result and provide "
            "the final answer directly."
        )
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN += 1
    return None


def set_rag_step_queue(queue) -> None:
    """Set the queue used by rag_pipeline to push live RAG step events."""
    global _RAG_STEP_QUEUE, _RAG_STEP_LOOP
    _RAG_STEP_QUEUE = queue
    if queue:
        import asyncio

        try:
            _RAG_STEP_LOOP = asyncio.get_running_loop()
        except RuntimeError:
            _RAG_STEP_LOOP = asyncio.get_event_loop()
    else:
        _RAG_STEP_LOOP = None


def emit_rag_step(icon: str, label: str, detail: str = "") -> None:
    """Push one RAG progress step from sync tools into the async SSE stream."""
    if _RAG_STEP_QUEUE is None or _RAG_STEP_LOOP is None:
        return
    step = {"icon": icon, "label": label, "detail": detail}
    try:
        if not _RAG_STEP_LOOP.is_closed():
            _RAG_STEP_LOOP.call_soon_threadsafe(_RAG_STEP_QUEUE.put_nowait, step)
    except Exception:
        pass
