import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, type Workspace, type WorkspaceDetails } from "../api/client";
import { useAuth } from "./AuthContext";

const WORKSPACE_KEY = "omniretrive_workspace_id";

interface WorkspaceContextValue {
  workspace: Workspace | null;
  details: WorkspaceDetails | null;
  loading: boolean;
  error: string | null;
  selectWorkspace: (workspace: Workspace) => void;
  createWorkspace: (name: string) => Promise<void>;
  deleteWorkspace: () => Promise<void>;
  refreshDetails: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { workspaces, refresh: refreshAuth } = useAuth();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [details, setDetails] = useState<WorkspaceDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (workspaces.length === 0) {
      setWorkspace(null);
      setDetails(null);
      localStorage.removeItem(WORKSPACE_KEY);
      return;
    }
    const savedId = localStorage.getItem(WORKSPACE_KEY);
    const saved = workspaces.find((item) => item.id === savedId);
    const next = saved ?? workspaces[0];
    setWorkspace(next);
    localStorage.setItem(WORKSPACE_KEY, next.id);
  }, [workspaces]);

  const selectWorkspace = useCallback((next: Workspace) => {
    setWorkspace(next);
    setDetails(null);
    localStorage.setItem(WORKSPACE_KEY, next.id);
  }, []);

  const refreshDetails = useCallback(async () => {
    if (!workspace) return;
    setDetails(null);
    setLoading(true);
    setError(null);
    try {
      setDetails(await api.getWorkspace(workspace.id));
    } catch (cause) {
      setDetails(null);
      setError(cause instanceof Error ? cause.message : "Could not load workspace details.");
      throw cause;
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    if (workspace) void refreshDetails().catch(() => undefined);
  }, [workspace, refreshDetails]);

  const createWorkspace = useCallback(
    async (name: string) => {
      const created = await api.createWorkspace(name);
      await refreshAuth();
      selectWorkspace(created);
    },
    [refreshAuth, selectWorkspace],
  );

  const deleteWorkspace = useCallback(async () => {
    if (!workspace) return;
    await api.deleteWorkspace(workspace.id);
    setDetails(null);
    await refreshAuth();
  }, [refreshAuth, workspace]);

  const value = useMemo(
    () => ({ workspace, details, loading, error, selectWorkspace, createWorkspace, deleteWorkspace, refreshDetails }),
    [workspace, details, loading, error, selectWorkspace, createWorkspace, deleteWorkspace, refreshDetails],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return context;
}
