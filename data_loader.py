import os
import logging
from pathlib import Path
from typing import List, Generator, Optional, Dict, Any
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


import re


def _extract_heading(text: str) -> Optional[str]:
    """Extracts markdown heading or uppercase section title from the beginning of a chunk/page."""
    lines = text.strip().split("\n")
    for line in lines[:3]:
        clean = line.strip()
        if not clean:
            continue
        if clean.startswith("#"):
            return clean.lstrip("#").strip()
        if len(clean) < 80 and (clean.isupper() or clean.endswith(":")):
            return clean.rstrip(":")
    return None


def deduplicate_chunks(chunks_with_meta: List[dict], threshold: float = 0.75) -> List[dict]:
    """
    Deduplicates near-identical chunks (e.g. repeated page headers, footers, boilerplate disclaimers).
    Ignores single page-number digits so running headers across pages are recognized as duplicates.
    """
    unique: List[dict] = []
    seen_sets: List[set] = []

    for c in chunks_with_meta:
        # Ignore pure digits like varying page numbers in running headers/footers
        words = set(t for t in re.findall(r"\w+", c.get("text", "").lower()) if not t.isdigit())
        if not words:
            continue
        is_dup = False
        for seen in seen_sets:
            intersection = len(words & seen)
            union = len(words | seen)
            if union > 0 and (intersection / union) >= threshold:
                is_dup = True
                break
        if not is_dup:
            unique.append(c)
            seen_sets.append(words)

    # Re-index chunks consecutively
    for i, item in enumerate(unique):
        item["chunk_index"] = i

    return unique


def load_and_chunk_pdf_with_metadata(path: str) -> List[dict]:
    """
    Memory-efficient PDF text extraction, heading detection, and chunking with page tracking.
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

    chunks_with_meta: List[dict] = []
    current_heading = None

    for page_idx, page_doc in enumerate(docs, start=1):
        page_text = getattr(page_doc, "text", "")
        page_num = page_idx
        if hasattr(page_doc, "metadata") and page_doc.metadata:
            label = page_doc.metadata.get("page_label")
            if label and str(label).isdigit():
                page_num = int(label)

        if page_text and page_text.strip():
            detected_heading = _extract_heading(page_text)
            if detected_heading:
                current_heading = detected_heading

            page_chunks = splitter.split_text(page_text.strip())
            for chunk_str in page_chunks:
                clean_chunk = chunk_str.strip()
                if not clean_chunk:
                    continue
                chunk_heading = _extract_heading(clean_chunk) or current_heading
                chunks_with_meta.append({
                    "text": clean_chunk,
                    "page_number": page_num,
                    "heading": chunk_heading,
                    "chunk_index": len(chunks_with_meta),
                })

    # Near-identical chunk deduplication (boilerplate, repeated headers/footers)
    return deduplicate_chunks(chunks_with_meta)


def load_and_chunk_pdf(path: str) -> List[str]:
    """
    Preserved backward-compatible PDF extraction returning List[str].
    """
    rich_chunks = load_and_chunk_pdf_with_metadata(path)
    return [c["text"] for c in rich_chunks]


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