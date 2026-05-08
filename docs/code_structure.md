# PaperPilot-RAG Code Structure

This document describes the current code boundaries after the project was narrowed around the PaperPilot-RAG research workflow. It is a guide to where behavior belongs; it does not imply a backend rewrite.

## Backend Boundaries

- `backend/routes/` owns HTTP API routing. Route modules should parse request inputs, call services or workflow modules, and return API responses while keeping route paths stable.
- `backend/services/` owns business workflows that are shared by routes, such as authentication, document upload/indexing, chat/session helpers, and indexing boundaries.
- `backend/tools/` owns LangChain Tool definitions, tool schemas, per-turn tool context, and compatibility tools. Tool functions may call RAG or paper workflows, but they should not duplicate retrieval internals.
- `backend/rag_pipeline.py` and `backend/rag_utils.py` own the RAG main flow: retrieval graph, retrieval strategy dispatch, query rewrite, optional rerank, auto-merge, and trace data used by the frontend.
- `backend/paper_parser.py` owns scientific-paper parsing and section-aware chunk construction.
- `backend/citation_builder.py` owns citation id assignment and evidence-context formatting for citation-grounded QA.
- `backend/evaluation/` owns retrieval evaluation metrics, runners, and report generation.
- `backend/memory_manager.py` owns user, project, paper, and session memory. Memory can enrich prompts, but it is not citation evidence.
- `frontend/` owns the Vue CDN single-page frontend (`index.html`, `script.js`, and `style.css`).

## Compatibility Notes

Some internal names still carry historical SuperMew compatibility:

- Docker container names such as `supermew-postgres` and `supermew-redis` are deployment identifiers. Renaming them would affect local Docker volumes, docs, and scripts, so they are left unchanged for now.
- `REDIS_KEY_PREFIX` defaults to `supermew` in `backend/cache.py` to avoid invalidating existing cached keys unexpectedly.
- `pyproject.toml` and `uv.lock` still carry the original package name. Changing package identity is safer as a separate packaging cleanup.
- The legacy `search_knowledge_base` tool and weather tool are kept for route/tool compatibility. New research-facing behavior should prefer the PaperPilot research tools.

User-facing titles and descriptions should use PaperPilot-RAG unless they are explicitly documenting this compatibility history.
