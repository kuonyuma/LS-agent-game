import {
  AlertCircle,
  BookOpen,
  Check,
  CheckCircle2,
  Database,
  FileText,
  Loader2,
  Plus,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  fetchKnowledgeDocuments,
  searchKnowledgeBase,
  uploadKnowledgeDocuments,
} from "../api";
import type {
  EmbeddingDefaults,
  EmbeddingProviderType,
  KnowledgeBaseSummary,
  KnowledgeDocumentInfo,
  KnowledgeHit,
} from "../types";

interface KnowledgeBaseModalProps {
  open: boolean;
  allowCustomEmbedding: boolean;
  knowledgeBases: KnowledgeBaseSummary[];
  selectedIds: string[];
  defaultEmbedding: EmbeddingDefaults | null;
  geminiApiKey: string;
  geminiEnvConfigured: boolean;
  onToggleSelect: (kbId: string) => void;
  onSelectKb: (kbId: string) => void;
  onRefreshKnowledgeBases: () => Promise<KnowledgeBaseSummary[]>;
  onClose: () => void;
}

const ACCEPTED_FILE_EXTENSIONS =
  ".pdf,.md,.markdown,.txt,.csv,.json,.yaml,.yml,.rst,.log,.py,.ts,.tsx,.js,.jsx,.html,.css,.sql,.xml";

function providerDisplayName(provider: EmbeddingProviderType): string {
  switch (provider) {
    case "gemini":
      return "Gemini";
    case "ollama":
      return "Ollama 本地";
    case "openai_compat":
      return "OpenAI 兼容";
  }
}

export function KnowledgeBaseModal({
  open,
  allowCustomEmbedding,
  knowledgeBases,
  selectedIds,
  defaultEmbedding,
  geminiApiKey,
  geminiEnvConfigured,
  onToggleSelect,
  onSelectKb,
  onRefreshKnowledgeBases,
  onClose,
}: KnowledgeBaseModalProps) {
  const [activeKbId, setActiveKbId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocumentInfo[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);

  // 新建知识库表单状态
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newProvider, setNewProvider] = useState<EmbeddingProviderType>("gemini");
  const [newModel, setNewModel] = useState("");
  const [newBaseUrl, setNewBaseUrl] = useState("");
  const [newDim, setNewDim] = useState("1536");
  const [createSubmitting, setCreateSubmitting] = useState(false);

  // 上传与 OCR 状态
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [enableOcr, setEnableOcr] = useState(false);
  const [customApiKey, setCustomApiKey] = useState("");
  const [uploadSuccess, setUploadSuccess] = useState("");
  const [uploadErrors, setUploadErrors] = useState<
    Array<{ filename: string; error: string }>
  >([]);

  // 删除二次确认状态（避免原生 alert/confirm）
  const [confirmDeleteKbId, setConfirmDeleteKbId] = useState<string | null>(null);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);

  // 检索预览状态
  const [searchQuery, setSearchQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchHits, setSearchHits] = useState<KnowledgeHit[]>([]);
  const [hasSearched, setHasSearched] = useState(false);

  const [errorMsg, setErrorMsg] = useState("");

  const activeKb =
    knowledgeBases.find((kb) => kb.kb_id === activeKbId) ?? null;

  // 打开弹窗时初始化选中项
  useEffect(() => {
    if (!open) return;
    setErrorMsg("");
    setUploadSuccess("");
    setUploadErrors([]);
    setConfirmDeleteKbId(null);
    if (knowledgeBases.length > 0) {
      setActiveKbId((prev) =>
        prev && knowledgeBases.some((kb) => kb.kb_id === prev)
          ? prev
          : knowledgeBases[0].kb_id,
      );
    } else {
      setActiveKbId(null);
      setDocuments([]);
    }
  }, [open, knowledgeBases]);

  // Esc 关闭弹窗
  useEffect(() => {
    if (!open) return;
    const handleEsc = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [open, onClose]);

  const loadDocuments = useCallback(async (kbId: string) => {
    setDocsLoading(true);
    setErrorMsg("");
    try {
      const res = await fetchKnowledgeDocuments(kbId);
      setDocuments(res.documents);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "加载文档列表失败");
    } finally {
      setDocsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open || !activeKbId) {
      setDocuments([]);
      return;
    }
    setSearchHits([]);
    setHasSearched(false);
    setUploadSuccess("");
    setUploadErrors([]);
    void loadDocuments(activeKbId);
  }, [open, activeKbId, loadDocuments]);

  if (!open) return null;

  const resolveEffectiveApiKey = (
    kb: KnowledgeBaseSummary,
    forUpload = false,
  ): string | undefined => {
    if (customApiKey.trim()) return customApiKey.trim();
    if (
      (kb.embedding_provider === "gemini" ||
        (forUpload && enableOcr && kb.embedding_provider === "ollama")) &&
      geminiApiKey.trim()
    ) {
      return geminiApiKey.trim();
    }
    return undefined;
  };

  const resetCreateForm = () => {
    setNewName("");
    setNewDesc("");
    setNewProvider(defaultEmbedding?.provider ?? "gemini");
    setNewModel("");
    setNewBaseUrl("");
    setNewDim(String(defaultEmbedding?.dimensions ?? 1536));
    setCreating(false);
  };

  const handleCreateKb = async () => {
    const trimmed = newName.trim();
    if (!trimmed || createSubmitting) return;
    setErrorMsg("");
    setCreateSubmitting(true);
    try {
      const parsedDim = Number(newDim);
      const created = await createKnowledgeBase({
        name: trimmed,
        description: newDesc.trim(),
        ...(allowCustomEmbedding ? {
          embedding_provider: newProvider,
          embedding_model: newModel.trim() || undefined,
          embedding_base_url: newBaseUrl.trim() || undefined,
          embedding_dim:
            newProvider === "gemini" && Number.isFinite(parsedDim) && parsedDim >= 64
              ? Math.floor(parsedDim)
              : 0,
        } : {}),
      });
      await onRefreshKnowledgeBases();
      setActiveKbId(created.kb_id);
      onSelectKb(created.kb_id);
      resetCreateForm();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "创建知识库失败");
    } finally {
      setCreateSubmitting(false);
    }
  };

  const handleDeleteKb = async (kbId: string) => {
    setErrorMsg("");
    try {
      await deleteKnowledgeBase(kbId);
      setConfirmDeleteKbId(null);
      const updated = await onRefreshKnowledgeBases();
      if (activeKbId === kbId) {
        setActiveKbId(updated[0]?.kb_id ?? null);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "删除知识库失败");
    }
  };

  const handleUploadFiles = async (fileList: FileList | File[] | null) => {
    if (!activeKb || !fileList || uploading) return;
    const files = Array.from(fileList);
    if (files.length === 0) return;

    setErrorMsg("");
    setUploadSuccess("");
    setUploadErrors([]);
    setUploading(true);
    try {
      const res = await uploadKnowledgeDocuments(activeKb.kb_id, files, {
        apiKey: resolveEffectiveApiKey(activeKb, true),
        enableOcr,
      });
      await onRefreshKnowledgeBases();
      await loadDocuments(activeKb.kb_id);
      const totalPages = res.documents.reduce((acc, d) => acc + d.page_count, 0);
      const totalChunks = res.documents.reduce((acc, d) => acc + d.chunk_count, 0);
      if (res.documents.length > 0) {
        setUploadSuccess(
          `成功入库 ${res.documents.length} 份文件（共 ${totalPages} 页，生成 ${totalChunks} 个向量切片），已持久化到本地数据库。`,
        );
      }
      if (res.errors.length > 0) {
        setUploadErrors(res.errors);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "文档入库失败");
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteDoc = async (docId: string) => {
    if (!activeKb || deletingDocId) return;
    setErrorMsg("");
    setDeletingDocId(docId);
    try {
      await deleteKnowledgeDocument(activeKb.kb_id, docId);
      await onRefreshKnowledgeBases();
      await loadDocuments(activeKb.kb_id);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "删除文档失败");
    } finally {
      setDeletingDocId(null);
    }
  };

  const handleSearchTest = async () => {
    if (!activeKb || !searchQuery.trim() || searching) return;
    setErrorMsg("");
    setSearching(true);
    setHasSearched(true);
    try {
      const res = await searchKnowledgeBase(activeKb.kb_id, {
        query: searchQuery.trim(),
        top_k: 5,
        api_key: resolveEffectiveApiKey(activeKb),
      });
      setSearchHits(res.hits);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "知识库检索测试失败");
      setSearchHits([]);
    } finally {
      setSearching(false);
    }
  };

  const isActiveSelected = activeKb ? selectedIds.includes(activeKb.kb_id) : false;
  const geminiKeyReady = Boolean(geminiEnvConfigured || geminiApiKey.trim());

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-[2px] dark:bg-black/65 dark:backdrop-blur-xs"
      onClick={onClose}
    >
      <div
        className="flex h-[84vh] w-full max-w-4xl flex-col overflow-hidden rounded-3xl border border-neutral-200 bg-white shadow-[0_24px_64px_rgba(0,0,0,0.16)] dark:border-white/[0.11] dark:bg-[#141724] dark:shadow-[0_28px_84px_rgba(0,0,0,0.75)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4 dark:border-white/[0.08]">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-violet-50 text-violet-700 dark:bg-violet-500/[0.16] dark:text-violet-300">
              <Database className="h-4 w-4" />
            </span>
            <div>
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                本地向量知识库
              </h3>
              <p className="text-[11.5px] text-neutral-400 dark:text-[#747A96]">
                上传学校 PDF / 讲义，一步转成本地 SQLite 混合向量库，随时勾选让 AI 精准参考
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 text-neutral-400 transition hover:bg-neutral-100 hover:text-neutral-800 dark:text-[#7F85A3] dark:hover:bg-white/[0.08] dark:hover:text-[#F3F4FA]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Global Error Bar */}
        {errorMsg && (
          <div className="mx-5 mt-3 flex items-center justify-between gap-2 rounded-2xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-xs text-rose-700 dark:border-rose-400/25 dark:bg-rose-500/[0.11] dark:text-rose-200">
            <span className="flex items-center gap-1.5">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              {errorMsg}
            </span>
            <button type="button" onClick={() => setErrorMsg("")}>
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* Main 2-Column Layout */}
        <div className="grid min-h-0 flex-1 grid-cols-1 divide-y divide-neutral-100 dark:divide-white/[0.08] md:grid-cols-[276px_1fr] md:divide-x md:divide-y-0">
          {/* Left Column: Knowledge Bases */}
          <div className="flex min-h-0 flex-col bg-[#fafafa] dark:bg-[#0E101B]">
            <div className="flex items-center justify-between px-4 py-3">
              <span className="text-xs font-semibold text-neutral-700 dark:text-[#DCE0F0]">
                知识库列表 ({knowledgeBases.length})
              </span>
              <button
                type="button"
                onClick={() => {
                  setCreating((v) => !v);
                  setErrorMsg("");
                }}
                className="inline-flex items-center gap-1 rounded-full bg-neutral-950 px-2.5 py-1 text-[11px] font-medium text-white transition hover:bg-neutral-800 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
              >
                <Plus className="h-3 w-3" />
                新建库
              </button>
            </div>

            <div className="flex-1 space-y-2 overflow-y-auto px-3 pb-4">
              {creating && (
                <div className="space-y-2.5 rounded-2xl border border-violet-200 bg-white p-3 shadow-2xs dark:border-violet-400/35 dark:bg-[#141724]">
                  <div className="text-xs font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                    新建知识库
                  </div>
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="名称，如：2026 学生手册"
                    className="w-full rounded-xl border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-900 placeholder:text-neutral-400 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#F3F4FA] dark:placeholder:text-[#666B85]"
                  />
                  <input
                    type="text"
                    value={newDesc}
                    onChange={(e) => setNewDesc(e.target.value)}
                    placeholder="简介（可选，如：学籍与选课规定）"
                    className="w-full rounded-xl border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-900 placeholder:text-neutral-400 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#F3F4FA] dark:placeholder:text-[#666B85]"
                  />

                  {allowCustomEmbedding && <div className="space-y-1">
                    <label className="block text-[11px] font-medium text-neutral-500 dark:text-[#8E94B0]">
                      向量化引擎（建库后固定，保证空间一致）
                    </label>
                    <select
                      value={newProvider}
                      onChange={(e) => {
                        const val = e.target.value as EmbeddingProviderType;
                        setNewProvider(val);
                        setNewModel("");
                        setNewBaseUrl(
                          val === "ollama"
                            ? (defaultEmbedding?.ollama_default_url ??
                                "http://127.0.0.1:11434/v1")
                            : "",
                        );
                      }}
                      className="w-full rounded-xl border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-800 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6]"
                    >
                      <option value="gemini">
                        Gemini Embedding 2（推荐 · 复用 Gemini Key）
                      </option>
                      <option value="ollama">
                        本地 Ollama（离线免费 · 需本机安装）
                      </option>
                      <option value="openai_compat">
                        自定义 OpenAI 兼容 Embeddings 接口
                      </option>
                    </select>
                  </div>}

                  {allowCustomEmbedding && newProvider === "gemini" && (
                    <div className="grid grid-cols-2 gap-2">
                      <input
                        type="text"
                        value={newModel}
                        onChange={(e) => setNewModel(e.target.value)}
                        placeholder={defaultEmbedding?.model ?? "gemini-embedding-2"}
                        className="rounded-xl border border-neutral-200 bg-white px-2.5 py-1.5 font-mono text-[11px] text-neutral-800 placeholder:text-neutral-400 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85]"
                      />
                      <select
                        value={newDim}
                        onChange={(e) => setNewDim(e.target.value)}
                        className="rounded-xl border border-neutral-200 bg-white px-2 py-1.5 font-mono text-[11px] text-neutral-800 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6]"
                      >
                        <option value="768">768 维</option>
                        <option value="1536">1536 维（推荐）</option>
                        <option value="3072">3072 维</option>
                      </select>
                    </div>
                  )}

                  {allowCustomEmbedding && newProvider !== "gemini" && (
                    <div className="space-y-1.5">
                      <input
                        type="text"
                        value={newModel}
                        onChange={(e) => setNewModel(e.target.value)}
                        placeholder={
                          newProvider === "ollama"
                            ? "模型名（默认 bge-m3）"
                            : "Embedding 模型名（必填）"
                        }
                        className="w-full rounded-xl border border-neutral-200 bg-white px-2.5 py-1.5 font-mono text-[11px] text-neutral-800 placeholder:text-neutral-400 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85]"
                      />
                      <input
                        type="text"
                        value={newBaseUrl}
                        onChange={(e) => setNewBaseUrl(e.target.value)}
                        placeholder={
                          newProvider === "ollama"
                            ? "http://127.0.0.1:11434/v1"
                            : "Base URL（如 https://.../v1）"
                        }
                        className="w-full rounded-xl border border-neutral-200 bg-white px-2.5 py-1.5 font-mono text-[11px] text-neutral-800 placeholder:text-neutral-400 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85]"
                      />
                    </div>
                  )}

                  {!allowCustomEmbedding && defaultEmbedding?.model && (
                    <p className="text-[11px] text-neutral-500 dark:text-[#8E94B0]">向量模型由管理员配置 · {defaultEmbedding.model}</p>
                  )}

                  <div className="flex items-center justify-end gap-1.5 pt-1">
                    <button
                      type="button"
                      onClick={resetCreateForm}
                      className="rounded-full px-3 py-1 text-[11px] text-neutral-500 transition hover:bg-neutral-100 dark:text-[#8E94B0] dark:hover:bg-white/[0.07]"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      disabled={!newName.trim() || createSubmitting}
                      onClick={() => void handleCreateKb()}
                      className="inline-flex items-center gap-1 rounded-full bg-violet-700 px-3 py-1 text-[11px] font-medium text-white transition hover:bg-violet-800 disabled:opacity-45 dark:bg-violet-500 dark:hover:bg-violet-400"
                    >
                      {createSubmitting && (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      )}
                      创建
                    </button>
                  </div>
                </div>
              )}

              {knowledgeBases.length === 0 && !creating ? (
                <div className="rounded-2xl border border-dashed border-neutral-200 bg-white px-4 py-8 text-center dark:border-white/[0.12] dark:bg-[#141724]">
                  <BookOpen className="mx-auto h-6 w-6 text-neutral-300 dark:text-white/25" />
                  <div className="mt-2 text-xs font-medium text-neutral-700 dark:text-[#DCE0F0]">
                    还没有本地知识库
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-neutral-400 dark:text-[#6E7490]">
                    点击右上角「新建库」，再拖入学校 PDF 即可一键建成永久向量库
                  </p>
                </div>
              ) : (
                knowledgeBases.map((kb) => {
                  const isActive = kb.kb_id === activeKb?.kb_id;
                  const isChecked = selectedIds.includes(kb.kb_id);
                  const confirmingDelete = confirmDeleteKbId === kb.kb_id;

                  return (
                    <div
                      key={kb.kb_id}
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        setActiveKbId(kb.kb_id);
                        setConfirmDeleteKbId(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setActiveKbId(kb.kb_id);
                        }
                      }}
                      className={`group rounded-2xl border p-3 transition ${
                        isActive
                          ? "border-violet-300 bg-white shadow-2xs dark:border-violet-400/40 dark:bg-[#171B2C]"
                          : "border-transparent bg-white/75 hover:border-neutral-200 hover:bg-white dark:bg-[#121522] dark:hover:border-white/[0.12] dark:hover:bg-[#161928]"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                            {kb.name}
                          </div>
                          {kb.description && (
                            <div className="mt-0.5 truncate text-[11px] text-neutral-400 dark:text-[#747A96]">
                              {kb.description}
                            </div>
                          )}
                        </div>

                        {confirmingDelete ? (
                          <div
                            className="flex shrink-0 items-center gap-1"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <button
                              type="button"
                              onClick={() => void handleDeleteKb(kb.kb_id)}
                              className="rounded-md bg-rose-600 px-1.5 py-0.5 text-[10px] font-medium text-white hover:bg-rose-700"
                            >
                              删
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteKbId(null)}
                              className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-600 hover:bg-neutral-200 dark:bg-white/[0.08] dark:text-[#C8CCE0] dark:hover:bg-white/[0.14]"
                            >
                              取消
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            title="删除整个知识库"
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDeleteKbId(kb.kb_id);
                            }}
                            className="rounded-md p-1 text-neutral-400 opacity-0 transition hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100 dark:text-[#747A96] dark:hover:bg-rose-500/[0.14] dark:hover:text-rose-300"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>

                      <div className="mt-2 flex items-center justify-between gap-2 text-[10.5px] text-neutral-400 dark:text-[#747A96]">
                        <span>
                          {kb.document_count} 文档 · {kb.chunk_count} 切片
                        </span>
                        <span className="truncate rounded-full bg-neutral-100 px-1.5 py-0.5 font-mono text-[10px] text-neutral-500 dark:bg-white/[0.07] dark:text-[#9EA4BF]">
                          {providerDisplayName(kb.embedding_provider)}
                        </span>
                      </div>

                      <label
                        className="mt-2 flex cursor-pointer items-center gap-1.5 border-t border-neutral-100 pt-2 text-[11px] font-medium text-neutral-700 dark:border-white/[0.07] dark:text-[#C8CCE0]"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => onToggleSelect(kb.kb_id)}
                          className="h-3.5 w-3.5 accent-violet-700 dark:accent-violet-400"
                        />
                        <span
                          className={
                            isChecked ? "text-violet-700 dark:text-violet-300" : "text-neutral-500 dark:text-[#8E94B0]"
                          }
                        >
                          {isChecked ? "当前对话已启用参考" : "在当前对话中参考"}
                        </span>
                      </label>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Right Column: Active KB Documents & Upload & Search Test */}
          <div className="flex min-h-0 flex-col overflow-y-auto px-5 py-4">
            {!activeKb ? (
              <div className="flex flex-1 flex-col items-center justify-center text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50 text-violet-600 dark:bg-violet-500/[0.15] dark:text-violet-300">
                  <Sparkles className="h-5 w-5" />
                </div>
                <h4 className="mt-3 text-sm font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                  三步建立你的学校 PDF 向量知识库
                </h4>
                <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-neutral-500 dark:text-[#8E94B0]">
                  1. 点击「新建库」为某门课程或学校手册建库；2. 拖入 PDF 一键按页切片并持久存入 SQLite；3. 在输入框底部勾选该库，AI 回答时将精准引用页码。
                </p>
                <button
                  type="button"
                  onClick={() => setCreating(true)}
                  className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-violet-700 px-4 py-2 text-xs font-medium text-white transition hover:bg-violet-800 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
                >
                  <Plus className="h-3.5 w-3.5" />
                  新建第一个知识库
                </button>
              </div>
            ) : (
              <div className="space-y-5">
                {/* Active KB Summary & Chat Toggle */}
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                        {activeKb.name}
                      </h4>
                      <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-[10.5px] text-neutral-600 dark:bg-white/[0.08] dark:text-[#A9AEC6]">
                        {activeKb.embedding_provider} · {activeKb.embedding_model}
                        {activeKb.embedding_dim > 0
                          ? ` (${activeKb.embedding_dim}d)`
                          : ""}
                      </span>
                    </div>
                    {activeKb.description && (
                      <p className="mt-0.5 text-xs text-neutral-500 dark:text-[#8E94B0]">
                        {activeKb.description}
                      </p>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => onToggleSelect(activeKb.kb_id)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                      isActiveSelected
                        ? "border-violet-200 bg-violet-50 text-violet-800 hover:bg-violet-100 dark:border-violet-400/35 dark:bg-violet-500/[0.16] dark:text-violet-200 dark:hover:bg-violet-500/[0.24]"
                        : "border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50 hover:text-neutral-900 dark:border-white/[0.12] dark:bg-white/[0.05] dark:text-[#C8CCE0] dark:hover:bg-white/[0.1] dark:hover:text-[#F3F4FA]"
                    }`}
                  >
                    {isActiveSelected ? (
                      <>
                        <Check className="h-3.5 w-3.5 text-violet-700 dark:text-violet-300" />
                        当前对话已参考
                      </>
                    ) : (
                      <>
                        <BookOpen className="h-3.5 w-3.5 text-neutral-400 dark:text-[#7F85A3]" />
                        点击在当前对话启用
                      </>
                    )}
                  </button>
                </div>

                {/* One-Step Upload Dropzone */}
                <section className="space-y-2.5">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPTED_FILE_EXTENSIONS}
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      void handleUploadFiles(e.target.files);
                      e.target.value = "";
                    }}
                  />

                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => {
                      if (!uploading) fileInputRef.current?.click();
                    }}
                    onKeyDown={(e) => {
                      if ((e.key === "Enter" || e.key === " ") && !uploading) {
                        e.preventDefault();
                        fileInputRef.current?.click();
                      }
                    }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDragOver(true);
                    }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDragOver(false);
                      void handleUploadFiles(e.dataTransfer.files);
                    }}
                    className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-6 text-center transition ${
                      dragOver
                        ? "border-violet-400 bg-violet-50/70 dark:border-violet-400 dark:bg-violet-500/[0.14]"
                        : "border-neutral-200 bg-[#fafafa] hover:border-violet-300 hover:bg-violet-50/30 dark:border-white/[0.13] dark:bg-[#0E101B] dark:hover:border-violet-400/45 dark:hover:bg-violet-500/[0.08]"
                    } ${uploading ? "pointer-events-none opacity-75" : ""}`}
                  >
                    {uploading ? (
                      <>
                        <Loader2 className="h-6 w-6 animate-spin text-violet-600 dark:text-violet-400" />
                        <div className="mt-2 text-xs font-medium text-neutral-800 dark:text-[#E5E8F5]">
                          正在逐页解析、切块并生成向量，请稍候...
                        </div>
                        <div className="mt-0.5 text-[11px] text-neutral-400 dark:text-[#747A96]">
                          写入完成后会永久保存在本地 SQLite 数据库
                        </div>
                      </>
                    ) : (
                      <>
                        <Upload className="h-5 w-5 text-violet-600 dark:text-violet-400" />
                        <div className="mt-2 text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                          点击选择或拖入学校 PDF / Markdown / 文本文件
                        </div>
                        <div className="mt-0.5 text-[11px] text-neutral-400 dark:text-[#747A96]">
                          支持批量上传（单文件上限 50MB），自动保留页码并建立向量 + 全文双重索引
                        </div>
                      </>
                    )}
                  </div>

                  {/* Upload Options: OCR + Embedding Key */}
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-neutral-100 bg-[#fafafa] px-3.5 py-2.5 text-[11.5px] dark:border-white/[0.08] dark:bg-[#0E101B]">
                    <label className="flex cursor-pointer items-center gap-2 text-neutral-700 dark:text-[#C8CCE0]">
                      <input
                        type="checkbox"
                        checked={enableOcr}
                        onChange={(e) => setEnableOcr(e.target.checked)}
                        className="h-3.5 w-3.5 accent-violet-700 dark:accent-violet-400"
                      />
                      <span>扫描件 PDF 启用视觉 OCR 兜底（纯图片页自动识字）</span>
                    </label>

                    {activeKb.embedding_provider === "gemini" && geminiKeyReady ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700 dark:text-emerald-300">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        已复用 Gemini Key
                      </span>
                    ) : activeKb.embedding_provider === "ollama" ? (
                      <span className="text-[11px] text-neutral-500 dark:text-[#8E94B0]">
                        本地 Ollama 无需 API Key
                      </span>
                    ) : (
                      <input
                        type="password"
                        value={customApiKey}
                        onChange={(e) => setCustomApiKey(e.target.value)}
                        placeholder={
                          activeKb.embedding_provider === "gemini"
                            ? "未检测到 Gemini Key，请在此输入或去「模型设置」填写"
                            : "Embedding API Key（如接口需要）"
                        }
                        className="w-full rounded-xl border border-neutral-200 bg-white px-2.5 py-1 font-mono text-[11px] text-neutral-800 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85] sm:w-64"
                      />
                    )}
                  </div>

                  {uploadSuccess && (
                    <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50/80 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-400/25 dark:bg-emerald-500/[0.1] dark:text-emerald-200">
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                      <span>{uploadSuccess}</span>
                    </div>
                  )}

                  {uploadErrors.length > 0 && (
                    <div className="space-y-1 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-800 dark:border-amber-400/25 dark:bg-amber-500/[0.1] dark:text-amber-200">
                      {uploadErrors.map((err, idx) => (
                        <div key={`${err.filename}-${idx}`}>
                          <span className="font-semibold">{err.filename}</span>：
                          {err.error}
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                {/* Document List */}
                <section className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                      已入库文档 ({documents.length})
                    </span>
                    <span className="text-[11px] text-neutral-400 dark:text-[#747A96]">
                      共 {activeKb.chunk_count} 个检索切片
                    </span>
                  </div>

                  {docsLoading ? (
                    <div className="flex items-center gap-2 py-4 text-xs text-neutral-400 dark:text-[#747A96]">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      正在加载文档列表...
                    </div>
                  ) : documents.length === 0 ? (
                    <div className="rounded-2xl border border-neutral-100 bg-[#fafafa] px-4 py-5 text-center text-xs text-neutral-400 dark:border-white/[0.08] dark:bg-[#0E101B] dark:text-[#6E7490]">
                      该知识库还没有文档，请从上方拖入 PDF 或文本文件
                    </div>
                  ) : (
                    <div className="divide-y divide-neutral-100 overflow-hidden rounded-2xl border border-neutral-200/80 dark:divide-white/[0.07] dark:border-white/[0.09]">
                      {documents.map((doc) => (
                        <div
                          key={doc.doc_id}
                          className="flex items-center justify-between gap-3 bg-white px-3.5 py-2.5 text-xs dark:bg-[#0E101B]"
                        >
                          <div className="flex min-w-0 items-center gap-2.5">
                            <FileText className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" />
                            <div className="min-w-0">
                              <div className="truncate font-medium text-neutral-900 dark:text-[#F3F4FA]">
                                {doc.filename}
                              </div>
                              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-neutral-400 dark:text-[#747A96]">
                                <span>{doc.page_count} 页</span>
                                <span>·</span>
                                <span>{doc.chunk_count} 切片</span>
                                {doc.ocr_pages > 0 && (
                                  <span className="rounded-full bg-violet-50 px-1.5 py-0.2 text-[10px] font-medium text-violet-700 dark:bg-violet-500/[0.16] dark:text-violet-300">
                                    OCR {doc.ocr_pages} 页
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>

                          <button
                            type="button"
                            disabled={deletingDocId === doc.doc_id}
                            onClick={() => void handleDeleteDoc(doc.doc_id)}
                            title="从知识库移除此文档"
                            className="rounded-lg p-1.5 text-neutral-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40 dark:text-[#747A96] dark:hover:bg-rose-500/[0.14] dark:hover:text-rose-300"
                          >
                            {deletingDocId === doc.doc_id ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="h-3.5 w-3.5" />
                            )}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                {/* Instant Hybrid Search Verification */}
                {activeKb.chunk_count > 0 && (
                  <section className="space-y-2.5 border-t border-neutral-100 pt-4 dark:border-white/[0.08]">
                    <div className="text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                      即时检索验证（语义向量 + FTS5 全文混合召回）
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void handleSearchTest();
                          }
                        }}
                        placeholder="输入学生问题测试命中效果，如：缓考怎么申请？或 CS302 学分"
                        className="min-w-0 flex-1 rounded-xl border border-neutral-200 bg-white px-3 py-2 text-xs text-neutral-900 placeholder:text-neutral-400 focus:border-violet-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#F3F4FA] dark:placeholder:text-[#666B85]"
                      />
                      <button
                        type="button"
                        disabled={!searchQuery.trim() || searching}
                        onClick={() => void handleSearchTest()}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-neutral-950 px-3.5 py-2 text-xs font-medium text-white transition hover:bg-neutral-800 disabled:opacity-45 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
                      >
                        {searching ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Search className="h-3.5 w-3.5" />
                        )}
                        检索测试
                      </button>
                    </div>

                    {hasSearched && !searching && searchHits.length === 0 && (
                      <div className="rounded-xl bg-[#fafafa] px-3.5 py-3 text-xs text-neutral-400 dark:bg-[#0E101B] dark:text-[#6E7490]">
                        未命中相关片段，请尝试更换关键词。
                      </div>
                    )}

                    {searchHits.length > 0 && (
                      <div className="space-y-2">
                        {searchHits.map((hit, index) => (
                          <div
                            key={hit.chunk_id}
                            className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3 text-xs dark:border-white/[0.08] dark:bg-[#0E101B]"
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <span className="font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                                #{index + 1} 《{hit.filename}》第 {hit.page_number} 页
                              </span>
                              <div className="flex items-center gap-1.5 text-[10.5px]">
                                {hit.vector_score !== null && (
                                  <span className="rounded-full bg-violet-100/80 px-2 py-0.5 font-mono text-violet-800 dark:bg-violet-500/[0.16] dark:text-violet-200">
                                    语义 {hit.vector_score.toFixed(3)}
                                  </span>
                                )}
                                {hit.fts_rank !== null && (
                                  <span className="rounded-full bg-emerald-100/80 px-2 py-0.5 font-mono text-emerald-800 dark:bg-emerald-500/[0.14] dark:text-emerald-300">
                                    全文 #{hit.fts_rank}
                                  </span>
                                )}
                              </div>
                            </div>
                            <p className="mt-1.5 max-h-28 overflow-y-auto whitespace-pre-wrap text-[11.5px] leading-relaxed text-neutral-600 dark:text-[#A9AEC6]">
                              {hit.content}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-neutral-100 px-5 py-3 dark:border-white/[0.08]">
          <div className="text-[11px] text-neutral-400 dark:text-[#747A96]">
            {selectedIds.length > 0
              ? `当前对话已勾选 ${selectedIds.length} 个知识库，提问时 AI 将自动按需检索并标注页码`
              : "未勾选任何知识库：AI 不会检索本地文档，按常规对话模式回答"}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full bg-neutral-950 px-5 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-800 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  );
}
