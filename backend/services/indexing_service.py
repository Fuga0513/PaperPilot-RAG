"""Shared indexing entry points for global documents and user papers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from document_loader import DocumentLoader
from embedding import embedding_service
from milvus_client import MilvusManager
from milvus_writer import MilvusWriter
from models import Paper
from paper_indexer import index_paper_chunks
from parent_chunk_store import ParentChunkStore

ProgressCallback = Callable[[int, int], None]


@dataclass
class IndexingResult:
    parent_chunks: int
    leaf_chunks: int


class IndexingService:
    """Coordinate parse/chunk/index storage without tying it to HTTP routers."""

    def __init__(
        self,
        *,
        loader: DocumentLoader | None = None,
        milvus_manager: MilvusManager | None = None,
        parent_chunk_store: ParentChunkStore | None = None,
        writer: MilvusWriter | None = None,
    ):
        self.loader = loader or DocumentLoader()
        self.milvus_manager = milvus_manager or MilvusManager()
        self.parent_chunk_store = parent_chunk_store or ParentChunkStore()
        self.writer = writer or MilvusWriter(embedding_service=embedding_service, milvus_manager=self.milvus_manager)

    def index_global_document(
        self,
        *,
        file_path: str,
        filename: str,
        progress_callback: ProgressCallback | None = None,
    ) -> IndexingResult:
        docs = self.loader.load_document(file_path, filename)
        if not docs:
            raise ValueError("Document processing failed: no content extracted")

        parent_docs = [doc for doc in docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise ValueError("Document processing failed: no searchable leaf chunks were generated")

        self.parent_chunk_store.upsert_documents(parent_docs)
        self.writer.write_documents(leaf_docs, progress_callback=progress_callback)
        return IndexingResult(parent_chunks=len(parent_docs), leaf_chunks=len(leaf_docs))

    def index_user_paper(self, db: Session, paper: Paper) -> IndexingResult:
        leaf_count = index_paper_chunks(
            db,
            paper,
            milvus_manager=self.milvus_manager,
            parent_chunk_store=self.parent_chunk_store,
        )
        parent_count = len([chunk for chunk in paper.chunks if int(chunk.chunk_level or 0) in (1, 2)])
        return IndexingResult(parent_chunks=parent_count, leaf_chunks=leaf_count)
