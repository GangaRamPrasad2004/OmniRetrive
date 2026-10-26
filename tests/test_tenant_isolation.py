import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db, Document
from storage import LocalDocumentStorage
from vector_db import QdrantStorage
from main import app


@pytest.fixture(scope="function")
def tenant_test_env():
    temp_dir = tempfile.mkdtemp()
    test_db_path = os.path.join(temp_dir, "tenant_test.db")
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

    with patch("main.SessionLocal", TestingSessionLocal), patch(
        "main.get_document_storage", return_value=LocalDocumentStorage(temp_dir)
    ):
        client = TestClient(app)
        yield client, TestingSessionLocal, temp_dir

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. VECTOR DB MULTI-TENANT ISOLATION TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestQdrantTenantIsolation:
    @patch("vector_db.QdrantClient")
    def test_search_without_workspace_is_rejected(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True
        storage = QdrantStorage(url="http://mock:6333", collection="docs_hf")

        with pytest.raises(ValueError, match="workspace_id is required"):
            storage.search([0.1] * 768, top_k=5)

        mock_client.query_points.assert_not_called()

    @patch("vector_db.QdrantClient")
    def test_search_enforces_workspace_filter(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True

        # Mock results returned by Qdrant
        mock_point = MagicMock()
        mock_point.payload = {
            "text": "Acme Confidential Secret Report",
            "source": "secret.pdf",
            "workspace_id": "ws-acme",
        }
        mock_response = MagicMock()
        mock_response.points = [mock_point]
        mock_client.query_points.return_value = mock_response

        storage = QdrantStorage(url="http://mock:6333", collection="docs_hf")

        # 1. Search with Tenant A filter
        res_acme = storage.search([0.1] * 768, top_k=5, workspace_id="ws-acme")
        assert len(res_acme["contexts"]) == 1
        assert "Acme Confidential" in res_acme["contexts"][0]

        # Verify that Qdrant query_points was called with a filter containing workspace_id = ws-acme
        called_args, called_kwargs = mock_client.query_points.call_args
        query_filter = called_kwargs.get("query_filter")
        assert query_filter is not None
        assert len(query_filter.must) == 1
        assert query_filter.must[0].key == "workspace_id"
        assert query_filter.must[0].match.value == "ws-acme"

    @patch("vector_db.QdrantClient")
    def test_delete_by_workspace_purges_tenant_vectors(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.collection_exists.return_value = True

        storage = QdrantStorage(url="http://mock:6333", collection="docs_hf")
        success = storage.delete_by_workspace("ws-tenant-to-delete")
        assert success is True

        called_args, called_kwargs = mock_client.delete.call_args
        selector = called_kwargs.get("points_selector")
        assert selector is not None
        assert selector.filter.must[0].key == "workspace_id"
        assert selector.filter.must[0].match.value == "ws-tenant-to-delete"


# ─────────────────────────────────────────────────────────────────────────────
# 2. PYTHON AI SERVICE MULTI-TENANT QUERY & CLEANUP ENDPOINT TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestTenantAPIEndpoints:
    @patch("main.QdrantStorage")
    def test_query_documents_passes_workspace_id(self, mock_storage_cls, tenant_test_env):
        client, SessionLocal, _ = tenant_test_env
        db = SessionLocal()
        db.add(
            Document(
                id="doc-alpha-query",
                filename="alpha_doc.pdf",
                file_hash="alpha-query-hash",
                file_size=10,
                storage_path="workspaces/ws-alpha-123/documents/doc-alpha-query/versions/1/original.pdf",
                workspace_id="ws-alpha-123",
                status="completed",
                chunk_count=1,
            )
        )
        db.commit()
        db.close()

        mock_storage = MagicMock()
        mock_storage.search.return_value = {
            "contexts": ["Confidential context passage for Workspace Alpha."],
            "sources": ["alpha_doc.pdf"],
        }
        mock_storage_cls.return_value = mock_storage

        with patch("main.embed_texts", return_value=[[0.05] * 768]):
            resp = client.post(
                "/query",
                json={
                    "question": "What is in alpha?",
                    "top_k": 3,
                    "workspace_id": "ws-alpha-123",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["workspace_id"] == "ws-alpha-123"
        assert len(data["sources"]) == 1
        assert "alpha_doc.pdf" in data["sources"]

        # Verify search was called with workspace_id
        mock_storage.search.assert_called_once()
        _, kwargs = mock_storage.search.call_args
        assert kwargs["workspace_id"] == "ws-alpha-123"
        assert kwargs["top_k"] == 3

    @patch("main.QdrantStorage")
    def test_cleanup_workspace_endpoint(self, mock_storage_cls, tenant_test_env):
        client, SessionLocal, temp_dir = tenant_test_env

        mock_storage = MagicMock()
        mock_storage_cls.return_value = mock_storage

        # Create dummy file and DB records for Workspace Alpha and Workspace Beta
        dummy_file_path = os.path.join(temp_dir, "ws_alpha_file.pdf")
        with open(dummy_file_path, "wb") as f:
            f.write(b"%PDF-1.4 dummy")

        db = SessionLocal()
        doc_alpha = Document(
            id="doc-alpha-1",
            filename="ws_alpha_file.pdf",
            file_hash="hash_alpha",
            file_size=14,
            storage_path=dummy_file_path,
            workspace_id="ws-alpha",
            status="completed",
            chunk_count=2,
            page_count=1,
        )
        doc_beta = Document(
            id="doc-beta-1",
            filename="ws_beta_file.pdf",
            file_hash="hash_beta",
            file_size=20,
            storage_path="/tmp/beta.pdf",
            workspace_id="ws-beta",
            status="completed",
            chunk_count=3,
            page_count=1,
        )
        db.add_all([doc_alpha, doc_beta])
        db.commit()
        db.close()

        # Delete Workspace Alpha
        resp = client.delete("/workspaces/ws-alpha/cleanup")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["workspace_id"] == "ws-alpha"
        assert resp.json()["documents_deleted"] == 1

        # File was unlinked
        assert not os.path.exists(dummy_file_path)

        # Vector deletion was called on Qdrant
        mock_storage.delete_by_workspace.assert_called_once_with("ws-alpha")

        # Verify Workspace Alpha document deleted from DB, but Workspace Beta document preserved
        db = SessionLocal()
        assert db.query(Document).filter(Document.workspace_id == "ws-alpha").first() is None
        assert db.query(Document).filter(Document.workspace_id == "ws-beta").first() is not None
        db.close()
