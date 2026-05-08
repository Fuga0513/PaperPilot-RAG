# PaperPilot-RAG Demo Script

This script is for a GitHub, resume, or interview walkthrough. It assumes Docker services, `.env`, and the FastAPI app are already running.

## 0. Preparation

Start services:

```bash
docker compose up -d
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

Open:

- App: <http://127.0.0.1:8000/>
- API docs: <http://127.0.0.1:8000/docs>

Prepare:

- 2-3 research papers in PDF format.
- 1 reviewer-comment example.
- Optional JSONL evaluation dataset.

## 1. Register / Login

Demo steps:

1. Open the app.
2. Register a normal user.
3. Log in with that user.
4. Mention that JWT is used for protected requests.

Talking point:

- "Every paper, session, memory item, and evaluation run is scoped to the logged-in user."

## 2. Upload Paper

Demo steps:

1. Go to Paper Library.
2. Upload a scientific PDF.
3. Wait for parse/index status to complete.
4. Upload a second paper for comparison later.

Talking point:

- "Upload triggers file persistence, paper row creation, section-aware parsing, chunk storage, metadata extraction, and Milvus indexing."

## 3. View Paper Library

Demo steps:

1. Show the paper list.
2. Open paper details.
3. Show extracted metadata if available.
4. Show parsed chunks or chunk count.

Talking point:

- "The Paper Library is user-owned. Admin global documents are separate from private user papers."

## 4. Single-Paper QA

Example prompt:

```text
Summarize the method, dataset, metrics, and key limitation of this paper.
```

Or:

```text
What problem does this paper solve, and what evidence supports the claimed contribution?
```

Talking point:

- "The Agent calls `search_research_documents`, retrieves owner-filtered chunks, and answers only from retrieved evidence."

## 5. View Citations

Demo steps:

1. Ask a paper question.
2. Point to citation ids like `[C1]`, `[C2]`.
3. Open citation details in the UI if available.

Talking point:

- "Citations are generated from retrieved chunks. The answer is not allowed to invent papers, datasets, numbers, or citation ids."

## 6. View RAG Trace

Demo steps:

1. Expand the RAG Trace panel.
2. Show retrieval mode, candidate count, rerank state, rewrite strategy, retrieved chunks, and selected chunks.

Talking point:

- "RAG Trace makes the retrieval process debuggable. It shows whether the answer came from initial retrieval or rewritten retrieval."

## 7. Multi-Paper Comparison

Example prompt:

```text
Compare these papers by problem, method, dataset, metric, contribution, and limitation.
```

Demo steps:

1. Select or reference two uploaded papers.
2. Run comparison.
3. Show Markdown table and citations.

Talking point:

- "`compare_papers` is a dedicated tool. It produces a structured comparison grounded in current-user paper chunks."

## 8. Reviewer Comment Analysis

Example reviewer comments:

```text
Reviewer 1: The novelty over prior work is unclear.
Reviewer 2: The experiments lack ablation studies and the robustness claim is not sufficiently supported.
Reviewer 3: The paper should clarify assumptions behind the proposed model.
```

Demo steps:

1. Paste comments into reviewer analysis.
2. Show issue type, severity, strategy, required action, and evidence need.

Talking point:

- "Reviewer analysis converts unstructured review text into an actionable response plan."

## 9. Related Work Generation

Example prompt:

```text
Generate a related work draft about sparse sensing and sequence modeling based on my uploaded papers.
```

Demo steps:

1. Run the related-work or research-writing tool.
2. Show evidence-based facts, suggested writing, warnings, and revision notes.

Talking point:

- "The writing tool separates grounded paper facts from suggested prose and warnings."

## 10. Run Evaluation Report

Prepare a JSONL dataset, for example:

```json
{"question":"Which dataset is used for the main experiment?","gold_keywords":["dataset name"]}
{"question":"What limitation does the paper mention?","gold_keywords":["limitation","future work"]}
```

Demo steps:

1. Open evaluation view or use API docs.
2. Run evaluation for the logged-in user.
3. Compare strategies such as dense-only, BM25-only, hybrid, hybrid-rerank, and hybrid-rerank-rewrite.
4. Open the generated Markdown report.

Talking point:

- "The evaluation module turns RAG quality into measurable retrieval metrics rather than relying only on manual inspection."

## Suggested 5-Minute Demo Flow

1. Login.
2. Show Paper Library with uploaded papers.
3. Ask one citation-grounded question.
4. Open citations and RAG Trace.
5. Run multi-paper comparison.
6. Paste reviewer comments and generate analysis.
7. Show evaluation report.

## Suggested 10-Minute Demo Flow

1. Start with architecture overview.
2. Register/login.
3. Upload a paper and explain parse/index pipeline.
4. Ask single-paper QA.
5. Show citations and RAG Trace.
6. Compare two papers.
7. Analyze reviewer comments.
8. Generate related work.
9. Show evaluation metrics.
10. Close with roadmap: Graph RAG, multimodal chart understanding, MCP integrations, team workspace, RBAC, Celery/RQ.
