# PaperPilot-RAG

PaperPilot-RAG is a research-oriented Agentic RAG application for user-scoped paper management, scientific-paper parsing, citation-grounded question answering, paper comparison, reviewer-response assistance, writing support, memory, and retrieval evaluation.

The project is designed as a portfolio-friendly full-stack AI system: FastAPI backend, Vue CDN frontend, PostgreSQL user data, Redis cache, Milvus hybrid vector retrieval, LangChain Agent tools, and an observable RAG pipeline.

> This repository does not include real API keys. Copy `.env.example` to `.env` and fill local secrets before running.

## Features

### Core Features

- Login / Auth: registration, JWT authentication, and role-aware access control.
- User-level Paper Library with owner-scoped paper records, chunks, metadata, and vector filters.
- Scientific paper parsing for uploaded PDF, DOCX, and TXT research papers.
- Section-aware chunking with `chunk_level`, `parent_chunk_id`, and `root_chunk_id`.
- Milvus Hybrid Search with dense embeddings plus BM25 sparse vectors.
- Rerank through an optional rerank-compatible HTTP endpoint.
- Query Rewrite with Step-back and HyDE expansion paths.
- Citation-grounded QA with stable citation ids such as `[C1]`.
- RAG Trace for retrieval mode, rewrite route, rerank status, selected chunks, and citations.

### Advanced Research Tools

- Multi-paper comparison with evidence-backed Markdown tables.
- Reviewer analysis into issue type, severity, response strategy, required action, and evidence need.
- Rebuttal drafting grounded in the current user's paper evidence.
- Research writing for abstract rewriting, contribution polishing, introduction logic checks, grant-question polishing, and experiment-setting summaries.
- Related Work generation from retrieved private-paper evidence.

### Experimental / Engineering Extensions

- Memory through PostgreSQL records plus session summaries.
- Evaluation with retrieval strategy ablations and Markdown/JSON reports.
- Graph RAG roadmap over paper entities, citations, methods, datasets, and claims.
- MCP roadmap for external research-system integrations.
- Multimodal roadmap for chart/table understanding.

## Tech Stack

- Backend: FastAPI, Pydantic, SQLAlchemy, Uvicorn.
- Agent and RAG: LangChain, LangGraph, LangChain tools / `StructuredTool`.
- Retrieval: Milvus, dense vector search, BM25 sparse vector search, RRF hybrid ranking, optional rerank.
- Embedding: local HuggingFace embedding model, default `BAAI/bge-m3`.
- Storage: PostgreSQL, Redis, local upload folders under `data/`.
- Frontend: Vue 3 CDN, plain JavaScript, CSS, marked, highlight.js.
- Devops: Docker Compose for PostgreSQL, Redis, Milvus, MinIO, etcd, Attu.

## System Architecture

```text
frontend/
  index.html + script.js + style.css
        |
        v
FastAPI app
  routes/              HTTP endpoints
  services/            business workflows
  agent.py             LangChain Agent + streaming chat
  tools/               Agent tools and tool context
  rag_pipeline.py      query -> retrieve -> grade -> rewrite -> retrieve
  rag_utils.py         retrieval strategies, rerank, auto-merge
        |
        +--> PostgreSQL: users, sessions, papers, chunks, memory, eval runs
        +--> Redis: session/cache acceleration
        +--> Milvus: dense + sparse vectors and metadata filters
        +--> local data/: uploads, BM25 state, evaluation reports
```

Key backend folders after the structural refactor:

- `backend/routes/`: auth, chat, session, and global document routes.
- `backend/services/`: auth, chat, document, and indexing service logic.
- `backend/tools/`: LangChain tool registry, schemas, user context, and weather compatibility tool.
- `backend/evaluation/`: retrieval evaluation runner and metrics.

More details are in [docs/architecture.md](docs/architecture.md) and [docs/code_structure.md](docs/code_structure.md).

## Database Design

Core PostgreSQL tables are defined in `backend/models.py`.

| Table | Purpose |
| --- | --- |
| `users` | Login identity, password hash, role. |
| `chat_sessions` | User-owned chat session metadata. |
| `chat_messages` | Persisted messages and optional `rag_trace`. |
| `parent_chunks` | L1/L2 parent chunks used for auto-merging. |
| `papers` | User-owned uploaded paper metadata and status. |
| `paper_chunks` | User-owned section-aware chunks parsed from papers. |
| `paper_metadata` | Structured extraction such as problem, motivation, contributions, datasets, metrics, and limitations. |
| `research_projects` | User-owned research project grouping, currently basic. |
| `memory_items` | Owner-scoped memory records. |
| `project_memories` | Project-scoped memory snapshots. |
| `evaluation_runs` | User-owned evaluation run metadata and report paths. |
| `evaluation_item_results` | Per-question retrieval metrics and retrieved chunks. |

Important isolation fields:

- `papers.owner_id`
- `paper_chunks.owner_id`
- `paper_metadata.owner_id`
- `memory_items.owner_id`
- `evaluation_runs.owner_id`

The project currently uses `create_all` plus lightweight additive migrations. Alembic is recommended as a future improvement.

## Milvus Schema

Milvus stores searchable leaf chunks with dense and sparse vector fields plus metadata filters.

Vector fields:

- `dense_embedding`: `FLOAT_VECTOR`, default dimension controlled by `DENSE_EMBEDDING_DIM`.
- `sparse_embedding`: `SPARSE_FLOAT_VECTOR`, generated from BM25 sparse vectors.

Important scalar fields:

- `text`, `filename`, `file_type`, `file_path`
- `page_number`, `chunk_idx`
- `source_type`: `document` for admin global docs, `paper` for user papers
- `owner_id`, `paper_id`
- `paper_title`, `section_title`, `subsection_title`
- `page_start`, `page_end`, `chunk_type`, `year`, `venue`
- `chunk_id`, `parent_chunk_id`, `root_chunk_id`, `chunk_level`

Indexes:

- Dense vector: HNSW + IP.
- Sparse vector: `SPARSE_INVERTED_INDEX` + IP.
- Hybrid retrieval: Milvus `hybrid_search` with `RRFRanker`.

## RAG Pipeline

The main retrieval graph lives in `backend/rag_pipeline.py`.

```text
user question
  -> retrieve_initial
  -> grade_documents
     -> if relevant: return selected docs
     -> if insufficient: rewrite_question
  -> retrieve_expanded
  -> Agent final answer with citations
```

Retrieval details:

- `rag_utils.retrieve_documents()` uses an explicit `RETRIEVAL_STRATEGIES` dispatch table.
- Default path is hybrid retrieval; dense fallback is used if hybrid retrieval fails.
- Rerank is optional and controlled by `.env`.
- Auto-merging can replace multiple matching child chunks with parent chunks.
- RAG Trace records retrieval mode, candidate size, rewrite strategy, rerank state, selected chunks, citations, and fallback reasons.

## Agent Tools

Tools are registered in `backend/tools/registry.py` and exported through `backend/tools/__init__.py`.

Current research tools:

- `search_research_documents`
- `summarize_paper`
- `compare_papers`
- `analyze_reviewer_comments`
- `draft_rebuttal`
- `research_writing`
- `generate_related_work`

Compatibility tools:

- `search_knowledge_base`
- `get_current_weather`

The implementation is LangChain tool/function-calling style. MCP is not currently used; it is listed in the roadmap for external integrations.

## Authentication and Permissions

- Users register and log in through `/auth/register` and `/auth/login`.
- Passwords are stored as PBKDF2-SHA256 hashes.
- JWT Bearer tokens identify the current user.
- `admin` routes protect global document management.
- Regular users can manage their own sessions and Paper Library records.
- Private paper retrieval requires `owner_id` filtering in PostgreSQL and Milvus.
- Memory and evaluation data are owner-scoped.

## Local Setup

### 1. Install dependencies

```bash
uv sync
```

Alternative:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -e .
```

### 2. Start infrastructure

```bash
docker compose up -d
docker compose ps
```

Typical ports:

- PostgreSQL: `5432`
- Redis: `6379`
- Milvus: `19530`
- Attu: `8080`
- MinIO: `9000` / `9001`

### 3. Configure `.env`

```bash
copy .env.example .env
```

Fill local values only. Do not commit `.env`.

### 4. Run backend and frontend

```bash
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

Open:

- App: <http://127.0.0.1:8000/>
- API docs: <http://127.0.0.1:8000/docs>

## `.env` Configuration

Common variables:

```env
ARK_API_KEY=your_api_key_here
BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL=qwen-plus
GRADE_MODEL=qwen-plus

EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu
DENSE_EMBEDDING_DIM=1024

RERANK_MODEL=
RERANK_BINDING_HOST=
RERANK_API_KEY=

MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_COLLECTION=embeddings_collection

DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/langchain_app
REDIS_URL=redis://127.0.0.1:6379/0

JWT_SECRET_KEY=replace-with-a-long-random-secret
ADMIN_INVITE_CODE=paperpilot-admin-local

BM25_STATE_PATH=data/bm25_state.json
AUTO_MERGE_ENABLED=true
AUTO_MERGE_THRESHOLD=2
LEAF_RETRIEVE_LEVEL=3
```

Never put real keys in README, issues, screenshots, or commits.

## Test and Verification Flow

Basic checks:

```bash
uv run python -m py_compile backend\config.py backend\app.py backend\agent.py backend\paper_api.py backend\rag_utils.py
uv run python -c "import sys; sys.path.insert(0, 'backend'); from app import app; print('ok')"
```

Manual product test:

1. Start Docker services.
2. Start FastAPI.
3. Register a user.
4. Upload one or more papers.
5. Ask a paper-specific question.
6. Check citations and RAG Trace.
7. Compare two papers.
8. Run reviewer analysis or related-work generation.
9. Run a retrieval evaluation dataset if available.

## FAQ

### Why does GitHub show an old version?

Check which branch GitHub is displaying. The latest work may be on `stage/20` while GitHub defaults to `main`.

### Why does paper indexing fail after pulling new schema code?

Existing Milvus collections may miss newer metadata fields such as `owner_id`, `paper_id`, or `chunk_level`. Rebuild the collection or migrate it before indexing private papers.

### Why are answers saying evidence is insufficient?

The Agent is instructed to answer only from retrieved evidence. Upload and index relevant papers, choose the right retrieval scope, and inspect RAG Trace for retrieved chunks.

### Does this project use MCP?

No. Current tool calling is LangChain Tool / StructuredTool around local Python functions. MCP is a future direction for external tools.

### Is `.env` safe to commit?

No. Commit `.env.example`, not `.env`.

## Roadmap

Completed:

- Auth and user isolation.
- Paper Library and paper upload.
- Scientific paper parsing and chunking.
- Hybrid retrieval, rerank integration, query rewrite, citation grounding, and RAG Trace.
- Multi-paper comparison, reviewer analysis, rebuttal drafting, and writing assistance.
- Memory records and retrieval evaluation.
- Backend structure split into routes, services, tools, and config.

Planned:

- Alembic migrations.
- Background task queue with Celery or RQ.
- Graph RAG over paper entities, citations, methods, datasets, and claims.
- Multimodal chart/table understanding.
- MCP integrations for external systems such as Zotero, arXiv, CrossRef, Semantic Scholar, GitHub, Google Drive, and OneDrive.
- Team collaboration spaces.
- More granular RBAC and sharing permissions.
- Frontend modularization and richer evaluation dashboards.

See [docs/roadmap.md](docs/roadmap.md) for a more detailed plan.
