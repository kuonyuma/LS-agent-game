import type {
  AttachmentPreview,
  EmbeddingDefaults,
  EmbeddingProviderType,
  ImageOptions,
  ImageSearchStatus,
  KnowledgeBaseSummary,
  KnowledgeDocumentInfo,
  KnowledgeHit,
  ProviderInfo,
  ReasoningEffort,
  SessionDetail,
  SessionSummary,
  ToolInfo,
  WebAccount,
  WorkspaceBrowseResult,
} from "./types";

export const ACCOUNT_EXPIRED_EVENT = "agent-workbench-account-expired";
let currentAccountId: string | null = null;

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export function setRequestAccount(id: string | null) {
  currentAccountId = id;
}

async function apiFetch(url: string, init?: RequestInit, attachAccount = true): Promise<Response> {
  const accountId = attachAccount ? currentAccountId : null;
  const headers = new Headers(init?.headers);
  if (typeof init?.body === "string") headers.set("Content-Type", "application/json");
  if (init?.method && init.method !== "GET") headers.set("X-Agent-Request", "1");
  if (accountId) headers.set("X-Agent-User", accountId);
  const res = await fetch(url, { ...init, headers, credentials: "same-origin" });
  if (res.status === 401 && accountId && accountId === currentAccountId) {
    window.dispatchEvent(new Event(ACCOUNT_EXPIRED_EVENT));
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 保留 HTTP 状态作为错误提示。
    }
    throw new ApiError(res.status, detail);
  }
  return res;
}

async function requestJson<T>(url: string, init?: RequestInit, attachAccount = true): Promise<T> {
  const res = await apiFetch(url, init, attachAccount);
  return (await res.json()) as T;
}

export function fetchCurrentAccount(): Promise<WebAccount> {
  return requestJson("/api/auth/me", undefined, false);
}

export async function canCreateAdmin(): Promise<boolean> {
  try {
    const setup = await requestJson<{ can_create_admin: boolean }>("/api/auth/setup", undefined, false);
    return setup.can_create_admin;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return false;
    throw err;
  }
}

export function createFirstAdmin(username: string, password: string): Promise<WebAccount> {
  return requestJson("/api/auth/setup", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  }, false);
}

export function loginAccount(username: string, password: string): Promise<WebAccount> {
  return requestJson("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  }, false);
}

export function logoutAccount(): Promise<{ logged_out: boolean }> {
  return requestJson("/api/auth/logout", { method: "POST" });
}

export function createWorkbenchUser(username: string, password: string): Promise<Omit<WebAccount, "mode">> {
  return requestJson("/api/auth/users", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function fetchHealth(): Promise<{
  status: string;
  workspace: string;
  workspace_name: string;
  default_provider: string;
  default_model: string;
  image_search?: ImageSearchStatus;
}> {
  return requestJson("/api/health");
}

export function fetchProviders(refreshModels = false): Promise<{
  default_provider: string;
  providers: ProviderInfo[];
  embedding?: EmbeddingDefaults;
}> {
  const query = refreshModels ? "?refresh_models=true" : "";
  return requestJson(`/api/providers${query}`);
}

export interface ChatGPTAuthStatus {
  connected: boolean;
  account_email: string;
  account_name: string;
  has_client_id: boolean;
  expires_at: number;
  usage_url: string;
}

export function startChatGPTLogin(): Promise<{
  authorize_url: string;
  state: string;
  redirect_uri: string;
}> {
  return requestJson("/api/auth/chatgpt/start", {
    method: "POST",
  });
}

export function fetchChatGPTAuthStatus(): Promise<ChatGPTAuthStatus> {
  return requestJson("/api/auth/chatgpt/status");
}

export function logoutChatGPT(): Promise<
  ChatGPTAuthStatus & { logged_out: boolean }
> {
  return requestJson("/api/auth/chatgpt/logout", {
    method: "POST",
  });
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
  embedding_provider?: EmbeddingProviderType;
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

export function uploadKnowledgeDocuments(
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

  return requestJson(
    `/api/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
    {
      method: "POST",
      body: form,
    },
  );
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
  serper_api_key?: string;
  tavily_api_key?: string;
  embedding_api_key?: string;
  knowledge_base_ids?: string[];
  thinking_enabled: boolean;
  reasoning_effort: ReasoningEffort;
  safety_enabled: boolean;
  plan_and_execute: boolean;
  max_tokens?: number;
  system_prompt_override?: string;
  attachments?: AttachmentPreview[];
  timezone?: string;
  image_options?: ImageOptions;
}

export async function streamSessionRun(
  sessionId: string,
  payload: StreamRunPayload,
  onEvent: (event: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await apiFetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/runs`,
    {
      method: "POST",
      body: JSON.stringify(payload),
      signal,
    },
  );

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
