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
  project_id?: number | null;
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
  /** 回答內文的 [C{label}] 數字；舊訊息無此欄（fallback 用 chunk_index） */
  label?: number;
  chunk_index: number;
  chunk_id: number;
  page: number;
  bbox_list: BBox[];
  document_id?: number;
  document_title?: string;
}

export interface Project {
  id: number;
  name: string;
  document_count?: number;
  created_at: string;
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
  model?: string | null;
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

export type AnnotationType = "underline" | "highlight" | "note";
export type AnnotationColor = "amber" | "terracotta" | "sage" | "slate";

export interface Annotation {
  id: number;
  document_id: number;
  type: AnnotationType;
  color: AnnotationColor;
  page: number;
  bbox_list: BBox[];
  chunk_id: number | null;
  selected_text: string;
  note_text: string;
  created_at: string;
  updated_at: string;
}

export interface AnnotationCreate {
  type: AnnotationType;
  color: AnnotationColor;
  page: number;
  bbox_list: BBox[];
  chunk_id: number | null;
  selected_text: string;
  note_text?: string;
}

// ---- glossary ----

export interface GlossaryEntry {
  id: number;
  document_id: number;
  term: string;
  translation: string;
  target_lang: string;
  page: number;
  bbox_list: BBox[];
  chunk_id: number | null;
  created_at: string;
}

export interface GlossaryCreate {
  term: string;
  page: number;
  bbox_list: BBox[];
  chunk_id: number | null;
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

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  /** 最近 60 秒的 LLM API 請求數 */
  rpm: number;
}

export type ChatBackend = "openai" | "claude-sdk";

/** 選項陣列驅動（新增後端＝在此加一項） */
export const CHAT_BACKEND_OPTIONS: { value: ChatBackend }[] = [
  { value: "openai" },
  { value: "claude-sdk" },
];

/** 內建 Claude 模型清單（前端持一份，值須與後端一致） */
export const CLAUDE_MODELS: { value: string; label: string }[] = [
  { value: "claude-sonnet-5", label: "Claude Sonnet 5" },
  { value: "claude-opus-4-8", label: "Claude Opus 4.8" },
  { value: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
];

export interface SettingsView {
  llm_base_url?: string;
  llm_chat_model?: string;
  llm_chat_models?: string[];
  llm_api_key_set: boolean;
  system_prompt_extra?: string;
  chat_backend?: ChatBackend;
  claude_oauth_token_set: boolean;
  translation_target_lang?: string;
  defaults: {
    llm_base_url: string;
    llm_chat_model: string;
    llm_chat_models: string[];
    chat_backend: ChatBackend;
  };
}

export interface SettingsPatch {
  llm_base_url?: string;
  llm_api_key?: string;
  llm_chat_model?: string;
  llm_chat_models?: string[];
  system_prompt_extra?: string;
  chat_backend?: ChatBackend;
  claude_oauth_token?: string;
  translation_target_lang?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
}

export function getSettings(): Promise<SettingsView> {
  return request<SettingsView>("/api/settings");
}

export function updateSettings(patch: SettingsPatch): Promise<SettingsView> {
  return request<SettingsView>("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export function getTools(): Promise<ToolInfo[]> {
  return request<ToolInfo[]>("/api/tools");
}

export function getUsage(): Promise<Usage> {
  return request<Usage>("/api/usage");
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

export function setConversationModel(
  convId: number,
  model: string | null,
): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${convId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
}

// ---- projects ----

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/api/projects");
}

export function createProject(name: string): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function renameProject(id: number, name: string): Promise<Project> {
  return request<Project>(`/api/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function deleteProject(id: number): Promise<void> {
  return request<void>(`/api/projects/${id}`, { method: "DELETE" });
}

export function assignProject(docId: number, projectId: number | null): Promise<Doc> {
  return request<Doc>(`/api/documents/${docId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
}

/** scope 版對話 API：kind=project 需帶 projectId */
export function listScopedConversations(
  kind: "project" | "library",
  projectId?: number,
): Promise<Conversation[]> {
  const path =
    kind === "project"
      ? `/api/projects/${projectId}/conversations`
      : "/api/library/conversations";
  return request<Conversation[]>(path);
}

export function createScopedConversation(
  kind: "project" | "library",
  projectId?: number,
  title = "新對話",
): Promise<Conversation> {
  const path =
    kind === "project"
      ? `/api/projects/${projectId}/conversations`
      : "/api/library/conversations";
  return request<Conversation>(path, {
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

export interface ToolEvent {
  name: string;
  status: "start" | "done" | "error";
}

export interface StreamHandlers {
  onToken: (text: string) => void;
  /** 推理模型的思考摘要（即時顯示用，不入庫） */
  onReasoning?: (text: string) => void;
  /** LLM 工具呼叫活動（即時顯示用，不入庫） */
  onTool?: (event: ToolEvent) => void;
  onCitations: (citations: Citation[]) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export interface StreamOptions {
  selection?: { text: string; chunk_id: number | null };
  language?: string;
  /** 傳入後可用 controller.abort() 中斷串流；中斷時靜默結束，不觸發 onError */
  signal?: AbortSignal;
}

function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

/** POST 提問並解析 SSE 串流（token* → citations → done | error）。
 * 若 options.signal 被 abort：靜默 return，不呼叫 onError（呼叫端自行處理 UI）。 */
export async function streamMessage(
  convId: number,
  content: string,
  handlers: StreamHandlers,
  options: StreamOptions = {},
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`/api/conversations/${convId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        selection: options.selection,
        language: options.language,
      }),
      signal: options.signal,
    });
  } catch (e) {
    if (isAbortError(e)) return;
    throw e;
  }
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
    else if (event === "reasoning") handlers.onReasoning?.(payload.text as string);
    else if (event === "tool") handlers.onTool?.(payload as ToolEvent);
    else if (event === "citations") handlers.onCitations(payload.citations as Citation[]);
    else if (event === "done") {
      ended = true;
      handlers.onDone();
    } else if (event === "error") {
      ended = true;
      handlers.onError(payload.message as string);
    }
  };
  try {
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
  } catch (e) {
    if (isAbortError(e) || options.signal?.aborted) return;
    throw e;
  }
  if (ended) return;
  if (options.signal?.aborted) return;
  handlers.onError("連線中斷");
}

// ---- annotations ----

export function listAnnotations(documentId: number): Promise<Annotation[]> {
  return request<Annotation[]>(`/api/documents/${documentId}/annotations`);
}

export function createAnnotation(
  documentId: number,
  input: AnnotationCreate,
): Promise<Annotation> {
  return request<Annotation>(`/api/documents/${documentId}/annotations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function updateAnnotation(
  id: number,
  patch: { note_text?: string; color?: AnnotationColor },
): Promise<Annotation> {
  return request<Annotation>(`/api/annotations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export function deleteAnnotation(id: number): Promise<void> {
  return request<void>(`/api/annotations/${id}`, { method: "DELETE" });
}

export function listGlossary(documentId: number): Promise<GlossaryEntry[]> {
  return request<GlossaryEntry[]>(`/api/documents/${documentId}/glossary`);
}

export function createGlossaryEntry(
  documentId: number,
  input: GlossaryCreate,
): Promise<GlossaryEntry> {
  return request<GlossaryEntry>(`/api/documents/${documentId}/glossary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function retranslateGlossaryEntry(id: number): Promise<GlossaryEntry> {
  return request<GlossaryEntry>(`/api/glossary/${id}/retranslate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export function deleteGlossaryEntry(id: number): Promise<void> {
  return request<void>(`/api/glossary/${id}`, { method: "DELETE" });
}
