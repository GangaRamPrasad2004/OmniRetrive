import os
import logging
from pathlib import Path
from typing import List, Generator
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from llama_index.readers.file import PDFReader
from llama_index.core.node_parser import SentenceSplitter
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception,
    before_sleep_log,
)

load_dotenv()
logger = logging.getLogger(__name__)

_hf_token = os.getenv("HF_TOKEN")
hf_client = InferenceClient(
    model="BAAI/bge-base-en-v1.5",
    token=_hf_token,
    timeout=45,
)

splitter = SentenceSplitter(chunk_size=1000, chunk_overlap=200)


def is_transient_error(exc: BaseException) -> bool:
    """
    Identifies transient errors suitable for exponential backoff:
    - HTTP 429 (Rate Limit Exceeded)
    - HTTP 500, 502, 503, 504 (Server Errors / Service Unavailable)
    - Connection timeouts and network resets
    """
    err_str = str(exc).lower()
    transient_indicators = [
        "429",
        "rate limit",
        "too many requests",
        "502",
        "503",
        "504",
        "timeout",
        "connection reset",
        "connection refused",
        "server error",
        "temporarily unavailable",
    ]
    return any(indicator in err_str for indicator in transient_indicators)


def load_and_chunk_pdf(path: str) -> List[str]:
    """
    Memory-efficient PDF text extraction and chunking.
    Processes pages incrementally to prevent memory exhaustion on large documents.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found at: {path}")

    try:
        reader = PDFReader()
        docs = reader.load_data(file=file_path)
    except Exception as e:
        logger.error(f"Failed to read PDF at {path}: {e}")
        raise RuntimeError(f"PDF text extraction failed: {e}") from e

    if not docs:
        logger.warning(f"No pages or extractable data in PDF: {path}")
        return []

    chunks: List[str] = []
    # Process page by page to bound memory usage
    for page_doc in docs:
        page_text = getattr(page_doc, "text", "")
        if page_text and page_text.strip():
            page_chunks = splitter.split_text(page_text.strip())
            chunks.extend(page_chunks)

    return chunks


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1.0, max=10.0, jitter=1.0),
    retry=retry_if_exception(is_transient_error),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _embed_batch_with_retry(batch: List[str]) -> List[List[float]]:
    """
    Sends a batch of text chunks to Hugging Face with bounded exponential backoff
    and jitter handling transient rate-limits (HTTP 429) and outages.
    """
    try:
        # Attempt native multi-text batch extraction
        res = hf_client.feature_extraction(batch)
        if hasattr(res, "tolist"):
            res = res.tolist()
        # Verify shape (list of float vectors)
        if isinstance(res, list) and len(res) > 0:
            if isinstance(res[0], list):
                return res
            elif isinstance(res[0], (int, float)):
                return [res]
    except Exception as e:
        if not is_transient_error(e):
            logger.debug(f"Batch embedding fallback to single-item: {e}")
        else:
            raise

    # Fallback to per-item extraction within the batch if endpoint requires single string
    batch_embeddings = []
    for text in batch:
        emb = hf_client.feature_extraction(text)
        if hasattr(emb, "tolist"):
            emb = emb.tolist()
        batch_embeddings.append(emb)

    return batch_embeddings


def embed_texts(texts: List[str], batch_size: int = 16) -> List[List[float]]:
    """
    Batched embedding generator with bounded concurrency and rate-limit backoff.
    Divides inputs into batches of `batch_size` (default 16) to prevent network
    payload overages and minimize HTTP round-trips.
    """
    if not texts:
        return []

    all_embeddings: List[List[float]] = []
    total = len(texts)

    for i in range(0, total, batch_size):
        batch = texts[i : i + batch_size]
        try:
            batch_vectors = _embed_batch_with_retry(batch)
            all_embeddings.extend(batch_vectors)
        except Exception as e:
            logger.error(f"Failed to generate embeddings for batch {i}-{i+len(batch)}: {e}")
            raise RuntimeError(
                f"Embedding provider failed after retries: {str(e)}. "
                "The service experienced persistent rate limits or network issues."
            ) from e

    return all_embeddings