import io
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pypdf import PdfWriter

from database import Base, get_db, Document, DocumentJob
from data_loader import embed_texts, load_and_chunk_pdf, is_transient_error
from vector_db import QdrantStorage
from main import app, recover_interrupted_jobs


def create_blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    s = io.BytesIO()
    writer.write(s)
    return s.getvalue()


@pytest.fixture(scope="function")
def scalable_test_env():
    temp_dir = tempfile.mkdtemp()
    test_db_path = os.path.join(temp_dir, "scalable_test.db")
    test_engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with patch("main.SessionLocal", TestingSessionLocal):
        client = TestClient(app)
        yield client, TestingSessionLocal, temp_dir

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. BATCHED EMBEDDINGS & RETRY BACKOFF TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestBatchedEmbeddingsAndBackoff:
    def test_transient_error_detection(self):
        assert is_transient_error(RuntimeError("HTTP 429 Too Many Requests")) is True
        assert is_transient_error(ConnectionError("Connection reset by peer")) is True
        assert is_transient_error(RuntimeError("503 Service Unavailable")) is True
        assert is_transient_error(ValueError("Invalid token syntax")) is False

    @patch("data_loader.hf_client")
    def test_embed_texts_batching_and_retry(self, mock_hf):
        # Setup: 4 texts, batch_size=2 -> 2 batches
        texts = ["Text 1", "Text 2", "Text 3", "Text 4"]

        # Simulate transient 429 error on first attempt, then success
        call_count = {"count": 0}

        def mock_feature_extraction(batch):
            call_count["count"] += 1
            if call_count["count"] == 1:
                raise RuntimeError("429 rate limit exceeded. Please back off.")
            # Return 2 vectors for a batch of 2
            if isinstance(batch, list):
                return [[0.1] * 768 for _ in batch]
            return [0.1] * 768

        mock_hf.feature_extraction.side_effect = mock_feature_extraction

        embeddings = embed_texts(texts, batch_size=2)
        assert len(embeddings) == 4
        # Verify that retry occurred (total calls >= 3 due to 1 failure + 2 successful batches)
        assert call_count["count"] >= 3

    @patch("data_loader.hf_client")
    def test_embed_texts_empty(self, mock_hf):
        assert embed_texts([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. BATCHED QDRANT UPSERTS & SAFE VECTOR REPLACEMENT
# ─────────────────────────────────────────────────────────────────────────────
class TestBatchedUpsertAndSafeReplacement:
    @patch("vector_db.QdrantClient")
    def test_batched_upsert(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True

        storage = QdrantStorage(url="http://mock:6333", collection="docs_hf")

        ids = [f"id_{i}" for i in range(5)]
        vectors = [[0.1] * 768 for _ in range(5)]
        payloads = [{"source": "test.pdf", "chunk_index": i} for i in range(5)]

        # Batch size of 2 -> 5 items will result in 3 upsert calls (2, 2, 1)
        total = storage.upsert(ids, vectors, payloads, batch_size=2)
        assert total == 5
        assert mock_client.upsert.call_count == 3

    @patch("vector_db.QdrantClient")
    def test_safe_replacement_cleans_prior_vectors(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True

        storage = QdrantStorage(url="http://mock:6333", collection="docs_hf")

        # Deleting by source before upsert ensures no orphaned vector chunks remain
        deleted = storage.delete_by_source("doc-update-123")
        assert deleted is True
        assert mock_client.delete.called


# ─────────────────────────────────────────────────────────────────────────────
# 3. JOB CANCELLATION & PARTIAL VECTOR CLEANUP
# ─────────────────────────────────────────────────────────────────────────────
class TestJobCancellation:
    @patch("main.QdrantStorage")
    def test_cancel_in_flight_job(self, mock_storage_cls, scalable_test_env):
        client, SessionLocal, _ = scalable_test_env
        mock_storage = MagicMock()
        mock_storage_cls.return_value = mock_storage

        # Create active document in DB
        db = SessionLocal()
        doc = Document(
            id="doc-cancel-1",
            filename="cancel_test.pdf",
            file_hash="hash_cancel",
            file_size=1024,
            storage_path="/tmp/cancel.pdf",
            workspace_id="ws-cancel",
            status="processing",
            chunk_count=10,
            page_count=2,
        )
        job = DocumentJob(
            id="job-cancel-1",
            document_id="doc-cancel-1",
            job_type="ingest",
            status="processing",
        )
        db.add_all([doc, job])
        db.commit()
        db.close()

        # Cancel the job
        resp = client.post("/documents/doc-cancel-1/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # Verify Qdrant partial vector cleanup was executed
        mock_storage.delete_by_source.assert_called_with("doc-cancel-1")

        # Verify database statuses updated
        db = SessionLocal()
        d = db.query(Document).filter(Document.id == "doc-cancel-1").first()
        assert d.status == "cancelled"
        assert "cancelled by user" in d.error_message.lower()

        j = db.query(DocumentJob).filter(DocumentJob.id == "job-cancel-1").first()
        assert j.status == "cancelled"
        db.close()

    def test_cancel_completed_document_rejected(self, scalable_test_env):
        client, SessionLocal, _ = scalable_test_env
        db = SessionLocal()
        doc = Document(
            id="doc-completed-1",
            filename="done.pdf",
            file_hash="hash_done",
            file_size=1024,
            storage_path="/tmp/done.pdf",
            workspace_id="ws-done",
            status="completed",
            chunk_count=5,
            page_count=1,
        )
        db.add(doc)
        db.commit()
        db.close()

        resp = client.post("/documents/doc-completed-1/cancel")
        assert resp.status_code == 400
        assert "already completed" in resp.json()["detail"].lower()

    def test_update_doc_status_does_not_overwrite_cancelled(self, scalable_test_env):
        from main import _update_doc_status
        _, SessionLocal, _ = scalable_test_env
        db = SessionLocal()
        doc = Document(
            id="doc-cancelled-protect",
            filename="cancelled.pdf",
            file_hash="hash_canc_prot",
            file_size=1024,
            storage_path="/tmp/cancelled.pdf",
            workspace_id="ws-prot",
            status="cancelled",
            chunk_count=0,
            page_count=1,
            error_message="User cancelled",
        )
        db.add(doc)
        db.commit()
        db.close()

        # Attempt to mark it as completed or processing from a late step
        _update_doc_status("doc-cancelled-protect", "completed", chunk_count=10)

        # Check DB; status must still be cancelled
        db = SessionLocal()
        d = db.query(Document).filter(Document.id == "doc-cancelled-protect").first()
        assert d.status == "cancelled"
        assert d.error_message == "User cancelled"
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 4. RECOVERY FROM PARTIAL FAILURES ON APPLICATION RESTART
# ─────────────────────────────────────────────────────────────────────────────
class TestStartupJobRecovery:
    @patch("main.QdrantStorage")
    def test_recover_interrupted_jobs(self, mock_storage_cls, scalable_test_env):
        _, SessionLocal, _ = scalable_test_env
        mock_storage = MagicMock()
        mock_storage_cls.return_value = mock_storage

        # Create two interrupted documents (one processing, one retrying)
        db = SessionLocal()
        d1 = Document(
            id="doc-crash-1",
            filename="crash1.pdf",
            file_hash="hash_crash1",
            file_size=2048,
            storage_path="/tmp/crash1.pdf",
            workspace_id="ws-crash",
            status="processing",
            chunk_count=8,
            page_count=2,
        )
        d2 = Document(
            id="doc-crash-2",
            filename="crash2.pdf",
            file_hash="hash_crash2",
            file_size=4096,
            storage_path="/tmp/crash2.pdf",
            workspace_id="ws-crash",
            status="retrying",
            chunk_count=4,
            page_count=1,
        )
        j1 = DocumentJob(id="job-crash-1", document_id="doc-crash-1", status="processing")
        j2 = DocumentJob(id="job-crash-2", document_id="doc-crash-2", status="queued")
        db.add_all([d1, d2, j1, j2])
        db.commit()
        db.close()

        # Simulate application startup recovery
        recover_interrupted_jobs()

        # Verify documents transitioned to failed with actionable message
        db = SessionLocal()
        doc1 = db.query(Document).filter(Document.id == "doc-crash-1").first()
        doc2 = db.query(Document).filter(Document.id == "doc-crash-2").first()

        assert doc1.status == "failed"
        assert "interrupted by application restart" in doc1.error_message
        assert doc2.status == "failed"
        assert "interrupted by application restart" in doc2.error_message

        # Partial vectors were purged
        assert mock_storage.delete_by_source.call_count >= 2

        # Jobs transitioned to failed
        job1 = db.query(DocumentJob).filter(DocumentJob.id == "job-crash-1").first()
        job2 = db.query(DocumentJob).filter(DocumentJob.id == "job-crash-2").first()
        assert job1.status == "failed"
        assert job2.status == "failed"
        db.close()
