import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/langchain_app",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    # Delayed import to avoid circular dependency.
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """Apply small additive schema fixes for create_all-based local development.

    SQLAlchemy create_all creates missing tables but does not alter existing
    tables. Until this project adopts Alembic, keep only safe, additive changes
    here so older local databases can start after model fields are added.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE paper_chunks "
            "ADD COLUMN IF NOT EXISTS paper_title VARCHAR(500) NOT NULL DEFAULT ''"
        ))
        conn.execute(text(
            "ALTER TABLE evaluation_runs "
            "ADD COLUMN IF NOT EXISTS markdown_report_path VARCHAR(1024) NOT NULL DEFAULT ''"
        ))
