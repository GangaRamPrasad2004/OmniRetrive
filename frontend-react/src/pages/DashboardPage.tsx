import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Document } from "../api/client";
import { useWorkspace } from "../context/WorkspaceContext";

export function DashboardPage() {
  const { workspace } = useWorkspace();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!workspace) return;
    let active = true;
    setLoading(true);
    api.listDocuments(workspace.id, new URLSearchParams({ sort_by: "created_at", sort_order: "desc" }))
      .then((result) => { if (active) setDocuments(result.documents); })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Unable to load documents."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [workspace]);

  if (loading) return <div className="loading-state">Loading workspace overview…</div>;
  if (error) return <div className="alert alert-error" role="alert">{error}</div>;

  const ready = documents.filter((document) => document.is_ready_for_qa).length;
  const active = documents.filter((document) => ["queued", "processing", "retrying"].includes(document.status)).length;
  const failed = documents.filter((document) => document.status === "failed").length;
  const recent = documents.slice(0, 5);

  return (
    <div className="page-stack">
      <section className="welcome-row">
        <div><span className="eyebrow">WORKSPACE OVERVIEW</span><h2>Your knowledge, at a glance.</h2><p>Documents and AI answers are kept inside this workspace.</p></div>
        <Link className="button button-secondary" to="/chat">Ask a question <span aria-hidden="true">↗</span></Link>
      </section>
      <section className="stats-grid" aria-label="Document statistics">
        <StatCard label="Total documents" value={documents.length} detail="In this workspace" icon="▤" />
        <StatCard label="Ready to ask" value={ready} detail="Indexed and searchable" icon="✳" />
        <StatCard label="Processing" value={active} detail="Ingestion in progress" icon="◷" />
        <StatCard label="Need attention" value={failed} detail="Failed ingestion jobs" icon="!" />
      </section>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">YOUR LIBRARY</span><h2>Recent documents</h2></div><Link className="text-link" to="/documents">View all <span aria-hidden="true">→</span></Link></div>
        {recent.length === 0 ? (
          <div className="empty-inline"><div className="empty-icon">▤</div><h3>Your library starts here</h3><p>Upload a PDF and OmniRetrive will prepare it for searching and Q&amp;A.</p><Link className="button button-primary" to="/documents#upload">Upload a document</Link></div>
        ) : (
          <div className="document-list">
            {recent.map((document) => <DocumentRow key={document.id} document={document} />)}
          </div>
        )}
      </section>
      <section className="tip-card"><div className="tip-icon">✦</div><div><strong>Ask with confidence</strong><p>Answers are grounded in your selected documents and include citations to the source pages.</p></div><Link to="/chat">Try it <span aria-hidden="true">→</span></Link></section>
    </div>
  );
}

function StatCard({ label, value, detail, icon }: { label: string; value: number; detail: string; icon: string }) {
  return <article className="stat-card"><div className="stat-top"><span>{label}</span><span className="stat-icon">{icon}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}

export function DocumentRow({ document }: { document: Document }) {
  return (
    <Link className="document-row" to={`/documents/${document.id}`}>
      <div className="file-icon">PDF</div>
      <div className="document-name"><strong>{document.filename}</strong><small>{document.page_count} pages · {formatBytes(document.file_size)} · {formatDate(document.created_at)}</small></div>
      <StatusBadge status={document.status} ready={document.is_ready_for_qa} />
      <span className="row-arrow" aria-hidden="true">→</span>
    </Link>
  );
}

export function StatusBadge({ status, ready }: { status: string; ready?: boolean }) {
  const label = ready ? "Ready" : status.replaceAll("_", " ");
  return <span className={`status-badge status-${ready ? "ready" : status}`}>{label}</span>;
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(date: string): string {
  return date ? new Date(date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "Unknown date";
}
