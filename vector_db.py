import logging
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

    def upsert(self, ids, vectors, payloads):
        points = [
            PointStruct(id=ids[i], vector=vectors[i], payload=payloads[i])
            for i in range(len(ids))
        ]
        return self.client.upsert(self.collection, points=points)

    def search(self, query_vector, top_k: int = 5):
        results = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            with_payload=True,
            limit=top_k,
        )
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
        Ensures deletion removes derived vector data from Qdrant.
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
