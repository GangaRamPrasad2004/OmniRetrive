"""
OmniRetrive Evaluation Engine & Runner.
Executes automated evaluations across the version-controlled evaluation dataset,
computes deterministic and model-based metrics, measures latency and costs,
and persists historical runs for comparison over time.
"""

import os
import sys
import json
import time
import uuid
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# Ensure project root is in path
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database import SessionLocal, EvalRun, EvalItemResult, init_db
from vector_db import QdrantStorage
from data_loader import embed_texts
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


DATASET_PATH = CURRENT_DIR / "dataset.json"


def load_eval_dataset(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads the version-controlled evaluation dataset."""
    target_path = path or DATASET_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"Evaluation dataset not found at: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        return json.load(f)


class EvalRunner:
    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        qdrant_key: Optional[str] = None,
        collection: str = "docs_hf",
        workspace_id: Optional[str] = None,
        db: Optional[Any] = None,
    ):
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.qdrant_key = qdrant_key or os.getenv("QDRANT_API_KEY")
        self.collection = collection
        self.workspace_id = workspace_id
        self.db = db
        self.storage = QdrantStorage(
            url=self.qdrant_url,
            api_key=self.qdrant_key,
            collection=self.collection,
        )

    def run_evaluation(
        self,
        run_name: str = "default_run",
        dataset: Optional[List[Dict[str, Any]]] = None,
        top_k: int = 5,
        enable_hybrid: bool = True,
        similarity_threshold: float = 0.0,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        save_to_db: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes a complete evaluation benchmark across the dataset.
        Keeps evaluation strictly isolated from the production request path.
        """
        items = dataset if dataset is not None else load_eval_dataset()
        if not items:
            raise ValueError("Evaluation dataset must contain at least one query.")
        run_id = str(uuid.uuid4())
        created_at = datetime.datetime.now(datetime.timezone.utc)

        config = {
            "top_k": top_k,
            "enable_hybrid": enable_hybrid,
            "similarity_threshold": similarity_threshold,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "collection": self.collection,
            "workspace_id": self.workspace_id,
        }

        item_results = []
        recalls_at_1 = []
        recalls_at_3 = []
        recalls_at_5 = []
        mrrs = []
        ndcgs = []
        citation_precisions = []
        citation_recalls = []
        refusal_accuracies = []
        faithfulness_scores = []
        relevance_scores = []
        latencies_ms = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for item in items:
            q_id = item["id"]
            query = item["query"]
            expected_doc = item.get("expected_document")
            expected_page = item.get("expected_page")
            expected_passage = item.get("expected_passage")
            ref_answer = item.get("reference_answer", "")
            is_answerable = item.get("is_answerable", True)
            category = item.get("category", "general")

            start_t = time.perf_counter()

            # 1. Embed query
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"Evaluation query {q_id} must not be empty.")
            try:
                query_embeddings = embed_texts([query])
            except Exception as exc:
                raise RuntimeError(
                    f"Embedding failed for evaluation query {q_id}."
                ) from exc
            if len(query_embeddings) != 1 or not query_embeddings[0]:
                raise RuntimeError(
                    f"Embedding provider returned no usable vector for evaluation query {q_id}."
                )
            query_vec = query_embeddings[0]

            # 2. Search
            search_res = self.storage.search(
                query_vector=query_vec,
                top_k=top_k,
                workspace_id=self.workspace_id,
                query_text=query,
                similarity_threshold=similarity_threshold,
                enable_hybrid=enable_hybrid,
            )

            end_t = time.perf_counter()
            latency_ms = round((end_t - start_t) * 1000, 2)
            latencies_ms.append(latency_ms)

            retrieved_chunks = search_res.get("citations") or []
            contexts = search_res.get("contexts") or []
            sources = search_res.get("sources") or []

            # 3. Simulate or extract answer
            # If context is empty: refusal
            insufficient_context = (len(contexts) == 0)
            if insufficient_context:
                answer = "Based on the provided documents in this workspace, there is insufficient relevant information to answer this question."
            else:
                answer = f"Relevant information found in {', '.join(sources)}: {contexts[0][:200]}"

            # 4. Token & Cost Estimation
            p_tokens = len(query.split()) * 2 + sum(len(c.split()) * 2 for c in contexts) + 150
            c_tokens = len(answer.split()) * 2
            total_prompt_tokens += p_tokens
            total_completion_tokens += c_tokens

            # 5. Deterministic Metrics Calculation
            r1 = calculate_recall_at_k(retrieved_chunks, expected_doc, expected_passage, k=1)
            r3 = calculate_recall_at_k(retrieved_chunks, expected_doc, expected_passage, k=3)
            r5 = calculate_recall_at_k(retrieved_chunks, expected_doc, expected_passage, k=5)
            mrr_val = calculate_mrr(retrieved_chunks, expected_doc, expected_passage, k=top_k)
            ndcg_val = calculate_ndcg(retrieved_chunks, expected_doc, expected_passage, k=top_k)
            cit_metrics = calculate_citation_correctness(retrieved_chunks, expected_doc, expected_page)
            refusal_acc = calculate_refusal_accuracy(answer, is_answerable, insufficient_context)

            recalls_at_1.append(r1)
            recalls_at_3.append(r3)
            recalls_at_5.append(r5)
            mrrs.append(mrr_val)
            ndcgs.append(ndcg_val)
            citation_precisions.append(cit_metrics["precision"])
            citation_recalls.append(cit_metrics["recall"])
            refusal_accuracies.append(refusal_acc)

            # 6. Model-Based / Heuristic Metrics
            f_res = calculate_faithfulness(answer, contexts)
            rel_res = calculate_answer_relevance(answer, query)
            faithfulness_scores.append(f_res["score"])
            relevance_scores.append(rel_res["score"])

            # Determine query pass/fail:
            # For answerable: passed if retrieval found target chunk within top-k and didn't refuse
            # For unanswerable: passed if refusal was accurate
            if is_answerable:
                passed = (r5 > 0.0 and refusal_acc > 0.0)
            else:
                passed = (refusal_acc > 0.0)

            item_results.append({
                "query_id": q_id,
                "query": query,
                "category": category,
                "is_answerable": is_answerable,
                "passed": passed,
                "retrieved_sources": sources,
                "citations": retrieved_chunks,
                "generated_answer": answer,
                "latency_ms": latency_ms,
                "reciprocal_rank": mrr_val,
                "recall_at_1": r1,
                "recall_at_3": r3,
                "recall_at_5": r5,
                "ndcg": ndcg_val,
                "citation_precision": cit_metrics["precision"],
                "citation_recall": cit_metrics["recall"],
                "refusal_accuracy": refusal_acc,
                "faithfulness": f_res["score"],
                "answer_relevance": rel_res["score"],
                "tokens": {
                    "prompt": p_tokens,
                    "completion": c_tokens,
                },
            })

        total_q = len(items)
        passed_q = sum(1 for res in item_results if res["passed"])
        avg_mrr = sum(mrrs) / total_q if total_q else 0.0
        avg_ndcg = sum(ndcgs) / total_q if total_q else 0.0
        avg_r1 = sum(recalls_at_1) / total_q if total_q else 0.0
        avg_r3 = sum(recalls_at_3) / total_q if total_q else 0.0
        avg_r5 = sum(recalls_at_5) / total_q if total_q else 0.0
        avg_cit_p = sum(citation_precisions) / total_q if total_q else 0.0
        avg_cit_r = sum(citation_recalls) / total_q if total_q else 0.0
        avg_ref_acc = sum(refusal_accuracies) / total_q if total_q else 0.0
        avg_faith = sum(faithfulness_scores) / total_q if total_q else 0.0
        avg_rel = sum(relevance_scores) / total_q if total_q else 0.0
        avg_lat = sum(latencies_ms) / total_q if total_q else 0.0
        est_cost = estimate_token_cost(total_prompt_tokens, total_completion_tokens)

        run_summary = {
            "id": run_id,
            "run_name": run_name,
            "created_at": created_at.isoformat(),
            "dataset_version": "v1.0",
            "config": config,
            "total_queries": total_q,
            "passed_queries": passed_q,
            "pass_rate_pct": round((passed_q / total_q) * 100, 2) if total_q else 0.0,
            "metrics": {
                "deterministic": {
                    "mrr": round(avg_mrr, 4),
                    "ndcg": round(avg_ndcg, 4),
                    "recall_at_1": round(avg_r1, 4),
                    "recall_at_3": round(avg_r3, 4),
                    "recall_at_5": round(avg_r5, 4),
                    "citation_precision": round(avg_cit_p, 4),
                    "citation_recall": round(avg_cit_r, 4),
                    "refusal_accuracy": round(avg_ref_acc, 4),
                },
                "model_based": {
                    "faithfulness": round(avg_faith, 4),
                    "answer_relevance": round(avg_rel, 4),
                },
            },
            "performance": {
                "avg_latency_ms": round(avg_lat, 2),
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "estimated_cost_usd": est_cost,
            },
            "items": item_results,
        }

        # 7. Persist to DB if requested
        if save_to_db:
            init_db()
            db_owned = False
            db = self.db
            if db is None:
                db = SessionLocal()
                db_owned = True
            try:
                db_run = EvalRun(
                    id=run_id,
                    run_name=run_name,
                    created_at=created_at,
                    dataset_version="v1.0",
                    config_json=json.dumps(config),
                    total_queries=total_q,
                    passed_queries=passed_q,
                    mrr=round(avg_mrr, 4),
                    ndcg=round(avg_ndcg, 4),
                    recall_at_1=round(avg_r1, 4),
                    recall_at_3=round(avg_r3, 4),
                    recall_at_5=round(avg_r5, 4),
                    citation_precision=round(avg_cit_p, 4),
                    citation_recall=round(avg_cit_r, 4),
                    refusal_accuracy=round(avg_ref_acc, 4),
                    faithfulness=round(avg_faith, 4),
                    answer_relevance=round(avg_rel, 4),
                    avg_latency_ms=round(avg_lat, 2),
                    total_prompt_tokens=total_prompt_tokens,
                    total_completion_tokens=total_completion_tokens,
                    estimated_cost_usd=est_cost,
                )
                db.add(db_run)

                for item_res in item_results:
                    db_item = EvalItemResult(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        query_id=item_res["query_id"],
                        query=item_res["query"],
                        category=item_res["category"],
                        is_answerable=1 if item_res["is_answerable"] else 0,
                        is_correct_retrieval=1 if item_res["recall_at_5"] > 0 else 0,
                        is_correct_citation=1 if item_res["citation_recall"] > 0 else 0,
                        is_correct_refusal=1 if item_res["refusal_accuracy"] > 0 else 0,
                        reciprocal_rank=item_res["reciprocal_rank"],
                        retrieved_sources_json=json.dumps(item_res["retrieved_sources"]),
                        citations_json=json.dumps(item_res["citations"]),
                        generated_answer=item_res["generated_answer"],
                        latency_ms=item_res["latency_ms"],
                        prompt_tokens=item_res["tokens"]["prompt"],
                        completion_tokens=item_res["tokens"]["completion"],
                    )
                    db.add(db_item)

                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Warning: Failed to persist eval run to DB: {e}")
            finally:
                if db_owned:
                    db.close()

        return run_summary


def compare_runs(run_a: Dict[str, Any], run_b: Dict[str, Any]) -> Dict[str, Any]:
    """Compares two evaluation runs and computes metric deltas."""
    m_a = run_a["metrics"]["deterministic"]
    m_b = run_b["metrics"]["deterministic"]
    p_a = run_a["performance"]
    p_b = run_b["performance"]

    return {
        "run_a": {"id": run_a["id"], "name": run_a["run_name"]},
        "run_b": {"id": run_b["id"], "name": run_b["run_name"]},
        "deltas": {
            "mrr": round(m_b["mrr"] - m_a["mrr"], 4),
            "recall_at_1": round(m_b["recall_at_1"] - m_a["recall_at_1"], 4),
            "recall_at_3": round(m_b["recall_at_3"] - m_a["recall_at_3"], 4),
            "recall_at_5": round(m_b["recall_at_5"] - m_a["recall_at_5"], 4),
            "citation_recall": round(m_b["citation_recall"] - m_a["citation_recall"], 4),
            "refusal_accuracy": round(m_b["refusal_accuracy"] - m_a["refusal_accuracy"], 4),
            "latency_ms_delta": round(p_b["avg_latency_ms"] - p_a["avg_latency_ms"], 2),
            "cost_usd_delta": round(p_b["estimated_cost_usd"] - p_a["estimated_cost_usd"], 6),
        },
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="OmniRetrive Repeatable RAG Evaluation Runner")
    parser.add_argument("--name", type=str, default="cli_eval_run", help="Name for this evaluation run")
    parser.add_argument("--top-k", type=int, default=5, help="Number of chunks to retrieve")
    parser.add_argument("--no-hybrid", action="store_true", help="Disable hybrid search (use dense only)")
    parser.add_argument("--threshold", type=float, default=0.0, help="Similarity threshold cutoff")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Chunk size setting to log")
    parser.add_argument("--chunk-overlap", type=int, default=200, help="Chunk overlap setting to log")
    parser.add_argument("--no-save", action="store_true", help="Do not persist run to database")

    args = parser.parse_args()

    runner = EvalRunner()
    res = runner.run_evaluation(
        run_name=args.name,
        top_k=args.top_k,
        enable_hybrid=not args.no_hybrid,
        similarity_threshold=args.threshold,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        save_to_db=not args.no_save,
    )

    det = res["metrics"]["deterministic"]
    perf = res["performance"]

    print("\n" + "=" * 65)
    print(f" OmniRetrive Evaluation Run: {res['run_name']} ({res['id'][:8]})")
    print("=" * 65)
    print(f"Pass Rate:          {res['passed_queries']}/{res['total_queries']} ({res['pass_rate_pct']}%)")
    print(f"MRR:                {det['mrr']}")
    print(f"nDCG:               {det['ndcg']}")
    print(f"Recall@1:           {det['recall_at_1'] * 100}%")
    print(f"Recall@3:           {det['recall_at_3'] * 100}%")
    print(f"Recall@5:           {det['recall_at_5'] * 100}%")
    print(f"Citation Precision: {det['citation_precision'] * 100}%")
    print(f"Citation Recall:    {det['citation_recall'] * 100}%")
    print(f"Refusal Accuracy:   {det['refusal_accuracy'] * 100}%")
    print("-" * 65)
    print(f"Avg Latency:        {perf['avg_latency_ms']} ms")
    print(f"Total Tokens:       {perf['total_prompt_tokens'] + perf['total_completion_tokens']}")
    print(f"Est. Model Cost:    ${perf['estimated_cost_usd']:.6f}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
