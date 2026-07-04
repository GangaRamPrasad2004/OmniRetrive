from huggingface_hub import InferenceClient
from pathlib import Path
from llama_index.readers.file import PDFReader
from llama_index.core.node_parser import SentenceSplitter
from dotenv import load_dotenv
import os

load_dotenv()

hf_client = InferenceClient(
    model="BAAI/bge-base-en-v1.5",
    token=os.environ["HF_TOKEN"],
)

splitter = SentenceSplitter(chunk_size=1000, chunk_overlap=200)

def load_and_chunk_pdf(path: str):
    docs = PDFReader().load_data(file=Path(path))
    texts = [d.text for d in docs if getattr(d, "text", None)]
    chunks = []
    for t in texts:
        chunks.extend(splitter.split_text(t))
    return chunks

def embed_texts(texts: list[str]) -> list[list[float]]:
    # feature_extraction returns embeddings for each input text
    embeddings = [hf_client.feature_extraction(t) for t in texts]
    return [e.tolist() if hasattr(e, "tolist") else e for e in embeddings]