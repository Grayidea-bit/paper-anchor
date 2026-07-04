export interface Health {
  status: string;
  db: boolean;
  chat_model: string;
  llm_key_set: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init);
  if (!resp.ok) {
    throw new Error(`API ${resp.status}: ${await resp.text()}`);
  }
  return resp.json() as Promise<T>;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/healthz");
}
