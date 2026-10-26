# Architecture Decision Record

## Status

Accepted for the current OmniRetrive implementation.

## Context

OmniRetrive already has a Python/FastAPI document and RAG service, a Go gateway, a React UI, Qdrant, Inngest background functions, and separate metadata/identity storage. Replacing these working components would add migration risk without improving their current responsibilities.

## Decisions

- **Go and PostgreSQL own identity and authorization.** The gateway is the only owner of users, workspaces, memberships, and roles. It validates membership before proxying tenant-scoped requests.
- **Python remains responsible for document and RAG processing.** Its SQLite database stores documents, ingestion jobs, and evaluation data only. Tenant IDs in that database and in Qdrant are references used for scope enforcement, not duplicate identity records.
- **Original PDFs use a private storage adapter.** Local development stores generated-key files in persistent local storage; production can use AWS S3, Cloudflare R2, or another S3-compatible private bucket. The database stores an internal key and metadata, not file bytes.
- **Qdrant stores vectors, not identity or lifecycle truth.** Retrieval requires a workspace filter. The embedding model is `BAAI/bge-base-en-v1.5` (768 dimensions); switching models requires reindexing into a compatible collection.
- **Inngest handles background functions.** Local Compose uses the Inngest development server; production must use a configured hosted Inngest deployment. This avoids introducing a second queue or worker system.
- **Sentence-aware chunks retain page metadata.** The existing splitter uses 1,000-character chunks with 200-character overlap and preserves page and heading metadata for citations. Keep this baseline until evaluation on representative documents justifies a change.
- **React remains the only frontend.** The Go gateway is the public application API; the Python service is internal in Compose.

## Consequences and limitations

- Cross-service deletion is not a distributed transaction. Failures are surfaced and records are retained when cleanup cannot be confirmed; persistent reconciliation/outbox processing is still needed for recovery from process crashes between stores.
- Local file storage is persistent for the Compose MVP but is not suitable for horizontally scaled production. Production S3-compatible storage must use a private bucket and least-privilege backend credentials.
- Document replacement/version activation is not implemented yet. New uploads are version 1; changing an existing document in place should wait for version-aware metadata and active-version filtering.
- Python document/job/evaluation metadata remains SQLite, separate from PostgreSQL identity data, because that is the established owner and changing it would be a separate migration.
- Tests use SQLite and mocked external services. PostgreSQL and full Docker runtime behavior require validation in an environment with those services available.
- The evaluation dataset currently refers to synthetic document names/passages, and the evaluation runner uses heuristic answers rather than the production LLM. Its scores are not production benchmarks.
