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
  Image,
  Plus,
  Square,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type {
  AttachmentPreview,
  ImageAspectRatio,
  ImageOptions,
  KnowledgeBaseSummary,
  ReasoningEffort,
} from "../types";
import { EffortSliderInline } from "./EffortSliderInline";
import { WorkspaceBar } from "./WorkspaceBar";

interface ComposerProps {
  value: string;
  placeholder?: string;
  attachments: AttachmentPreview[];
  workspace: string;
  showWorkspace: boolean;
  recentWorkspaces: string[];
  pickingNativeWorkspace?: boolean;
  supportsVision: boolean;
  canGenerateImage: boolean;
  imageOptions: ImageOptions | null;
  onImageOptionsChange: (options: ImageOptions | null) => void;
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
  placeholder = "向智启 Agent 提问，或布置一个需要调用工具、规划与协同的任务...",
  attachments,
  workspace,
  showWorkspace,
  recentWorkspaces,
  pickingNativeWorkspace = false,
  supportsVision,
  canGenerateImage,
  imageOptions,
  onImageOptionsChange,
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
      {showWorkspace && (
        <WorkspaceBar
          workspace={workspace}
          recentWorkspaces={recentWorkspaces}
          disabled={isRunning}
          pickingNative={pickingNativeWorkspace}
          onSelectWorkspace={onSelectWorkspace}
          onPickNative={onPickNativeWorkspace}
          onOpenBrowser={onOpenWorkspaceBrowser}
        />
      )}

      <div
        className={`rounded-[26px] border bg-white transition-all duration-200 ${
          centered
            ? "border-violet-200/80 shadow-[0_8px_34px_rgba(109,40,217,0.09)] focus-within:border-violet-300 focus-within:shadow-[0_8px_36px_rgba(109,40,217,0.14)] dark:border-violet-400/25 dark:bg-[#121420]/78 dark:backdrop-blur-xl dark:shadow-[0_14px_46px_rgba(88,28,135,0.22),inset_0_1px_0_rgba(255,255,255,0.06)] dark:focus-within:border-violet-400/45 dark:focus-within:shadow-[0_16px_52px_rgba(109,40,217,0.32),inset_0_1px_0_rgba(255,255,255,0.09)]"
            : "border-neutral-200/90 shadow-[0_2px_14px_rgba(0,0,0,0.04)] focus-within:border-neutral-300 focus-within:shadow-[0_4px_22px_rgba(0,0,0,0.07)] dark:border-white/[0.11] dark:bg-[#121420] dark:shadow-[0_8px_28px_rgba(0,0,0,0.42)] dark:focus-within:border-white/[0.2] dark:focus-within:shadow-[0_10px_34px_rgba(0,0,0,0.55)]"
        }`}
      >
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 px-4 pt-3">
            {attachments.map((att, idx) => (
              <div
                key={`${att.name}-${idx}`}
                className="group relative inline-flex items-center gap-2 rounded-xl border border-neutral-200 bg-neutral-50 px-2.5 py-1.5 text-xs text-neutral-700 dark:border-white/[0.1] dark:bg-white/[0.05] dark:text-[#DCE0F0]"
              >
                {att.kind === "image" && att.data_base64 ? (
                  <img
                    src={`data:${att.mime_type};base64,${att.data_base64}`}
                    alt={att.name}
                    className="h-6 w-6 rounded-md object-cover"
                  />
                ) : (
                  <FileCode2 className="h-4 w-4 shrink-0 text-neutral-500 dark:text-[#8E94B0]" />
                )}
                <div className="min-w-0">
                  <div className="max-w-36 truncate font-medium">{att.name}</div>
                  {att.size ? (
                    <div className="text-[10px] text-neutral-400 dark:text-[#6E7490]">
                      {formatFileSize(att.size)}
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(idx)}
                  className="rounded-full p-0.5 text-neutral-400 transition hover:bg-neutral-200 hover:text-neutral-800 dark:text-[#7F85A3] dark:hover:bg-white/[0.12] dark:hover:text-[#F3F4FA]"
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
            placeholder={imageOptions ? "描述你想生成的图片" : placeholder}
            className="max-h-52 w-full resize-none bg-transparent text-[15px] leading-relaxed text-neutral-900 placeholder:text-neutral-400 focus:outline-none disabled:opacity-60 dark:text-[#F3F4FA] dark:placeholder:text-[#6E7490]"
          />
        </div>

        {imageOptions && (
          <div className="flex flex-wrap items-center gap-2 px-4 pb-2 text-xs">
            <button
              type="button"
              disabled={isRunning}
              onClick={() => onImageOptionsChange(null)}
              aria-label="退出生图模式"
              className="inline-flex items-center gap-1.5 rounded-full bg-violet-100 px-2.5 py-1.5 font-medium text-violet-800 hover:bg-violet-200 disabled:opacity-60 dark:bg-violet-500/20 dark:text-violet-200 dark:hover:bg-violet-500/30"
            >
              <Image size={14} aria-hidden="true" /> 生图 <X size={12} aria-hidden="true" />
            </button>
            <label className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-2 py-1 text-neutral-600 dark:border-white/15 dark:text-[#C8CCE0]">
              宽高比
              <select
                value={imageOptions.aspect_ratio ?? ""}
                disabled={isRunning}
                onChange={(event) => onImageOptionsChange({
                  aspect_ratio: (event.target.value as ImageAspectRatio) || undefined,
                })}
                className="rounded bg-transparent px-0.5 focus-visible:outline-violet-500 dark:[color-scheme:dark]"
              >
                <option value="">自动</option>
                <option>1:1</option>
                <option>9:16</option>
                <option>3:4</option>
                <option>4:3</option>
                <option>16:9</option>
              </select>
            </label>
          </div>
        )}

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
              accept=".txt,.md,.markdown,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.ini,.cfg,.csv,.log,.html,.css,.scss,.sql,.sh,.ps1,.xml,.java,.go,.rs,.c,.cpp,.h,.hpp,.pdf,.docx,.xlsx,text/*"
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
                title="添加附件、使用工具或切换目录"
                className={`inline-flex h-8 w-8 items-center justify-center rounded-full border transition disabled:opacity-40 ${
                  plusMenuOpen
                    ? "border-neutral-300 bg-neutral-100 text-neutral-950 dark:border-white/[0.18] dark:bg-white/[0.11] dark:text-[#F3F4FA]"
                    : "border-transparent text-neutral-600 hover:bg-neutral-100 hover:text-neutral-950 dark:text-[#9DA3BE] dark:hover:bg-white/[0.08] dark:hover:text-[#F3F4FA]"
                }`}
              >
                <Plus
                  className={`h-[18px] w-[18px] transition-transform duration-200 ${
                    plusMenuOpen ? "rotate-45" : ""
                  }`}
                />
              </button>

              {plusMenuOpen && plusMenu === "actions" && (
                <div className="effort-popover-enter absolute bottom-full left-0 z-40 mb-2 max-h-[45dvh] w-60 max-w-[calc(100dvw-3rem)] overflow-y-auto rounded-2xl border border-neutral-200/90 bg-white p-1.5 shadow-[0_14px_40px_rgba(0,0,0,0.13)] dark:border-white/[0.11] dark:bg-[#171A29] dark:shadow-[0_18px_48px_rgba(0,0,0,0.68)]">
                  <button
                    type="button"
                    onClick={() => {
                      setPlusMenu(null);
                      fileInputRef.current?.click();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-neutral-100 dark:text-[#E5E8F5] dark:hover:bg-white/[0.06]"
                  >
                    <FileUp className="h-4 w-4 shrink-0 text-neutral-600 dark:text-[#989EB8]" />
                    <div>
                      <div>传递文件</div>
                      <div className="text-[10.5px] font-normal text-neutral-400 dark:text-[#6E7490]">
                        Word、Excel、PDF、代码和文本
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
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent dark:text-[#E5E8F5] dark:hover:bg-white/[0.06]"
                  >
                    <ImagePlus className="h-4 w-4 shrink-0 text-neutral-600 dark:text-[#989EB8]" />
                    <div>
                      <div>上传图片</div>
                      <div className="text-[10.5px] font-normal text-neutral-400 dark:text-[#6E7490]">
                        {supportsVision
                          ? "支持多模态视觉理解"
                          : "当前模型不支持视觉输入"}
                      </div>
                    </div>
                  </button>

                  <button
                    type="button"
                    disabled={!canGenerateImage}
                    aria-pressed={imageOptions !== null}
                    onClick={() => {
                      setPlusMenu(null);
                      onImageOptionsChange(imageOptions ?? {});
                      textareaRef.current?.focus();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition enabled:hover:bg-violet-50 enabled:hover:text-violet-900 disabled:cursor-not-allowed disabled:text-neutral-500 dark:text-[#E5E8F5] dark:enabled:hover:bg-violet-500/15 dark:enabled:hover:text-violet-200 dark:disabled:text-[#9DA3BE]"
                  >
                    <Image className={`h-4 w-4 shrink-0 ${canGenerateImage ? "text-violet-600 dark:text-violet-300" : "text-neutral-400 dark:text-[#6E7490]"}`} aria-hidden="true" />
                    <span>
                      <span className="block">生成图片</span>
                      {!canGenerateImage && (
                        <span className="block text-[10.5px] font-normal text-neutral-500 dark:text-[#9DA3BE]">
                          当前工作台未启用百炼生图
                        </span>
                      )}
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setPlusMenu("knowledge")}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-violet-50 hover:text-violet-900 dark:text-[#E5E8F5] dark:hover:bg-violet-500/[0.14] dark:hover:text-violet-200"
                  >
                    <BookOpen className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" />
                    <span className="flex-1">引用知识库</span>
                    {selectedKbs.length > 0 && (
                      <span className="text-[10.5px] font-normal text-violet-700 dark:text-violet-300">
                        已选 {selectedKbs.length} 个
                      </span>
                    )}
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-neutral-400 dark:text-[#747A96]" />
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setPlusMenu(null);
                      onOpenKnowledgeModal();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-violet-50 hover:text-violet-900 dark:text-[#E5E8F5] dark:hover:bg-violet-500/[0.14] dark:hover:text-violet-200"
                  >
                    <Database className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" />
                    <div>
                      <div>导入 PDF 到向量知识库</div>
                      <div className="text-[10.5px] font-normal text-neutral-400 dark:text-[#6E7490]">
                        一键转持久向量库，长期复用检索
                      </div>
                    </div>
                  </button>

                  {showWorkspace && (
                    <>
                      <div className="my-1 border-t border-neutral-100 dark:border-white/[0.08]" />
                      <button
                        type="button"
                        onClick={() => {
                          setPlusMenu(null);
                          onPickNativeWorkspace();
                        }}
                        className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 hover:text-neutral-950 dark:text-[#C8CCE0] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
                      >
                        <FolderOpen className="h-4 w-4 shrink-0 text-neutral-500 dark:text-[#8E94B0]" />
                        <span>切换工作目录（系统窗口）</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setPlusMenu(null);
                          onOpenWorkspaceBrowser();
                        }}
                        className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 hover:text-neutral-950 dark:text-[#C8CCE0] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
                      >
                        <FolderSearch className="h-4 w-4 shrink-0 text-neutral-500 dark:text-[#8E94B0]" />
                        <span>浏览本机目录 / 粘贴路径</span>
                      </button>
                    </>
                  )}
                </div>
              )}
              {plusMenuOpen && plusMenu === "knowledge" && (
                <div className="effort-popover-enter absolute bottom-full left-0 z-40 mb-2 flex max-h-[45dvh] w-72 max-w-[calc(100dvw-3rem)] flex-col overflow-hidden rounded-2xl border border-neutral-200/90 bg-white p-1.5 shadow-[0_14px_40px_rgba(0,0,0,0.13)] dark:border-white/[0.11] dark:bg-[#171A29] dark:shadow-[0_18px_48px_rgba(0,0,0,0.68)]">
                  <div className="flex shrink-0 items-center justify-between gap-2 px-1.5 py-0.5">
                    <button
                      ref={knowledgeBackRef}
                      type="button"
                      aria-label="返回添加菜单"
                      onClick={() => {
                        setPlusMenu("actions");
                        plusButtonRef.current?.focus();
                      }}
                      className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-xs font-semibold text-neutral-700 transition hover:bg-neutral-100 dark:text-[#DCE0F0] dark:hover:bg-white/[0.06]"
                    >
                      <ArrowLeft className="h-3.5 w-3.5 shrink-0" />
                      <span>引用知识库</span>
                    </button>
                    {selectedKbs.length > 0 && (
                      <button
                        type="button"
                        onClick={onClearKnowledgeBases}
                        className="text-[11px] font-medium text-neutral-400 transition hover:text-neutral-800 dark:text-[#787E99] dark:hover:text-[#F3F4FA]"
                      >
                        全部取消
                      </button>
                    )}
                  </div>

                  {knowledgeBases.length === 0 ? (
                    <div className="min-h-0 overflow-y-auto px-2.5 py-4 text-center text-[11.5px] text-neutral-400 dark:text-[#6E7490]">
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
                                ? "bg-violet-50/90 text-violet-950 dark:bg-violet-500/[0.16] dark:text-violet-100"
                                : "text-neutral-700 hover:bg-neutral-100 dark:text-[#C8CCE0] dark:hover:bg-white/[0.06]"
                            }`}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="truncate font-medium">
                                  {kb.name}
                                </span>
                                {kb.chunk_count === 0 && (
                                  <span className="shrink-0 rounded bg-amber-100 px-1 py-0.2 text-[9.5px] text-amber-700 dark:bg-amber-500/[0.16] dark:text-amber-300">
                                    空库
                                  </span>
                                )}
                              </div>
                              <div className="text-[10.5px] text-neutral-400 dark:text-[#6E7490]">
                                {kb.document_count} 文档 · {kb.chunk_count} 切片
                              </div>
                            </div>
                            <span
                              className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-md border transition ${
                                checked
                                  ? "border-violet-600 bg-violet-600 text-white dark:border-violet-400 dark:bg-violet-500"
                                  : "border-neutral-300 bg-white dark:border-white/20 dark:bg-white/[0.04]"
                              }`}
                            >
                              {checked && <Check className="h-3 w-3 stroke-[2.5]" />}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}

                  <div className="mt-1 shrink-0 border-t border-neutral-100 pt-1 dark:border-white/[0.08]">
                    <button
                      type="button"
                      onClick={() => {
                        setPlusMenu(null);
                        onOpenKnowledgeModal();
                      }}
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl px-2.5 py-2 text-xs font-medium text-violet-700 transition hover:bg-violet-50 dark:text-violet-300 dark:hover:bg-violet-500/[0.14]"
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
              className="inline-flex min-w-0 max-w-full items-center rounded-full border border-neutral-200 bg-neutral-100 p-0.5 text-xs font-medium text-neutral-600 transition-colors focus-within:border-neutral-300 hover:border-neutral-300 dark:border-white/[0.1] dark:bg-white/[0.05] dark:text-[#C8CCE0] dark:focus-within:border-white/[0.2] dark:hover:border-white/[0.16]"
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
                className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-neutral-950 text-white transition hover:bg-neutral-800 active:scale-95 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
              >
                <Square className="h-3 w-3 fill-current" />
              </button>
            ) : (
              <button
                type="button"
                disabled={!value.trim()}
                onClick={onSend}
                title="发送（Enter）"
                className={`inline-flex h-8 w-8 items-center justify-center rounded-full text-white transition active:scale-95 disabled:bg-neutral-200 disabled:text-neutral-400 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white dark:disabled:bg-white/[0.1] dark:disabled:text-white/30 ${
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
