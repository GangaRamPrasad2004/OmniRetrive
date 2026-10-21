import io
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pypdf import PdfWriter

from pathlib import Path

from database import Base, get_db, Document, DocumentJob
from file_utils import validate_pdf_file, compute_sha256, sanitize_filename
from main import app, _update_doc_status


def create_blank_pdf(pages: int = 1) -> bytes:
    """Helper to generate a clean, valid PDF in memory."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


# Setup isolated test database and test client fixture
@pytest.fixture(scope="function")
def test_env():
    # Temporary SQLite DB
    temp_dir = tempfile.mkdtemp()
    test_db_path = os.path.join(temp_dir, "test.db")
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

    # Patch SessionLocal in main and database so background helpers use test db
    with patch("main.SessionLocal", TestingSessionLocal), \
         patch("main.UPLOADS_DIR", Path(temp_dir)), \
         patch("main.inngest_client.send", return_value=["test-event-id-123"]), \
         patch("main.QdrantStorage") as mock_qdrant:

        mock_instance = MagicMock()
        mock_instance.delete_by_source.return_value = True
        mock_qdrant.return_value = mock_instance

        client = TestClient(app)
        yield client, TestingSessionLocal, temp_dir

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. FILE UTILITIES & VALIDATION TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestFileUtilsAndValidation:
    def test_sanitize_filename(self):
        assert sanitize_filename("safe_file.pdf") == "safe_file.pdf"
        assert sanitize_filename("../../../etc/passwd.pdf") == "passwd.pdf"
        assert sanitize_filename('weird/name:*?"<>|.pdf') == "name_______.pdf"

    def test_compute_sha256(self):
        data = b"Sample document bytes for hash"
        h1 = compute_sha256(data)
        h2 = compute_sha256(data)
        assert len(h1) == 64
        assert h1 == h2

    def test_validate_valid_pdf(self):
        pdf_bytes = create_blank_pdf(pages=3)
        valid, err, page_count = validate_pdf_file("sample.pdf", pdf_bytes)
        assert valid is True
        assert err == ""
        assert page_count == 3

    def test_validate_empty_file(self):
        valid, err, page_count = validate_pdf_file("empty.pdf", b"")
        assert valid is False
        assert "empty" in err.lower()
        assert page_count == 0

    def test_validate_non_pdf_extension(self):
        pdf_bytes = create_blank_pdf()
        valid, err, _ = validate_pdf_file("sample.txt", pdf_bytes)
        assert valid is False
        assert "expected '.pdf'" in err.lower()

    def test_validate_corrupted_header(self):
        corrupt_bytes = b"NOT_A_REAL_PDF_HEADER_CONTENT_AT_ALL"
        valid, err, _ = validate_pdf_file("corrupt.pdf", corrupt_bytes)
        assert valid is False
        assert "header" in err.lower()

    def test_validate_oversized_file(self):
        pdf_bytes = create_blank_pdf()
        # Set max_size to smaller than the generated PDF
        valid, err, _ = validate_pdf_file("oversized.pdf", pdf_bytes, max_size=100)
        assert valid is False
        assert "exceeds maximum allowed limit" in err.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 2. DOCUMENT MANAGEMENT API ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
class TestDocumentAPI:
    def test_health_check(self, test_env):
        client, _, _ = test_env
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

    def test_upload_valid_pdf_lifecycle(self, test_env):
        client, SessionLocal, _ = test_env
        pdf_bytes = create_blank_pdf(pages=2)

        resp = client.post(
            "/upload",
            files={"file": ("test_doc.pdf", pdf_bytes, "application/pdf")},
            data={"workspace_id": "ws-1", "owner_id": "alice"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "queued"
        assert data["filename"] == "test_doc.pdf"
        assert data["event_id"] == "test-event-id-123"

        doc = data["document"]
        assert doc["page_count"] == 2
        assert doc["status"] == "queued"
        assert doc["chunk_count"] == 0
        assert doc["is_ready_for_qa"] is False
        assert doc["workspace_id"] == "ws-1"
        assert doc["owner_id"] == "alice"

        # Verify DB persistence
        db = SessionLocal()
        db_doc = db.query(Document).filter(Document.id == doc["id"]).first()
        assert db_doc is not None
        assert db_doc.file_size == len(pdf_bytes)
        assert db_doc.file_hash == compute_sha256(pdf_bytes)
        db.close()

    def test_upload_invalid_file_rejected(self, test_env):
        client, _, _ = test_env
        resp = client.post(
            "/upload",
            files={"file": ("malicious.exe", b"MZfakebinarycontent", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "expected '.pdf'" in resp.json()["detail"].lower()

    def test_duplicate_prevention_completed(self, test_env):
        client, SessionLocal, _ = test_env
        pdf_bytes = create_blank_pdf(pages=1)

        # 1. First upload
        resp1 = client.post(
            "/upload",
            files={"file": ("doc_dup.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp1.status_code == 201
        doc_id = resp1.json()["document"]["id"]

        # Simulate job completion
        db = SessionLocal()
        doc = db.query(Document).filter(Document.id == doc_id).first()
        doc.status = "completed"
        doc.chunk_count = 5
        db.commit()
        db.close()

        # 2. Second upload with identical content
        resp2 = client.post(
            "/upload",
            files={"file": ("doc_dup_copy.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "already_exists"
        assert data2["document"]["is_ready_for_qa"] is True
        assert data2["event_id"] is None

    def test_duplicate_prevention_processing(self, test_env):
        client, SessionLocal, _ = test_env
        pdf_bytes = create_blank_pdf(pages=1)

        # Upload
        resp1 = client.post(
            "/upload",
            files={"file": ("doc_active.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp1.status_code == 201
        doc_id = resp1.json()["document"]["id"]

        # Set status to processing
        db = SessionLocal()
        doc = db.query(Document).filter(Document.id == doc_id).first()
        doc.status = "processing"
        db.commit()
        db.close()

        # Re-upload same file
        resp2 = client.post(
            "/upload",
            files={"file": ("doc_active.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "already_processing"

    def test_search_and_sort_documents(self, test_env):
        client, SessionLocal, _ = test_env
        pdf1 = create_blank_pdf(pages=1)
        pdf2 = create_blank_pdf(pages=2)

        client.post("/upload", files={"file": ("alpha_report.pdf", pdf1, "application/pdf")})
        client.post("/upload", files={"file": ("beta_summary.pdf", pdf2, "application/pdf")})

        # List all
        resp = client.get("/documents")
        assert resp.status_code == 200
        docs = resp.json()["documents"]
        assert len(docs) == 2

        # Search by filename
        resp_search = client.get("/documents?search=alpha")
        docs_alpha = resp_search.json()["documents"]
        assert len(docs_alpha) == 1
        assert docs_alpha[0]["filename"] == "alpha_report.pdf"

        # Sort by filename ascending
        resp_sorted = client.get("/documents?sort_by=filename&sort_order=asc")
        docs_sorted = resp_sorted.json()["documents"]
        assert docs_sorted[0]["filename"] == "alpha_report.pdf"
        assert docs_sorted[1]["filename"] == "beta_summary.pdf"

    def test_get_document_details(self, test_env):
        client, _, _ = test_env
        pdf_bytes = create_blank_pdf(pages=1)
        resp = client.post("/upload", files={"file": ("details_test.pdf", pdf_bytes, "application/pdf")})
        doc_id = resp.json()["document"]["id"]

        resp_get = client.get(f"/documents/{doc_id}")
        assert resp_get.status_code == 200
        data = resp_get.json()
        assert data["id"] == doc_id
        assert data["filename"] == "details_test.pdf"
        assert "jobs" in data
        assert len(data["jobs"]) >= 1

        # 404 for nonexistent
        resp_404 = client.get("/documents/non-existent-id")
        assert resp_404.status_code == 404

    def test_retry_failed_document(self, test_env):
        client, SessionLocal, _ = test_env
        pdf_bytes = create_blank_pdf(pages=1)
        resp = client.post("/upload", files={"file": ("retry_test.pdf", pdf_bytes, "application/pdf")})
        doc_id = resp.json()["document"]["id"]

        # Mark as failed with error
        db = SessionLocal()
        doc = db.query(Document).filter(Document.id == doc_id).first()
        doc.status = "failed"
        doc.error_message = "Network timeout communicating with HuggingFace."
        db.commit()
        db.close()

        # Trigger retry
        resp_retry = client.post(f"/documents/{doc_id}/retry")
        assert resp_retry.status_code == 200
        data = resp_retry.json()
        assert data["status"] == "retrying"
        assert data["document"]["error_message"] is None

        # Attempting to retry an already processing document returns 409
        db = SessionLocal()
        doc = db.query(Document).filter(Document.id == doc_id).first()
        doc.status = "processing"
        db.commit()
        db.close()

        resp_conflict = client.post(f"/documents/{doc_id}/retry")
        assert resp_conflict.status_code == 409

    def test_document_deletion_removes_data_and_vectors(self, test_env):
        client, SessionLocal, _ = test_env
        pdf_bytes = create_blank_pdf(pages=1)
        resp = client.post("/upload", files={"file": ("delete_me.pdf", pdf_bytes, "application/pdf")})
        doc_id = resp.json()["document"]["id"]
        storage_path = resp.json()["document"]["storage_path"]

        assert os.path.exists(storage_path)

        # Delete document
        resp_del = client.delete(f"/documents/{doc_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "deleted"

        # Verify file is deleted from disk
        assert not os.path.exists(storage_path)

        # Verify DB record deleted
        db = SessionLocal()
        assert db.query(Document).filter(Document.id == doc_id).first() is None
        # Verify cascaded jobs deleted
        assert db.query(DocumentJob).filter(DocumentJob.document_id == doc_id).count() == 0
        db.close()

        # Subsequent GET is 404
        assert client.get(f"/documents/{doc_id}").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 3. PIPELINE ERROR HANDLING & READY-FOR-QA VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────
class TestPipelineFailureAndReadiness:
    def test_status_update_helper(self, test_env):
        _, SessionLocal, _ = test_env
        db = SessionLocal()
        doc = Document(
            id="doc-test-helper",
            filename="helper.pdf",
            file_hash="hash123",
            file_size=1024,
            storage_path="/tmp/helper.pdf",
            status="queued",
            chunk_count=0,
            page_count=1,
        )
        db.add(doc)
        db.commit()
        db.close()

        # Update to processing
        _update_doc_status("doc-test-helper", "processing", chunk_count=12)
        db = SessionLocal()
        d = db.query(Document).filter(Document.id == "doc-test-helper").first()
        assert d.status == "processing"
        assert d.chunk_count == 12
        assert d.to_dict()["is_ready_for_qa"] is False
        db.close()

        # Simulate failure
        _update_doc_status("doc-test-helper", "failed", error_msg="Embedding model unavailable")
        db = SessionLocal()
        d = db.query(Document).filter(Document.id == "doc-test-helper").first()
        assert d.status == "failed"
        assert d.error_message == "Embedding model unavailable"
        assert d.to_dict()["is_ready_for_qa"] is False
        db.close()

        # Simulate successful completion
        _update_doc_status("doc-test-helper", "completed")
        db = SessionLocal()
        d = db.query(Document).filter(Document.id == "doc-test-helper").first()
        assert d.status == "completed"
        assert d.error_message is None
        assert d.to_dict()["is_ready_for_qa"] is True
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 4. VECTOR DATABASE UNIT TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestQdrantStorage:
    @patch("vector_db.QdrantClient")
    def test_delete_by_source_success(self, mock_client_cls):
        from vector_db import QdrantStorage
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True

        storage = QdrantStorage(url="http://mock:6333", collection="test_col")
        success = storage.delete_by_source("doc-12345")
        assert success is True
        assert mock_client.delete.called

    @patch("vector_db.QdrantClient")
    def test_delete_by_source_handles_exception(self, mock_client_cls):
        from vector_db import QdrantStorage
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True
        mock_client.delete.side_effect = RuntimeError("Qdrant connection error")

        storage = QdrantStorage(url="http://mock:6333", collection="test_col")
        success = storage.delete_by_source("doc-12345")
        assert success is False

