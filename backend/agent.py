from dotenv import load_dotenv
import os
import json
import asyncio
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk, SystemMessage
from tools import (
    RESEARCH_TOOLS,
    get_current_weather,
    search_knowledge_base,
    get_last_rag_context,
    reset_tool_call_guards,
    set_rag_step_queue,
    set_tool_user_context,
)
from datetime import datetime
from cache import cache
from database import SessionLocal
from memory_manager import MemoryManager
from models import User, ChatSession, ChatMessage

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

class ConversationStorage:
    """对话存储（PostgreSQL + Redis）。"""

    @staticmethod
    def _messages_cache_key(user_id: str, session_id: str) -> str:
        return f"chat_messages:{user_id}:{session_id}"

    @staticmethod
    def _sessions_cache_key(user_id: str) -> str:
        return f"chat_sessions:{user_id}"

    @staticmethod
    def _to_langchain_messages(records: list[dict]) -> list:
        messages = []
        for msg_data in records:
            msg_type = msg_data.get("type")
            content = msg_data.get("content", "")
            if msg_type == "human":
                messages.append(HumanMessage(content=content))
            elif msg_type == "ai":
                messages.append(AIMessage(content=content))
            elif msg_type == "system":
                messages.append(SystemMessage(content=content))
        return messages

    def save(self, user_id: str, session_id: str, messages: list, metadata: dict = None, extra_message_data: list = None):
        """保存对话"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return

            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                session = ChatSession(user_id=user.id, session_id=session_id, metadata_json=metadata or {})
                db.add(session)
                db.flush()
            else:
                session.metadata_json = metadata or {}

            db.query(ChatMessage).filter(ChatMessage.session_ref_id == session.id).delete(synchronize_session=False)

            serialized = []
            now = datetime.utcnow()
            for idx, msg in enumerate(messages):
                rag_trace = None
                if extra_message_data and idx < len(extra_message_data):
                    extra = extra_message_data[idx] or {}
                    rag_trace = extra.get("rag_trace")

                db.add(
                    ChatMessage(
                        session_ref_id=session.id,
                        message_type=msg.type,
                        content=str(msg.content),
                        timestamp=now,
                        rag_trace=rag_trace,
                    )
                )
                serialized.append(
                    {
                        "type": msg.type,
                        "content": str(msg.content),
                        "timestamp": now.isoformat(),
                        "rag_trace": rag_trace,
                    }
                )

            session.updated_at = now
            db.commit()

            cache.set_json(self._messages_cache_key(user_id, session_id), serialized)
            cache.delete(self._sessions_cache_key(user_id))
        finally:
            db.close()

    def load(self, user_id: str, session_id: str) -> list:
        """加载对话"""
        cached = cache.get_json(self._messages_cache_key(user_id, session_id))
        if cached is not None:
            return self._to_langchain_messages(cached)

        records = self.get_session_messages(user_id, session_id)
        cache.set_json(self._messages_cache_key(user_id, session_id), records)
        return self._to_langchain_messages(records)

    def list_sessions(self, user_id: str) -> list:
        """列出用户的所有会话"""
        return [item["session_id"] for item in self.list_session_infos(user_id)]

    def list_session_infos(self, user_id: str) -> list[dict]:
        cached = cache.get_json(self._sessions_cache_key(user_id))
        if cached is not None:
            return cached

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []

            sessions = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
                .all()
            )
            result = []
            for s in sessions:
                count = db.query(ChatMessage).filter(ChatMessage.session_ref_id == s.id).count()
                result.append(
                    {
                        "session_id": s.session_id,
                        "updated_at": s.updated_at.isoformat(),
                        "message_count": count,
                    }
                )
            cache.set_json(self._sessions_cache_key(user_id), result)
            return result
        finally:
            db.close()

    def get_session_messages(self, user_id: str, session_id: str) -> list[dict]:
        cached = cache.get_json(self._messages_cache_key(user_id, session_id))
        if cached is not None:
            return cached

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return []

            rows = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_ref_id == session.id)
                .order_by(ChatMessage.id.asc())
                .all()
            )
            result = [
                {
                    "type": row.message_type,
                    "content": row.content,
                    "timestamp": row.timestamp.isoformat(),
                    "rag_trace": row.rag_trace,
                }
                for row in rows
            ]
            cache.set_json(self._messages_cache_key(user_id, session_id), result)
            return result
        finally:
            db.close()

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """删除指定用户的会话，返回是否删除成功"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return False
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return False

            db.delete(session)
            db.commit()
            cache.delete(self._messages_cache_key(user_id, session_id))
            cache.delete(self._sessions_cache_key(user_id))
            return True
        finally:
            db.close()



def create_agent_instance():
    model = init_chat_model(
        model=MODEL,
        model_provider="openai",
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=0.3,
        stream_usage=True,
    )

    agent = create_agent(
        model=model,
        tools=[get_current_weather, search_knowledge_base, *RESEARCH_TOOLS],
        system_prompt=(
            "You are PaperPilot-RAG, a research-oriented Agentic RAG assistant. "
            "When responding, you may use tools to assist. "
            "For explicit paper comparison intents such as 比较, 对比, 区别, 相同点, 不同点, related work table, survey, or 'summarize these papers', use compare_papers first. "
            "For reviewer comments, decision letters, review analysis, response planning, or rebuttal drafting, use analyze_reviewer_comments or draft_rebuttal first. "
            "For research writing tasks such as related work, abstract rewriting, contribution polishing, introduction logic checks, grant scientific questions, or experimental-setting summaries, use research_writing first. "
            "For other Chat with Papers, scientific paper questions, project documents, reviewer comments, research evidence, definitions from uploaded papers, summaries, or citation-backed QA, use search_research_documents. "
            "Use search_knowledge_base only when the user explicitly asks about the legacy/global knowledge base or administrator-uploaded global documents. "
            "Use summarize_paper, compare_papers, analyze_reviewer_comments, draft_rebuttal, and generate_related_work only for their named research workflows. "
            "Do not call the same retrieval-style tool repeatedly in one turn. At most one research/knowledge retrieval tool call per turn. "
            "Once you receive retrieved chunks, immediately produce the final answer based on that result. "
            "After receiving a retrieval result, do not call another tool. "
            "Use only provided evidence context when answering research questions. Key claims must cite provided ids like [C1] or [C1][C2]. "
            "Never fabricate citation ids, papers, numbers, datasets, experiments, or evidence. "
            "If retrieved context is insufficient, say 当前文档证据不足 and state what is missing. "
            "If tool results include a Step-back Question/Answer, use the general principle to reason and answer, but do not reveal chain-of-thought. "
            "Respect user isolation: private papers and traces must be scoped to the logged-in user when user-level paper storage is available."
        ),
    )
    return agent, model


agent, model = create_agent_instance()

storage = ConversationStorage()

def summarize_old_messages(model, messages: list) -> str:
    """将旧消息总结为摘要"""
    # 提取旧对话
    old_conversation = "\n".join([
        f"{'用户' if msg.type == 'human' else 'AI'}: {msg.content}"
        for msg in messages
    ])

    # 生成摘要
    summary_prompt = f"""请总结以下对话的关键信息：

{old_conversation}
总结（包含用户信息、重要事实、待办事项）："""

    summary = model.invoke(summary_prompt).content
    return summary


def _build_memory_system_message(owner_id: int | None, session_id: str) -> SystemMessage | None:
    """Build transient user-scoped memory context for the Agent prompt."""
    if owner_id is None:
        return None
    db = SessionLocal()
    try:
        context = MemoryManager(db).inject_relevant_memory_into_prompt(owner_id, session_id)
        if not context:
            return None
        return SystemMessage(content=f"User-scoped memory context:\n{context}")
    finally:
        db.close()


def _update_memory_session_summary(owner_id: int | None, session_id: str) -> None:
    """Refresh short-term session summary after messages are persisted."""
    if owner_id is None:
        return
    db = SessionLocal()
    try:
        MemoryManager(db).update_session_summary(owner_id, session_id)
    finally:
        db.close()


def chat_with_agent(
    user_text: str,
    user_id: str = "default_user",
    session_id: str = "default_session",
    owner_id: int | None = None,
    role: str | None = None,
):
    """使用 Agent 处理用户消息并返回响应"""
    messages = storage.load(user_id, session_id)

    # 清理可能残留的 RAG 上下文，避免跨请求污染
    get_last_rag_context(clear=True)
    reset_tool_call_guards()
    set_tool_user_context(user_id=user_id, role=role, owner_id=owner_id)
    
    if len(messages) > 50:
        summary = summarize_old_messages(model, messages[:40])

        messages = [
            SystemMessage(content=f"之前的对话摘要：\n{summary}")
        ] + messages[40:]

    memory_message = _build_memory_system_message(owner_id, session_id)
    runtime_messages = list(messages)
    if memory_message:
        runtime_messages.append(memory_message)
    runtime_messages.append(HumanMessage(content=user_text))
    result = agent.invoke(
        {"messages": runtime_messages},
        config={"recursion_limit": 8},
    )

    response_content = ""
    if isinstance(result, dict):
        if "output" in result:
            response_content = result["output"]
        elif "messages" in result and result["messages"]:
            msg = result["messages"][-1]
            response_content = getattr(msg, "content", str(msg))
        else:
            response_content = str(result)
    elif hasattr(result, "content"):
        response_content = result.content
    else:
        response_content = str(result)
    
    messages.append(HumanMessage(content=user_text))
    messages.append(AIMessage(content=response_content))

    rag_context = get_last_rag_context(clear=True)
    rag_trace = rag_context.get("rag_trace") if rag_context else None
    citations = rag_trace.get("citations", []) if rag_trace else []
    tool_calls = rag_trace.get("tool_calls", []) if rag_trace else []

    extra_message_data = [None] * (len(messages) - 1) + [{"rag_trace": rag_trace}]
    storage.save(user_id, session_id, messages, extra_message_data=extra_message_data)
    _update_memory_session_summary(owner_id, session_id)

    return {
        "response": response_content,
        "citations": citations,
        "rag_trace": rag_trace,
        "tool_calls": tool_calls,
    }


async def chat_with_agent_stream(
    user_text: str,
    user_id: str = "default_user",
    session_id: str = "default_session",
    owner_id: int | None = None,
    role: str | None = None,
):
    """使用 Agent 处理用户消息并流式返回响应。
    
    架构：使用统一输出队列 + 后台任务，确保 RAG 检索步骤在工具执行期间实时推送，
    而非等待工具完成后才显示。
    """
    messages = storage.load(user_id, session_id)

    # 清理可能残留的 RAG 上下文
    get_last_rag_context(clear=True)
    reset_tool_call_guards()
    set_tool_user_context(user_id=user_id, role=role, owner_id=owner_id)

    # 统一输出队列：所有事件（content / rag_step）都汇入这里
    output_queue = asyncio.Queue()

    class _RagStepProxy:
        """代理对象：将 emit_rag_step 的原始 step dict 包装后放入统一输出队列。"""
        def put_nowait(self, step):
            output_queue.put_nowait({"type": "rag_step", "step": step})

    set_rag_step_queue(_RagStepProxy())

    if len(messages) > 50:
        summary = summarize_old_messages(model, messages[:40])
        messages = [
            SystemMessage(content=f"之前的对话摘要：\n{summary}")
        ] + messages[40:]

    memory_message = _build_memory_system_message(owner_id, session_id)
    runtime_messages = list(messages)
    if memory_message:
        runtime_messages.append(memory_message)
    runtime_messages.append(HumanMessage(content=user_text))

    full_response = ""

    async def _agent_worker():
        """后台任务：运行 agent 并将内容 chunk 推入输出队列。"""
        nonlocal full_response
        try:
            async for msg, metadata in agent.astream(
                {"messages": runtime_messages},
                stream_mode="messages",
                config={"recursion_limit": 8},
            ):
                if not isinstance(msg, AIMessageChunk):
                    continue
                if getattr(msg, "tool_call_chunks", None):
                    continue

                content = ""
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, str):
                            content += block
                        elif isinstance(block, dict) and block.get("type") == "text":
                            content += block.get("text", "")

                if content:
                    full_response += content
                    await output_queue.put({"type": "content", "content": content})
        except Exception as e:
            await output_queue.put({"type": "error", "content": str(e)})
        finally:
            # 哨兵：通知主循环 agent 已完成
            await output_queue.put(None)

    # 启动后台任务
    agent_task = asyncio.create_task(_agent_worker())

    try:
        # 主循环：持续从统一队列取事件并 yield SSE
        # RAG 步骤在工具执行期间通过 call_soon_threadsafe 实时入队，不需要等 agent 产出 chunk
        while True:
            event = await output_queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"
    except GeneratorExit:
        # 客户端断开连接（AbortController）时，FastAPI 会向此生成器抛出 GeneratorExit
        # 我们必须在此处取消后台任务
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass  # 任务已成功取消
        raise  # 重新抛出 GeneratorExit 以便 FastAPI 正确处理关闭
    finally:
        # 正常结束或异常退出时清理
        set_rag_step_queue(None)
        if not agent_task.done():
             agent_task.cancel()

    # 获取 RAG trace
    rag_context = get_last_rag_context(clear=True)
    rag_trace = rag_context.get("rag_trace") if rag_context else None
    citations = rag_trace.get("citations", []) if rag_trace else []
    tool_calls = rag_trace.get("tool_calls", []) if rag_trace else []

    if citations:
        yield f"data: {json.dumps({'type': 'citations', 'citations': citations})}\n\n"

    # 发送 trace 信息
    if rag_trace:
        yield f"data: {json.dumps({'type': 'trace', 'rag_trace': rag_trace, 'citations': citations, 'tool_calls': tool_calls})}\n\n"

    # 发送结束信号
    yield "data: [DONE]\n\n"

    # 保存对话
    messages.append(HumanMessage(content=user_text))
    messages.append(AIMessage(content=full_response))
    extra_message_data = [None] * (len(messages) - 1) + [{"rag_trace": rag_trace}]
    storage.save(user_id, session_id, messages, extra_message_data=extra_message_data)
    _update_memory_session_summary(owner_id, session_id)
