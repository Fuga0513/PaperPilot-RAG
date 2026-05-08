# PaperPilot-RAG Architecture

This document explains the current architecture of PaperPilot-RAG as a user-scoped research RAG system.

## 1. Backend Module Structure

```text
backend/
  app.py                    FastAPI app factory, CORS, static frontend mount
  config.py                 centralized env/path configuration
  database.py               SQLAlchemy engine/session/base initialization
  models.py                 PostgreSQL ORM models
  schemas.py                API request/response schemas

  routes/
    auth_routes.py          register, login, current user
    chat_routes.py          chat and streaming chat
    session_routes.py       session list/detail/delete
    document_routes.py      admin global document upload/delete/jobs

  services/
    auth_service.py         auth business logic
    chat_service.py         chat/session service wrappers
    document_service.py     global document upload, indexing, deletion
    indexing_service.py     shared indexing boundary for documents and papers

  tools/
    context.py              per-turn tool context, RAG trace, SSE step queue
    schemas.py              LangChain StructuredTool schemas
    registry.py             research tools and retrieval wrappers
    weather_tools.py        legacy weather compatibility tool

  agent.py                  LangChain Agent, storage, streaming orchestration
  rag_pipeline.py           LangGraph retrieval graph
  rag_utils.py              retrieval strategies, rerank, auto-merge
  citation_builder.py       citation id and evidence-context construction

  paper_api.py              Paper Library API and paper workflows
  paper_parser.py           scientific paper parsing and chunking
  paper_indexer.py          user paper Milvus indexing
  paper_metadata_extractor.py
  paper_comparison.py
  paper_rebuttal.py
  paper_writing.py

  memory_manager.py         user/project/session memory logic
  memory_api.py             memory and project endpoints
  evaluation_api.py         evaluation endpoints
  evaluation/               metrics and evaluation runner
```

The old large `api.py` is now a compatibility router that includes the split route modules. Existing route paths remain stable.

## 2. Frontend Structure

```text
frontend/
  index.html
  script.js
  style.css
```

The frontend is a Vue 3 CDN single-page app. It currently avoids a build step and talks directly to the FastAPI backend. Major frontend responsibilities:

- Login/register and token storage.
- Paper Library upload/list/detail actions.
- Chat and streaming chat through SSE.
- Rendering Markdown answers.
- Showing citations and RAG Trace.
- Running paper comparison, reviewer, writing, memory, and evaluation views where implemented.

Future modularization can split `script.js` into `api.js`, `auth.js`, `paper.js`, `chat.js`, `memory.js`, `evaluation.js`, and `admin_documents.js`.

## 3. User Authentication Flow

1. User registers through `POST /auth/register`.
2. Password is hashed with PBKDF2-SHA256.
3. User logs in through `POST /auth/login`.
4. Backend returns a JWT access token.
5. Frontend sends `Authorization: Bearer <token>` on protected requests.
6. `auth.get_current_user` validates the token and loads the `User` row.
7. Admin-only routes use `require_admin`.

Roles:

- `user`: private Paper Library, chat, memory, evaluation, own sessions.
- `admin`: admin global document operations in addition to normal authenticated access.

## 4. Data Permission Isolation

PostgreSQL isolation:

- `papers.owner_id`
- `paper_chunks.owner_id`
- `paper_metadata.owner_id`
- `chat_sessions.user_id`
- `memory_items.owner_id`
- `evaluation_runs.owner_id`

Milvus isolation:

- Private paper vectors use `source_type == "paper"` and an `owner_id` scalar field.
- Retrieval for private papers requires `owner_id == current_user.id`.
- Admin/global documents use `source_type == "document"` and are read-only evidence unless admin routes modify them.

Agent context:

- `tools.context.set_tool_user_context` stores the current request user, role, owner id, and retrieval scope for one Agent turn.
- Research tools read this context before running private-paper retrieval.

## 5. Paper Library Data Flow

```text
Upload paper
  -> /papers/upload
  -> save file under data/uploads/{user_id}/papers/
  -> create Paper row
  -> ResearchPaperParser.parse_file()
  -> replace PaperChunk rows
  -> metadata extraction
  -> index_user_paper()
  -> Milvus leaf vectors + ParentChunkStore parent chunks
  -> Paper status indexed / failed / index_failed
```

Key behavior:

- Uploads are user-scoped.
- Same filename is not overwritten; stored names include a short UUID.
- Parsed chunks are written to PostgreSQL first.
- Leaf chunks are indexed into Milvus.
- Parent chunks support auto-merging.
- Deleting a paper removes database rows, uploaded file, and paper vectors when possible.

## 6. RAG Retrieval Flow

```text
question
  -> retrieve_initial
       -> retrieve_documents()
       -> hybrid search or dense fallback
       -> optional rerank
       -> auto-merge child chunks to parents
  -> grade_documents
       -> yes: use retrieved context
       -> no: rewrite_question
  -> retrieve_expanded
       -> Step-back and/or HyDE query
       -> retrieve again
       -> dedupe results
  -> tool returns evidence context
  -> Agent writes final answer with citation ids
```

`rag_utils.py` exposes an explicit retrieval strategy table:

```python
RETRIEVAL_STRATEGIES = {
    "hybrid": _retrieve_hybrid,
    "dense_fallback": _retrieve_dense,
}
```

Evaluation-only strategies also include:

- `dense_only`
- `bm25_only`
- `hybrid`
- `hybrid_rerank`
- `hybrid_rerank_rewrite`

## 7. Citation Generation Flow

Citation flow is handled by `citation_builder.py` and the tool registry.

1. Retrieval returns chunk dictionaries from Milvus and/or parent store.
2. `build_citations(docs, owner_id=...)` assigns stable ids such as `[C1]`.
3. The tool filters selected docs to citation-backed docs.
4. `build_evidence_context` formats evidence for the Agent.
5. Tool output instructs the Agent to cite claims only with provided ids.
6. `rag_trace` stores `citations`, `retrieved_chunks`, and `selected_context_chunks`.
7. Frontend displays citations and trace details.

This makes answer grounding explicit. If evidence is insufficient, the Agent should say so rather than inventing citations.

## 8. MemoryManager

`MemoryManager` stores user-scoped memory in PostgreSQL. It is prompt context, not citation evidence.

Memory scopes:

- `global`
- `project`
- `paper`
- `session`

Memory types:

- `preference`
- `fact`
- `task`
- `style`
- `reviewer_issue`

Runtime behavior:

1. Chat loads recent session history.
2. `MemoryManager.inject_relevant_memory_into_prompt` builds a bounded memory context.
3. The context is appended as a transient system message.
4. After chat is saved, `update_session_summary` refreshes short-term session metadata.

Memory does not replace RAG citations. Paper facts still require retrieved evidence chunks.

## 9. Evaluation Pipeline

Evaluation code lives under `backend/evaluation/`.

Flow:

```text
JSONL dataset
  -> run_evaluation()
  -> normalize strategies
  -> retrieve_documents_for_evaluation()
  -> evaluate_retrieval()
  -> write EvaluationRun + EvaluationItemResult rows
  -> write report.json and report.md under data/evaluation/
```

Dataset items require at least:

```json
{"question": "What method does the paper use?", "gold_keywords": ["keyword"]}
```

Optional fields include `gold_paper` and `gold_sections`.

Metrics currently include:

- Hit@k
- Recall@k
- MRR
- Citation hit rate

Reports are stored per user and can be rendered in the frontend.
