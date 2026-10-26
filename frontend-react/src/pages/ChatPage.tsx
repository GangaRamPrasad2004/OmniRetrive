import { useEffect, useState, type FormEvent } from "react";
import { api, type Document, type QueryResponse } from "../api/client";
import { useWorkspace } from "../context/WorkspaceContext";

export function ChatPage() {
  const { workspace } = useWorkspace();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!workspace) return;
    let active = true;
    api.listDocuments(workspace.id, new URLSearchParams({ sort_by: "filename", sort_order: "asc" }))
      .then(({ documents: items }) => { if (active) setDocuments(items.filter((item) => item.is_ready_for_qa)); })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Unable to load searchable documents."); });
    return () => { active = false; };
  }, [workspace]);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace || !question.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await api.query(workspace.id, question.trim(), topK, selectedIds));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to answer this question.");
    } finally {
      setLoading(false);
    }
  }

  function toggleDocument(id: string) {
    setSelectedIds((previous) => previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id]);
  }

  return (
    <div className="page-stack">
      <section className="welcome-row"><div><span className="eyebrow">GROUNDED ANSWERS</span><h2>Ask your documents</h2><p>Ask a question and get an answer with verified references from this workspace.</p></div></section>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      <section className="panel chat-panel">
        <form onSubmit={(event) => void ask(event)}>
          <label className="field">Your question<textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="For example: What are the key requirements in section 3?" rows={4} maxLength={2000} required /></label>
          <div className="chat-options">
            <label className="field compact-field">Context chunks<select value={topK} onChange={(event) => setTopK(Number(event.target.value))}>{[3, 5, 8, 10, 15, 20].map((count) => <option key={count} value={count}>{count}</option>)}</select></label>
            <span className="muted">Search is scoped to <strong>{workspace?.name}</strong>.</span>
            <button className="button button-primary" type="submit" disabled={loading || documents.length === 0}>{loading ? "Searching…" : "Ask question"} <span aria-hidden="true">↗</span></button>
          </div>
          <details className="document-filter">
            <summary>Limit to specific documents {selectedIds.length > 0 && <span className="count-pill">{selectedIds.length} selected</span>}</summary>
            {documents.length === 0 ? <p className="muted">Upload and index a PDF before asking a question.</p> : <div className="checkbox-list">
              <p className="muted">Leave all unchecked to search every ready document.</p>
              {documents.map((document) => <label key={document.id}><input type="checkbox" checked={selectedIds.includes(document.id)} onChange={() => toggleDocument(document.id)} />{document.filename}</label>)}
            </div>}
          </details>
        </form>
      </section>
      {loading && <div className="loading-state">Searching workspace documents and preparing a grounded answer…</div>}
      {result && <section className="panel answer-panel">
        <div className="answer-heading"><div className="answer-icon">✳</div><div><span className="eyebrow">OMNIRETRIVE ANSWER</span><h2>Response</h2></div><span className={`grounding-badge ${result.insufficient_context ? "grounding-warning" : ""}`}>{result.insufficient_context ? "Limited context" : "Grounded"}</span></div>
        {result.insufficient_context && <div className="alert alert-warning">The indexed documents did not provide enough evidence for a confident answer.</div>}
        <p className="answer-copy">{result.answer}</p>
        <div className="citation-heading"><span className="eyebrow">SOURCES</span><span>{result.num_contexts} passages retrieved</span></div>
        {result.citations.length > 0 ? <div className="citations-list">{result.citations.map((citation, index) => (
          <article className="citation-card" key={`${citation.document_id}-${citation.chunk_index}-${index}`}>
            <div className="citation-meta"><span className="file-icon file-icon-small">PDF</span><div><strong>{citation.document_name}</strong><small>{citation.heading ? `${citation.heading} · ` : ""}Page {citation.page_number}</small></div><span className="score-pill">{Math.round(citation.score * 100)}% match</span></div>
            <blockquote>{citation.relevant_text}</blockquote>
          </article>
        ))}</div> : result.sources.length > 0 ? <ul className="source-list">{result.sources.map((source) => <li key={source}>{source}</li>)}</ul> : <p className="muted">No matching source passages were found.</p>}
      </section>}
    </div>
  );
}
