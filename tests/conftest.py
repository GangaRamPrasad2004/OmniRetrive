"""Test configuration for OmniRetrive project.
Adds the project root to PYTHONPATH so that test modules can import
`main`, `database`, `data_loader`, etc. Also provides a FastAPI test
client with a mocked TelemetryClient to avoid external calls.
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure the project root (one level up from the tests directory) is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_STUBS = os.path.join(os.path.dirname(__file__), "stubs")
if TEST_STUBS not in sys.path:
    sys.path.insert(0, TEST_STUBS)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Test database configuration (SQLite in‑memory) – isolated from production DB
# ---------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Import after sys.path manipulation so imports resolve correctly
from database import Base, get_db
from main import app, init_db
from unittest import mock

@pytest.fixture(scope="session", autouse=True)
def create_test_database():
    """Create tables once for the whole test session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="session", autouse=True)
def disable_application_startup_side_effects():
    """Keep test clients from migrating or recovering against the configured runtime database."""
    with (
        mock.patch("main.init_db"),
        mock.patch("main.recover_interrupted_jobs"),
        mock.patch("eval.runner.init_db"),
    ):
        yield

# Override the FastAPI dependency to use the test DB session
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture()
def client():
    """FastAPI test client that uses the test DB and a mocked TelemetryClient."""
    with mock.patch("telemetry_client.telemetry") as mock_telemetry:
        mock_telemetry.record_http_request.return_value = None
        mock_telemetry.record_ingestion.return_value = None
        mock_telemetry.record_embedding.return_value = None
        mock_telemetry.record_retrieval.return_value = None
        mock_telemetry.record_llm.return_value = None
        yield TestClient(app)
