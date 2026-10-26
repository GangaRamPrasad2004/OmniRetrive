import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type DocumentDetails } from "../api/client";
import { useWorkspace } from "../context/WorkspaceContext";
import { formatBytes, formatDate, StatusBadge } from "./DashboardPage";

export function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const { workspace } = useWorkspace();
  const [document, setDocument] = useState<DocumentDetails | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!workspace) return;
    let active = true;
    api.getDocument(workspace.id, documentId)
      .then((data) => { if (active) setDocument(data); })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Unable to load document."); });
    return () => { active = false; };
  }, [workspace, documentId]);

  if (error) return <div className="page-stack"><Link className="text-link" to="/documents">← All documents</Link><div className="alert alert-error">{error}</div></div>;
  if (!document) return <div className="loading-state">Loading document details…</div>;

  return (
    <div className="page-stack">
      <Link className="text-link back-link" to="/documents">← All documents</Link>
      <section className="panel detail-header">
        <div className="file-icon file-icon-large">PDF</div>
        <div className="detail-title"><span className="eyebrow">DOCUMENT DETAILS</span><h2>{document.filename}</h2><p>Added {formatDate(document.created_at)} · {formatBytes(document.file_size)}</p></div>
        <StatusBadge status={document.status} ready={document.is_ready_for_qa} />
      </section>
      {document.error_message && <div className="alert alert-error">{document.error_message}</div>}
      <section className="stats-grid detail-stats">
        <DetailStat label="Pages" value={document.page_count} />
        <DetailStat label="Indexed chunks" value={document.chunk_count} />
        <DetailStat label="In workspace" value={document.workspace_id === workspace?.id ? "Yes" : "No"} />
      </section>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">PROCESSING HISTORY</span><h2>Ingestion jobs</h2></div></div>
        {!document.jobs.length ? <p className="muted">No processing jobs recorded yet.</p> : document.jobs.map((job) => (
          <div className="job-row" key={job.id}><div><strong>{job.job_type}</strong><small>{formatDate(job.created_at)}</small>{job.error_message && <small className="document-error">{job.error_message}</small>}</div><StatusBadge status={job.status} /></div>
        ))}
      </section>
    </div>
  );
}

function DetailStat({ label, value }: { label: string; value: string | number }) {
  return <article className="stat-card"><small>{label}</small><strong className="detail-stat-value">{value}</strong></article>;
}
