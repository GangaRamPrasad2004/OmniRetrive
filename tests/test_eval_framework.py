import pytest
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, EvalRun, EvalItemResult
from main import app, get_db
from eval.metrics import (
    calculate_recall_at_k,
    calculate_mrr,
    calculate_ndcg,
    calculate_citation_correctness,
    calculate_refusal_accuracy,
    calculate_faithfulness,
    calculate_answer_relevance,
    estimate_token_cost,
)
from eval.runner import EvalRunner, compare_runs, load_eval_dataset


@pytest.fixture
def eval_test_env(tmp_path):
    test_db_path = tmp_path / "test_eval.db"
    engine = create_engine(
        f"sqlite:///{test_db_path}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield client, TestingSessionLocal, tmp_path

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


# ─────────────────────────────────────────────────────────────────────────────
# 1. DETERMINISTIC METRICS UNIT TESTS
# ─────────────────────────────────────────────────────────────────────────────
class TestDeterministicMetrics:
    def test_recall_at_k(self):
        chunks = [
            {"document_name": "other.pdf", "relevant_text": "Unrelated content"},
            {"document_name": "security_policy.pdf", "relevant_text": "All data at rest must use AES-256-GCM."},
            {"document_name": "security_policy.pdf", "relevant_text": "Another chunk"},
        ]
        # At k=1, target is not present
        assert calculate_recall_at_k(chunks, "security_policy.pdf", "AES-256-GCM", k=1) == 0.0
        # At k=2, target is present
        assert calculate_recall_at_k(chunks, "security_policy.pdf", "AES-256-GCM", k=2) == 1.0
        # At k=3, target is present
        assert calculate_recall_at_k(chunks, "security_policy.pdf", "AES-256-GCM", k=3) == 1.0

    def test_mrr_calculation(self):
        chunks = [
            {"document_name": "doc1.pdf", "relevant_text": "Irrelevant intro"},
            {"document_name": "security_policy.pdf", "relevant_text": "Target text: AES-256-GCM is mandated."},
        ]
        # Target is at rank 2 -> MRR = 1/2 = 0.5
        mrr = calculate_mrr(chunks, "security_policy.pdf", "AES-256-GCM", k=5)
        assert mrr == 0.5

        # When target is at rank 1 -> MRR = 1/1 = 1.0
        chunks_rank1 = [
            {"document_name": "security_policy.pdf", "relevant_text": "Target text: AES-256-GCM is mandated."}
        ]
        assert calculate_mrr(chunks_rank1, "security_policy.pdf", "AES-256-GCM", k=5) == 1.0

        # When target is absent -> MRR = 0.0
        assert calculate_mrr([], "security_policy.pdf", "AES-256-GCM", k=5) == 0.0

    def test_ndcg_calculation(self):
        chunks = [
            {"document_name": "doc1.pdf", "relevant_text": "Irrelevant"},
            {"document_name": "target.pdf", "relevant_text": "Target passage content"},
        ]
        # Rank 2: DCG = 1 / log2(3) = 0.6309; IDCG = 1 / log2(2) = 1.0 -> NDCG = 0.6309
        ndcg = calculate_ndcg(chunks, "target.pdf", "Target passage content", k=5)
        assert 0.60 <= ndcg <= 0.65

    def test_citation_correctness(self):
        citations = [
            {"document_name": "security_policy.pdf", "page_number": 1, "relevant_text": "..."},
            {"document_name": "wrong_doc.pdf", "page_number": 4, "relevant_text": "..."},
        ]
        metrics = calculate_citation_correctness(citations, "security_policy.pdf", expected_page=1)
        assert metrics["precision"] == 0.5  # 1 out of 2 is correct
        assert metrics["recall"] == 1.0     # Expected document and page was cited

    def test_refusal_accuracy_unanswerable_query(self):
        # When unanswerable, refusal response yields 1.0
        unanswerable_resp = "Based on the provided documents in this workspace, there is insufficient relevant information to answer this question."
        assert calculate_refusal_accuracy(unanswerable_resp, is_answerable=False, insufficient_context_flag=True) == 1.0

        # When unanswerable, hallucinated response yields 0.0
        hallucinated_resp = "The company offers 25 days of paid annual vacation leave."
        assert calculate_refusal_accuracy(hallucinated_resp, is_answerable=False, insufficient_context_flag=False) == 0.0

    def test_refusal_accuracy_answerable_query(self):
        # When answerable, legitimate answer yields 1.0
        legit_resp = "Data at rest must be encrypted using AES-256-GCM."
        assert calculate_refusal_accuracy(legit_resp, is_answerable=True, insufficient_context_flag=False) == 1.0

        # When answerable, false refusal yields 0.0
        false_refusal = "Based on the provided documents, there is insufficient information."
        assert calculate_refusal_accuracy(false_refusal, is_answerable=True, insufficient_context_flag=True) == 0.0

    def test_token_cost_estimation(self):
        # 10,000 prompt tokens @ $0.00015/1k = $0.0015
        # 1,000 completion tokens @ $0.0006/1k = $0.0006
        # Total = $0.0021
        cost = estimate_token_cost(prompt_tokens=10000, completion_tokens=1000)
        assert cost == 0.0021


# ─────────────────────────────────────────────────────────────────────────────
# 2. MODEL-BASED / HEURISTIC METRICS (Explicitly Tagged)
# ─────────────────────────────────────────────────────────────────────────────
class TestModelBasedMetrics:
    def test_faithfulness_and_relevance_tagged_model_based(self):
        answer = "All data at rest requires AES-256-GCM encryption."
        contexts = ["Section 4.1.2: All data at rest must use AES-256-GCM."]
        query = "What encryption is used at rest?"

        faith_res = calculate_faithfulness(answer, contexts)
        assert faith_res["model_based"] is True
        assert faith_res["score"] >= 0.5

        rel_res = calculate_answer_relevance(answer, query)
        assert rel_res["model_based"] is True
        assert rel_res["score"] >= 0.5


# ─────────────────────────────────────────────────────────────────────────────
# 3. REPEATABLE RUNNER & EXPERIMENT COMPARISON
# ─────────────────────────────────────────────────────────────────────────────
class TestEvalRunnerAndComparison:
    @patch("eval.runner.embed_texts")
    @patch("eval.runner.QdrantStorage")
    def test_run_evaluation_end_to_end(self, mock_storage_cls, mock_embed, eval_test_env):
        _, SessionLocal, _ = eval_test_env
        mock_embed.return_value = [[0.1] * 768]
        mock_store = MagicMock()
        mock_storage_cls.return_value = mock_store

        mock_store.search.return_value = {
            "contexts": ["Section 4.1.2: Encryption Standards. All data at rest must use AES-256-GCM."],
            "sources": ["security_policy.pdf"],
            "citations": [{
                "document_name": "security_policy.pdf",
                "document_id": "doc-sec-1",
                "page_number": 1,
                "chunk_index": 0,
                "relevant_text": "Section 4.1.2: Encryption Standards. All data at rest must use AES-256-GCM.",
                "score": 0.95,
                "verified": True,
            }],
        }

        # Mock SessionLocal inside eval.runner to use test database
        with patch("eval.runner.SessionLocal", SessionLocal):
            runner = EvalRunner()
            summary = runner.run_evaluation(run_name="test_ci_run", top_k=3, save_to_db=True)

        assert summary["run_name"] == "test_ci_run"
        assert summary["total_queries"] > 0
        assert "deterministic" in summary["metrics"]
        assert "mrr" in summary["metrics"]["deterministic"]
        assert summary["performance"]["avg_latency_ms"] >= 0.0

        # Verify persisted into DB
        db = SessionLocal()
        db_run = db.query(EvalRun).filter(EvalRun.run_name == "test_ci_run").first()
        assert db_run is not None
        assert db_run.total_queries == summary["total_queries"]
        items = db.query(EvalItemResult).filter(EvalItemResult.run_id == db_run.id).all()
        assert len(items) == summary["total_queries"]
        db.close()

    def test_compare_runs_metric_deltas(self):
        run_a = {
            "id": "run-1",
            "run_name": "baseline_dense",
            "metrics": {
                "deterministic": {
                    "mrr": 0.70,
                    "recall_at_1": 0.60,
                    "recall_at_3": 0.80,
                    "recall_at_5": 0.85,
                    "citation_recall": 0.75,
                    "refusal_accuracy": 0.90,
                }
            },
            "performance": {"avg_latency_ms": 120.5, "estimated_cost_usd": 0.0010},
        }
        run_b = {
            "id": "run-2",
            "run_name": "hybrid_tuned",
            "metrics": {
                "deterministic": {
                    "mrr": 0.90,
                    "recall_at_1": 0.85,
                    "recall_at_3": 0.95,
                    "recall_at_5": 1.00,
                    "citation_recall": 0.95,
                    "refusal_accuracy": 1.00,
                }
            },
            "performance": {"avg_latency_ms": 95.2, "estimated_cost_usd": 0.0009},
        }

        diff = compare_runs(run_a, run_b)
        assert diff["deltas"]["mrr"] == 0.20
        assert diff["deltas"]["recall_at_1"] == 0.25
        assert diff["deltas"]["latency_ms_delta"] == -25.3
        assert diff["deltas"]["cost_usd_delta"] == -0.0001


# ─────────────────────────────────────────────────────────────────────────────
# 4. EVALUATION API ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
class TestEvalAPIEndpoints:
    @patch("eval.runner.embed_texts")
    @patch("eval.runner.QdrantStorage")
    def test_trigger_and_list_evaluation_runs(self, mock_storage_cls, mock_embed, eval_test_env):
        client, _, _ = eval_test_env
        mock_embed.return_value = [[0.1] * 768]
        mock_store = MagicMock()
        mock_storage_cls.return_value = mock_store
        mock_store.search.return_value = {
            "contexts": ["All data at rest must use AES-256-GCM."],
            "sources": ["security_policy.pdf"],
            "citations": [{
                "document_name": "security_policy.pdf",
                "document_id": "doc-1",
                "page_number": 1,
                "chunk_index": 0,
                "relevant_text": "All data at rest must use AES-256-GCM.",
                "score": 0.9,
                "verified": True,
            }],
        }

        # 1. Trigger evaluation run via POST /eval/runs
        post_resp = client.post(
            "/eval/runs",
            json={
                "run_name": "api_test_run",
                "top_k": 3,
                "enable_hybrid": True,
                "similarity_threshold": 0.0,
                "chunk_size": 1000,
                "chunk_overlap": 200,
                "save_to_db": True,
            },
        )
        assert post_resp.status_code == 200
        run_data = post_resp.json()
        run_id = run_data["id"]
        assert run_data["run_name"] == "api_test_run"

        # 2. List historical runs via GET /eval/runs
        list_resp = client.get("/eval/runs")
        assert list_resp.status_code == 200
        runs = list_resp.json()["runs"]
        assert len(runs) >= 1
        assert runs[0]["id"] == run_id

        # 3. Get run details via GET /eval/runs/{run_id}
        details_resp = client.get(f"/eval/runs/{run_id}")
        assert details_resp.status_code == 200
        assert details_resp.json()["id"] == run_id
        assert len(details_resp.json()["items"]) > 0

        # 4. Generate markdown report via GET /eval/report
        report_resp = client.get("/eval/report")
        assert report_resp.status_code == 200
        assert "OmniRetrive: RAG Evaluation Report" in report_resp.json()["report_markdown"]
        assert "api_test_run" in report_resp.json()["report_markdown"]
