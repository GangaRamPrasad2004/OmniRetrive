import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Document } from "../api/client";
import { useWorkspace } from "../context/WorkspaceContext";
import { formatBytes, formatDate, StatusBadge } from "./DashboardPage";

const statuses = ["all", "uploading", "queued", "processing", "retrying", "completed", "failed", "cancelled"];

export function DocumentsPage() {
  const { workspace, details } = useWorkspace();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState("desc");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [busyDocument, setBusyDocument] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const canDelete = details?.current_role === "owner" || details?.current_role === "admin";

  const loadDocuments = useCallback(async () => {
    if (!workspace) return;
    setError("");
    const params = new URLSearchParams({ sort_by: sortBy, sort_order: sortOrder });
    params.set("limit", "50");
    params.set("offset", "0");
    if (search.trim()) params.set("search", search.trim());
    if (status !== "all") params.set("status", status);
    const result = await api.listDocuments(workspace.id, params);
    setDocuments(result.documents);
    setTotalDocuments(result.total);
  }, [workspace, sortBy, sortOrder, search, status]);

  useEffect(() => {
    void loadDocuments().catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Unable to load documents."));
  }, [loadDocuments]);

  useEffect(() => {
    if (!documents.some((document) => ["uploading", "queued", "processing", "retrying", "deleting"].includes(document.status))) return;
    const timer = window.setInterval(() => {
      void loadDocuments().catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : "Unable to refresh document status."),
      );
    }, 5000);
    return () => window.clearInterval(timer);
  }, [documents, loadDocuments]);

  async function loadMore() {
    if (!workspace) return;
    setLoadingMore(true);
    setError("");
    try {
      const params = new URLSearchParams({
        sort_by: sortBy,
        sort_order: sortOrder,
        limit: "50",
        offset: String(documents.length),
      });
      if (search.trim()) params.set("search", search.trim());
      if (status !== "all") params.set("status", status);
      const result = await api.listDocuments(workspace.id, params);
      setDocuments((current) => [...current, ...result.documents]);
      setTotalDocuments(result.total);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load more documents.");
    } finally {
      setLoadingMore(false);
    }
  }

  async function upload() {
    if (!workspace || !selectedFile) return;
    if (selectedFile.size > 50 * 1024 * 1024) {
      setError("The PDF is larger than the 50 MB upload limit.");
      return;
    }
    setUploading(true);
    setUploadProgress(0);
    setError("");
    setNotice("");
    try {
      const result = await api.uploadDocument(workspace.id, selectedFile, setUploadProgress);
      setNotice(result.message || "Upload accepted.");
      setSelectedFile(null);
      if (inputRef.current) inputRef.current.value = "";
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function runAction(documentId: string, action: "retry" | "cancel" | "delete") {
    if (!workspace) return;
    if (action === "delete" && !window.confirm("Permanently delete this document and its indexed vectors?")) return;
    setBusyDocument(documentId);
    setError("");
    setNotice("");
    try {
      if (action === "retry") await api.retryDocument(workspace.id, documentId);
      if (action === "cancel") await api.cancelDocument(workspace.id, documentId);
      if (action === "delete") await api.deleteDocument(workspace.id, documentId);
      setNotice(action === "delete" ? "Document deleted." : `Document ${action} request completed.`);
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Unable to ${action} document.`);
    } finally {
      setBusyDocument(null);
    }

  }

  async function download(document: Document) {
    if (!workspace) return;
    setError("");
    try {
      const blob = await api.downloadDocument(workspace.id, document.id);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = document.filename;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to download document.");
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    setSelectedFile(event.target.files?.[0] ?? null);
    setError("");
  }

  return (
    <div className="page-stack">
      <section className="welcome-row"><div><span className="eyebrow">WORKSPACE LIBRARY</span><h2>Documents</h2><p>Upload PDFs, track ingestion, and manage your indexed files.</p></div></section>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {notice && <div className="alert alert-success" role="status">{notice}</div>}
      <section className="panel upload-panel" id="upload">
        <div className="section-heading"><div><span className="eyebrow">ADD TO YOUR LIBRARY</span><h2>Upload a PDF</h2></div><span className="upload-symbol">↑</span></div>
        <p className="muted">PDF files up to 50 MB. Processing begins as soon as the upload is accepted.</p>
        <div className="upload-controls">
          <input ref={inputRef} type="file" accept="application/pdf,.pdf" onChange={onFileChange} aria-label="Choose PDF file" />
          <button className="button button-primary" disabled={!selectedFile || uploading} onClick={() => void upload()}>
            {uploading ? `Uploading ${uploadProgress}%…` : "Upload and index"}
          </button>
        </div>
        {uploading && <progress className="upload-progress" max={100} value={uploadProgress} aria-label="PDF upload progress" />}
        {selectedFile && <small className="muted">{selectedFile.name} · {formatBytes(selectedFile.size)}</small>}
      </section>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">ALL FILES</span><h2>Your documents <span className="count-pill">{documents.length}</span></h2></div><button className="button button-secondary button-small" onClick={() => void loadDocuments()}>Refresh</button></div>
        <div className="filter-bar">
          <label className="search-field"><span aria-hidden="true">⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search by filename" aria-label="Search by filename" /></label>
          <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter by status">{statuses.map((item) => <option key={item} value={item}>{item === "all" ? "All statuses" : item}</option>)}</select>
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)} aria-label="Sort documents">{[["created_at", "Upload date"], ["filename", "Filename"], ["file_size", "File size"], ["status", "Status"]].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          <select value={sortOrder} onChange={(event) => setSortOrder(event.target.value)} aria-label="Sort order"><option value="desc">Descending</option><option value="asc">Ascending</option></select>
        </div>
        {documents.length === 0 ? <div className="empty-inline"><div className="empty-icon">▤</div><h3>No documents found</h3><p>Try adjusting your filters or upload a PDF to this workspace.</p></div> : (
          <div className="managed-document-list">
            {documents.map((document) => (
              <article className="managed-document" key={document.id}>
                <div className="managed-document-main">
                  <div className="file-icon">PDF</div>
                  <div className="document-name"><Link to={`/documents/${document.id}`}><strong>{document.filename}</strong></Link><small>{document.page_count} pages · {formatBytes(document.file_size)} · Added {formatDate(document.created_at)}</small></div>
                  <StatusBadge status={document.status} ready={document.is_ready_for_qa} />
                </div>
                {document.error_message && <p className="document-error">{document.error_message}</p>}
                <div className="managed-actions">
                  <span>{document.chunk_count} indexed chunks</span>
                  <button className="button button-secondary button-small" onClick={() => void download(document)}>Download</button>
                  {(document.status === "failed" || document.status === "cancelled") && <button className="button button-secondary button-small" disabled={busyDocument === document.id} onClick={() => void runAction(document.id, "retry")}>Retry</button>}
                  {["queued", "processing", "retrying"].includes(document.status) && <button className="button button-secondary button-small" disabled={busyDocument === document.id} onClick={() => void runAction(document.id, "cancel")}>Cancel</button>}
                  {canDelete && <button className="button button-danger button-small" disabled={busyDocument === document.id} onClick={() => void runAction(document.id, "delete")}>Delete</button>}
                </div>
              </article>
            ))}
          </div>
        )}
        {documents.length < totalDocuments && <div className="load-more"><button className="button button-secondary" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : `Load more (${documents.length} of ${totalDocuments})`}</button></div>}
      </section>
    </div>
  );
}
