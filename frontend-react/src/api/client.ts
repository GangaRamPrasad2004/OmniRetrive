const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const TOKEN_KEY = "omniretrive_token";

export interface User {
  id: string;
  email: string;
  full_name: string;
}

export interface Workspace {
  id: string;
  name: string;
  owner_id: string;
}

export interface WorkspaceMember {
  id: string;
  workspace_id: string;
  user_id: string;
  role: "owner" | "admin" | "member";
  email: string;
  full_name: string;
}

export interface WorkspaceDetails {
  workspace: Workspace;
  members: WorkspaceMember[];
  current_role: WorkspaceMember["role"];
}

export interface Document {
  id: string;
  filename: string;
  workspace_id: string;
  status: string;
  file_size: number;
  page_count: number;
  chunk_count: number;
  error_message: string | null;
  is_ready_for_qa: boolean;
  created_at: string;
  updated_at: string;
}

export interface DocumentJob {
  id: string;
  job_type: string;
  status: string;
  error_message: string | null;
  created_at: string;
}

export interface DocumentDetails extends Document {
  jobs: DocumentJob[];
}

export interface Citation {
  document_name: string;
  document_id: string;
  page_number: number;
  chunk_index: number;
  relevant_text: string;
  score: number;
  heading?: string | null;
  verified: boolean;
}

export interface QueryResponse {
  answer: string;
  sources: string[];
  citations: Citation[];
  num_contexts: number;
  grounded: boolean;
  insufficient_context: boolean;
}

interface RequestOptions extends RequestInit {
  auth?: boolean;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { auth = true, headers: initialHeaders, ...init } = options;
  const headers = new Headers(initialHeaders);
  if (auth) {
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }

  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("Could not reach the API. Check that the Go gateway is running.", 0);
  }

  if (response.status === 401 && getToken()) {
    setToken(null);
    window.dispatchEvent(new Event("omniretrive:session-expired"));
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const body = payload as { error?: string; detail?: string };
    const message =
      (typeof payload === "string" && payload) ||
      body?.error ||
      body?.detail ||
      `Request failed (${response.status})`;
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      auth: false,
      body: JSON.stringify({ email, password }),
    }),
  register: (email: string, password: string, full_name: string) =>
    request<{ token: string; user: User }>("/auth/register", {
      method: "POST",
      auth: false,
      body: JSON.stringify({ email, password, full_name }),
    }),
  me: () => request<{ user: User; workspaces: Workspace[] }>("/auth/me"),
  listWorkspaces: () => request<Workspace[]>("/workspaces"),
  createWorkspace: (name: string) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  getWorkspace: (id: string) =>
    request<WorkspaceDetails>(`/workspaces/${encodeURIComponent(id)}`),
  deleteWorkspace: (id: string) =>
    request<{ status: string }>(`/workspaces/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  addMember: (workspaceId: string, email: string, role: string) =>
    request<WorkspaceMember>(`/workspaces/${encodeURIComponent(workspaceId)}/members`, {
      method: "POST",
      body: JSON.stringify({ email, role }),
    }),
  removeMember: (workspaceId: string, userId: string) =>
    request<{ status: string }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
      { method: "DELETE" },
    ),
  listDocuments: (workspaceId: string, params: URLSearchParams = new URLSearchParams()) =>
    request<{ documents: Document[]; total: number }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/documents?${params.toString()}`,
    ),
  getDocument: (workspaceId: string, documentId: string) =>
    request<DocumentDetails>(
      `/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}`,
    ),
  uploadDocument: (
    workspaceId: string,
    file: File,
    onProgress?: (percent: number) => void,
  ) => {
    const form = new FormData();
    form.append("file", file);
    return new Promise<{ status: string; message: string; document?: Document }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(
        "POST",
        `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/documents/upload`,
      );
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onerror = () =>
        reject(new ApiError("Could not reach the API. Check that the Go gateway is running.", 0));
      xhr.onload = () => {
        if (xhr.status === 401 && getToken()) {
          setToken(null);
          window.dispatchEvent(new Event("omniretrive:session-expired"));
        }
        let payload: unknown;
        try {
          payload = JSON.parse(xhr.responseText);
        } catch {
          payload = xhr.responseText;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload as { status: string; message: string; document?: Document });
          return;
        }
        const body = payload as { error?: string; detail?: string };
        reject(new ApiError(
          (typeof payload === "string" && payload) ||
            body?.error ||
            body?.detail ||
            `Request failed (${xhr.status})`,
          xhr.status,
        ));
      };
      xhr.send(form);
    });
  },
  downloadDocument: async (workspaceId: string, documentId: string): Promise<Blob> => {
    const token = getToken();
    const response = await fetch(
      `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}/download`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    );
    if (!response.ok) {
      const contentType = response.headers.get("content-type") ?? "";
      const payload: unknown = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
      const body = payload as { error?: string; detail?: string };
      throw new ApiError(
        (typeof payload === "string" && payload) ||
          body?.error ||
          body?.detail ||
          `Download failed (${response.status})`,
        response.status,
      );
    }
    return response.blob();
  },
  retryDocument: (workspaceId: string, documentId: string) =>
    request<{ message: string }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}/retry`,
      { method: "POST" },
    ),
  cancelDocument: (workspaceId: string, documentId: string) =>
    request<{ message: string }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}/cancel`,
      { method: "POST" },
    ),
  deleteDocument: (workspaceId: string, documentId: string) =>
    request<{ status: string }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}`,
      { method: "DELETE" },
    ),
  query: (workspaceId: string, question: string, top_k: number, document_ids: string[]) =>
    request<QueryResponse>(`/workspaces/${encodeURIComponent(workspaceId)}/query`, {
      method: "POST",
      body: JSON.stringify({ question, top_k, document_ids }),
    }),
};
