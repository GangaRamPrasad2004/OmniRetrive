import os
import datetime
from typing import Optional
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Integer,
    DateTime,
    Text,
    ForeignKey,
    desc,
    asc,
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
    workspace_id = Column(String(100), default="default_workspace", index=True)
    version = Column(Integer, default=1)
    status = Column(String(50), default="queued", index=True)  # queued, processing, completed, failed, retrying
    chunk_count = Column(Integer, default=0)
    page_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    jobs = relationship("DocumentJob", back_populates="document", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "storage_path": self.storage_path,
            "owner_id": self.owner_id,
            "workspace_id": self.workspace_id,
            "version": self.version,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "page_count": self.page_count,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
