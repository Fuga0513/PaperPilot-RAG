# PaperPilot-RAG LangChain Usage

This document summarizes how the current SuperMew-derived codebase uses
LangChain/LangGraph and where PaperPilot-RAG should extend it.

## 1. Current LangChain Modules

### `backend/agent.py`

- `langchain.chat_models.init_chat_model`
  - Creates the OpenAI-compatible chat model.
  - Reads `ARK_API_KEY`, `MODEL`, and `BASE_URL` from `.env`.
  - The current target model can be `qwen-plus` through compatible API settings.
- `langchain.agents.create_agent`
  - Builds the main Agent.
  - Receives a model, tool list, and system prompt.
  - The Agent decides when to call tools based on the prompt, tool names,
    descriptions, and input schemas.
- `langchain_core.messages`
  - Converts stored chat rows into `HumanMessage`, `AIMessage`, and
    `SystemMessage`.
  - `AIMessageChunk` is used in streaming mode to identify token chunks.
- `agent.invoke(...)`
  - Used by `POST /chat` for non-streaming replies.
- `agent.astream(..., stream_mode="messages")`
  - Used by `POST /chat/stream` for SSE token streaming.

### `backend/tools.py`

- `langchain_core.tools.tool`
  - Keeps the legacy `search_knowledge_base` tool name compatible with the
    original SuperMew flow.
- `langchain_core.tools.StructuredTool`
  - Registers PaperPilot research tools with explicit Pydantic argument schemas.
- `pydantic.BaseModel`
  - Defines tool input schemas such as `ResearchSearchInput`,
    `ComparePapersInput`, and `DraftRebuttalInput`.

### `backend/rag_pipeline.py`

- `langchain.chat_models.init_chat_model`
  - Creates lightweight LLM helpers for relevance grading and rewrite routing.
- `model.with_structured_output(...)`
  - Parses grader/router output into Pydantic models:
    `GradeDocuments` and `RewriteStrategy`.
- `langgraph.graph.StateGraph`
  - Implements the custom RAG workflow:
    initial retrieval -> grade -> optional rewrite -> expanded retrieval.
  - LangGraph orchestrates nodes, but the retrieval implementation remains
    custom.

### `backend/rag_utils.py`

- `langchain.chat_models.init_chat_model`
  - Used for step-back question generation, step-back answer generation, and
    HyDE hypothetical document generation.
- Retrieval itself is custom:
  - dense embedding from `EmbeddingService`;
  - sparse BM25 vector from `EmbeddingService`;
  - Milvus hybrid search;
  - RRF fusion in Milvus;
  - optional Jina rerank through HTTP;
  - auto-merging via parent chunks.

### `backend/embedding.py`

- `langchain_huggingface.HuggingFaceEmbeddings`
  - Loads the local dense embedding model.
  - Default model is `BAAI/bge-m3`.
- BM25 sparse embedding is custom code in `EmbeddingService`.

### `backend/document_loader.py`

- `langchain_community.document_loaders`
  - `PyPDFLoader`, `Docx2txtLoader`, and `UnstructuredExcelLoader` parse files.
- `langchain_text_splitters.RecursiveCharacterTextSplitter`
  - Creates three chunk levels used by auto-merging.

## 2. How the Agent Selects Tools

The Agent is created in `create_agent_instance()` in `backend/agent.py`.

Current tool list:

- `get_current_weather`
- `search_knowledge_base`
- `search_research_documents`
- `summarize_paper`
- `compare_papers`
- `analyze_reviewer_comments`
- `draft_rebuttal`
- `generate_related_work`

The Agent uses the system prompt plus each tool's name, description, and schema
to decide whether to call a tool. The prompt now tells it:

- use `search_research_documents` for papers, project docs, reviewer comments,
  and research evidence;
- keep `search_knowledge_base` for legacy knowledge-base questions;
- use workflow tools only for their named tasks;
- call at most one retrieval-style tool per turn;
- never invent citations or evidence.

`backend/tools.py` also enforces a simple per-turn guard with
`_KNOWLEDGE_TOOL_CALLS_THIS_TURN`.

## 3. How Knowledge Search Calls the RAG Pipeline

`search_knowledge_base` and `search_research_documents` both call:

```text
_run_research_rag_search(...)
  -> rag_pipeline.run_rag_graph(query)
      -> retrieve_initial(...)
          -> rag_utils.retrieve_documents(...)
              -> EmbeddingService dense + sparse vectors
              -> MilvusManager.hybrid_retrieve(...)
              -> Jina rerank if configured
              -> auto-merging through ParentChunkStore
      -> grade_documents_node(...)
      -> rewrite_question_node(...) if needed
      -> retrieve_expanded(...) if needed
```

The tool returns formatted retrieved chunks to the Agent. `rag_trace` is saved in
`_LAST_RAG_CONTEXT`, then `agent.py` attaches it to the assistant message and
streams it to the frontend.

## 4. Where LangChain Should Continue To Be Used

LangChain is a good fit for:

- OpenAI-compatible LLM calls through `init_chat_model`;
- Agent tool selection and tool invocation;
- `StructuredTool` input schemas;
- prompt-driven helper tasks such as query rewrite, HyDE, grading, and routing;
- structured output parsing with Pydantic models;
- message objects and streaming chunks.

## 5. Where Custom Logic Must Stay

Do not replace these with a LangChain black-box retriever:

- Milvus collection schema and index setup;
- dense + sparse hybrid retrieval;
- BM25 sparse vector creation and persisted statistics;
- RRF fusion;
- Jina rerank configuration and HTTP call;
- auto-merging from leaf chunks to parent chunks;
- RAG trace fields consumed by the frontend;
- SSE progress step events emitted by `emit_rag_step`;
- authentication and user/session isolation.

The PaperPilot research tools should act as LangChain-facing boundaries. They
may decide which workflow to run, but the actual retrieval and citation data
must continue to come from the custom RAG pipeline.

## 6. User Context and Permission Boundary

`backend/agent.py` calls `set_tool_user_context(user_id=...)` before each
non-streaming and streaming Agent run. `backend/tools.py` stores this context so
future retrieval code can apply user filters.

Stage 5 does not yet add `Paper`, `PaperChunk`, or `PaperMetadata` models, so
the current Milvus documents remain global/admin-oriented. The required future
flow is:

```text
FastAPI auth -> current_user
  -> agent.chat_with_agent_stream(user_id=current_user.username)
  -> tools.set_tool_user_context(user_id)
  -> run_rag_graph(query, user_id)
  -> retrieve_documents(..., filter_expr includes owner/user scope)
  -> Milvus + PostgreSQL parent chunk lookup scoped by user
```

Until that schema exists, private paper libraries must not rely on the legacy
global document collection for isolation.

## 7. Stage 5 Research Tools

### `search_research_documents`

- Input: `{"query": "..."}`
- Output: retrieved chunks with filename, page, chunk id, and text.
- Current implementation: calls existing `run_rag_graph`.
- Citation rule: final answers must cite only returned chunks.

### `summarize_paper`

- Input: `{"paper_id": "...", "question": "optional focus"}`
- Output: stage-5 skeleton plus retrieval-backed chunks when available.
- Future implementation: filter retrieval by `current_user` and `paper_id`.

### `compare_papers`

- Input: `{"paper_ids": ["..."], "comparison_focus": "optional"}`
- Output: stage-5 not-implemented skeleton.
- Future implementation: compare multiple user-owned papers with citations.

### `analyze_reviewer_comments`

- Input: `{"comments": "...", "paper_id": "optional"}`
- Output: stage-5 not-implemented skeleton.
- Future implementation: classify concerns and connect each issue to paper
  evidence.

### `draft_rebuttal`

- Input: `{"comments": "...", "evidence_query": "optional"}`
- Output: stage-5 not-implemented skeleton.
- Future implementation: generate polite rebuttal paragraphs grounded in
  retrieved evidence only.

### `generate_related_work`

- Input: `{"topic": "...", "constraints": "optional"}`
- Output: stage-5 not-implemented skeleton.
- Future implementation: produce related-work outlines/drafts with citations
  from retrieved paper chunks.
