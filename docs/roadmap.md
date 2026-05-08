# Roadmap

This roadmap separates completed work from planned work. Planned items are not claimed as current functionality.

## 1. Completed Features

Authentication and users:

- Registration and login.
- JWT authentication.
- PBKDF2 password hashing.
- Basic `user` and `admin` roles.
- User-scoped sessions.

Paper Library:

- User-owned paper upload.
- Paper records in PostgreSQL.
- Parsed chunks in PostgreSQL.
- Structured paper metadata extraction.
- Paper deletion with vector cleanup attempt.

RAG:

- Scientific PDF/DOCX/TXT parsing.
- Section-aware chunking.
- L1/L2/L3 chunk hierarchy.
- Parent chunk store.
- Dense embedding retrieval.
- BM25 sparse retrieval.
- Milvus hybrid search with RRF.
- Optional rerank.
- Query rewrite with Step-back and HyDE paths.
- Auto-merging from child chunks to parent chunks.
- Citation-grounded evidence context.
- RAG Trace.

Agent tools:

- Research document search.
- Paper summarization skeleton.
- Multi-paper comparison.
- Reviewer comment analysis.
- Rebuttal drafting.
- Research writing.
- Related work generation.

Memory and evaluation:

- User-scoped memory items.
- Session summary injection.
- Project memory data model.
- Retrieval evaluation strategies.
- JSON and Markdown evaluation reports.

Engineering structure:

- Backend route split.
- Service layer extraction.
- Tool package split.
- Shared indexing service boundary.
- Central `config.py`.
- Explicit retrieval strategy table.

## 2. Unfinished or Partially Complete Features

These areas exist as foundations but still need more work:

- Alembic migrations. Current database evolution uses `create_all` plus lightweight additive migrations.
- Paper summarization is still a retrieval-backed tool skeleton rather than a specialized summarization pipeline.
- Research project workspace exists at the model/memory level, but full team/project UI is not complete.
- Frontend is still mostly a Vue CDN single-page script and can be modularized.
- Evaluation dataset management is basic; richer labeling and dashboard views are future work.
- Background upload/index jobs are in-process, not distributed queue workers.
- Global document and private paper indexing share a service boundary, but deeper pipeline consolidation can continue.
- Fine-grained sharing and collaboration permissions are not implemented.

## 3. Future Extensions

### Graph RAG

Planned direction:

- Extract entities such as paper, method, dataset, metric, task, claim, limitation, and citation.
- Build relationships between papers and claims.
- Use graph traversal plus vector retrieval for multi-hop questions.

Expected benefit:

- Better support for literature maps, method lineage, and cross-paper reasoning.

### Multimodal Chart and Table Understanding

Planned direction:

- Parse tables, figures, chart captions, and image regions.
- Add OCR or vision-language model extraction.
- Store figure/table evidence with citation ids.

Expected benefit:

- Better answers for experiment results, ablation tables, and architecture diagrams.

### MCP Tool Calling

Planned direction:

- Keep core RAG internal.
- Add MCP servers for external integrations such as Zotero, arXiv, CrossRef, Semantic Scholar, GitHub, Google Drive, OneDrive, and local file import.

Expected benefit:

- Cleaner tool boundaries and reuse across IDEs, desktop clients, and backend services.

### Team Collaboration Space

Planned direction:

- Team/project membership.
- Shared paper collections.
- Shared notes and evaluation runs.
- Commenting or task assignment.

Expected benefit:

- Support lab or group workflows instead of only single-user research.

### More Granular RBAC

Planned direction:

- Roles beyond `user` and `admin`.
- Resource-level permissions.
- Owner/editor/viewer access.
- Audit trail for sensitive operations.

Expected benefit:

- Safer deployment in team environments.

### Background Task Queue: Celery / RQ

Planned direction:

- Move long-running parsing, embedding, indexing, and evaluation jobs out of FastAPI process memory.
- Persist task state in Redis or PostgreSQL.
- Add retry and failure recovery.

Expected benefit:

- More reliable processing for large PDFs and batch imports.

## 4. Suggested Priority

1. Add Alembic migrations.
2. Introduce Celery or RQ for upload/index/evaluation jobs.
3. Modularize frontend JavaScript.
4. Improve evaluation dataset management and dashboards.
5. Add external literature search/import tools.
6. Add team workspace and granular RBAC.
7. Explore Graph RAG and multimodal extraction.
