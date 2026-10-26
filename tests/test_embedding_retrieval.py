'''python
"""Tests for embedding and retrieval adapters.

These tests mock the Qdrant storage layer to avoid external dependencies.
"""

import pytest
from unittest.mock import MagicMock, patch

# Import the functions/classes under test. Adjust the import path according to the actual project layout.
# Assuming the embedding functions are in ai/embedding.py and the storage class in vector_db/qdrant_storage.py
from ai.embedding import embed_texts, upsert_documents, search_documents
from vector_db.qdrant_storage import QdrantStorage

@pytest.fixture
def fake_qdrant():
    """Return a mock QdrantStorage instance with in‑memory behavior."""
    storage = MagicMock(spec=QdrantStorage)
    # In‑memory dict to hold vectors keyed by document id
    storage._vectors = {}

    def upsert(ids, vectors, payloads=None, **kwargs):
        for doc_id, vec in zip(ids, vectors):
            storage._vectors[doc_id] = {"vector": vec, "payload": payloads.get(doc_id) if payloads else None}
        return True

    def search(query_vec, top_k=5, filter=None, **kwargs):
        # Very simple Euclidean distance search over stored vectors
        results = []
        for doc_id, data in storage._vectors.items():
            # Dummy distance calculation (not real)
            distance = sum((a - b) ** 2 for a, b in zip(query_vec, data["vector"]))
            results.append({"id": doc_id, "distance": distance, "payload": data["payload"]})
        # sort by distance ascending and return top_k
        results.sort(key=lambda r: r["distance"])
        return results[:top_k]

    storage.upsert.side_effect = upsert
    storage.search.side_effect = search
    return storage

def test_embed_texts_returns_correct_shape():
    # Mock the OpenAI embedding call inside embed_texts
    with patch("ai.embedding.openai_embeddings") as mock_embed:
        mock_embed.return_value = [[0.1] * 1536 for _ in range(2)]  # assume 1536‑dim vectors
        vectors = embed_texts(["hello world", "test sentence"])
        assert len(vectors) == 2
        assert all(len(v) == 1536 for v in vectors)

def test_upsert_and_search_workflow(fake_qdrant):
    docs = [
        {"id": "doc1", "text": "The quick brown fox"},
        {"id": "doc2", "text": "Jumps over the lazy dog"},
    ]
    # Mock embedding generation
    with patch("ai.embedding.embed_texts") as mock_embed:
        mock_embed.return_value = [[0.2] * 1536, [0.4] * 1536]
        vectors = embed_texts([d["text"] for d in docs])
        # Upsert using the fake storage
        upsert_documents(fake_qdrant, [d["id"] for d in docs], vectors, payloads={d["id"]: d for d in docs})
        # Prepare a query vector (mid‑point between the two)
        query_vec = [0.3] * 1536
        results = search_documents(fake_qdrant, query_vec, top_k=2)
        # Expect both documents returned, ordered by distance (doc1 first, then doc2)
        assert len(results) == 2
        assert results[0]["id"] == "doc1"
        assert results[1]["id"] == "doc2"
        # Payload should match original document dicts
        assert results[0]["payload"]["text"] == "The quick brown fox"
        assert results[1]["payload"]["text"] == "Jumps over the lazy dog"
'''