"""LangChain tools for SuperMew/PaperPilot.

This module is the boundary between the Agent and the custom RAG pipeline. Tool
functions may be selected by LangChain, but retrieval itself stays in
rag_pipeline/rag_utils so Milvus hybrid search, BM25, RRF, rerank, auto-merging,
RAG trace, and SSE step events remain under our control.
"""

from typing import Optional
import os
import requests

from dotenv import load_dotenv
from pydantic import BaseModel, Field

try:
    from langchain_core.tools import StructuredTool, tool
except ImportError:
    from langchain_core.tools import StructuredTool, tool

load_dotenv()

AMAP_WEATHER_API = os.getenv("AMAP_WEATHER_API")
AMAP_API_KEY = os.getenv("AMAP_API_KEY")

_LAST_RAG_CONTEXT = None  
_KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0
_RAG_STEP_QUEUE = None
_RAG_STEP_LOOP = None
_CURRENT_TOOL_USER_CONTEXT = None


class ResearchSearchInput(BaseModel):
    """Input schema for PaperPilot document search."""

    query: str = Field(..., description="Natural-language research question or retrieval query.")


class PaperIdInput(BaseModel):
    """Input schema for tools that target one uploaded paper."""

    paper_id: str = Field(..., description="Stable paper id or filename. Future stages will map this to Paper.id.")
    question: str = Field("", description="Optional focus question for the paper summary.")


class ComparePapersInput(BaseModel):
    """Input schema for multi-paper comparison."""

    paper_ids: list[str] = Field(..., description="Paper ids or filenames to compare.")
    comparison_focus: str = Field("", description="Aspect to compare, such as method, dataset, results, or limitations.")


class ReviewerCommentsInput(BaseModel):
    """Input schema for reviewer-comment analysis."""

    comments: str = Field(..., description="Reviewer comments or decision letter text.")
    paper_id: str = Field("", description="Optional paper id or filename associated with the comments.")


class DraftRebuttalInput(BaseModel):
    """Input schema for rebuttal drafting."""

    comments: str = Field(..., description="Reviewer comments to respond to.")
    evidence_query: str = Field("", description="Optional retrieval query for evidence from uploaded papers or project docs.")


class RelatedWorkInput(BaseModel):
    """Input schema for related-work generation."""

    topic: str = Field(..., description="Research topic or claim for the related-work section.")
    constraints: str = Field("", description="Optional scope, venue, time range, or style constraints.")


# 用户上下文管理：记住“现在是谁在提问”
def set_tool_user_context(user_id: str | None, role: str | None = None, owner_id: int | None = None) -> None:
    """Set the current request user context for future retrieval filters.

    Stage 5 only reserves this boundary. Once Paper/PaperChunk are user-owned,
    this user_id must be propagated into Milvus and PostgreSQL filters.
    """
    global _CURRENT_TOOL_USER_CONTEXT
    _CURRENT_TOOL_USER_CONTEXT = {"user_id": user_id, "role": role, "owner_id": owner_id}


def get_tool_user_context() -> Optional[dict]:
    """Return the current request context reserved for tool-side filtering."""
    return _CURRENT_TOOL_USER_CONTEXT

# RAG Trace 管理：记录“思考和搜索的痕迹”
def _set_last_rag_context(context: dict) -> None:
    global _LAST_RAG_CONTEXT
    _LAST_RAG_CONTEXT = context


def get_last_rag_context(clear: bool = True) -> Optional[dict]:
    """Return the latest RAG trace context captured by a retrieval tool."""
    global _LAST_RAG_CONTEXT
    context = _LAST_RAG_CONTEXT
    if clear:
        _LAST_RAG_CONTEXT = None
    return context


# 工具调用频率管理：限制“每轮思考只能调用一次检索工具”
def reset_tool_call_guards() -> None:
    """Reset per-turn retrieval guard counters."""
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0


def _acquire_research_search_slot(tool_name: str) -> Optional[str]:
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


# RAG 步骤队列管理：设置用于推送实时 RAG 步骤事件的队列
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


# 格式化从知识库查回来的文档片段 -→ [1] 论文A.pdf (Page 5, Chunk 23)：这里是论文里的一段内容...
def _format_retrieved_chunks(docs: list[dict]) -> str:
    """Format retrieved chunks for the Agent while preserving citation anchors."""
    formatted = []
    for i, result in enumerate(docs, 1):
        source = result.get("filename", "Unknown")
        page = result.get("page_number", "N/A")
        text = result.get("text", "")
        chunk_id = result.get("chunk_id", "")
        formatted.append(f"[{i}] {source} (Page {page}, Chunk {chunk_id}):\n{text}")
    return "Retrieved Chunks:\n" + "\n\n---\n\n".join(formatted)


# 执行一次 RAG 检索
def _run_rag_search(query: str, tool_name: str, source_type: str = "document") -> str:
    """Run the custom RAG graph with legacy or private-paper retrieval scope."""
    limit_message = _acquire_research_search_slot(tool_name)
    if limit_message:
        return limit_message

    user_context = get_tool_user_context()
    owner_id = None
    if source_type == "paper":
        owner_id = (user_context or {}).get("owner_id")
        if owner_id is None:
            return "No authenticated user context is available for private paper retrieval."

    from rag_pipeline import run_rag_graph

    rag_result = run_rag_graph(query, owner_id=owner_id, source_type=source_type)
    docs = rag_result.get("docs", []) if isinstance(rag_result, dict) else []
    rag_trace = rag_result.get("rag_trace", {}) if isinstance(rag_result, dict) else {}
    if rag_trace:
        rag_trace["tool_name"] = tool_name
        rag_trace["user_context_reserved"] = bool(user_context)
        _set_last_rag_context({"rag_trace": rag_trace})

    if not docs:
        return "No relevant documents found. Do not invent citations or evidence."

    return _format_retrieved_chunks(docs)


def get_current_weather(location: str, extensions: Optional[str] = "base") -> str:
    """Get weather information from AMap.

    This legacy SuperMew tool is kept for compatibility.
    """
    if not location:
        return "location cannot be empty."
    if extensions not in ("base", "all"):
        return "extensions must be 'base' or 'all'."
    if not AMAP_WEATHER_API or not AMAP_API_KEY:
        return "Weather service is not configured; missing AMAP_WEATHER_API or AMAP_API_KEY."

    params = {
        "key": AMAP_API_KEY,
        "city": location,
        "extensions": extensions,
        "output": "json",
    }
    try:
        resp = requests.get(AMAP_WEATHER_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            return f"Weather query failed: {data.get('info', 'unknown error')}"

        if extensions == "base":
            lives = data.get("lives", [])
            if not lives:
                return f"No weather data found for {location}."
            w = lives[0]
            return (
                f"{w.get('city', location)} realtime weather\n"
                f"Weather: {w.get('weather', 'unknown')}\n"
                f"Temperature: {w.get('temperature', 'unknown')} C\n"
                f"Humidity: {w.get('humidity', 'unknown')}%\n"
                f"Wind: {w.get('winddirection', 'unknown')} {w.get('windpower', 'unknown')}\n"
                f"Updated: {w.get('reporttime', 'unknown')}"
            )

        forecasts = data.get("forecasts", [])
        if not forecasts:
            return f"No weather forecast found for {location}."
        f0 = forecasts[0]
        casts = f0.get("casts") or []
        today = casts[0] if casts else {}
        return (
            f"{f0.get('city', location)} weather forecast\n"
            f"Updated: {f0.get('reporttime', 'unknown')}\n"
            f"Today: {today.get('dayweather', 'unknown')} / {today.get('nightweather', 'unknown')}\n"
            f"Temperature: {today.get('nighttemp', 'unknown')}~{today.get('daytemp', 'unknown')} C"
        )
    except requests.exceptions.Timeout:
        return "Weather service request timed out."
    except requests.exceptions.RequestException as e:
        return f"Weather service request failed: {e}"
    except Exception as e:
        return f"Weather data parsing failed: {e}"


@tool("search_knowledge_base")
def search_knowledge_base(query: str) -> str:
    """Search the existing knowledge base with the custom RAG pipeline.

    Use for legacy document/knowledge questions. Input: query string. Output:
    numbered retrieved chunks with filename, page, chunk id, and text. The final
    answer must cite only these chunks and must not fabricate evidence.
    """
    return _run_rag_search(query=query, tool_name="search_knowledge_base", source_type="document")


def _search_research_documents(query: str) -> str:
    """Search research papers, project docs, reviews, and notes."""
    return _run_rag_search(query=query, tool_name="search_research_documents", source_type="paper")


def _summarize_paper(paper_id: str, question: str = "") -> str:
    """Skeleton for single-paper summarization with retrieval-backed citations."""
    focus = question.strip() or "summarize the problem, method, experiments, results, and limitations"
    query = f"paper:{paper_id} {focus}".strip()
    # TODO(paper-model): after Paper.owner_id and PaperChunk.paper_id exist, filter
    # retrieval by current user_id + paper_id instead of relying on filename text.
    return (
        "summarize_paper is a PaperPilot research-tool skeleton. For now it runs "
        "the existing retrieval pipeline as supporting evidence.\n\n"
        + _run_rag_search(query=query, tool_name="summarize_paper", source_type="paper")
    )


def _compare_papers(paper_ids: list[str], comparison_focus: str = "") -> str:
    """Skeleton for multi-paper comparison."""
    return (
        "compare_papers is not fully implemented in stage 5. Expected output will be "
        "a citation-backed comparison table covering methods, datasets, results, "
        "limitations, and open questions. It must only cite chunks retrieved from the "
        "current user's papers.\n"
        f"Received paper_ids={paper_ids}, comparison_focus={comparison_focus!r}."
    )


def _analyze_reviewer_comments(comments: str, paper_id: str = "") -> str:
    """Skeleton for reviewer-comment analysis."""
    return (
        "analyze_reviewer_comments is not fully implemented in stage 5. Expected output "
        "will group reviewer concerns, severity, required evidence, and response plan. "
        "Any factual claim about the paper must cite retrieved chunks.\n"
        f"Received paper_id={paper_id!r}, comments_preview={comments[:500]!r}."
    )


def _draft_rebuttal(comments: str, evidence_query: str = "") -> str:
    """Skeleton for rebuttal drafting."""
    return (
        "draft_rebuttal is not fully implemented in stage 5. Expected output will draft "
        "polite, evidence-backed rebuttal paragraphs. It must not invent experiments, "
        "numbers, or citations; evidence must come from retrieved chunks.\n"
        f"Received evidence_query={evidence_query!r}, comments_preview={comments[:500]!r}."
    )


def _generate_related_work(topic: str, constraints: str = "") -> str:
    """Skeleton for related-work generation."""
    return (
        "generate_related_work is not fully implemented in stage 5. Expected output will "
        "produce a related-work outline or draft grounded in retrieved papers. It must "
        "cite only retrieved chunks and clearly mark gaps when evidence is missing.\n"
        f"Received topic={topic!r}, constraints={constraints!r}."
    )


search_research_documents = StructuredTool.from_function(
    func=_search_research_documents,
    name="search_research_documents",
    args_schema=ResearchSearchInput,
    description=(
        "Use for searching scientific papers, project documents, reviewer comments, "
        "and research notes. Input: {'query': string}. Output: numbered retrieved "
        "chunks with filename, page, chunk id, and text. Retrieval is performed by "
        "the existing custom RAG pipeline, not a LangChain black-box retriever. Do "
        "not invent evidence; final citations must come only from retrieved chunks."
    ),
)

summarize_paper = StructuredTool.from_function(
    func=_summarize_paper,
    name="summarize_paper",
    args_schema=PaperIdInput,
    description=(
        "Use for summarizing one uploaded paper. Input: {'paper_id': string, "
        "'question': optional string}. Stage 5 output is a skeleton plus any "
        "retrieved evidence from the existing RAG pipeline. Do not invent claims; "
        "citations must come from retrieved chunks."
    ),
)

compare_papers = StructuredTool.from_function(
    func=_compare_papers,
    name="compare_papers",
    args_schema=ComparePapersInput,
    description=(
        "Use for comparing multiple papers. Input: {'paper_ids': list[string], "
        "'comparison_focus': optional string}. Stage 5 output is a not-implemented "
        "skeleton. Future output must be a citation-backed comparison; never invent "
        "evidence and cite only retrieved chunks."
    ),
)

analyze_reviewer_comments = StructuredTool.from_function(
    func=_analyze_reviewer_comments,
    name="analyze_reviewer_comments",
    args_schema=ReviewerCommentsInput,
    description=(
        "Use for analyzing reviewer comments or decision letters. Input: {'comments': "
        "string, 'paper_id': optional string}. Stage 5 output is a skeleton that "
        "classifies concerns and plans evidence needs. Do not invent paper details; "
        "citations must come from retrieved chunks."
    ),
)

draft_rebuttal = StructuredTool.from_function(
    func=_draft_rebuttal,
    name="draft_rebuttal",
    args_schema=DraftRebuttalInput,
    description=(
        "Use for drafting rebuttal responses to reviewers. Input: {'comments': string, "
        "'evidence_query': optional string}. Stage 5 output is a skeleton. Future "
        "drafts must be grounded in retrieved evidence, with no fabricated experiments, "
        "numbers, or citations."
    ),
)

generate_related_work = StructuredTool.from_function(
    func=_generate_related_work,
    name="generate_related_work",
    args_schema=RelatedWorkInput,
    description=(
        "Use for generating a related-work outline or draft. Input: {'topic': string, "
        "'constraints': optional string}. Stage 5 output is a skeleton. Future output "
        "must cite retrieved paper chunks only and explicitly state when evidence is "
        "missing."
    ),
)


RESEARCH_TOOLS = [
    search_research_documents,
    summarize_paper,
    compare_papers,
    analyze_reviewer_comments,
    draft_rebuttal,
    generate_related_work,
]
