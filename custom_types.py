from typing import List, Optional, Dict, Any
import pydantic


class CitationItem(pydantic.BaseModel):
    document_name: str
    document_id: str
    page_number: int
    chunk_index: int
    relevant_text: str
    score: float = 0.0
    heading: Optional[str] = None
    verified: bool = True


class RAGChunksAndSrc(pydantic.BaseModel):
    chunks: List[str]
    source_id: Optional[str] = None
    document_id: Optional[str] = None
    workspace_id: Optional[str] = "default_workspace"
    page_count: Optional[int] = 0
    chunk_metadata: Optional[List[Dict[str, Any]]] = None


class RAGUpsertResult(pydantic.BaseModel):
    ingested: int
    document_id: Optional[str] = None
    workspace_id: Optional[str] = None


class RAGSearchResult(pydantic.BaseModel):
    contexts: List[str]
    sources: List[str]
    citations: Optional[List[CitationItem]] = None


class RAGQueryResult(pydantic.BaseModel):
    answer: str
    sources: List[str]
    citations: List[CitationItem] = []
    num_contexts: int
    grounded: bool = True
    insufficient_context: bool = False
    workspace_id: Optional[str] = None


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
