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
from citation_builder import build_citations, build_evidence_context

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

    query: str = Field("Compare the selected papers", description="User comparison question or focus.")
    paper_ids: list[str | int] = Field(default_factory=list, description="Optional current-user Paper.id values to compare.")
    filenames: list[str] = Field(default_factory=list, description="Optional current-user filenames or titles to compare.")
    compare_aspects: list[str] = Field(
        default_factory=lambda: ["problem", "method", "contribution", "dataset", "metric", "limitation"],
        description="Optional comparison aspects/columns.",
    )


class ReviewerCommentsInput(BaseModel):
    """Input schema for reviewer-comment analysis."""

    comments: str = Field(..., description="Reviewer comments or decision letter text.")
    paper_id: str = Field("", description="Optional paper id or filename associated with the comments.")


class DraftRebuttalInput(BaseModel):
    """Input schema for rebuttal drafting."""

    comments: str = Field(..., description="Reviewer comments to respond to.")
    paper_id: str | int | None = Field(None, description="Optional current-user Paper.id for the rebuttal target.")


class RelatedWorkInput(BaseModel):
    """Input schema for related-work generation."""

    topic: str = Field(..., description="Research topic or claim for the related-work section.")
    constraints: str = Field("", description="Optional scope, venue, time range, or style constraints.")


class ResearchWritingInput(BaseModel):
    """Input schema for research writing assistance."""

    task_type: str = Field(..., description="Writing task type, such as Generate Related Work or Rewrite Abstract.")
    topic: str = Field("", description="Optional research topic or writing target.")
    user_text: str = Field("", description="Optional draft text to polish, rewrite, or check.")
    paper_ids: list[str | int] = Field(default_factory=list, description="Optional current-user Paper.id values.")
    writing_style: str = Field("general academic", description="Target style, such as TMC, IWQoS, NSFC, or general academic.")
    language: str = Field("en", description="Output language: zh or en.")


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
def _format_retrieved_chunks(docs: list[dict], citations: list[dict]) -> str:
    """Format retrieved chunks as bounded evidence with stable [C1] anchors."""
    context = build_evidence_context(docs, citations)
    return (
        "Use only the evidence below to answer the user.\n"
        "Rules:\n"
        "- Cite key claims with the provided citation ids, such as [C1] or [C1][C2].\n"
        "- If the evidence is insufficient, say \"当前文档证据不足\" and explain what is missing.\n"
        "- Do not invent papers, datasets, experiments, numbers, or citation ids.\n"
        "- Keep the answer structured and concise.\n\n"
        f"Evidence Context:\n{context}"
    )


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
    citations = build_citations(docs, owner_id=owner_id if source_type == "paper" else None)
    cited_ids = {item.get("citation_id") for item in citations}
    docs = [doc for doc in docs if doc.get("citation_id") in cited_ids]
    rag_trace = rag_result.get("rag_trace", {}) if isinstance(rag_result, dict) else {}
    if rag_trace:
        rag_trace["tool_name"] = tool_name
        rag_trace["user_context_reserved"] = bool(user_context)
        rag_trace["citations"] = citations
        rag_trace["tool_calls"] = [{"name": tool_name, "detail": rag_trace.get("retrieval_stage") or "retrieval"}]
        rag_trace["retrieved_chunks"] = docs
        rag_trace["selected_context_chunks"] = docs
        if rag_trace.get("retrieval_stage") == "expanded":
            rag_trace["expanded_retrieved_chunks"] = docs
            rag_trace["second_retrieval_results"] = docs
        else:
            rag_trace["initial_retrieved_chunks"] = docs
            rag_trace["first_retrieval_results"] = docs
        _set_last_rag_context({"rag_trace": rag_trace})

    if not citations:
        return "当前文档证据不足：No relevant accessible evidence chunks were retrieved. Do not invent citations or evidence."

    return _format_retrieved_chunks(docs, citations)


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


def _compare_papers(
    query: str = "Compare the selected papers",
    paper_ids: list[str | int] | None = None,
    filenames: list[str] | None = None,
    compare_aspects: list[str] | None = None,
) -> str:
    """Compare current-user papers with retrieval-backed citations."""
    limit_message = _acquire_research_search_slot("compare_papers")
    if limit_message:
        return limit_message

    user_context = get_tool_user_context() or {}
    owner_id = user_context.get("owner_id")
    username = user_context.get("user_id")
    if owner_id is None or not username:
        return "No authenticated user context is available for private paper comparison."

    from database import SessionLocal
    from models import User
    from paper_comparison import compare_user_papers

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == owner_id).first()
        if not user:
            return "No authenticated user context is available for private paper comparison."
        result = compare_user_papers(
            db,
            user,
            query=query,
            paper_ids=paper_ids or [],
            filenames=filenames or [],
            compare_aspects=compare_aspects or None,
        )
        result.rag_trace["user_context_reserved"] = True
        _set_last_rag_context({"rag_trace": result.rag_trace})
        return result.response
    except PermissionError:
        return "One or more selected papers are not accessible to the current user."
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"compare_papers failed: {exc}"
    finally:
        db.close()


def _get_current_tool_user():
    """Load the SQLAlchemy User row for a private-paper tool call."""
    user_context = get_tool_user_context() or {}
    owner_id = user_context.get("owner_id")
    if owner_id is None:
        return None, None

    from database import SessionLocal
    from models import User

    db = SessionLocal()
    user = db.query(User).filter(User.id == owner_id).first()
    if not user:
        db.close()
        return None, None
    return db, user


def _analyze_reviewer_comments(comments: str, paper_id: str | int | None = None) -> str:
    """Analyze reviewer comments into issue cards."""
    db, user = _get_current_tool_user()
    if not db or not user:
        return "No authenticated user context is available for reviewer analysis."

    from paper_rebuttal import analyze_review_comments

    try:
        result = analyze_review_comments(db, user, comments=comments, paper_id=paper_id)
        rows = ["| Reviewer Comment | Issue Type | Severity | Strategy | Required Action | Evidence Needed |", "| --- | --- | --- | --- | --- | --- |"]
        for point in result.points:
            rows.append(
                "| "
                + " | ".join(str(point.get(key, "")).replace("|", "\\|") for key in [
                    "reviewer_original_comment",
                    "issue_type",
                    "severity",
                    "response_strategy",
                    "required_action",
                    "evidence_needed",
                ])
                + " |"
            )
        return "\n".join(rows)
    except PermissionError:
        return "Selected paper is not accessible to the current user."
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"analyze_reviewer_comments failed: {exc}"
    finally:
        db.close()


def _draft_rebuttal(comments: str, paper_id: str | int | None = None) -> str:
    """Draft a rebuttal using current-user paper evidence."""
    limit_message = _acquire_research_search_slot("draft_rebuttal")
    if limit_message:
        return limit_message

    db, user = _get_current_tool_user()
    if not db or not user:
        return "No authenticated user context is available for rebuttal drafting."

    from paper_rebuttal import draft_rebuttal

    try:
        result = draft_rebuttal(db, user, comments=comments, paper_id=paper_id)
        result.rag_trace["user_context_reserved"] = True
        _set_last_rag_context({"rag_trace": result.rag_trace})
        return result.response
    except PermissionError:
        return "Selected paper is not accessible to the current user."
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"draft_rebuttal failed: {exc}"
    finally:
        db.close()


def _format_writing_tool_output(result) -> str:
    """Format structured writing output for Agent consumption."""
    fact_lines = "\n".join(f"- {item}" for item in result.evidence_based_facts) or "- No evidence-based facts found."
    warning_lines = "\n".join(f"- {item}" for item in result.warnings) or "- None."
    note_lines = "\n".join(f"- {item}" for item in result.revision_notes) or "- None."
    return (
        "## Evidence-based facts\n"
        f"{fact_lines}\n\n"
        "## Suggested writing\n"
        f"{result.suggested_writing or 'No suggested writing generated.'}\n\n"
        "## Warnings\n"
        f"{warning_lines}\n\n"
        "## Revision notes\n"
        f"{note_lines}"
    )


def _research_writing(
    task_type: str,
    topic: str = "",
    user_text: str = "",
    paper_ids: list[str | int] | None = None,
    writing_style: str = "general academic",
    language: str = "en",
) -> str:
    """Run a current-user-scoped research writing task."""
    limit_message = _acquire_research_search_slot("research_writing")
    if limit_message:
        return limit_message

    db, user = _get_current_tool_user()
    if not db or not user:
        return "No authenticated user context is available for research writing."

    from paper_writing import run_research_writing_task

    try:
        result = run_research_writing_task(
            db,
            user,
            task_type=task_type,
            topic=topic,
            user_text=user_text,
            paper_ids=paper_ids or [],
            writing_style=writing_style,
            language=language,
        )
        result.rag_trace["user_context_reserved"] = True
        _set_last_rag_context({"rag_trace": result.rag_trace})
        return _format_writing_tool_output(result)
    except PermissionError:
        return "One or more selected papers are not accessible to the current user."
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return f"research_writing failed: {exc}"
    finally:
        db.close()


def _generate_related_work(topic: str, constraints: str = "") -> str:
    """Generate related work through the research writing tool."""
    return _research_writing(
        task_type="Generate Related Work",
        topic=topic,
        user_text=constraints,
        writing_style="general academic",
        language="en",
    )


search_research_documents = StructuredTool.from_function(
    func=_search_research_documents,
    name="search_research_documents",
    args_schema=ResearchSearchInput,
    description=(
        "Use for searching scientific papers, project documents, reviewer comments, "
        "and research notes. Input: {'query': string}. Output: numbered retrieved "
        "evidence chunks with [C1] citation ids, filename, page, chunk id, and text. Retrieval is performed by "
        "the existing custom RAG pipeline, not a LangChain black-box retriever. Do "
        "not invent evidence; final citations must come only from retrieved chunks. "
        "Use this instead of search_knowledge_base for Chat with Papers and scientific-paper questions."
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
        "Use first for paper comparison intents, including 比较, 对比, 区别, 相同点, 不同点, "
        "related work table, survey, or 'summarize these papers'. Input: {'query': string, "
        "'paper_ids': optional list[string], 'filenames': optional list[string], "
        "'compare_aspects': optional list[string]}. Output is a Markdown comparison table "
        "grounded in retrieved current-user paper chunks. Never invent evidence and cite only retrieved chunks."
    ),
)

analyze_reviewer_comments = StructuredTool.from_function(
    func=_analyze_reviewer_comments,
    name="analyze_reviewer_comments",
    args_schema=ReviewerCommentsInput,
    description=(
        "Use for analyzing reviewer comments or decision letters. Input: {'comments': "
        "string, 'paper_id': optional current-user Paper.id}. Output classifies each "
        "reviewer point by issue type, severity, response strategy, required action, "
        "and evidence needed. Do not invent paper details."
    ),
)

draft_rebuttal = StructuredTool.from_function(
    func=_draft_rebuttal,
    name="draft_rebuttal",
    args_schema=DraftRebuttalInput,
    description=(
        "Use for drafting rebuttal responses to reviewers. Input: {'comments': string, "
        "'paper_id': optional current-user Paper.id}. The draft must be grounded in "
        "current-user retrieved chunks, separate existing evidence from suggested "
        "experiments and manuscript revisions, and explicitly mark insufficient evidence. "
        "Never fabricate experiments, numbers, results, or citation ids."
    ),
)

research_writing = StructuredTool.from_function(
    func=_research_writing,
    name="research_writing",
    args_schema=ResearchWritingInput,
    description=(
        "Use for research writing tasks: Generate Related Work, Polish Contributions, "
        "Rewrite Abstract, Check Introduction Logic, Polish Grant Scientific Question, "
        "and Summarize Experimental Settings. Input includes task_type, optional topic, "
        "user_text, paper_ids, writing_style, and language. Concrete paper facts must "
        "come from current-user retrieved chunks with citations; do not invent references, "
        "experiments, datasets, metrics, numbers, or results."
    ),
)

generate_related_work = StructuredTool.from_function(
    func=_generate_related_work,
    name="generate_related_work",
    args_schema=RelatedWorkInput,
    description=(
        "Use for generating a related-work outline or draft. Input: {'topic': string, "
        "'constraints': optional string}. Output is delegated to research_writing and "
        "must cite current-user retrieved chunks only. Explicitly state when evidence is missing."
    ),
)


RESEARCH_TOOLS = [
    search_research_documents,
    summarize_paper,
    compare_papers,
    analyze_reviewer_comments,
    draft_rebuttal,
    research_writing,
    generate_related_work,
]
