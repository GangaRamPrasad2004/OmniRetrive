import os
import logging
import re
from pathlib import Path
from typing import List, Optional

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

# ---------------------------------------------------------------------------
# OCR availability check — performed once at module load time.
# Tesseract binary may not be installed; we degrade gracefully.
# ---------------------------------------------------------------------------
try:
    import pytesseract
    pytesseract.get_tesseract_version()  # raises TesseractNotFoundError if absent
    OCR_AVAILABLE = True
    logger.info("Tesseract binary detected — OCR fallback enabled.")
except Exception:
    OCR_AVAILABLE = False
    logger.info("Tesseract binary not found — OCR fallback disabled.")

# ---------------------------------------------------------------------------
# HuggingFace embedding client
# ---------------------------------------------------------------------------
_hf_token = os.getenv("HF_TOKEN")
hf_client = InferenceClient(
    model="BAAI/bge-base-en-v1.5",
    token=_hf_token,
    timeout=45,
)

splitter = SentenceSplitter(chunk_size=1000, chunk_overlap=200)

# ---------------------------------------------------------------------------
# Threshold: pages whose extracted text is shorter than this are considered
# scanned / image-only pages with no meaningful selectable text.
# ---------------------------------------------------------------------------
_SCANNED_PAGE_CHAR_THRESHOLD = 50


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class ExtractionError(Exception):
    """Raised when PDF text extraction fails at the document level."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _strip_outer_blank_lines(text: str) -> str:
    """Strip leading/trailing blank lines but preserve internal whitespace (tables, indentation)."""
    lines = text.split("\n")
    # Find first and last non-blank line
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines) - 1
    while end >= start and not lines[end].strip():
        end -= 1
    return "\n".join(lines[start : end + 1])


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


def _ocr_page(image) -> str:
    """Run Tesseract OCR on a single PIL image. Returns extracted text."""
    import pytesseract
    return pytesseract.image_to_string(image)


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

def load_and_chunk_pdf_with_metadata(
    path: str,
    max_pages: Optional[int] = None,
) -> List[dict]:
    """
    Smart OCR-fallback PDF extraction pipeline.

    1. Text-first extraction via LlamaIndex PDFReader.
    2. Scanned-page detection: pages with < 50 chars are considered image-only.
    3. OCR fallback (Tesseract) for scanned pages when OCR_AVAILABLE is True.
    4. Graceful degradation with placeholder chunks when OCR is unavailable.
    5. Partial-extraction reporting: warns when > 20% pages are empty.
    6. Large document streaming: existing incremental processing kept;
       optional max_pages safety cap.

    Args:
        path: Absolute or relative path to the PDF file.
        max_pages: If set, only process the first N pages.

    Returns:
        List of chunk dicts with keys:
            text, page_number, heading, chunk_index,
            ocr_fallback, ocr_attempted, extraction_method
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found at: {path}")

    # --- Load PDF ---
    try:
        reader = PDFReader()
        docs = reader.load_data(file=file_path)
    except Exception as e:
        logger.error(f"Failed to read PDF at {path}: {e}")
        raise ExtractionError(f"PDF text extraction failed for '{path}': {e}") from e

    if not docs:
        logger.warning(f"No pages or extractable data in PDF: {path}")
        return []

    # Apply max_pages cap
    if max_pages is not None:
        docs = docs[:max_pages]

    # --- Per-document stats ---
    pages_total = len(docs)
    pages_text_extracted = 0
    pages_ocr_fallback = 0
    pages_empty = 0

    chunks_with_meta: List[dict] = []
    current_heading: Optional[str] = None

    for page_idx, page_doc in enumerate(docs, start=1):
        page_text = getattr(page_doc, "text", "") or ""
        page_num = page_idx
        if hasattr(page_doc, "metadata") and page_doc.metadata:
            label = page_doc.metadata.get("page_label")
            if label and str(label).isdigit():
                page_num = int(label)

        is_scanned = len(page_text.strip()) < _SCANNED_PAGE_CHAR_THRESHOLD

        # --- Path 1: Page has usable text ---
        if not is_scanned:
            pages_text_extracted += 1
            detected_heading = _extract_heading(page_text)
            if detected_heading:
                current_heading = detected_heading

            # Preserve internal whitespace; only strip outer blank lines
            cleaned_page = _strip_outer_blank_lines(page_text)
            page_chunks = splitter.split_text(cleaned_page)
            for chunk_str in page_chunks:
                clean_chunk = _strip_outer_blank_lines(chunk_str)
                if not clean_chunk:
                    continue
                chunk_heading = _extract_heading(clean_chunk) or current_heading
                chunks_with_meta.append({
                    "text": clean_chunk,
                    "page_number": page_num,
                    "heading": chunk_heading,
                    "chunk_index": len(chunks_with_meta),
                    "ocr_fallback": False,
                    "ocr_attempted": False,
                    "extraction_method": "text",
                })
            continue

        # --- Path 2: Scanned page — attempt OCR if available ---
        if OCR_AVAILABLE:
            try:
                import pdf2image
                images = pdf2image.convert_from_path(str(file_path), first_page=page_idx, last_page=page_idx)
                if images:
                    ocr_text = _ocr_page(images[0])
                    ocr_text_clean = _strip_outer_blank_lines(ocr_text)
                    if ocr_text_clean:
                        pages_ocr_fallback += 1
                        detected_heading = _extract_heading(ocr_text_clean)
                        if detected_heading:
                            current_heading = detected_heading
                        ocr_chunks = splitter.split_text(ocr_text_clean)
                        for chunk_str in ocr_chunks:
                            clean_chunk = _strip_outer_blank_lines(chunk_str)
                            if not clean_chunk:
                                continue
                            chunk_heading = _extract_heading(clean_chunk) or current_heading
                            chunks_with_meta.append({
                                "text": clean_chunk,
                                "page_number": page_num,
                                "heading": chunk_heading,
                                "chunk_index": len(chunks_with_meta),
                                "ocr_fallback": True,
                                "ocr_attempted": True,
                                "extraction_method": "ocr",
                            })
                        continue
                    # OCR produced no usable text — fall through to placeholder
            except Exception as ocr_err:
                logger.warning(f"OCR failed on page {page_num} of '{path}': {ocr_err}")
            # OCR tried but produced nothing useful
            pages_empty += 1
            chunks_with_meta.append({
                "text": f"[Page {page_num}: No extractable text. OCR produced no output.]",
                "page_number": page_num,
                "heading": None,
                "chunk_index": len(chunks_with_meta),
                "ocr_fallback": False,
                "ocr_attempted": True,
                "extraction_method": "none",
            })
        else:
            # --- Path 3: OCR unavailable — placeholder ---
            pages_empty += 1
            chunks_with_meta.append({
                "text": f"[Page {page_num}: No extractable text. OCR unavailable.]",
                "page_number": page_num,
                "heading": None,
                "chunk_index": len(chunks_with_meta),
                "ocr_fallback": False,
                "ocr_attempted": False,
                "extraction_method": "none",
            })

    # --- Partial extraction reporting ---
    if pages_total > 0:
        empty_ratio = pages_empty / pages_total
        if empty_ratio > 0.20:
            logger.warning(
                f"Partial extraction warning for '{path}': "
                f"{pages_empty}/{pages_total} pages ({empty_ratio:.0%}) yielded no text. "
                f"pages_text_extracted={pages_text_extracted}, "
                f"pages_ocr_fallback={pages_ocr_fallback}."
            )

    # Near-identical chunk deduplication
    return deduplicate_chunks(chunks_with_meta)


def load_and_chunk_pdf(path: str) -> List[str]:
    """
    Preserved backward-compatible PDF extraction returning List[str].
    """
    rich_chunks = load_and_chunk_pdf_with_metadata(path)
    return [c["text"] for c in rich_chunks]


# ---------------------------------------------------------------------------
# Quality detection (no OCR, fast scan)
# ---------------------------------------------------------------------------

def detect_extraction_quality(path: str) -> dict:
    """
    Quickly scans the PDF using text extraction only and reports extraction
    quality metrics without performing OCR.

    Returns:
        {
          "pages_total": int,
          "pages_with_text": int,
          "pages_scanned": int,   # pages with < 50 chars of extracted text
          "ocr_recommended": bool,  # True if > 30% of pages are scanned
          "ocr_available": bool,
          "file_size_bytes": int,
        }
    """
    file_path = Path(path)
    file_size = file_path.stat().st_size if file_path.exists() else 0

    try:
        reader = PDFReader()
        docs = reader.load_data(file=file_path)
    except Exception as e:
        logger.error(f"detect_extraction_quality: failed to read '{path}': {e}")
        return {
            "pages_total": 0,
            "pages_with_text": 0,
            "pages_scanned": 0,
            "ocr_recommended": False,
            "ocr_available": OCR_AVAILABLE,
            "file_size_bytes": file_size,
        }

    pages_total = len(docs)
    pages_scanned = 0
    pages_with_text = 0

    for page_doc in docs:
        page_text = getattr(page_doc, "text", "") or ""
        if len(page_text.strip()) < _SCANNED_PAGE_CHAR_THRESHOLD:
            pages_scanned += 1
        else:
            pages_with_text += 1

    ocr_recommended = (pages_total > 0) and (pages_scanned / pages_total) > 0.30

    return {
        "pages_total": pages_total,
        "pages_with_text": pages_with_text,
        "pages_scanned": pages_scanned,
        "ocr_recommended": ocr_recommended,
        "ocr_available": OCR_AVAILABLE,
        "file_size_bytes": file_size,
    }


# ---------------------------------------------------------------------------
# Embedding pipeline (unchanged signatures)
# ---------------------------------------------------------------------------

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
    Records latency, chunk counts, and provider errors through the observability service.
    """
    if not texts:
        return []

    import time
    from telemetry_client import telemetry

    all_embeddings: List[List[float]] = []
    total = len(texts)
    start_time = time.perf_counter()

    try:
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
        dur_ms = (time.perf_counter() - start_time) * 1000
        telemetry.record_embedding(num_chunks=total, duration_ms=dur_ms, success=True)
    except Exception:
        dur_ms = (time.perf_counter() - start_time) * 1000
        telemetry.record_embedding(num_chunks=total, duration_ms=dur_ms, success=False)
        raise

    return all_embeddings
