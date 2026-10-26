import os
import json
import datetime
from typing import Optional
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Text,
    ForeignKey,
    desc,
    asc,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

Base = declarative_base()

class Document(Base):
    __tablename__ = "documents"

    id = Column(String(64), primary_key=True)
    filename = Column(String(255), nullable=False, index=True)
    file_hash = Column(String(64), nullable=False, index=True)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String(100), default="application/pdf")
    storage_path = Column(String(500), nullable=False)
    owner_id = Column(String(100), default="default_user", index=True)
    uploaded_by = Column(String(100), nullable=True, index=True)
    workspace_id = Column(String(100), default="default_workspace", index=True)
    version = Column(Integer, default=1)
    status = Column(String(50), default="queued", index=True)  # queued, processing, completed, failed, retrying
    chunk_count = Column(Integer, default=0)
    page_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))
    last_processed_at = Column(DateTime, nullable=True)

    jobs = relationship("DocumentJob", back_populates="document", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "owner_id": self.owner_id,
            "uploaded_by": self.uploaded_by or self.owner_id,
            "workspace_id": self.workspace_id,
            "version": self.version,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "page_count": self.page_count,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_processed_at": self.last_processed_at.isoformat() if self.last_processed_at else None,
            "is_ready_for_qa": (self.status == "completed" and self.chunk_count > 0),
        }


class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id = Column(String(64), primary_key=True)
    document_id = Column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    job_type = Column(String(50), default="ingest")  # ingest, retry
    status = Column(String(50), default="queued")  # queued, processing, completed, failed
    event_id = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    document = relationship("Document", back_populates="jobs")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "job_type": self.job_type,
            "status": self.status,
            "event_id": self.event_id,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id = Column(String(64), primary_key=True)
    run_name = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    dataset_version = Column(String(50), default="v1.0")
    config_json = Column(Text, default="{}")

    # Aggregate Deterministic Metrics
    total_queries = Column(Integer, default=0)
    passed_queries = Column(Integer, default=0)
    mrr = Column(Float, default=0.0)
    ndcg = Column(Float, default=0.0)
    recall_at_1 = Column(Float, default=0.0)
    recall_at_3 = Column(Float, default=0.0)
    recall_at_5 = Column(Float, default=0.0)
    citation_precision = Column(Float, default=0.0)
    citation_recall = Column(Float, default=0.0)
    refusal_accuracy = Column(Float, default=0.0)

    # Model-based / Heuristic Metrics (Clearly distinguished)
    faithfulness = Column(Float, default=0.0)
    answer_relevance = Column(Float, default=0.0)

    # Performance, Latency & Cost
    avg_latency_ms = Column(Float, default=0.0)
    total_prompt_tokens = Column(Integer, default=0)
    total_completion_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)

    items = relationship("EvalItemResult", back_populates="run", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        parsed_config = {}
        try:
            parsed_config = json.loads(self.config_json)
        except Exception:
            pass

        return {
            "id": self.id,
            "run_name": self.run_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "dataset_version": self.dataset_version,
            "config": parsed_config,
            "total_queries": self.total_queries,
            "passed_queries": self.passed_queries,
            "metrics": {
                "deterministic": {
                    "mrr": self.mrr,
                    "ndcg": self.ndcg,
                    "recall_at_1": self.recall_at_1,
                    "recall_at_3": self.recall_at_3,
                    "recall_at_5": self.recall_at_5,
                    "citation_precision": self.citation_precision,
                    "citation_recall": self.citation_recall,
                    "refusal_accuracy": self.refusal_accuracy,
                },
                "model_based": {
                    "faithfulness": self.faithfulness,
                    "answer_relevance": self.answer_relevance,
                },
            },
            "performance": {
                "avg_latency_ms": self.avg_latency_ms,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "estimated_cost_usd": self.estimated_cost_usd,
            },
        }


class EvalItemResult(Base):
    __tablename__ = "eval_item_results"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    query_id = Column(String(64), nullable=False)
    query = Column(Text, nullable=False)
    category = Column(String(100), default="general")
    is_answerable = Column(Integer, default=1)

    is_correct_retrieval = Column(Integer, default=0)
    is_correct_citation = Column(Integer, default=0)
    is_correct_refusal = Column(Integer, default=0)
    reciprocal_rank = Column(Float, default=0.0)

    retrieved_sources_json = Column(Text, default="[]")
    citations_json = Column(Text, default="[]")
    generated_answer = Column(Text, default="")

    latency_ms = Column(Float, default=0.0)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)

    run = relationship("EvalRun", back_populates="items")

    def to_dict(self) -> dict:
        sources = []
        cits = []
        try:
            sources = json.loads(self.retrieved_sources_json)
            cits = json.loads(self.citations_json)
        except Exception:
            pass

        return {
            "id": self.id,
            "run_id": self.run_id,
            "query_id": self.query_id,
            "query": self.query,
            "category": self.category,
            "is_answerable": bool(self.is_answerable),
            "is_correct_retrieval": bool(self.is_correct_retrieval),
            "is_correct_citation": bool(self.is_correct_citation),
            "is_correct_refusal": bool(self.is_correct_refusal),
            "reciprocal_rank": self.reciprocal_rank,
            "retrieved_sources": sources,
            "citations": cits,
            "generated_answer": self.generated_answer,
            "latency_ms": self.latency_ms,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
            },
        }


def get_db_url() -> str:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        # Standardize postgres:// to postgresql:// for SQLAlchemy compatibility
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return db_url
    # Default to sqlite file
    db_path = os.getenv("SQLITE_PATH", "omniretrive.db")
    return f"sqlite:///{db_path}"


engine = create_engine(
    get_db_url(),
    connect_args={"check_same_thread": False} if get_db_url().startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    document_columns = {column["name"] for column in inspector.get_columns("documents")}
    additions = []
    if "uploaded_by" not in document_columns:
        additions.append("ADD COLUMN uploaded_by VARCHAR(100)")
    if "last_processed_at" not in document_columns:
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
        additions.append(f"ADD COLUMN last_processed_at {timestamp_type}")
    if additions:
        with engine.begin() as connection:
            for addition in additions:
                connection.execute(text(f"ALTER TABLE documents {addition}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
