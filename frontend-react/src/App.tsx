import { useEffect } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import { WorkspaceProvider, useWorkspace } from "./context/WorkspaceContext";
import type { Workspace } from "./api/client";
import { ChatPage } from "./pages/ChatPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { LoginPage } from "./pages/LoginPage";
import { WorkspaceSettingsPage } from "./pages/WorkspaceSettingsPage";

function ProtectedLayout() {
  const { user, workspaces, loading, logout } = useAuth();
  const location = useLocation();
  if (loading) return <div className="page-loader">Restoring your session…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  return (
    <WorkspaceProvider>
      <Shell onLogout={logout} userName={user.full_name} userEmail={user.email} workspaces={workspaces} />
    </WorkspaceProvider>
  );
}

function Shell({
  onLogout,
  userName,
  userEmail,
  workspaces,
}: {
  onLogout: () => void;
  userName: string;
  userEmail: string;
  workspaces: Workspace[];
}) {
  const { workspace, selectWorkspace } = useWorkspace();
  const navigate = useNavigate();

  useEffect(() => {
    if (workspaces.length > 0 && !workspace) selectWorkspace(workspaces[0]);
  }, [workspaces, workspace, selectWorkspace]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/">
          <span className="brand-mark">O</span>
          <span>Omni<span className="brand-light">Retrive</span></span>
        </Link>
        <div className="workspace-select">
          <label htmlFor="workspace-picker">Workspace</label>
          <select
            id="workspace-picker"
            value={workspace?.id ?? ""}
            onChange={(event) => {
              const selected = workspaces.find((item) => item.id === event.target.value);
              if (selected) selectWorkspace(selected);
            }}
          >
            {workspaces.length === 0 && <option value="">No workspaces</option>}
            {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </div>
        <nav className="primary-nav" aria-label="Main navigation">
          <NavLink to="/" end><span>▦</span> Overview</NavLink>
          <NavLink to="/documents"><span>▤</span> Documents</NavLink>
          <NavLink to="/chat"><span>✳</span> Ask documents</NavLink>
          <NavLink to="/workspace"><span>⚙</span> Workspace settings</NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="user-profile">
            <div className="avatar">{userName.slice(0, 1).toUpperCase()}</div>
            <div className="user-copy"><strong>{userName}</strong><small>{userEmail}</small></div>
          </div>
          <button
            className="button button-quiet logout-button"
            onClick={() => { onLogout(); navigate("/login", { replace: true }); }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div><span className="eyebrow">DOCUMENT INTELLIGENCE</span><h1>{workspace?.name ?? "Your workspaces"}</h1></div>
          {workspace && <Link className="button button-primary" to="/documents#upload">Upload PDF <span aria-hidden="true">＋</span></Link>}
        </header>
        <div className="content-area">
          {workspace ? <Outlet /> : <EmptyWorkspace />}
        </div>
      </main>
    </div>
  );
}

function EmptyWorkspace() {
  return (
    <section className="empty-state panel">
      <div className="empty-icon">▦</div>
      <h2>No workspace yet</h2>
      <p>Create a workspace to upload documents and ask questions about them.</p>
      <Link className="button button-primary" to="/workspace">Create workspace</Link>
    </section>
  );
}

function LoginRoute() {
  const { user, loading } = useAuth();
  if (loading) return <div className="page-loader">Restoring your session…</div>;
  if (user) return <Navigate to="/" replace />;
  return <LoginPage />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route element={<ProtectedLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/workspace" element={<WorkspaceSettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
