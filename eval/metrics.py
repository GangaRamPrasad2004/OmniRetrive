"""
Evaluation Metrics Engine for OmniRetrive RAG Pipeline.
Strictly distinguishes deterministic ground-truth metrics from model-based / heuristic metrics.
"""

import math
import re
from typing import List, Dict, Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# 1. DETERMINISTIC RETRIEVAL & CITATION METRICS (Mathematical / Ground Truth)
# ─────────────────────────────────────────────────────────────────────────────

def _is_passage_match(chunk_text: str, expected_passage: Optional[str], threshold: float = 0.60) -> bool:
    """Checks if a retrieved chunk contains or matches the expected passage."""
    if not expected_passage or not chunk_text:
        return False
    # Check exact substring containment
    clean_expected = expected_passage.strip().lower()
    clean_chunk = chunk_text.strip().lower()
    if clean_expected in clean_chunk or clean_chunk in clean_expected:
        return True

    # Check Jaccard word token overlap
    exp_words = set(re.findall(r"\w+", clean_expected))
    chunk_words = set(re.findall(r"\w+", clean_chunk))
    if not exp_words:
        return False
    overlap = len(exp_words & chunk_words) / len(exp_words)
    return overlap >= threshold


def calculate_recall_at_k(
    retrieved_chunks: List[Dict[str, Any]],
    expected_doc: Optional[str],
    expected_passage: Optional[str],
    k: int = 3,
) -> float:
    """
    Deterministic Recall@k:
    Returns 1.0 if any chunk in retrieved_chunks[:k] matches the expected document
    and expected passage, 0.0 otherwise.
    """
    if not expected_doc and not expected_passage:
        return 1.0  # unanswerable query has no expected passage to retrieve

    top_chunks = retrieved_chunks[:k]
    for c in top_chunks:
        doc_name = c.get("document_name") or c.get("source") or ""
        text = c.get("relevant_text") or c.get("text") or ""
        doc_match = (expected_doc.lower() in doc_name.lower()) if expected_doc else True
        passage_match = _is_passage_match(text, expected_passage) if expected_passage else True
        if doc_match and passage_match:
            return 1.0
    return 0.0


def calculate_mrr(
    retrieved_chunks: List[Dict[str, Any]],
    expected_doc: Optional[str],
    expected_passage: Optional[str],
    k: int = 5,
) -> float:
    """
    Deterministic Mean Reciprocal Rank (MRR):
    Returns 1.0 / rank of the first relevant chunk found within the top-k results.
    Returns 0.0 if not found within top-k.
    """
    if not expected_doc and not expected_passage:
        return 1.0

    for rank, c in enumerate(retrieved_chunks[:k], start=1):
        doc_name = c.get("document_name") or c.get("source") or ""
        text = c.get("relevant_text") or c.get("text") or ""
        doc_match = (expected_doc.lower() in doc_name.lower()) if expected_doc else True
        passage_match = _is_passage_match(text, expected_passage) if expected_passage else True
        if doc_match and passage_match:
            return 1.0 / rank
    return 0.0


def calculate_ndcg(
    retrieved_chunks: List[Dict[str, Any]],
    expected_doc: Optional[str],
    expected_passage: Optional[str],
    k: int = 5,
) -> float:
    """
    Deterministic nDCG@k:
    Calculates Normalized Discounted Cumulative Gain for binary relevance.
    """
    if not expected_doc and not expected_passage:
        return 1.0

    dcg = 0.0
    for rank, c in enumerate(retrieved_chunks[:k], start=1):
        doc_name = c.get("document_name") or c.get("source") or ""
        text = c.get("relevant_text") or c.get("text") or ""
        doc_match = (expected_doc.lower() in doc_name.lower()) if expected_doc else True
        passage_match = _is_passage_match(text, expected_passage) if expected_passage else True
        rel = 1.0 if (doc_match and passage_match) else 0.0
        if rel > 0:
            dcg += rel / math.log2(rank + 1)
            break  # Single primary relevant target

    idcg = 1.0 / math.log2(2)  # Ideal DCG when relevant item is at rank 1
    return round(dcg / idcg, 4)


def calculate_citation_correctness(
    citations: List[Dict[str, Any]],
    expected_doc: Optional[str],
    expected_page: Optional[int],
) -> Dict[str, float]:
    """
    Deterministic Citation Verification:
    - precision: proportion of returned citations that match the expected document and page.
    - recall: whether at least one returned citation points to the expected document and page.
    """
    if not expected_doc:
        # Negative control: no citations should be returned
        return {"precision": 1.0 if len(citations) == 0 else 0.0, "recall": 1.0 if len(citations) == 0 else 0.0}

    if not citations:
        return {"precision": 0.0, "recall": 0.0}

    correct_citations = 0
    for cit in citations:
        doc_name = cit.get("document_name", "")
        page = cit.get("page_number")
        doc_matches = expected_doc.lower() in doc_name.lower()
        page_matches = (expected_page is None) or (page == expected_page)
        if doc_matches and page_matches:
            correct_citations += 1

    precision = correct_citations / len(citations)
    recall = 1.0 if correct_citations > 0 else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4)}


def calculate_refusal_accuracy(
    answer: str,
    is_answerable: bool,
    insufficient_context_flag: bool,
) -> float:
    """
    Deterministic Refusal Accuracy:
    Verifies that unanswerable queries are correctly refused with an insufficient-context
    response rather than fabricating hallucinations, and answerable queries are not falsely refused.
    """
    lower_ans = answer.lower()
    is_refused = insufficient_context_flag or ("insufficient" in lower_ans and "information" in lower_ans)

    if not is_answerable:
        # Expected: Refusal
        return 1.0 if is_refused else 0.0
    else:
        # Expected: Not Refused
        return 1.0 if not is_refused else 0.0


def estimate_token_cost(
    prompt_tokens: int,
    completion_tokens: int,
    prompt_price_per_1k: float = 0.00015,
    completion_price_per_1k: float = 0.0006,
) -> float:
    """Deterministic token usage cost estimation."""
    cost = (prompt_tokens / 1000.0) * prompt_price_per_1k + (completion_tokens / 1000.0) * completion_price_per_1k
    return round(cost, 6)


# ─────────────────────────────────────────────────────────────────────────────
# 2. MODEL-BASED / HEURISTIC METRICS (Tagged explicitly as non-deterministic)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_faithfulness(
    answer: str,
    retrieved_contexts: List[str],
) -> Dict[str, Any]:
    """
    Heuristic Faithfulness Score:
    Measures the fraction of key informative terms in the generated answer
    that are grounded in the retrieved context passages.
    Tagged explicitly with model_based=True.
    """
    if not answer or not retrieved_contexts:
        return {"score": 0.0, "model_based": True, "description": "Heuristic token-level grounding"}

    # Extract substantive words from answer
    answer_tokens = [w for w in re.findall(r"\w+", answer.lower()) if len(w) > 3]
    if not answer_tokens:
        return {"score": 1.0, "model_based": True, "description": "Heuristic token-level grounding"}

    combined_context = " ".join(retrieved_contexts).lower()
    grounded_count = sum(1 for t in answer_tokens if t in combined_context)
    score = grounded_count / len(answer_tokens)

    return {
        "score": round(score, 4),
        "model_based": True,
        "description": "Heuristic token-level grounding against context",
    }


def calculate_answer_relevance(
    answer: str,
    query: str,
) -> Dict[str, Any]:
    """
    Heuristic Answer Relevance Score:
    Measures topical overlap between the question terms and the response terms.
    Tagged explicitly with model_based=True.
    """
    if not answer or not query:
        return {"score": 0.0, "model_based": True, "description": "Lexical term overlap"}

    query_tokens = set(w for w in re.findall(r"\w+", query.lower()) if len(w) > 3)
    if not query_tokens:
        return {"score": 1.0, "model_based": True, "description": "Lexical term overlap"}

    ans_text = answer.lower()
    overlap = sum(1 for t in query_tokens if t in ans_text)
    score = overlap / len(query_tokens)

    return {
        "score": round(score, 4),
        "model_based": True,
        "description": "Lexical term overlap with query",
    }
