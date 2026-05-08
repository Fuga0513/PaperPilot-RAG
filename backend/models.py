from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    papers = relationship("Paper", back_populates="owner", cascade="all, delete-orphan")
    research_projects = relationship("ResearchProject", back_populates="owner", cascade="all, delete-orphan")
    memory_items = relationship("MemoryItem", back_populates="owner", cascade="all, delete-orphan")
    evaluation_runs = relationship("EvaluationRun", back_populates="owner", cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (UniqueConstraint("user_id", "session_id", name="uq_user_session"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_ref_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    rag_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    session = relationship("ChatSession", back_populates="messages")


class ParentChunk(Base):
    __tablename__ = "parent_chunks"

    chunk_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_type: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parent_chunk_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    root_chunk_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    chunk_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_idx: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Paper(Base):
    """User-owned uploaded paper or research document.

    Stage 6 stores metadata and ownership in PostgreSQL. Later stages will connect
    these records to upload parsing, Milvus metadata, and citation filtering.
    """

    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    authors: Mapped[str] = mapped_column(Text, default="", nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    abstract: Mapped[str] = mapped_column(Text, default="", nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="uploaded", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="papers")
    chunks = relationship("PaperChunk", back_populates="paper", cascade="all, delete-orphan")
    extraction = relationship("PaperMetadata", back_populates="paper", cascade="all, delete-orphan", uselist=False)


class PaperChunk(Base):
    """User-owned chunk extracted from a Paper."""

    __tablename__ = "paper_chunks"
    __table_args__ = (UniqueConstraint("paper_id", "chunk_id", name="uq_paper_chunk_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    paper_title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    section_title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    subsection_title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_chunk_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    root_chunk_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(50), default="text", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    paper = relationship("Paper", back_populates="chunks")


class PaperMetadata(Base):
    """Structured extraction result for one user-owned Paper."""

    __tablename__ = "paper_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    problem: Mapped[str] = mapped_column(Text, default="", nullable=False)
    motivation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    contributions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    method_modules: Mapped[str] = mapped_column(Text, default="", nullable=False)
    datasets: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metrics: Mapped[str] = mapped_column(Text, default="", nullable=False)
    baselines: Mapped[str] = mapped_column(Text, default="", nullable=False)
    limitations: Mapped[str] = mapped_column(Text, default="", nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    paper = relationship("Paper", back_populates="extraction")


class ResearchProject(Base):
    """User-owned research project for grouping papers, tasks, and memories."""

    __tablename__ = "research_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="research_projects")
    memory_items = relationship("MemoryItem", back_populates="project", cascade="all, delete-orphan")
    project_memories = relationship("ProjectMemory", back_populates="project", cascade="all, delete-orphan")


class MemoryItem(Base):
    """User-owned memory item for preferences, tasks, project context, and session notes."""

    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_session_id: Mapped[str] = mapped_column(String(120), default="", nullable=False, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=True, index=True)
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="memory_items")
    project = relationship("ResearchProject", back_populates="memory_items")


class ProjectMemory(Base):
    """Project-scoped memory snapshot for project goals, status, and writing state."""

    __tablename__ = "project_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("ResearchProject", back_populates="project_memories")


class EvaluationRun(Base):
    """User-owned RAG retrieval evaluation run and report metadata."""

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    dataset_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    strategies: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    report_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    markdown_report_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="evaluation_runs")
    item_results = relationship("EvaluationItemResult", back_populates="run", cascade="all, delete-orphan")


class EvaluationItemResult(Base):
    """Per-question, per-strategy retrieval evaluation result."""

    __tablename__ = "evaluation_item_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    hit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recall: Mapped[str] = mapped_column(String(40), default="0", nullable=False)
    mrr: Mapped[str] = mapped_column(String(40), default="0", nullable=False)
    citation_hit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieved_chunks_json: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)

    run = relationship("EvaluationRun", back_populates="item_results")
