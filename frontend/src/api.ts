import type {
  AttachmentPreview,
  EmbeddingDefaults,
  EmbeddingProviderType,
  KnowledgeBaseSummary,
  KnowledgeDocumentInfo,
  KnowledgeHit,
  ProviderInfo,
  ReasoningEffort,
  SessionDetail,
  SessionSummary,
  ToolInfo,
  WorkspaceBrowseResult,
} from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // ignore parse failure
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function fetchHealth(): Promise<{
  status: string;
  workspace: string;
  workspace_name: string;
  default_provider: string;
  default_model: string;
}> {
  return requestJson("/api/health");
}

export function fetchProviders(): Promise<{
  default_provider: string;
  providers: ProviderInfo[];
  embedding?: EmbeddingDefaults;
}> {
  return requestJson("/api/providers");
}

export function fetchKnowledgeBases(): Promise<{
  knowledge_bases: KnowledgeBaseSummary[];
  default_embedding: EmbeddingDefaults;
}> {
  return requestJson("/api/knowledge-bases");
}

export function createKnowledgeBase(payload: {
  name: string;
  description?: string;
  embedding_provider: EmbeddingProviderType;
  embedding_model?: string;
  embedding_base_url?: string;
  embedding_dim?: number;
}): Promise<KnowledgeBaseSummary> {
  return requestJson("/api/knowledge-bases", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteKnowledgeBase(
  kbId: string,
): Promise<{ deleted: boolean; kb_id: string }> {
  return requestJson(`/api/knowledge-bases/${encodeURIComponent(kbId)}`, {
    method: "DELETE",
  });
}

export function fetchKnowledgeDocuments(kbId: string): Promise<{
  knowledge_base: KnowledgeBaseSummary;
  documents: KnowledgeDocumentInfo[];
}> {
  return requestJson(
    `/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
  );
}

export async function uploadKnowledgeDocuments(
  kbId: string,
  files: File[],
  options?: { apiKey?: string; enableOcr?: boolean },
): Promise<{
  knowledge_base: KnowledgeBaseSummary;
  documents: KnowledgeDocumentInfo[];
  errors: Array<{ filename: string; error: string }>;
}> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  if (options?.apiKey?.trim()) {
    form.append("api_key", options.apiKey.trim());
  }
  form.append("enable_ocr", options?.enableOcr ? "true" : "false");

  const res = await fetch(
    `/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
    {
      method: "POST",
      body: form,
    },
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // ignore parse failure
    }
    throw new Error(detail);
  }
  return (await res.json()) as {
    knowledge_base: KnowledgeBaseSummary;
    documents: KnowledgeDocumentInfo[];
    errors: Array<{ filename: string; error: string }>;
  };
}

export function deleteKnowledgeDocument(
  kbId: string,
  docId: string,
): Promise<{
  deleted: boolean;
  doc_id: string;
  knowledge_base: KnowledgeBaseSummary | null;
}> {
  return requestJson(
    `/api/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}`,
    {
      method: "DELETE",
    },
  );
}

export function searchKnowledgeBase(
  kbId: string,
  payload: { query: string; top_k?: number; api_key?: string },
): Promise<{
  kb_id: string;
  query: string;
  hits: KnowledgeHit[];
}> {
  return requestJson(
    `/api/knowledge-bases/${encodeURIComponent(kbId)}/search`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function fetchTools(
  workspace?: string,
): Promise<{ workspace: string; tools: ToolInfo[] }> {
  const query = workspace
    ? `?workspace=${encodeURIComponent(workspace)}`
    : "";
  return requestJson(`/api/tools${query}`);
}

export function browseWorkspace(path = ""): Promise<WorkspaceBrowseResult> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return requestJson(`/api/workspace/browse${query}`);
}

export function validateWorkspace(
  path: string,
): Promise<{ path: string; name: string }> {
  return requestJson("/api/workspace/validate", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function pickNativeWorkspace(
  initialDir?: string,
): Promise<{ cancelled: boolean; path: string | null; name: string }> {
  return requestJson("/api/workspace/pick", {
    method: "POST",
    body: JSON.stringify({ initial_dir: initialDir || null }),
  });
}

export function fetchSessions(): Promise<{ sessions: SessionSummary[] }> {
  return requestJson("/api/sessions");
}

export function createSession(payload: {
  title?: string;
  provider: string;
  model_name: string;
  workspace?: string;
  knowledge_base_ids?: string[];
}): Promise<SessionDetail> {
  return requestJson("/api/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchSessionDetail(sessionId: string): Promise<SessionDetail> {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
}

export function updateSession(
  sessionId: string,
  payload: {
    title?: string;
    provider?: string;
    model_name?: string;
    workspace?: string;
    knowledge_base_ids?: string[];
  },
): Promise<SessionDetail> {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteSession(sessionId: string): Promise<{ deleted: boolean }> {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export function submitApproval(
  sessionId: string,
  approvalId: string,
  approved: boolean,
  requestHash: string,
): Promise<{ accepted: boolean }> {
  return requestJson(
    `/api/sessions/${encodeURIComponent(sessionId)}/approvals/${encodeURIComponent(approvalId)}`,
    {
      method: "POST",
      body: JSON.stringify({ approved, request_hash: requestHash }),
    },
  );
}

export function stopSessionRun(sessionId: string): Promise<{ stopped: boolean }> {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}/stop`, {
    method: "POST",
  });
}

export interface StreamRunPayload {
  query: string;
  provider: string;
  model_name: string;
  workspace?: string;
  api_key?: string;
  search_api_key?: string;
  embedding_api_key?: string;
  knowledge_base_ids?: string[];
  thinking_enabled: boolean;
  reasoning_effort: ReasoningEffort;
  safety_enabled: boolean;
  plan_and_execute: boolean;
  max_tokens?: number;
  system_prompt_override?: string;
  attachments?: AttachmentPreview[];
}

export async function streamSessionRun(
  sessionId: string,
  payload: StreamRunPayload,
  onEvent: (event: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/runs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    },
  );

  if (!res.ok) {
    let detail = `请求失败 (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("浏览器不支持流式响应读取");
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const flushBlock = (rawBlock: string) => {
    const lines = rawBlock.split(/\r?\n/);
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0) return;
    const rawJson = dataLines.join("\n");
    try {
      const parsed = JSON.parse(rawJson) as Record<string, unknown>;
      onEvent(parsed);
    } catch {
      // ignore malformed event frames
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separatorIdx = buffer.indexOf("\n\n");
    while (separatorIdx !== -1) {
      const block = buffer.slice(0, separatorIdx);
      buffer = buffer.slice(separatorIdx + 2);
      flushBlock(block);
      separatorIdx = buffer.indexOf("\n\n");
    }
  }
  if (buffer.trim()) {
    flushBlock(buffer);
  }
}
