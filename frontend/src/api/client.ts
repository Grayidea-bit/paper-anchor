export interface Health {
  status: string;
  db: boolean;
  chat_model: string;
  llm_key_set: boolean;
}

export type DocumentStatus =
  | "uploaded"
  | "parsing"
  | "embedding"
  | "digesting"
  | "ready"
  | "failed";

export interface Doc {
  id: number;
  title: string;
  filename: string;
  page_count: number;
  status: DocumentStatus;
  error_msg: string | null;
  created_at: string;
}

export interface Chunk {
  id: number;
  chunk_index: number;
  page: number;
  section: string | null;
  content: string;
  bbox_list: [number, number, number, number][];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init);
  if (!resp.ok) {
    let message = `API ${resp.status}`;
    try {
      const body = await resp.json();
      message = body?.error?.message ?? body?.detail ?? message;
    } catch {
      /* keep default */
    }
    throw new Error(message);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return resp.json() as Promise<T>;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/healthz");
}

export function listDocuments(): Promise<Doc[]> {
  return request<Doc[]>("/api/documents");
}

export function getDocument(id: number): Promise<Doc> {
  return request<Doc>(`/api/documents/${id}`);
}

export function uploadDocument(file: File): Promise<Doc> {
  const form = new FormData();
  form.append("file", file);
  return request<Doc>("/api/documents", { method: "POST", body: form });
}

export function deleteDocument(id: number): Promise<void> {
  return request<void>(`/api/documents/${id}`, { method: "DELETE" });
}

export function getChunks(id: number, limit = 500): Promise<Chunk[]> {
  return request<Chunk[]>(`/api/documents/${id}/chunks?limit=${limit}`);
}

export function documentFileUrl(id: number): string {
  return `/api/documents/${id}/file`;
}
