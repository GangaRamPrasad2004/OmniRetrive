import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useWorkspace } from "../context/WorkspaceContext";

export function WorkspaceSettingsPage() {
  const { workspaces } = useAuth();
  const { workspace, details, loading, error: workspaceError, createWorkspace, deleteWorkspace, refreshDetails } = useWorkspace();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const owner = details?.current_role === "owner";
  const canManageMembers = owner || details?.current_role === "admin";

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try { await createWorkspace(name.trim()); setName(""); setNotice("Workspace created."); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create workspace."); }
    finally { setBusy(false); }
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.addMember(workspace.id, email.trim(), role);
      setEmail(""); setNotice("Member added to the workspace.");
      await refreshDetails();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not add member."); }
    finally { setBusy(false); }
  }

  async function removeMember(userId: string) {
    if (!workspace || !window.confirm("Remove this person from the workspace?")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.removeMember(workspace.id, userId);
      setNotice("Member removed.");
      await refreshDetails();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not remove member."); }
    finally { setBusy(false); }
  }

  async function removeWorkspace() {
    if (!workspace || !window.confirm(`Delete "${workspace.name}" and its documents? This cannot be undone.`)) return;
    setBusy(true); setError(""); setNotice("");
    try { await deleteWorkspace(); setNotice("Workspace deleted."); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not delete workspace."); }
    finally { setBusy(false); }
  }

  return (
    <div className="page-stack">
      <section className="welcome-row"><div><span className="eyebrow">TEAM & ACCESS</span><h2>Workspace settings</h2><p>Manage your workspace, people, and access.</p></div></section>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {workspaceError && <div className="alert alert-error" role="alert">{workspaceError}</div>}
      {notice && <div className="alert alert-success" role="status">{notice}</div>}
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">ACTIVE WORKSPACE</span><h2>{workspace?.name ?? "No workspace selected"}</h2></div>{details && <span className="role-badge">{details.current_role}</span>}</div>
        {workspace && <p className="muted">Workspace ID <code>{workspace.id}</code></p>}
        {loading && <p className="muted">Loading workspace members…</p>}
        {details && <div className="member-list">{details.members.map((member) => (
          <div className="member-row" key={member.user_id}>
            <div className="avatar avatar-small">{(member.full_name || member.email).slice(0, 1).toUpperCase()}</div>
            <div className="member-copy"><strong>{member.full_name || member.email}</strong><small>{member.email}</small></div>
            <span className="role-badge">{member.role}</span>
            {canManageMembers && member.role !== "owner" && (owner || member.role !== "admin") && <button className="button button-danger button-small" disabled={busy} onClick={() => void removeMember(member.user_id)}>Remove</button>}
          </div>
        ))}</div>}
        {canManageMembers && workspace && <form className="invite-form" onSubmit={(event) => void invite(event)}>
          <h3>Invite an existing user</h3>
          <p className="muted">They must have registered an account before you can add them.</p>
          <div className="inline-form"><label className="field">Email address<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label><label className="field">Role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="member">Member</option>{owner && <option value="admin">Admin</option>}</select></label><button className="button button-primary" disabled={busy}>Add member</button></div>
        </form>}
      </section>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">CREATE</span><h2>New workspace</h2></div></div>
        <form className="inline-form" onSubmit={(event) => void create(event)}><label className="field">Workspace name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={120} required placeholder="e.g. Product research" /></label><button className="button button-primary" disabled={busy}>Create workspace</button></form>
        <p className="muted">{workspaces.length} workspace{workspaces.length === 1 ? "" : "s"} available to your account.</p>
      </section>
      {owner && workspace && <section className="panel danger-zone"><div><span className="eyebrow">DANGER ZONE</span><h2>Delete this workspace</h2><p>Workspace documents and indexed vectors will also be removed.</p></div><button className="button button-danger" disabled={busy} onClick={() => void removeWorkspace()}>Delete workspace</button></section>}
    </div>
  );
}
