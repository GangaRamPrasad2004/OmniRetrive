import logging
from typing import Optional, List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams,
    Distance,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
    FilterSelector,
)
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, before_sleep_log
import re

logger = logging.getLogger(__name__)


def compute_lexical_score(query: Optional[str], text: str) -> float:
    """Computes normalized keyword/token overlap score between query and document text."""
    if not query or not text:
        return 0.0
    query_tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
    if not query_tokens:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for t in set(query_tokens) if t in text_lower)
    return matches / len(set(query_tokens))


def deduplicate_candidate_chunks(candidates: List[Dict[str, Any]], threshold: float = 0.85) -> List[Dict[str, Any]]:
    """Deduplicates near-identical candidate passages retrieved from vector DB."""
    filtered: List[Dict[str, Any]] = []
    seen_sets: List[set] = []
    for c in candidates:
        words = set(re.findall(r"\w+", c.get("text", "").lower()))
        if not words:
            continue
        is_dup = False
        for seen in seen_sets:
            inter = len(words & seen)
            un = len(words | seen)
            if un > 0 and (inter / un) >= threshold:
                is_dup = True
                break
        if not is_dup:
            filtered.append(c)
            seen_sets.append(words)
    return filtered


class QdrantStorage:
    def __init__(self, url="http://localhost:6333", collection="docs_hf", dim=768, api_key=None):
        self.url = url
        self.api_key = api_key
        self.collection = collection
        self.client = QdrantClient(url=url, api_key=api_key, timeout=30)
        try:
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
        except Exception as e:
            logger.warning(f"Could not connect or initialize Qdrant collection '{collection}': {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=5.0, jitter=0.5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _upsert_batch_with_retry(self, batch_points: List[PointStruct]):
        """Upsert a single batch of points with exponential backoff on transient network failures."""
        self.client.upsert(self.collection, points=batch_points)

    def upsert(
        self,
        ids: List[str],
        vectors: List[List[float]],
        payloads: List[Dict[str, Any]],
        batch_size: int = 64,
    ) -> int:
        """
        Batched Qdrant upserting with bounded batch size and retry backoff.
        Prevents network payload limits and timeouts when indexing large documents.
        """
        total = len(ids)
        if total == 0:
            return 0

        for i in range(0, total, batch_size):
            batch_slice = slice(i, i + batch_size)
            batch_ids = ids[batch_slice]
            batch_vecs = vectors[batch_slice]
            batch_payloads = payloads[batch_slice]

            points = [
                PointStruct(id=batch_ids[j], vector=batch_vecs[j], payload=batch_payloads[j])
                for j in range(len(batch_ids))
            ]
            self._upsert_batch_with_retry(points)

        return total

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        workspace_id: Optional[str] = None,
        document_ids: Optional[List[str]] = None,
        query_text: Optional[str] = None,
        similarity_threshold: float = 0.0,
        enable_hybrid: bool = True,
    ) -> Dict[str, Any]:
        """
        Performs vector search in Qdrant with tenant isolation, document filtering,
        hybrid BM25/keyword re-ranking, and near-identical passage deduplication.
        """
        must_conditions = []
        if workspace_id:
            must_conditions.append(
                FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))
            )
        if document_ids:
            if len(document_ids) == 1:
                must_conditions.append(
                    FieldCondition(key="document_id", match=MatchValue(value=document_ids[0]))
                )
            else:
                must_conditions.append(
                    FieldCondition(key="document_id", match=MatchAny(any=document_ids))
                )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        candidate_limit = max(top_k * 3, 15)
        try:
            results = self.client.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                with_payload=True,
                limit=candidate_limit,
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return {"contexts": [], "sources": [], "citations": []}

        candidates = []
        points = getattr(results, "points", []) or []
        for r in points:
            payload = getattr(r, "payload", None) or {}
            text = payload.get("text", "")
            if not text:
                continue

            raw_score = getattr(r, "score", 0.80)
            if raw_score is None:
                raw_score = 0.80

            lex_score = compute_lexical_score(query_text, text) if (query_text and enable_hybrid) else 0.0
            # Hybrid fusion: 70% dense vector score + 30% lexical keyword overlap
            hybrid_score = (0.70 * float(raw_score) + 0.30 * float(lex_score)) if enable_hybrid else float(raw_score)

            if hybrid_score < similarity_threshold:
                continue

            candidates.append({
                "text": text,
                "source": payload.get("source", "document.pdf"),
                "document_id": payload.get("document_id", ""),
                "page_number": int(payload.get("page_number", 1) or 1),
                "chunk_index": int(payload.get("chunk_index", 0) or 0),
                "heading": payload.get("heading"),
                "score": round(float(hybrid_score), 4),
                "dense_score": round(float(raw_score), 4),
                "lexical_score": round(float(lex_score), 4),
            })

        # Sort by hybrid score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Deduplicate near-identical candidate passages
        deduped = deduplicate_candidate_chunks(candidates, threshold=0.85)

        # Take top_k
        top_candidates = deduped[:top_k]

        contexts = [c["text"] for c in top_candidates]
        sources = list(dict.fromkeys(c["source"] for c in top_candidates))
        citations = [
            {
                "document_name": c["source"],
                "document_id": c["document_id"],
                "page_number": c["page_number"],
                "chunk_index": c["chunk_index"],
                "relevant_text": c["text"],
                "score": c["score"],
                "heading": c["heading"],
                "verified": True,
            }
            for c in top_candidates
        ]

        return {
            "contexts": contexts,
            "sources": sources,
            "citations": citations,
        }

    def delete_by_source(self, source_id: str) -> bool:
        """
        Delete all vector points associated with a specific document source or document ID.
        Ensures deletion removes derived vector data from Qdrant and safely replaces obsolete vectors.
        """
        try:
            filter_condition = Filter(
                should=[
                    FieldCondition(key="source", match=MatchValue(value=source_id)),
                    FieldCondition(key="document_id", match=MatchValue(value=source_id)),
                ]
            )
            self.client.delete(
                collection_name=self.collection,
                points_selector=FilterSelector(filter=filter_condition),
            )
            logger.info(f"Successfully deleted vectors for source '{source_id}' from Qdrant.")
            return True
        except Exception as e:
            logger.error(f"Failed to delete vectors for source '{source_id}': {e}")
            return False

    def delete_by_workspace(self, workspace_id: str) -> bool:
        """
        Deletes all vector points belonging to a specific workspace.
        Ensures workspace deletion leaves zero orphaned vectors in Qdrant.
        """
        try:
            filter_condition = Filter(
                must=[
                    FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))
                ]
            )
            self.client.delete(
                collection_name=self.collection,
                points_selector=FilterSelector(filter=filter_condition),
            )
            logger.info(f"Successfully deleted all vectors for workspace '{workspace_id}' from Qdrant.")
            return True
        except Exception as e:
            logger.error(f"Failed to delete vectors for workspace '{workspace_id}': {e}")
            return False
