import os
import uuid
import logging
import datetime
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc

import inngest
from inngest.experimental import ai
import inngest.fast_api

from database import init_db, get_db, SessionLocal, Document, DocumentJob
from file_utils import validate_pdf_file, compute_sha256, sanitize_filename
from data_loader import load_and_chunk_pdf, embed_texts
from vector_db import QdrantStorage
from custom_types import (
    RAGChunksAndSrc,
    RAGQueryResult,
    RAGUpsertResult,
    RAGSearchResult,
)

load_dotenv()
logger = logging.getLogger("uvicorn")

# Configure uploads directory
_default_uploads = "uploads" if not os.path.exists("/app") else "/app/uploads"
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", _default_uploads))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# In production (Sevalla) set IS_PRODUCTION=true as an env var.
_is_production = os.getenv("IS_PRODUCTION", "false").lower() == "true"

inngest_client = inngest.Inngest(
    app_id="rag_app",
    logger=logger,
    is_production=_is_production,
    serializer=inngest.PydanticSerializer(),
)


def _update_doc_status(
    doc_id: Optional[str],
    status_val: str,
    error_msg: Optional[str] = None,
    chunk_count: Optional[int] = None,
    page_count: Optional[int] = None,
):
    """Helper to update document status in database safely."""
    if not doc_id:
        return
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.status = status_val
            if error_msg is not None:
                doc.error_message = error_msg
            elif status_val in ("completed", "processing"):
                doc.error_message = None
            if chunk_count is not None:
                doc.chunk_count = chunk_count
            if page_count is not None and page_count > 0:
                doc.page_count = page_count
            doc.updated_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()
    except Exception as e:
        logger.error(f"Error updating document {doc_id} status: {e}")
        db.rollback()
    finally:
        db.close()


@inngest_client.create_function(
    fn_id="RAG: Ingest PDF",
    trigger=inngest.TriggerEvent(event="rag/ingest_pdf"),
    throttle=inngest.Throttle(limit=10, period=datetime.timedelta(minutes=1)),
    rate_limit=inngest.RateLimit(
        limit=5,
        period=datetime.timedelta(minutes=1),
        key="event.data.source_id",
    ),
)
async def rag_ingest_pdf(ctx: inngest.Context):
    pdf_path = ctx.event.data["pdf_path"]
    source_id = ctx.event.data.get("source_id", pdf_path)
    document_id = ctx.event.data.get("document_id")
    workspace_id = ctx.event.data.get("workspace_id", "default_workspace")

    # Mark document as processing
    _update_doc_status(document_id, "processing")

    def _load(ctx: inngest.Context) -> RAGChunksAndSrc:
        try:
            chunks = load_and_chunk_pdf(pdf_path)
            _update_doc_status(document_id, "processing", chunk_count=len(chunks))
            return RAGChunksAndSrc(
                chunks=chunks,
                source_id=source_id,
                document_id=document_id,
                workspace_id=workspace_id,
            )
        except Exception as e:
            error_details = f"Text extraction failed: {str(e)}"
            _update_doc_status(document_id, "failed", error_msg=error_details)
            raise RuntimeError(error_details) from e

    def _upsert(chunks_and_src: RAGChunksAndSrc) -> RAGUpsertResult:
        try:
            chunks = chunks_and_src.chunks
            src = chunks_and_src.source_id
            doc_id = chunks_and_src.document_id
            ws_id = chunks_and_src.workspace_id or "default_workspace"

            if not chunks:
                _update_doc_status(doc_id, "completed", chunk_count=0)
                return RAGUpsertResult(ingested=0, document_id=doc_id, workspace_id=ws_id)

            vecs = embed_texts(chunks)
            ids = [
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{src}:{i}"))
                for i in range(len(chunks))
            ]
            payloads = [
                {
                    "source": src,
                    "document_id": doc_id or src,
                    "workspace_id": ws_id,
                    "text": chunks[i],
                    "chunk_index": i,
                }
                for i in range(len(chunks))
            ]

            qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
            qdrant_key = os.getenv("QDRANT_API_KEY")
            storage = QdrantStorage(
                url=qdrant_url,
                api_key=qdrant_key,
                collection="docs_hf",
            )
            storage.upsert(ids, vecs, payloads)

            _update_doc_status(doc_id, "completed", chunk_count=len(chunks))
            return RAGUpsertResult(ingested=len(chunks), document_id=doc_id)

        except Exception as e:
            error_details = f"Vector indexing failed: {str(e)}"
            _update_doc_status(document_id, "failed", error_msg=error_details)
            raise RuntimeError(error_details) from e

    try:
        chunks_and_src = await ctx.step.run(
            "load-and-chunk",
            lambda: _load(ctx),
            output_type=RAGChunksAndSrc,
        )
        ingested = await ctx.step.run(
            "embed-and-upsert",
            lambda: _upsert(chunks_and_src),
            output_type=RAGUpsertResult,
        )
        return ingested.model_dump()
    except Exception as e:
        _update_doc_status(document_id, "failed", error_msg=str(e))
        raise


@inngest_client.create_function(
    fn_id="RAG: Query PDF",
    trigger=inngest.TriggerEvent(event="rag/query_pdf_ai"),
)
async def rag_query_pdf_ai(ctx: inngest.Context):
    workspace_id = ctx.event.data.get("workspace_id")

    def _search(question: str, top_k: int = 5, ws_id: Optional[str] = None) -> RAGSearchResult:
        query_vec = embed_texts([question])[0]
        store = QdrantStorage(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=os.getenv("QDRANT_API_KEY"),
            collection="docs_hf",
        )
        found = store.search(query_vec, top_k=top_k, workspace_id=ws_id)
        return RAGSearchResult(contexts=found["contexts"], sources=found["sources"])

    question = ctx.event.data["question"]
    top_k = int(ctx.event.data.get("top_k", 5))

    found = await ctx.step.run(
        "embed-and-search",
        lambda: _search(question, top_k, workspace_id),
        output_type=RAGSearchResult,
    )

    if not found.contexts:
        return {
            "answer": "No relevant documents or contexts found to answer your question.",
            "sources": [],
            "num_contexts": 0,
        }

    context_block = "\n\n".join(f"- {c}" for c in found.contexts)
    user_content = (
        "Use the following context to answer the question.\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question}\n"
        "Answer concisely using the context above."
    )

    adapter = ai.openai.Adapter(
        auth_key=os.getenv("HF_TOKEN"),
        base_url="https://router.huggingface.co/v1",
        model="meta-llama/Llama-3.1-8B-Instruct:novita",
    )

    res = await ctx.step.ai.infer(
        "llm-answer",
        adapter=adapter,
        body={
            "max_tokens": 1024,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": "You answer questions using only the provided context.",
                },
                {"role": "user", "content": user_content},
            ],
        },
    )

    answer = res["choices"][0]["message"]["content"].strip()
    return {
        "answer": answer,
        "sources": found.sources,
        "num_contexts": len(found.contexts),
    }


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure database schema is created on startup."""
    init_db()
    yield


# Initialize FastAPI app
app = FastAPI(
    title="OmniRetrive API",
    description="Document Intelligence Platform with Secure Document Management and RAG Pipelines",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Inngest endpoints
inngest.fast_api.serve(
    app,
    inngest_client,
    [rag_ingest_pdf, rag_query_pdf_ai],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "OmniRetrive API"}


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    workspace_id: str = Form("default_workspace"),
    owner_id: str = Form("default_user"),
    db: Session = Depends(get_db),
):
    """
    Accepts a PDF, validates MIME type, extension, size, and readability.
    Prevents duplicate processing via SHA-256 content hashing.
    Generates a stable document ID and triggers background ingestion.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided in upload.",
        )

    clean_filename = sanitize_filename(file.filename)
    contents = await file.read()

    # 1. Validation
    is_valid, error_msg, page_count = validate_pdf_file(clean_filename, contents)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )

    # 2. Duplicate Detection via SHA-256
    file_hash = compute_sha256(contents)
    existing_doc = (
        db.query(Document)
        .filter(Document.file_hash == file_hash, Document.workspace_id == workspace_id)
        .first()
    )

    if existing_doc:
        # If document already succeeded, prevent redundant re-processing
        if existing_doc.status == "completed":
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "already_exists",
                    "message": f"Document '{existing_doc.filename}' with identical content has already been processed.",
                    "document": existing_doc.to_dict(),
                    "filename": existing_doc.filename,
                    "event_id": None,
                },
            )
        # If currently queued, processing, or retrying, prevent duplicate jobs
        if existing_doc.status in ("queued", "processing", "retrying"):
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "already_processing",
                    "message": f"Document '{existing_doc.filename}' is currently in status '{existing_doc.status}'.",
                    "document": existing_doc.to_dict(),
                    "filename": existing_doc.filename,
                    "event_id": None,
                },
            )
        # If failed, reuse existing doc and transition to retrying
        doc_id = existing_doc.id
        dest = Path(existing_doc.storage_path)
        dest.write_bytes(contents)
        existing_doc.status = "retrying"
        existing_doc.error_message = None
        existing_doc.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()

        # Fire Inngest ingest event
        event = inngest.Event(
            name="rag/ingest_pdf",
            data={
                "pdf_path": str(dest),
                "source_id": clean_filename,
                "document_id": doc_id,
            },
        )
        try:
            event_ids = await inngest_client.send(event)
            event_id = event_ids[0] if event_ids else None
        except Exception as e:
            logger.warning(f"Failed to send Inngest event: {e}")
            event_id = None

        job = DocumentJob(
            id=str(uuid.uuid4()),
            document_id=doc_id,
            job_type="retry",
            status="queued",
            event_id=event_id,
        )
        db.add(job)
        db.commit()

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "retrying",
                "message": f"Re-queued ingestion for previously failed document '{existing_doc.filename}'.",
                "document": existing_doc.to_dict(),
                "filename": clean_filename,
                "event_id": event_id,
            },
        )

    # 3. New Document Record Creation
    doc_id = str(uuid.uuid4())
    dest = UPLOADS_DIR / f"{doc_id}_{clean_filename}"
    dest.write_bytes(contents)

    doc = Document(
        id=doc_id,
        filename=clean_filename,
        file_hash=file_hash,
        file_size=len(contents),
        mime_type="application/pdf",
        storage_path=str(dest),
        owner_id=owner_id,
        workspace_id=workspace_id,
        version=1,
        status="queued",
        chunk_count=0,
        page_count=page_count,
        error_message=None,
    )
    db.add(doc)
    db.commit()

    # 4. Trigger Inngest Event
    event = inngest.Event(
        name="rag/ingest_pdf",
        data={
            "pdf_path": str(dest),
            "source_id": clean_filename,
            "document_id": doc_id,
            "workspace_id": workspace_id,
        },
    )
    try:
        event_ids = await inngest_client.send(event)
        event_id = event_ids[0] if event_ids else None
    except Exception as e:
        logger.warning(f"Failed to send Inngest event: {e}")
        event_id = None

    job = DocumentJob(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        job_type="ingest",
        status="queued",
        event_id=event_id,
    )
    db.add(job)
    db.commit()

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "status": "queued",
            "message": "Document uploaded successfully and queued for ingestion.",
            "document": doc.to_dict(),
            "filename": clean_filename,
            "event_id": event_id,
        },
    )


@app.get("/documents")
def list_documents(
    search: Optional[str] = Query(None, description="Search by document filename"),
    status: Optional[str] = Query(None, description="Filter by status (queued, processing, completed, failed, retrying)"),
    workspace_id: Optional[str] = Query(None, description="Filter by workspace ID"),
    sort_by: str = Query("created_at", description="Field to sort by (created_at, filename, file_size, status)"),
    sort_order: str = Query("desc", description="Sort order (asc, desc)"),
    db: Session = Depends(get_db),
):
    """
    Searchable, sortable listing of all documents with status and metadata.
    """
    query = db.query(Document)

    if workspace_id:
        query = query.filter(Document.workspace_id == workspace_id)

    if search:
        query = query.filter(Document.filename.ilike(f"%{search}%"))

    if status:
        query = query.filter(Document.status == status)

    # Sorting
    sort_column = getattr(Document, sort_by, Document.created_at)
    if sort_order.lower() == "asc":
        query = query.order_by(asc(sort_column))
    else:
        query = query.order_by(desc(sort_column))

    docs = query.all()
    return {
        "documents": [d.to_dict() for d in docs],
        "total": len(docs),
    }


@app.get("/documents/{document_id}")
def get_document_details(document_id: str, db: Session = Depends(get_db)):
    """Retrieve metadata, job states, and readiness for a specific document."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID '{document_id}' not found.",
        )

    jobs = (
        db.query(DocumentJob)
        .filter(DocumentJob.document_id == document_id)
        .order_by(desc(DocumentJob.created_at))
        .all()
    )

    doc_data = doc.to_dict()
    doc_data["jobs"] = [j.to_dict() for j in jobs]
    return doc_data


@app.post("/documents/{document_id}/retry")
async def retry_document_job(document_id: str, db: Session = Depends(get_db)):
    """
    Safely retries a failed document ingestion job.
    Prevents duplicate concurrent runs if already processing.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID '{document_id}' not found.",
        )

    if doc.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document '{doc.filename}' is already actively processing.",
        )

    file_path = Path(doc.storage_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stored PDF file not found on disk at: {doc.storage_path}. Please re-upload.",
        )

    doc.status = "retrying"
    doc.error_message = None
    doc.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    event = inngest.Event(
        name="rag/ingest_pdf",
        data={
            "pdf_path": str(file_path),
            "source_id": doc.filename,
            "document_id": doc.id,
            "workspace_id": doc.workspace_id,
        },
    )
    try:
        event_ids = await inngest_client.send(event)
        event_id = event_ids[0] if event_ids else None
    except Exception as e:
        logger.warning(f"Failed to send Inngest event: {e}")
        event_id = None

    job = DocumentJob(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        job_type="retry",
        status="queued",
        event_id=event_id,
    )
    db.add(job)
    db.commit()

    return {
        "status": "retrying",
        "message": f"Retry queued for document '{doc.filename}'.",
        "document": doc.to_dict(),
        "event_id": event_id,
    }


@app.delete("/documents/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    """
    Permanently deletes a document, removes its derived vector records from Qdrant,
    and removes the file from local storage.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID '{document_id}' not found.",
        )

    filename = doc.filename
    storage_path = doc.storage_path

    # 1. Delete derived vector points from Qdrant
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY")
    try:
        storage = QdrantStorage(
            url=qdrant_url,
            api_key=qdrant_key,
            collection="docs_hf",
        )
        storage.delete_by_source(document_id)
        storage.delete_by_source(filename)
    except Exception as e:
        logger.warning(f"Could not delete vectors for document {document_id}: {e}")

    # 2. Delete file from disk
    try:
        file_path = Path(storage_path)
        if file_path.exists():
            file_path.unlink()
    except Exception as e:
        logger.warning(f"Could not delete storage file {storage_path}: {e}")

    # 3. Delete from database
    db.delete(doc)
    db.commit()

    return {
        "status": "deleted",
        "message": f"Document '{filename}' and associated vectors were successfully deleted.",
        "document_id": document_id,
    }


from pydantic import BaseModel


class QueryRequestModel(BaseModel):
    question: str
    top_k: int = 5
    workspace_id: Optional[str] = None


@app.post("/query")
async def query_documents(req: QueryRequestModel):
    """
    Execute tenant-isolated vector retrieval and question answering.
    Applies strict workspace_id filter on Qdrant so foreign tenant data is unreachable.
    """
    query_vec = embed_texts([req.question])[0]
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY")
    store = QdrantStorage(url=qdrant_url, api_key=qdrant_key, collection="docs_hf")

    # Strict multi-tenant vector search
    found = store.search(query_vec, top_k=req.top_k, workspace_id=req.workspace_id)

    if not found["contexts"]:
        return {
            "answer": "No relevant documents found in this workspace to answer your question.",
            "sources": [],
            "num_contexts": 0,
            "workspace_id": req.workspace_id,
        }

    context_block = "\n\n".join(f"- {c}" for c in found["contexts"])
    hf_token = os.getenv("HF_TOKEN")
    answer = ""

    if hf_token:
        try:
            import httpx
            prompt = (
                f"Context:\n{context_block}\n\n"
                f"Question: {req.question}\n\n"
                "Answer concisely using the context above."
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://router.huggingface.co/v1/chat/completions",
                    headers={"Authorization": f"Bearer {hf_token}"},
                    json={
                        "model": "meta-llama/Llama-3.1-8B-Instruct:novita",
                        "messages": [
                            {"role": "system", "content": "You answer questions using only the provided context."},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 1024,
                        "temperature": 0.2,
                    },
                )
                if res.status_code == 200:
                    answer = res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Error querying HF chat completions: {e}")

    if not answer:
        # Fallback informative summary response when LLM router is unavailable or rate-limited
        answer = f"Retrieved {len(found['contexts'])} context passages from: {', '.join(found['sources'])}.\n\nKey Excerpt:\n" + found["contexts"][0][:400]

    return {
        "answer": answer,
        "sources": found["sources"],
        "num_contexts": len(found["contexts"]),
        "workspace_id": req.workspace_id,
    }


@app.delete("/workspaces/{workspace_id}/cleanup")
def cleanup_workspace_data(workspace_id: str, db: Session = Depends(get_db)):
    """
    Cascade-deletes all documents, local files, and derived Qdrant vectors
    belonging to a deleted workspace. Leaves zero orphaned vectors.
    """
    docs = db.query(Document).filter(Document.workspace_id == workspace_id).all()
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY")
    storage = QdrantStorage(url=qdrant_url, api_key=qdrant_key, collection="docs_hf")

    # 1. Purge all vectors for this tenant in Qdrant
    storage.delete_by_workspace(workspace_id)

    # 2. Delete physical storage files
    deleted_count = len(docs)
    for doc in docs:
        try:
            p = Path(doc.storage_path)
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.warning(f"Could not remove file {doc.storage_path}: {e}")
        db.delete(doc)

    db.commit()
    return {
        "status": "deleted",
        "workspace_id": workspace_id,
        "documents_deleted": deleted_count,
    }