import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from llama_index.readers.file import PDFReader
from llama_index.core.node_parser import SentenceSplitter

load_dotenv()
logger = logging.getLogger(__name__)

_hf_token = os.getenv("HF_TOKEN")
hf_client = InferenceClient(
    model="BAAI/bge-base-en-v1.5",
    token=_hf_token,
)

splitter = SentenceSplitter(chunk_size=1000, chunk_overlap=200)


def load_and_chunk_pdf(path: str) -> list[str]:
    """
    Extracts text from PDF and splits into chunks using SentenceSplitter.
    Raises RuntimeError with readable explanation on extraction failures.
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

    texts = [d.text for d in docs if getattr(d, "text", None)]
    if not texts:
        logger.warning(f"No extractable text found in PDF: {path}")
        return []

    chunks = []
    for t in texts:
        split_chunks = splitter.split_text(t)
        chunks.extend(split_chunks)

    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generates 768-dimensional embeddings for a list of text chunks
    using HuggingFace BAAI/bge-base-en-v1.5.
    """
    if not texts:
        return []

    try:
        embeddings = [hf_client.feature_extraction(t) for t in texts]
        return [e.tolist() if hasattr(e, "tolist") else e for e in embeddings]
    except Exception as e:
        logger.error(f"Failed to generate embeddings: {e}")
        raise RuntimeError(f"Embedding generation failed: {e}") from e