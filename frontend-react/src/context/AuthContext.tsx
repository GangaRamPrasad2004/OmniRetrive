import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, getToken, setToken, type User, type Workspace } from "../api/client";

interface AuthContextValue {
  user: User | null;
  workspaces: Workspace[];
  loading: boolean;
  sessionError: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  refresh: () => Promise<void>;
  clearSessionError: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(Boolean(getToken()));
  const [sessionError, setSessionError] = useState<string | null>(null);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    setWorkspaces([]);
  }, []);

  const refresh = useCallback(async () => {
    const session = await api.me();
    setUser(session.user);
    setWorkspaces(session.workspaces);
  }, []);

  useEffect(() => {
    const onSessionExpired = () => logout();
    window.addEventListener("omniretrive:session-expired", onSessionExpired);
    if (getToken()) {
      void refresh()
        .catch((cause: unknown) => {
          setSessionError(
            cause instanceof Error ? cause.message : "Could not restore your session.",
          );
          logout();
        })
        .finally(() => setLoading(false));
    }
    return () => window.removeEventListener("omniretrive:session-expired", onSessionExpired);
  }, [logout, refresh]);

  const authenticate = useCallback(
    async (operation: Promise<{ token: string }>) => {
      const response = await operation;
      setToken(response.token);
      await refresh();
      setSessionError(null);
    },
    [refresh],
  );

  const login = useCallback(
    (email: string, password: string) => authenticate(api.login(email, password)),
    [authenticate],
  );
  const register = useCallback(
    (email: string, password: string, fullName: string) =>
      authenticate(api.register(email, password, fullName)),
    [authenticate],
  );

  const value = useMemo(
    () => ({
      user,
      workspaces,
      loading,
      sessionError,
      login,
      register,
      refresh,
      clearSessionError: () => setSessionError(null),
      logout,
    }),
    [user, workspaces, loading, sessionError, login, register, refresh, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
