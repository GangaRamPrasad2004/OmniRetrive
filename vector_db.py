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
    FilterSelector,
)
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, before_sleep_log

logger = logging.getLogger(__name__)


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

    def search(self, query_vector: List[float], top_k: int = 5, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Performs vector search in Qdrant.
        When workspace_id is provided, applies a strict tenant-isolation filter
        so vectors from other workspaces can never be retrieved.
        """
        query_filter = None
        if workspace_id:
            query_filter = Filter(
                must=[
                    FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))
                ]
            )

        try:
            results = self.client.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                with_payload=True,
                limit=top_k,
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return {"contexts": [], "sources": []}

        contexts = []
        sources = set()

        for r in results.points:
            payload = getattr(r, "payload", None) or {}
            text = payload.get("text", "")
            source = payload.get("source", "")
            if text:
                contexts.append(text)
                sources.add(source)
        return {"contexts": contexts, "sources": list(sources)}

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
