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
  digest?: Digest | null;
  created_at: string;
}

export type BBox = [number, number, number, number];

export interface Citation {
  chunk_index: number;
  chunk_id: number;
  page: number;
  bbox_list: BBox[];
}

export interface DigestSection {
  key: string;
  title: string;
  text: string;
  citations: Citation[];
}

export interface Digest {
  tldr: string;
  sections: DigestSection[];
}

export interface Conversation {
  id: number;
  document_id: number;
  title: string;
  created_at: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  selection?: { text: string; chunk_id: number | null } | null;
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

export function listConversations(docId: number): Promise<Conversation[]> {
  return request<Conversation[]>(`/api/documents/${docId}/conversations`);
}

export function createConversation(docId: number, title = "新對話"): Promise<Conversation> {
  return request<Conversation>(`/api/documents/${docId}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function listMessages(convId: number): Promise<Message[]> {
  return request<Message[]>(`/api/conversations/${convId}/messages`);
}

export function regenerateDigest(docId: number, language?: string): Promise<{ status: string }> {
  const qs = language ? `?language=${encodeURIComponent(language)}` : "";
  return request<{ status: string }>(`/api/documents/${docId}/digest${qs}`, { method: "POST" });
}

export interface StreamHandlers {
  onToken: (text: string) => void;
  onCitations: (citations: Citation[]) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export interface StreamOptions {
  selection?: { text: string; chunk_id: number | null };
  language?: string;
}

/** POST 提問並解析 SSE 串流（token* → citations → done | error） */
export async function streamMessage(
  convId: number,
  content: string,
  handlers: StreamHandlers,
  options: StreamOptions = {},
): Promise<void> {
  const resp = await fetch(`/api/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      selection: options.selection,
      language: options.language,
    }),
  });
  if (!resp.ok || !resp.body) {
    handlers.onError(`API ${resp.status}`);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let ended = false;
  const dispatch = (block: string) => {
    let event = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!event || !data) return;
    const payload = JSON.parse(data);
    if (event === "token") handlers.onToken(payload.text as string);
    else if (event === "citations") handlers.onCitations(payload.citations as Citation[]);
    else if (event === "done") {
      ended = true;
      handlers.onDone();
    } else if (event === "error") {
      ended = true;
      handlers.onError(payload.message as string);
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      dispatch(buf.slice(0, sep));
      buf = buf.slice(sep + 2);
    }
  }
  if (!ended) handlers.onError("連線中斷");
}
