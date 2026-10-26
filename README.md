# OmniRetrive — AI‑Powered Document Intelligence Platform

## 📖 Project Overview & Real‑World Problem
OmniRetrive is a document‑centric AI platform that helps organisations extract knowledge from collections of PDF files (manuals, contracts, research papers, etc.) and expose that knowledge through a context‑aware question‑answering (RAG) service with source citations. Many enterprises still store critical information in static PDFs, making it difficult for employees to locate relevant clauses or answers quickly. OmniRetrive solves this by ingesting PDFs, chunking them, embedding each chunk into a vector store, and providing tenant‑isolated retrieval and LLM‑generated answers.

---

## 🏛️ System Architecture
```mermaid
flowchart TD
    subgraph Frontend[Frontend (React)]
        UI[User Interface]
    end
    subgraph Gateway[Go Gateway (Chi)]
        Auth[Auth & RBAC]
        API[REST API]
    end
    subgraph AI[AI Service (FastAPI Python)]
        Ingest[Ingestion Worker]
        Embed[Embedding & Upsert]
        Query[Retrieval & LLM]
    end
    subgraph VectorDB[Qdrant]
        V[Vector Store]
    end
    subgraph GatewayDB[Go Gateway PostgreSQL]
        M[Users, Workspaces & Memberships]
    end
    subgraph AIDB[Python AI Service SQLite]
        D[Documents, Ingestion Jobs & Evaluation Results]
    end
    UI -->|calls| API
    API --> Auth
    API --> Ingest
    Ingest --> Embed --> V
    Query --> V
    Auth --> M
    Ingest --> D
    Query -->|document metadata| D
    Gateway --> AI
```

### Technology Stack & Service Responsibilities
| Service | Language / Framework | Responsibility |
|---|---|---|
| **Gateway** | Go (Chi) | Authentication, JWT issuance, workspace/role management, rate limiting, API routing |
| **AI Service** | Python (FastAPI) | PDF validation, text extraction (with OCR fallback), chunking, embedding generation, background job orchestration (Inngest), vector upsert & retrieval |
| **Vector DB** | Qdrant | Stores high‑dimensional embeddings with payload metadata (`workspace_id`, `document_id`, etc.) |
| **Identity database** | PostgreSQL, owned and migrated by the Go gateway | Users, workspaces, workspace memberships, and roles |
| **AI metadata database** | SQLite, owned by the Python AI service | Document records, ingestion jobs, and evaluation results; tenant IDs are references, not user/workspace records |
| **Original PDF storage** | Private local adapter for development; S3-compatible adapter for production | Stores original PDFs under generated, workspace-scoped keys outside the relational database |
| **Frontend** | React, TypeScript, Vite | Workspace dashboard, document management, and cited Q&A |
| **Background Worker** | Inngest | Reliable, retry‑aware execution of ingestion and query jobs |

---

## 📂 Document Ingestion Pipeline
1. **Upload** – Frontend streams a PDF to `POST /api/workspaces/{workspaceId}/documents/upload`.
2. **Validation** – `file_utils.validate_pdf_file` checks MIME type, magic bytes, size, readability (supports scanned PDFs). A SHA‑256 hash is computed for deduplication.
3. **Background Job** – Inngest triggers `rag_ingest_pdf`.
4. **Extraction** – `data_loader.load_and_chunk_pdf_with_metadata` extracts text; scanned pages fall back to OCR when `pytesseract` is available.
5. **Embedding** – `data_loader.embed_texts` uses Hugging Face Inference API with `BAAI/bge-base-en-v1.5`, producing 768-dimensional vectors.
6. **Upsert** – Vectors are upserted into Qdrant with payload `{workspace_id, document_id, source, page_number, heading}`.
7. **Status Update** – Document status moves through `queued → processing → completed/failed` and is stored in the metadata DB.

Original files are streamed through the authenticated gateway and staged to disk rather than buffered in memory. The AI service verifies the PDF and checksum before persisting it. Document listing is paginated; downloads are served through the authenticated gateway after workspace membership and document scope checks. Deletion first hides the document and marks it `deleting`; a failed Qdrant or file-store cleanup keeps the record for a safe retry.

---

## 🤖 RAG Question‑Answering Pipeline
1. **Query Request** – `POST /api/workspaces/{workspaceId}/query` with user question.
2. **Embedding** – Query text is embedded.
3. **Vector Retrieval** – Qdrant returns top‑k nearest chunks filtered by the tenant’s `workspace_id`.
4. **Context Construction** – Retrieved chunks are ordered and concatenated with source metadata.
5. **LLM Generation** – The Hugging Face router serves `meta-llama/Llama-3.1-8B-Instruct:novita`; retrieved context and the question are sent to the model.
6. **Response** – FastAPI returns the answer, citation data, and token usage statistics.

---

## 🗄️ Database Schema & Tenant Isolation Strategy
- **Users**, **workspaces**, and **workspace memberships** are stored and migrated by the Go gateway database.
- Go owns the only user/workspace tables in PostgreSQL; Go applies its versioned schema migrations at startup.
- **Documents**, **document_jobs**, **eval_runs**, and **eval_item_results** remain in the Python AI metadata database.
- Python document records keep `owner_id` and `workspace_id` as tenant-reference fields. They are not Python user/workspace tables and are required to scope documents and vectors to the workspace authorized by Go.
- **Vector payloads** in Qdrant also contain `workspace_id`; all retrieval calls add a mandatory filter on this field, guaranteeing cross‑tenant isolation at the vector engine level.
- The application never trusts client‑supplied tenant IDs; they are derived from the authenticated JWT and verified against the membership table before any DB or vector operation.

---

## 📡 API Endpoint Documentation
The Go gateway at `http://localhost:8080` is the public API and requires a bearer token for protected routes. The Python AI service is internal to Docker Compose and must only be reached through the gateway.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Register a new user and provision a default workspace. |
| `POST` | `/api/auth/login` | Authenticate and obtain a JWT. |
| `GET` | `/api/auth/me` | Retrieve authenticated user details and workspaces. |
| `POST` | `/api/workspaces` | Create a new workspace. |
| `GET` | `/api/workspaces` | List workspaces for the user. |
| `DELETE` | `/api/workspaces/{workspaceId}` | Delete workspace and purge all tenant data. |
| `POST` | `/api/workspaces/{workspaceId}/members` | Invite/add a member (owner/admin only). |
| `DELETE` | `/api/workspaces/{workspaceId}/members/{userId}` | Remove a member (owner/admin only). |
| `POST` | `/api/workspaces/{workspaceId}/documents/upload` | Upload PDF; triggers ingestion job. |
| `GET` | `/api/workspaces/{workspaceId}/documents` | List documents with status metadata. |
| `GET` | `/api/workspaces/{workspaceId}/documents/{docId}` | Get document details. |
| `GET` | `/api/workspaces/{workspaceId}/documents/{docId}/download` | Download the original PDF after workspace access checks. |
| `POST` | `/api/workspaces/{workspaceId}/documents/{docId}/retry` | Retry a failed ingestion job. |
| `DELETE` | `/api/workspaces/{workspaceId}/documents/{docId}` | Delete document and associated vectors. |
| `POST` | `/api/workspaces/{workspaceId}/query` | Perform RAG query within the workspace. |

---

## ⚙️ Environment Setup
1. **Clone the repository**
   ```powershell
   git clone https://github.com/GangaRamPrasad2004/OmniRetrive.git
   cd omniretrive
   ```
2. **Python environment** – install [uv](https://docs.astral.sh/uv/) if it is not already available, then sync the locked dependencies:
   ```powershell
   uv sync --frozen --group dev
   .\.venv\Scripts\activate
   ```
3. **Go dependencies** (inside `gateway/`)
   ```powershell
   cd gateway
   go mod download
   cd ..
   ```
4. **Environment variables** – copy `.env.example` to `.env`, replace the placeholder database password and JWT secret with strong local values, and set `HF_TOKEN` for Hugging Face inference:
   ```powershell
   Copy-Item .env.example .env
   ```
   Compose persists PostgreSQL identity data and keeps the Python AI service private so application requests pass through Go authentication and workspace authorization.
5. **Database initialization** – start the complete stack with `docker compose up --build`. PostgreSQL becomes healthy before the gateway starts; the Go gateway uses the embedded Tern migration files to initialize its identity schema. To run migrations manually, install Tern and use the Make targets below. When upgrading from the prior Compose setup, the gateway imports existing identity records from the mounted legacy gateway SQLite volume once. Python initializes only its document, ingestion-job, and evaluation tables:
   ```powershell
   python -c "import database; database.init_db()"
   ```

---

## 🐳 Docker Compose Instructions
```powershell
docker compose up --build
```
The React UI is reachable at `http://localhost:3000`; the Inngest development UI is at `http://localhost:8288`, and the gateway is at `http://localhost:8080`. The AI service is internal to Compose. PostgreSQL identity data, AI metadata, uploads, and Qdrant vectors are persisted separately. Compose uses the Inngest development server; configure hosted Inngest credentials and production URLs separately before deployment.

GNU Make targets are also available from the repository root (using GNU Make and the required project tools on `PATH`):
```sh
make help       # List targets
make setup      # Sync Python, Go, and frontend dependencies
make test       # Run Python and Go tests
make build      # Build/type-check the React frontend
make up         # Build and start the Compose stack
make down       # Stop the stack without removing persistent volumes
make lint       # Run Python and Go static checks
make docker-build # Build all four service images
make backup     # Back up local persistent data under backups/
make migrate-up # Start PostgreSQL and apply pending Tern migrations
make migrate-status
make migrate-new MIGRATION_NAME=add_document_metadata
make migrate-down MIGRATE_DOWN_CONFIRM=yes # Destructive: review down SQL first
```
The Compose targets require a configured `.env` file; see the setup steps above.
PostgreSQL migrations live in `gateway/db/migrations` and are managed by Tern. The initial migration creates the `users`, `workspaces`, `workspace_members`, and legacy-import tracking tables; its down migration drops them. The Go gateway applies the same embedded Tern migrations at startup, while the Make targets use the installed Tern CLI. The Compose PostgreSQL port is bound to localhost so the host-installed CLI can connect. Create migrations with `make migrate-new MIGRATION_NAME=add_workspace_field`, edit both the up and down SQL, then run `make migrate-up`. Rollbacks are destructive and require `MIGRATE_DOWN_CONFIRM=yes`; the initial migration's down SQL removes identity tables and their data.

### Persistent data backup and recovery

Run `make backup` on Windows with Docker Compose running. It creates a timestamped directory under `backups/` containing a custom-format PostgreSQL identity dump, Python metadata SQLite database, observability SQLite database, uploaded PDFs, and Qdrant storage. If the old gateway SQLite import file exists, it is included too. The script pauses background processing and file-backed services while copying their data, then restarts them. Backups contain private user documents and credentials-related data: keep them out of source control, encrypt them, restrict access, and copy them to a separate protected location. Test recovery periodically.

To recover locally, first preserve the current state with another backup. With PostgreSQL running, stop the application writers and vector store:
```powershell
docker compose stop gateway inngest ai-backend qdrant observability
$backup = "backups\<timestamp>"
$postgres = (docker compose ps -q postgres).Trim()
$backend = (docker compose ps -a -q ai-backend).Trim()
$observability = (docker compose ps -a -q observability).Trim()
docker cp "$backup\identity.dump" "${postgres}:/tmp/identity.dump"
docker compose exec -T postgres sh -c 'pg_restore --clean --if-exists --no-owner --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" /tmp/identity.dump'
docker cp "$backup\ai-metadata.db" "${backend}:/app/data/omniretrive.db"
docker cp "$backup\observability.db" "${observability}:/app/data/omniretrive.db"
docker compose rm -f ai-backend qdrant
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (Test-Path .\uploads) { Move-Item .\uploads ".\uploads.before-restore-$stamp" }
if (Test-Path .\qdrant_storage) { Move-Item .\qdrant_storage ".\qdrant_storage.before-restore-$stamp" }
Copy-Item -LiteralPath "$backup\uploads" -Destination . -Recurse
Copy-Item -LiteralPath "$backup\qdrant_storage" -Destination . -Recurse
docker compose up -d
```
The container removal does not remove named volumes; it ensures the recreated Qdrant container binds to the restored host folder. Restore all components from the same backup timestamp; mixing versions can leave metadata, originals, and vectors inconsistent. For production, use managed PostgreSQL backups/PITR, Qdrant snapshots or volume backups, and versioned/encrypted object-storage backups; the local script is not a production backup system.

### Production deployment

The Compose file and its localhost-bound ports are intended for local development. For production, use a TLS-terminating reverse proxy or managed ingress, private networking for PostgreSQL/Qdrant/AI services, strong secrets from the deployment platform's secret manager, a managed PostgreSQL service with SSL, durable private S3-compatible document storage, protected Qdrant persistence, and hosted Inngest. Set browser origins explicitly and review resource limits, retention, monitoring, and tested backups. No cloud provider credentials or environment-specific deployment manifests are included; the deployment owner must provision services and configure secrets/URLs in their platform.

For development, `STORAGE_BACKEND=local` writes private generated-key PDFs under `./uploads` (mounted at `/app/uploads` in the AI container); users upload from **Documents → Upload a PDF** in React. Copying a file directly into that folder does not register it or start ingestion. For production, provision a private S3/R2 bucket and a least-privilege backend credential, then set `STORAGE_BACKEND=s3`, `STORAGE_BUCKET`, `STORAGE_REGION`, and (for R2/other compatible services) `STORAGE_ENDPOINT_URL`, `STORAGE_ACCESS_KEY_ID`, and `STORAGE_SECRET_ACCESS_KEY`. Do not enable public bucket access or put storage credentials in the frontend. Existing local files remain accessible when using the local adapter; when migrating to S3, migrate both originals and their metadata references before switching providers.

---

## 🧪 Testing Instructions
### Python Backend Tests
```powershell
.\.venv\Scripts\python.exe -m pytest -v tests/
```
### Go Gateway Tests
```powershell
cd gateway
go test -v ./...
```
### React Frontend
```powershell
cd frontend-react
npm ci
npm run build
```
Python tests use SQLite and mocked Qdrant/LLM services. Go gateway tests use temporary SQLite databases for deterministic coverage; validate PostgreSQL runtime behavior against a live PostgreSQL instance before deployment. No Playwright end-to-end test suite is currently included.

---

## 📊 Evaluation Methodology & Actual Results
The repository includes a version-controlled query set at `eval/dataset.json` and deterministic retrieval metrics in `eval/metrics.py`. Evaluate against a populated Qdrant collection with `POST /eval/runs`; historical runs and reports are available from `/eval/runs`, `/eval/compare`, and `/eval/report`. Dataset document names and passages are synthetic references; matching PDF fixtures are not bundled, so meaningful retrieval scores require matching documents to be indexed. The current runner constructs a heuristic answer from retrieved context instead of invoking the production LLM. Its faithfulness/relevance scores and token/cost values are heuristics/estimates, not model-judge results or observed provider usage. No benchmark results are claimed.

---

## ⚠️ Known Limitations & Future Improvements
- **OCR support** uses Tesseract in the backend container; local Python installations need Tesseract and Poppler binaries separately.
- **Vector dimensionality** is fixed at 768 for `BAAI/bge-base-en-v1.5`. Changing embedding models requires a compatible collection and reindexing.
- **Document version replacement** is not yet implemented. Each uploaded original is stored under a versioned key, but existing document replacement, active-version switching, and old-version retention are not exposed in the UI/API.
- **Production object storage** requires a privately provisioned S3-compatible bucket and credentials. The local adapter persists files for single-host development, not multi-instance production.
- **Background job visibility** is currently limited to simple status fields; a richer monitoring UI is planned.
- **Scale testing** beyond a few thousand documents has not been performed; future work includes sharding Qdrant collections per tenant.
- **Frontend** is a React application served by Nginx; API requests are proxied through the Go gateway.

---

## 📚 Architecture Decision Record (ADR)
See the [architecture decision record](architecture_decision_record.md) for a concise rationale behind major choices:
- **Why Qdrant?** It provides efficient ANN search, payload filtering for tenant isolation, and easy Docker deployment.
- **Chunking strategy** – sentence‑level splitter with heading detection balances retrieval relevance and vector count.
- **Separation of Go and Python** – Go handles high‑throughput, low‑latency authentication, rate limiting and API routing, while Python leverages the rich LlamaIndex and HuggingFace ecosystem for AI‑heavy workloads.

---
