export type AgentPhase = "planning" | "executing" | "reflecting";
export type ReasoningEffort = "off" | "low" | "medium" | "high" | "xhigh" | "max";

export interface EffortVisual {
  level: ReasoningEffort;
  effect: "glint" | "align" | "double_arc";
  duration_ms: number;
}

export interface ModelOption {
  id: string;
  label: string;
  supports_vision: boolean;
  supports_thinking: boolean;
  thinking_levels: ReasoningEffort[];
  default_thinking_level: ReasoningEffort | "";
  max_output_tokens: number;
  effort_visual?: EffortVisual | null;
}

export interface ProviderInfo {
  id: string;
  label: string;
  protocol: "gemini" | "openai_compat";
  base_url: string;
  env_key: string;
  env_configured: boolean;
  default_model: string;
  thinking_mode: "none" | "toggleable" | "always_on";
  json_mode: "json_object" | "json_schema";
  models: ModelOption[];
}

export interface ToolInfo {
  name: string;
  description: string;
  read_only: boolean;
}

export interface WorkspaceDirectoryEntry {
  name: string;
  path: string;
}

export interface WorkspaceBrowseResult {
  current: string;
  name: string;
  parent: string;
  home: string;
  roots: WorkspaceDirectoryEntry[];
  directories: WorkspaceDirectoryEntry[];
}

export interface PlanStep {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "skipped";
  notes: string;
}

export interface ReflectionRecord {
  id: string;
  observation: string;
  diagnosis: string;
  strategy: "retry" | "replan" | "verify" | "complete";
  next_action: string;
  trigger: string;
  step_id: string;
}

export interface ExecutionPlan {
  goal: string;
  phase: AgentPhase;
  revision: number;
  replan_reason: string;
  pending_reflection_reason: string;
  unverified_edits: string[];
  steps: PlanStep[];
  reflections: ReflectionRecord[];
}

export interface ToolTrace {
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
  requires_approval: boolean;
  request_hash: string;
  status: "running" | "awaiting_approval" | "completed" | "error";
  result: string;
  is_error: boolean;
}

export interface SubagentTrace {
  call_id: string;
  action: string;
  role: string;
  agent_id: string;
  description: string;
  status: "running" | "completed" | "failed";
  summary?: string;
}

export interface PendingApproval {
  approval_id: string;
  tool_call_id: string;
  tool_name: string;
  tool_arguments: Record<string, unknown>;
  request_hash: string;
  submitting?: boolean;
}

export interface UsageStats {
  input_len?: number;
  output_len?: number;
  cached_len?: number;
}

export interface AttachmentPreview {
  name: string;
  mime_type: string;
  kind: "image" | "file";
  size?: number;
  data_base64?: string;
  text_content?: string;
}

export interface UIMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  attachments?: AttachmentPreview[];
  provider?: string;
  model_name?: string;
  thinking?: string;
  tool_calls?: ToolTrace[];
  subagents?: SubagentTrace[];
  plan?: ExecutionPlan | null;
  usage?: UsageStats;
  safety_usage?: UsageStats;
  safety_blocked?: boolean;
  safety_category?: string;
  reason?: string;
  error?: string;
  turns?: number;
  duration_ms?: number;
  streaming?: boolean;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  provider: string;
  model_name: string;
  workspace?: string;
  knowledge_base_ids?: string[];
  created_at: string;
  updated_at: string;
  message_count: number;
  usage: UsageStats;
  running?: boolean;
}

export interface SessionDetail extends SessionSummary {
  messages: UIMessage[];
}

export type EmbeddingProviderType = "gemini" | "ollama" | "openai_compat";

export interface EmbeddingDefaults {
  provider: EmbeddingProviderType;
  model: string;
  dimensions: number;
  base_url: string;
  ollama_default_url: string;
}

export interface KnowledgeBaseSummary {
  kb_id: string;
  name: string;
  description: string;
  embedding_provider: EmbeddingProviderType;
  embedding_model: string;
  embedding_base_url: string;
  embedding_dim: number;
  document_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentInfo {
  doc_id: string;
  kb_id: string;
  filename: string;
  file_hash: string;
  page_count: number;
  chunk_count: number;
  ocr_pages: number;
  created_at: string;
}

export interface KnowledgeHit {
  chunk_id: number;
  kb_id: string;
  kb_name: string;
  doc_id: string;
  filename: string;
  page_number: number;
  chunk_index: number;
  content: string;
  score: number;
  vector_score: number | null;
  fts_rank: number | null;
}

export interface RunSettings {
  apiKeys: Record<string, string>;
  effortByModel: Record<string, ReasoningEffort>;
  safetyEnabled: boolean;
  maxTokens: number | null;
  systemPromptOverride: string;
  recentWorkspaces: string[];
}
