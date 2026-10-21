from typing import List, Optional
import pydantic


class RAGChunksAndSrc(pydantic.BaseModel):
    chunks: List[str]
    source_id: Optional[str] = None
    document_id: Optional[str] = None
    page_count: Optional[int] = 0


class RAGUpsertResult(pydantic.BaseModel):
    ingested: int
    document_id: Optional[str] = None


class RAGSearchResult(pydantic.BaseModel):
    contexts: List[str]
    sources: List[str]


class RAGQueryResult(pydantic.BaseModel):
    answer: str
    sources: List[str]
    num_contexts: int


class DocumentMetadata(pydantic.BaseModel):
    id: str
    filename: str
    file_hash: str
    file_size: int
    mime_type: str
    storage_path: str
    owner_id: str
    workspace_id: str
    version: int
    status: str  # queued, processing, completed, failed, retrying
    chunk_count: int
    page_count: int
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_ready_for_qa: bool


class DocumentListResponse(pydantic.BaseModel):
    documents: List[DocumentMetadata]
    total: int


class DocumentActionResponse(pydantic.BaseModel):
    status: str
    message: str
    document: Optional[DocumentMetadata] = None
