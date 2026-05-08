# Learning Notes

This document summarizes the main engineering concepts practiced while building PaperPilot-RAG.

## 1. FastAPI

Key takeaways:

- Use route modules to keep HTTP endpoints small.
- Use dependency injection for database sessions and current-user authentication.
- Use `StreamingResponse` for SSE chat streaming.
- Keep business workflows in services instead of routers.
- Expose API docs through `/docs` for local testing.

Project examples:

- `backend/routes/chat_routes.py`
- `backend/routes/document_routes.py`
- `backend/services/chat_service.py`

## 2. LangChain

Key takeaways:

- LangChain Agent can call local Python tools through Tool / StructuredTool.
- Tool schemas should be explicit with Pydantic models.
- Agent prompts must constrain tool usage and citation behavior.
- Streaming Agent output requires careful handling of tool-call chunks.

Project examples:

- `backend/agent.py`
- `backend/tools/registry.py`
- `backend/tools/schemas.py`

## 3. Milvus

Key takeaways:

- Milvus can store both dense and sparse vector fields.
- Metadata scalar fields are essential for user isolation.
- Existing collections may need rebuilding if schema fields change.
- Hybrid search can combine multiple vector search requests through RRF.

Project examples:

- `backend/milvus_client.py`
- `backend/milvus_writer.py`
- `backend/paper_indexer.py`

## 4. PostgreSQL

Key takeaways:

- SQLAlchemy ORM models define the core data boundary.
- Foreign keys with cascade delete simplify cleanup of user-owned records.
- User isolation should be enforced in queries, not only in frontend state.
- JSON columns are useful for trace metadata, session metadata, evaluation metrics, and memory metadata.

Project examples:

- `backend/models.py`
- `backend/database.py`
- `backend/parent_chunk_store.py`

## 5. Redis

Key takeaways:

- Redis can cache hot session lists and messages.
- Cache invalidation should happen after writes and deletes.
- Cache is an optimization, not the source of truth.

Project examples:

- `backend/cache.py`
- `backend/agent.py`
- `backend/parent_chunk_store.py`

## 6. Hybrid RAG

Key takeaways:

- Dense retrieval captures semantic similarity.
- BM25 sparse retrieval captures exact keyword matching.
- RRF fuses dense and sparse rankings without needing manual score normalization.
- A dense fallback path improves reliability if sparse or hybrid retrieval fails.

Project examples:

- `backend/embedding.py`
- `backend/rag_utils.py`
- `backend/milvus_client.py`

## 7. Rerank

Key takeaways:

- Retrieval should often fetch more candidates than the final top-k.
- Rerank is optional because it depends on an external model endpoint.
- Trace metadata should show whether rerank was enabled, applied, or failed.

Project examples:

- `rag_utils.rerank_documents`
- `RERANK_MODEL`
- `RERANK_BINDING_HOST`
- `RERANK_API_KEY`

## 8. Citation

Key takeaways:

- RAG answers are easier to trust when each claim cites retrieved evidence.
- Citation ids should be stable within a response.
- Evidence context should bound what the Agent is allowed to use.
- If evidence is insufficient, the system should say so instead of fabricating.

Project examples:

- `backend/citation_builder.py`
- `backend/tools/registry.py`

## 9. Agent Tool

Key takeaways:

- Tool boundaries should map to user workflows, not only low-level functions.
- Separate tools are useful for search, paper comparison, reviewer analysis, rebuttal drafting, and writing.
- Per-turn tool guards prevent repeated retrieval loops.
- Tool context carries user id, role, owner id, and retrieval scope.

Project examples:

- `search_research_documents`
- `compare_papers`
- `analyze_reviewer_comments`
- `draft_rebuttal`
- `research_writing`

## 10. RAG Evaluation

Key takeaways:

- Retrieval quality needs measurable metrics.
- JSONL datasets are easy to create and version locally.
- Strategy ablation helps compare dense-only, sparse-only, hybrid, rerank, and rewrite variants.
- Reports should be persisted for later comparison.

Project examples:

- `backend/evaluation/runner.py`
- `backend/evaluation/metrics.py`
- `backend/evaluation_api.py`

## Personal Engineering Summary

PaperPilot-RAG covers the full chain from user authentication to paper ingestion, retrieval, Agent tool orchestration, citation grounding, memory, and evaluation. The most important lesson is that a useful RAG system is not only a vector search call: it also needs data ownership, parsing quality, retrieval observability, citation discipline, failure handling, and evaluation.
