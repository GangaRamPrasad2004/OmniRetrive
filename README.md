# OmniRetrive — AI-Powered Document Intelligence Platform

OmniRetrive is an enterprise-grade document intelligence platform that ingests PDF documents, validates and processes them via automated pipelines, generates vector embeddings into Qdrant, and enables context-aware question answering with source citations.

---

## 🏛️ System Architecture

- **Backend**: FastAPI (Python 3.13) with Uvicorn.
- **Workflow & Background Tasks**: Inngest (`rag/ingest_pdf`, `rag/query_pdf_ai`).
- **Database & Persistence**: SQLAlchemy ORM with SQLite (default for local development) and seamless PostgreSQL support via `DATABASE_URL`.
- **Vector Database**: Qdrant (`docs_hf` collection, 768 dimensions, Cosine distance).
- **Text Extraction & Chunking**: LlamaIndex `PDFReader` and `SentenceSplitter(chunk_size=1000, chunk_overlap=200)`.
- **Embedding Model**: Hugging Face `BAAI/bge-base-en-v1.5` (768-d).
- **LLM Reasoning**: Hugging Face Router `meta-llama/Llama-3.1-8B-Instruct:novita`.
- **Frontend**: Streamlit dashboard with full document lifecycle management and interactive Q&A.

---

## 🚀 Feature 1: Complete Document Management

### 1. Robust File Validation & Security
- **MIME Type & Magic Byte Checking**: Verifies file begins with `%PDF-` and matches `application/pdf`.
- **Filename Sanitization**: Cleanses filenames, prevents directory traversal attacks, and strips dangerous characters.
- **Size Limits**: Configurable upload threshold via `MAX_UPLOAD_SIZE_MB` (default 50 MB).
- **Readability & Integrity**: Uses `pypdf` to verify the document has pages, is uncorrupted, and is not password-protected.

### 2. Stable Document Lifecycle & Job States
- **Stable Document IDs**: Generates UUIDv4 for document entities and stores files safely with namespaced prefixes.
- **Job States**: Tracks states through `queued`, `processing`, `completed`, `failed`, and `retrying`.
- **Auditing & Metadata**: Stores filename, SHA-256 hash, size, MIME type, storage path, workspace/owner, version, page count, chunk count, error details, and timestamps.
- **Question-Answering Readiness**: Computes `is_ready_for_qa` dynamically (`status == "completed"` and `chunk_count > 0`).

### 3. Content Deduplication
- Calculates a SHA-256 checksum on upload.
- If a document with identical content has already completed processing in the workspace, OmniRetrive skips redundant re-ingestion and returns the existing indexed document.
- If identical content is actively processing, redundant duplicate jobs are prevented.

### 4. Safe Job Retry & Partial Failure Handling
- Errors at any stage (text extraction, embedding generation, Qdrant indexing) are captured cleanly, marking the document as `failed` with descriptive error details.
- Documents are never falsely marked as `completed`.
- Users can trigger a safe retry via `POST /documents/{id}/retry` or through the dashboard UI.
- Concurrent retries on already processing documents are prevented with HTTP 409 Conflict.

### 5. Document Deletion & Derived Vector Cleanup
- `DELETE /documents/{id}` removes:
  1. Associated vector points from Qdrant by matching payload `source` and `document_id`.
  2. The physical PDF file from disk.
  3. The database record and cascaded job history.

---

## 📡 API Endpoints

### Health Check
- `GET /health` — Verifies backend service status.

### Document Management
- `POST /upload`
  - Multipart form upload (`file: UploadFile`, `workspace_id: str`, `owner_id: str`).
  - Validates PDF, checks duplicates, creates metadata record, and queues Inngest ingestion.
- `GET /documents`
  - Query parameters:
    - `search`: Filter by filename substring (case-insensitive).
    - `status`: Filter by status (`queued`, `processing`, `completed`, `failed`, `retrying`).
    - `workspace_id`: Filter by workspace.
    - `sort_by`: Sort column (`created_at`, `filename`, `file_size`, `status`).
    - `sort_order`: `asc` or `desc`.
- `GET /documents/{document_id}`
  - Returns complete document metadata, chunk/page metrics, QA readiness, and job audit history.
- `POST /documents/{document_id}/retry`
  - Safely re-queues ingestion for a failed document.
- `DELETE /documents/{document_id}`
  - Deletes database record, local file, and vector embeddings in Qdrant.

### Inngest Workflow Handlers
- `POST /api/inngest` — Inngest execution endpoint serving `rag_ingest_pdf` and `rag_query_pdf_ai`.

---

## ⚙️ Environment Variables

Create or configure your `.env` file:

```env
# Hugging Face & Qdrant Configuration
HF_TOKEN=hf_your_token_here
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_key_if_applicable

# Backend Configuration
UPLOADS_DIR=uploads
MAX_UPLOAD_SIZE_MB=50
DATABASE_URL=sqlite:///./omniretrive.db
IS_PRODUCTION=false

# Streamlit Frontend Configuration
BACKEND_URL=http://localhost:8000
INNGEST_API_BASE=http://127.0.0.1:8288/v1
INNGEST_EVENT_KEY=
INNGEST_SIGNING_KEY=
```

---

## 🧪 Running the Automated Test Suite

Execute the comprehensive test suite verifying validation, deduplication, retry, deletion, and API routes:

```powershell
.venv\Scripts\python.exe -m pytest -v tests/test_document_management.py
```

---

## 🐳 Running with Docker

### Backend:
```bash
docker build -f Dockerfile.backend -t omniretrive-backend .
docker run -p 8000:8000 --env-file .env omniretrive-backend
```

### Frontend:
```bash
docker build -f Dockerfile.frontend -t omniretrive-frontend .
docker run -p 8501:8501 --env-file .env omniretrive-frontend
```
