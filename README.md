# OmniRetrive — AI-Powered Document Intelligence Platform

OmniRetrive is an enterprise-grade document intelligence platform that ingests PDF documents, validates and processes them via automated pipelines, generates vector embeddings into Qdrant, and enables context-aware question answering with source citations.

---

## 🏛️ System Architecture

- **Application Gateway & Auth Backend**: Go (Chi v5) handling user authentication, JWT token issuance, workspace management, RBAC, and rate limiting (`gateway/`).
- **AI Service & RAG Engine**: FastAPI (Python 3.13) handling document parsing, chunking, Hugging Face embeddings, Inngest jobs, and tenant-filtered Qdrant retrieval (`main.py`).
- **Workflow & Background Tasks**: Inngest (`rag/ingest_pdf`, `rag/query_pdf_ai`).
- **Databases & Persistence**:
  - **Gateway DB**: SQLite / PostgreSQL storing users, workspaces, and memberships.
  - **AI Metadata DB**: SQLAlchemy storing documents, hashes, and ingestion job history.
- **Vector Database**: Qdrant (`docs_hf` collection, 768 dimensions, Cosine distance) with strict `workspace_id` tenant filtering.
- **Frontend**: Streamlit dashboard with authentication, workspace switching, document lifecycle management, and tenant-isolated Q&A.

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

## 🛡️ Feature 2: Authentication and Multi-Tenant Isolation

### 1. User Authentication & Token Management
- **Registration & Login**: Secure account creation with email format validation and password length checks.
- **Password Security**: Passwords hashed using `bcrypt` with work factor 12.
- **Token Management**: Issues signed HMAC-SHA256 JWT bearer tokens containing subject `user_id` and `email` with 24-hour expiration.

### 2. Workspace Management & Role-Based Access Control (RBAC)
- **Automatic Workspace Provisioning**: Each registered user is automatically allocated a personal default workspace where they are assigned the `owner` role.
- **Multi-Tenant Roles**:
  - `owner`: Full control over the workspace, ability to delete the workspace, and ability to manage admins and members.
  - `admin`: Can upload, delete, retry documents, and invite or remove members.
  - `member`: Can view workspace documents, upload PDFs, and query documents.
- **Server-Side Ownership Validation**: Client-supplied tenant IDs are never trusted. All operations derive tenancy and user IDs strictly from the authenticated JWT context and verified workspace memberships.

### 3. Tenant Isolation as a Vector Database Security Boundary
- Vector payloads in Qdrant store the verified `workspace_id`.
- Retrieval (`POST /api/workspaces/{id}/query` and `/query`) **strictly applies a mandatory Qdrant payload filter** on `workspace_id`.
- Foreign tenant vectors are physically excluded from vector distance calculations. Retrieval authorization is enforced by the vector database engine, never by an LLM prompt.

### 4. Cascade Cleanup Without Orphaned Data
- Workspace deletion via `DELETE /api/workspaces/{id}`:
  1. Cascade deletes all workspace memberships from the Gateway DB.
  2. Calls `/workspaces/{id}/cleanup` on the AI service to purge all physical PDF files from disk.
  3. Executes `delete_by_workspace` on Qdrant, purging all derived vector points.
  4. Deletes all document records from the AI service database.

### 5. Rate Limiting
- IP-based token-bucket rate limiting applied to sensitive endpoints (e.g. `/api/auth/register`, `/api/auth/login`, and `/api/workspaces/{id}/documents/upload`).

---

## 📡 API Endpoints

### Go Gateway (`http://localhost:8080`)
- `POST /api/auth/register` — Register a new user account and provision default workspace.
- `POST /api/auth/login` — Authenticate credentials and receive JWT token.
- `GET /api/auth/me` — Get authenticated user details and active workspaces.
- `POST /api/workspaces` — Create a new workspace.
- `GET /api/workspaces` — List workspaces the authenticated user belongs to.
- `GET /api/workspaces/{workspaceId}` — Get workspace details and member list.
- `DELETE /api/workspaces/{workspaceId}` — Delete workspace and purge all tenant data (owner only).
- `POST /api/workspaces/{workspaceId}/members` — Invite/add member (owner or admin only).
- `DELETE /api/workspaces/{workspaceId}/members/{userId}` — Remove member (owner or admin only).
- `POST /api/workspaces/{workspaceId}/documents/upload` — Upload and ingest PDF into workspace.
- `GET /api/workspaces/{workspaceId}/documents` — List workspace documents.
- `GET /api/workspaces/{workspaceId}/documents/{docId}` — Get workspace document details.
- `POST /api/workspaces/{workspaceId}/documents/{docId}/retry` — Retry failed document ingestion.
- `DELETE /api/workspaces/{workspaceId}/documents/{docId}` — Delete document and vectors (admin/owner).
- `POST /api/workspaces/{workspaceId}/query` — Multi-tenant vector retrieval and question answering.

---

## 🧪 Automated Test Suites

### Run Go Gateway Tests (Auth, RBAC, Multi-Tenant Isolation, Rate Limiting):
```powershell
cd gateway
go test -v ./...
```

### Run Python AI Service Tests (Document Management & Vector Tenant Isolation):
```powershell
.venv\Scripts\python.exe -m pytest -v tests/
```

---

## 🐳 Docker Deployment

Run all services together with Docker Compose:
```bash
docker-compose up --build
```
* **Frontend**: `http://localhost:8501`
* **Go Gateway**: `http://localhost:8080`
* **Python AI Backend**: `http://localhost:8000`
* **Qdrant Vector DB**: `http://localhost:6333`
