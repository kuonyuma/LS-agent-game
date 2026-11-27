import {
  ArrowLeft,
  ArrowUp,
  BookOpen,
  Check,
  ChevronRight,
  Database,
  FileCode2,
  FileUp,
  FolderOpen,
  FolderSearch,
  ImagePlus,
  Plus,
  Square,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type {
  AttachmentPreview,
  KnowledgeBaseSummary,
  ReasoningEffort,
} from "../types";
import { EffortSliderInline } from "./EffortSliderInline";
import { WorkspaceBar } from "./WorkspaceBar";

interface ComposerProps {
  value: string;
  attachments: AttachmentPreview[];
  workspace: string;
  recentWorkspaces: string[];
  pickingNativeWorkspace?: boolean;
  supportsVision: boolean;
  modelPicker: ReactNode;
  knowledgeBases: KnowledgeBaseSummary[];
  selectedKnowledgeBaseIds: string[];
  thinkingLevels: ReasoningEffort[];
  reasoningEffort: ReasoningEffort;
  effortModelKey: string;
  effortEntryLevel?: ReasoningEffort;
  isRunning: boolean;
  centered?: boolean;
  onChange: (value: string) => void;
  onAttachImages: (files: FileList | null) => void;
  onAttachDocuments: (files: FileList | null) => void;
  onRemoveAttachment: (index: number) => void;
  onToggleKnowledgeBase: (kbId: string) => void;
  onClearKnowledgeBases: () => void;
  onOpenKnowledgeModal: () => void;
  onChangeEffort: (effort: ReasoningEffort) => void;
  onEffortPreview?: (ratio: number | null) => void;
  onEffortEntry?: () => void;
  onSelectWorkspace: (path: string) => void;
  onPickNativeWorkspace: () => void;
  onOpenWorkspaceBrowser: () => void;
  onSend: () => void;
  onStop: () => void;
}

function formatFileSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Composer({
  value,
  attachments,
  workspace,
  recentWorkspaces,
  pickingNativeWorkspace = false,
  supportsVision,
  modelPicker,
  knowledgeBases,
  selectedKnowledgeBaseIds,
  thinkingLevels,
  reasoningEffort,
  effortModelKey,
  effortEntryLevel,
  isRunning,
  centered = false,
  onChange,
  onAttachImages,
  onAttachDocuments,
  onRemoveAttachment,
  onToggleKnowledgeBase,
  onClearKnowledgeBases,
  onOpenKnowledgeModal,
  onChangeEffort,
  onEffortPreview,
  onEffortEntry,
  onSelectWorkspace,
  onPickNativeWorkspace,
  onOpenWorkspaceBrowser,
  onSend,
  onStop,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const plusMenuRef = useRef<HTMLDivElement | null>(null);
  const plusButtonRef = useRef<HTMLButtonElement | null>(null);
  const knowledgeBackRef = useRef<HTMLButtonElement | null>(null);
  const [plusMenu, setPlusMenu] = useState<"actions" | "knowledge" | null>(null);
  const plusMenuOpen = plusMenu !== null && !isRunning;

  const selectedKbs = knowledgeBases.filter((kb) =>
    selectedKnowledgeBaseIds.includes(kb.kb_id),
  );

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (!plusMenu) return;
    if (isRunning) {
      setPlusMenu(null);
      return;
    }
    if (plusMenu === "knowledge") knowledgeBackRef.current?.focus();

    const handleOutside = (event: MouseEvent) => {
      if (
        plusMenuRef.current &&
        !plusMenuRef.current.contains(event.target as Node)
      ) {
        setPlusMenu(null);
      }
    };
    const handleEsc = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPlusMenu(null);
        plusButtonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("keydown", handleEsc);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("keydown", handleEsc);
    };
  }, [plusMenu, isRunning]);

  return (
    <div className="w-full">
      {/* Codex-style workspace selector docked directly above the input card */}
      <WorkspaceBar
        workspace={workspace}
        recentWorkspaces={recentWorkspaces}
        disabled={isRunning}
        pickingNative={pickingNativeWorkspace}
        onSelectWorkspace={onSelectWorkspace}
        onPickNative={onPickNativeWorkspace}
        onOpenBrowser={onOpenWorkspaceBrowser}
      />

      <div
        className={`rounded-[26px] border bg-white transition-all duration-200 ${
          centered
            ? "border-violet-200/80 shadow-[0_8px_34px_rgba(109,40,217,0.09)] focus-within:border-violet-300 focus-within:shadow-[0_8px_36px_rgba(109,40,217,0.14)]"
            : "border-neutral-200/90 shadow-[0_2px_14px_rgba(0,0,0,0.04)] focus-within:border-neutral-300 focus-within:shadow-[0_4px_22px_rgba(0,0,0,0.07)]"
        }`}
      >
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 px-4 pt-3">
            {attachments.map((att, idx) => (
              <div
                key={`${att.name}-${idx}`}
                className="group relative inline-flex items-center gap-2 rounded-xl border border-neutral-200 bg-neutral-50 px-2.5 py-1.5 text-xs text-neutral-700"
              >
                {att.kind === "image" && att.data_base64 ? (
                  <img
                    src={`data:${att.mime_type};base64,${att.data_base64}`}
                    alt={att.name}
                    className="h-6 w-6 rounded-md object-cover"
                  />
                ) : (
                  <FileCode2 className="h-4 w-4 shrink-0 text-neutral-500" />
                )}
                <div className="min-w-0">
                  <div className="max-w-36 truncate font-medium">{att.name}</div>
                  {att.size ? (
                    <div className="text-[10px] text-neutral-400">
                      {formatFileSize(att.size)}
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(idx)}
                  className="rounded-full p-0.5 text-neutral-400 transition hover:bg-neutral-200 hover:text-neutral-800"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="px-4 pt-3.5 pb-1.5">
          <textarea
            ref={textareaRef}
            rows={centered ? 2 : 1}
            value={value}
            disabled={isRunning}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder="向智启 Agent 提问，或布置一个需要调用工具、规划与协同的任务..."
            className="max-h-52 w-full resize-none bg-transparent text-[15px] leading-relaxed text-neutral-900 placeholder:text-neutral-400 focus:outline-none disabled:opacity-60"
          />
        </div>

        <div className="flex items-end gap-2 px-3 pb-2.5 pt-1">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            {/* Hidden file inputs */}
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                onAttachImages(e.target.files);
                e.target.value = "";
              }}
            />
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.markdown,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.ini,.cfg,.csv,.log,.html,.css,.scss,.sql,.sh,.ps1,.xml,.java,.go,.rs,.c,.cpp,.h,.hpp,.pdf,text/*"
              multiple
              className="hidden"
              onChange={(e) => {
                onAttachDocuments(e.target.files);
                e.target.value = "";
              }}
            />

            {/* Plus Action Menu Button */}
            <div ref={plusMenuRef} className="relative shrink-0">
              <button
                ref={plusButtonRef}
                type="button"
                disabled={isRunning}
                aria-expanded={plusMenuOpen}
                onClick={() => setPlusMenu((menu) => (menu ? null : "actions"))}
                title="添加附件、引用知识库或切换目录"
                className={`inline-flex h-8 w-8 items-center justify-center rounded-full border transition disabled:opacity-40 ${
                  plusMenuOpen
                    ? "border-neutral-300 bg-neutral-100 text-neutral-950"
                    : "border-transparent text-neutral-600 hover:bg-neutral-100 hover:text-neutral-950"
                }`}
              >
                <Plus
                  className={`h-[18px] w-[18px] transition-transform duration-200 ${
                    plusMenuOpen ? "rotate-45" : ""
                  }`}
                />
              </button>

              {plusMenuOpen && plusMenu === "actions" && (
                <div className="effort-popover-enter absolute bottom-full left-0 z-40 mb-2 max-h-[45dvh] w-60 max-w-[calc(100dvw-3rem)] overflow-y-auto rounded-2xl border border-neutral-200/90 bg-white p-1.5 shadow-[0_14px_40px_rgba(0,0,0,0.13)]">
                  <button
                    type="button"
                    onClick={() => {
                      setPlusMenu(null);
                      fileInputRef.current?.click();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-neutral-100"
                  >
                    <FileUp className="h-4 w-4 shrink-0 text-neutral-600" />
                    <div>
                      <div>传递文件</div>
                      <div className="text-[10.5px] font-normal text-neutral-400">
                        代码、文档、日志、JSON / PDF
                      </div>
                    </div>
                  </button>

                  <button
                    type="button"
                    disabled={!supportsVision}
                    onClick={() => {
                      if (!supportsVision) return;
                      setPlusMenu(null);
                      imageInputRef.current?.click();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
                  >
                    <ImagePlus className="h-4 w-4 shrink-0 text-neutral-600" />
                    <div>
                      <div>上传图片</div>
                      <div className="text-[10.5px] font-normal text-neutral-400">
                        {supportsVision
                          ? "支持多模态视觉理解"
                          : "当前模型不支持视觉输入"}
                      </div>
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={() => setPlusMenu("knowledge")}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-violet-50 hover:text-violet-900"
                  >
                    <BookOpen className="h-4 w-4 shrink-0 text-violet-600" />
                    <span className="flex-1">引用知识库</span>
                    {selectedKbs.length > 0 && (
                      <span className="text-[10.5px] font-normal text-violet-700">
                        已选 {selectedKbs.length} 个
                      </span>
                    )}
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setPlusMenu(null);
                      onOpenKnowledgeModal();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-violet-50 hover:text-violet-900"
                  >
                    <Database className="h-4 w-4 shrink-0 text-violet-600" />
                    <div>
                      <div>导入 PDF 到向量知识库</div>
                      <div className="text-[10.5px] font-normal text-neutral-400">
                        一键转持久向量库，长期复用检索
                      </div>
                    </div>
                  </button>

                  <div className="my-1 border-t border-neutral-100" />

                  <button
                    type="button"
                    onClick={() => {
                      setPlusMenu(null);
                      onPickNativeWorkspace();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 hover:text-neutral-950"
                  >
                    <FolderOpen className="h-4 w-4 shrink-0 text-neutral-500" />
                    <span>切换工作目录（系统窗口）</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setPlusMenu(null);
                      onOpenWorkspaceBrowser();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 hover:text-neutral-950"
                  >
                    <FolderSearch className="h-4 w-4 shrink-0 text-neutral-500" />
                    <span>浏览本机目录 / 粘贴路径</span>
                  </button>
                </div>
              )}
              {plusMenuOpen && plusMenu === "knowledge" && (
                <div className="effort-popover-enter absolute bottom-full left-0 z-40 mb-2 flex max-h-[45dvh] w-72 max-w-[calc(100dvw-3rem)] flex-col overflow-hidden rounded-2xl border border-neutral-200/90 bg-white p-1.5 shadow-[0_14px_40px_rgba(0,0,0,0.13)]">
                  <div className="flex shrink-0 items-center justify-between gap-2 px-1.5 py-0.5">
                    <button
                      ref={knowledgeBackRef}
                      type="button"
                      aria-label="返回添加菜单"
                      onClick={() => {
                        setPlusMenu("actions");
                        plusButtonRef.current?.focus();
                      }}
                      className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-xs font-semibold text-neutral-700 transition hover:bg-neutral-100"
                    >
                      <ArrowLeft className="h-3.5 w-3.5 shrink-0" />
                      <span>引用知识库</span>
                    </button>
                    {selectedKbs.length > 0 && (
                      <button
                        type="button"
                        onClick={onClearKnowledgeBases}
                        className="text-[11px] font-medium text-neutral-400 transition hover:text-neutral-800"
                      >
                        全部取消
                      </button>
                    )}
                  </div>

                  {knowledgeBases.length === 0 ? (
                    <div className="min-h-0 overflow-y-auto px-2.5 py-4 text-center text-[11.5px] text-neutral-400">
                      暂无知识库，点击下方上传 PDF 一键建库
                    </div>
                  ) : (
                    <div className="min-h-0 max-h-52 space-y-0.5 overflow-y-auto py-0.5">
                      {knowledgeBases.map((kb) => {
                        const checked = selectedKnowledgeBaseIds.includes(kb.kb_id);
                        return (
                          <button
                            key={kb.kb_id}
                            type="button"
                            aria-pressed={checked}
                            onClick={() => onToggleKnowledgeBase(kb.kb_id)}
                            className={`flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-xs transition ${
                              checked
                                ? "bg-violet-50/90 text-violet-950"
                                : "text-neutral-700 hover:bg-neutral-100"
                            }`}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="truncate font-medium">
                                  {kb.name}
                                </span>
                                {kb.chunk_count === 0 && (
                                  <span className="shrink-0 rounded bg-amber-100 px-1 py-0.2 text-[9.5px] text-amber-700">
                                    空库
                                  </span>
                                )}
                              </div>
                              <div className="text-[10.5px] text-neutral-400">
                                {kb.document_count} 文档 · {kb.chunk_count} 切片
                              </div>
                            </div>
                            <span
                              className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-md border transition ${
                                checked
                                  ? "border-violet-600 bg-violet-600 text-white"
                                  : "border-neutral-300 bg-white"
                              }`}
                            >
                              {checked && <Check className="h-3 w-3 stroke-[2.5]" />}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}

                  <div className="mt-1 shrink-0 border-t border-neutral-100 pt-1">
                    <button
                      type="button"
                      onClick={() => {
                        setPlusMenu(null);
                        onOpenKnowledgeModal();
                      }}
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl px-2.5 py-2 text-xs font-medium text-violet-700 transition hover:bg-violet-50"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      管理知识库 / 上传 PDF...
                    </button>
                  </div>
                </div>
              )}
            </div>

            <div
              role="group"
              aria-label={thinkingLevels.length > 0 ? "模型与思考强度" : "模型"}
              className="inline-flex min-w-0 max-w-full items-center rounded-full border border-neutral-200 bg-neutral-100 p-0.5 text-xs font-medium text-neutral-600 transition-colors focus-within:border-neutral-300 hover:border-neutral-300"
            >
              {modelPicker}
              {thinkingLevels.length > 0 && (
                <EffortSliderInline
                  key={effortModelKey}
                  levels={thinkingLevels}
                  value={reasoningEffort}
                  disabled={isRunning}
                  onChange={onChangeEffort}
                  onPreview={onEffortPreview}
                  entryLevel={effortEntryLevel}
                  onEntry={onEffortEntry}
                />
              )}
            </div>
          </div>

          <div className="flex h-9 shrink-0 items-center">
            {isRunning ? (
              <button
                type="button"
                onClick={onStop}
                title="停止当前运行"
                className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-neutral-950 text-white transition hover:bg-neutral-800 active:scale-95"
              >
                <Square className="h-3 w-3 fill-current" />
              </button>
            ) : (
              <button
                type="button"
                disabled={!value.trim()}
                onClick={onSend}
                title="发送（Enter）"
                className={`inline-flex h-8 w-8 items-center justify-center rounded-full text-white transition active:scale-95 disabled:bg-neutral-200 disabled:text-neutral-400 ${
                  centered ? "bg-violet-700 hover:bg-violet-800" : "bg-neutral-950 hover:bg-neutral-800"
                }`}
              >
                <ArrowUp className="h-4 w-4 stroke-[2.2]" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
