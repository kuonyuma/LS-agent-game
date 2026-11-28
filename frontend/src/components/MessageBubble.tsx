import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileCode2,
  Loader2,
  Presentation,
  ShieldAlert,
  Sparkles,
  Terminal,
  XCircle,
} from "lucide-react";
import {
  createContext,
  isValidElement,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { useTheme } from "../theme";
import type { PendingApproval, ToolTrace, UIMessage } from "../types";
import { findVendorSpec, resolveVendorTrail, rgba } from "./intro/vendorCatalog";
import { CodeBlock } from "./CodeBlock";
import { GeneratedImageCard, ImageGenerationProgress } from "./ImageGeneration";
import {
  RichChartCard,
  RichImageGallery,
  RichMarkdownLink,
  collectVerifiedMediaUrls,
} from "./RichOutputBlocks";

interface MessageBubbleProps {
  message: UIMessage;
  sessionId?: string | null;
  pendingApprovals?: PendingApproval[];
  onApprove?: (approval: PendingApproval, approved: boolean) => void;
  handoffEcho?: boolean;
}

function VendorMicroMark({ provider }: { provider?: string }) {
  const { isDark } = useTheme();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const spec = findVendorSpec(provider);
  const trail = spec ? resolveVendorTrail(spec, isDark) : null;

  useLayoutEffect(() => {
    if (!spec) return;
    const cv = canvasRef.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const size = 18;
    cv.width = Math.round(size * dpr);
    cv.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.translate(size / 2, size / 2);
    spec.drawMark(ctx, 7.2, 0, 0.25, isDark);
  }, [spec, isDark]);

  if (!spec || !trail) {
    return <span className="h-2 w-2 animate-pulse rounded-full bg-neutral-900 dark:bg-[#F3F4FA]" />;
  }

  return (
    <span
      className="relative inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-white dark:bg-[#121522]/90"
      style={{
        boxShadow: isDark
          ? `0 1px 0 rgba(255,255,255,0.16) inset, 0 0 0 1px ${rgba(trail.head, 0.42)}, 0 0 14px ${rgba(trail.head, 0.36)}`
          : `0 0 0 1px ${rgba(trail.head, 0.26)}, 0 0 12px ${rgba(trail.head, 0.28)}`,
      }}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-[-2px] animate-ping rounded-full opacity-35"
        style={{ border: `1.2px solid ${rgba(trail.head, 0.55)}` }}
      />
      <canvas ref={canvasRef} className="block h-[18px] w-[18px]" />
    </span>
  );
}

function summarizeToolCall(trace: ToolTrace): { verb: string; target: string } {
  const args = trace.arguments ?? {};
  switch (trace.name) {
    case "read_file":
      return { verb: "读取文件", target: String(args.path ?? "") };
    case "list_files":
      return { verb: "浏览目录", target: String(args.path ?? ".") };
    case "write_file":
      return { verb: "写入文件", target: String(args.path ?? "") };
    case "edit_file":
      return { verb: "编辑文件", target: String(args.path ?? "") };
    case "load_yaml":
      return { verb: "读取配置", target: String(args.path ?? "") };
    case "run_command":
      return { verb: "执行命令", target: String(args.command ?? "").slice(0, 64) };
    case "run_quality_check":
      return { verb: "代码质检", target: String(args.check ?? "lint") };
    case "web_search":
      return { verb: "联网搜索", target: String(args.query ?? "") };
    case "search_images":
      return { verb: "检索参考配图", target: String(args.query ?? "") };
    case "fetch_web_page":
      return { verb: "阅读网页", target: String(args.url ?? "").slice(0, 64) };
    case "search_papers":
      return {
        verb: "检索 CS 学术论文",
        target: [args.query, args.venue ? `@${String(args.venue)}` : ""]
          .filter(Boolean)
          .join(" ")
          .slice(0, 72),
      };
    case "inspect_paper":
      return {
        verb: "核验论文与引文",
        target: String(args.paper_id ?? "").slice(0, 64),
      };
    case "read_paper_fulltext":
      return {
        verb: "精读论文全文",
        target: [
          args.paper_id || args.url || "",
          args.section_query ? `· ${String(args.section_query)}` : "",
        ]
          .filter(Boolean)
          .join(" ")
          .slice(0, 72),
      };
    case "search_knowledge_base":
      return { verb: "检索知识库", target: String(args.query ?? "") };
    case "generate_pptx":
      return {
        verb: {
          awaiting_approval: "等待确认 PPT 内容",
          running: "正在排版并导出 PPT",
          completed: "PPT 排版完成",
          error: "PPT 制作未完成",
        }[trace.status],
        target: String(args.title ?? ""),
      };
    case "update_plan":
      return {
        verb: "更新执行计划",
        target: String(args.action ?? args.goal ?? ""),
      };
    case "reflect":
      return { verb: "结构化反思", target: String(args.strategy ?? "") };
    case "subagent": {
      const role = String(args.role ?? args.agent_id ?? "explorer");
      const desc = String(args.description ?? args.action ?? "");
      return { verb: `协同子代理 · ${role}`, target: desc };
    }
    default:
      return { verb: trace.name, target: "" };
  }
}

function PresentationApproval({ approval, onApprove }: {
  approval: PendingApproval;
  onApprove: NonNullable<MessageBubbleProps["onApprove"]>;
}) {
  const { title, slides_markdown, language, instructions } = approval.tool_arguments;
  const slides = Array.isArray(slides_markdown)
    ? slides_markdown.filter((slide): slide is string => typeof slide === "string")
    : [];
  return (
    <section className="max-w-xl rounded-2xl border border-violet-200 bg-violet-50/65 p-4 dark:border-violet-400/25 dark:bg-violet-500/[0.09]" aria-label="确认制作 PPT">
      <div className="flex items-start gap-3">
        <Presentation className="mt-0.5 h-5 w-5 shrink-0 text-violet-700 dark:text-violet-300" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-neutral-900 dark:text-[#F3F4FA]">确认制作 PPT</h3>
          {typeof title === "string" && (
            <p className="mt-1 break-words text-sm text-neutral-700 dark:text-[#DCE0F0]">{title}{slides.length > 0 && ` · ${slides.length} 页`}</p>
          )}
          <p className="mt-1 text-xs leading-relaxed text-neutral-600 dark:text-[#AAB0CD]">确认后，逐页内容会提交给 PPT 排版服务。</p>
          {slides.length > 0 && (
            <details className="mt-3 rounded-xl border border-violet-100 bg-white/80 px-3 py-2 dark:border-white/[0.1] dark:bg-white/[0.04]">
              <summary className="cursor-pointer text-xs font-medium text-violet-800 dark:text-violet-200">查看逐页内容</summary>
              <div className="mt-2 max-h-72 space-y-3 overflow-y-auto pr-2 text-xs" role="region" aria-label="PPT 逐页内容" tabIndex={0}>
                {slides.map((slide, index) => (
                  <div key={index} className="border-t border-neutral-200 pt-2 dark:border-white/[0.1]">
                    <p className="mb-1 text-xs text-neutral-600 dark:text-[#AAB0CD]">第 {index + 1} 页</p>
                    <div className="markdown-body break-words">
                      <ReactMarkdown remarkPlugins={[remarkGfm]} disallowedElements={["a", "img"]} unwrapDisallowed skipHtml>
                        {slide}
                      </ReactMarkdown>
                    </div>
                  </div>
                ))}
                {typeof language === "string" && language !== "Chinese" && (
                  <p className="break-words text-neutral-600 dark:text-[#AAB0CD]">语言为 {language}</p>
                )}
                {typeof instructions === "string" && instructions.trim() && (
                  <p className="whitespace-pre-wrap break-words text-neutral-600 dark:text-[#AAB0CD]">排版要求<br />{instructions}</p>
                )}
              </div>
            </details>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={approval.submitting}
              onClick={() => onApprove(approval, true)}
              className="rounded-full bg-violet-700 px-4 py-2 text-xs font-medium text-white transition hover:bg-violet-800 disabled:opacity-50 dark:bg-violet-200 dark:text-violet-950 dark:hover:bg-violet-100"
            >
              生成 PPT
            </button>
            <button
              type="button"
              disabled={approval.submitting}
              onClick={() => onApprove(approval, false)}
              className="rounded-full px-4 py-2 text-xs font-medium text-neutral-700 transition hover:bg-white disabled:opacity-50 dark:text-[#DCE0F0] dark:hover:bg-white/[0.07]"
            >
              取消
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function CodexToolRow({ trace }: { trace: ToolTrace }) {
  const [open, setOpen] = useState(trace.is_error);
  const { verb, target } = summarizeToolCall(trace);

  const icon = {
    running: <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-neutral-500 dark:text-[#8E94B0]" />,
    awaiting_approval: (
      <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
    ),
    completed: <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />,
    error: <XCircle className="h-3.5 w-3.5 shrink-0 text-rose-600 dark:text-rose-400" />,
  }[trace.status];

  return (
    <div className="text-[13px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left transition hover:bg-neutral-100/90 dark:hover:bg-white/[0.06]"
      >
        {icon}
        <span className="font-medium text-neutral-700 dark:text-[#DCE0F0]">{verb}</span>
        {target && (
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-neutral-400 dark:text-[#747A96]">
            {target}
          </span>
        )}
        <span className="ml-auto text-neutral-300 transition group-hover:text-neutral-500 dark:text-white/25 dark:group-hover:text-[#9CA2BC]">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </span>
      </button>

      {open && (
        <div className="mt-1 ml-5 space-y-2 border-l border-neutral-200 pl-3 pb-1 text-xs dark:border-white/[0.11]">
          <div>
            <div className="mb-1 text-[11px] text-neutral-400 dark:text-[#6E7490]">参数</div>
            <CodeBlock
              code={JSON.stringify(trace.arguments, null, 2)}
              language="json"
              compact
            />
          </div>
          {trace.result && (
            <div>
              <div className="mb-1 text-[11px] text-neutral-400 dark:text-[#6E7490]">输出</div>
              <pre
                className={`max-h-56 overflow-auto whitespace-pre-wrap rounded-lg p-2.5 text-[11.5px] leading-[1.6] ${
                  trace.is_error
                    ? "border border-rose-200/70 bg-rose-50 text-rose-800 dark:border-rose-400/20 dark:bg-rose-500/[0.12] dark:text-rose-200"
                    : "border border-[var(--code-border)] bg-[var(--code-bg-compact)] text-[var(--code-fg)]"
                }`}
                style={{ fontFamily: "var(--font-code)" }}
              >
                {trace.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function extractNodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractNodeText).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: ReactNode } }).props;
    return extractNodeText(props?.children);
  }
  return "";
}

function VerifiedMarkdownImage({
  src,
  alt,
  sessionId,
  verifiedMediaUrls,
}: {
  src?: string;
  alt?: string;
  sessionId?: string | null;
  verifiedMediaUrls: ReadonlySet<string>;
}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return null;
  const trimmed = src.trim();
  let renderUrl: string | null = null;
  if (trimmed.startsWith("/api/sessions/")) {
    renderUrl = trimmed;
  } else if (/^https?:\/\//i.test(trimmed) && sessionId && verifiedMediaUrls.has(trimmed)) {
    renderUrl = `/api/sessions/${encodeURIComponent(sessionId)}/media-proxy?url=${encodeURIComponent(trimmed)}`;
  }
  if (!renderUrl) return null;
  return (
    <figure className="my-3 overflow-hidden rounded-2xl border border-neutral-200/90 bg-white dark:border-white/[0.1] dark:bg-[#121420]">
      <img
        src={renderUrl}
        alt={alt || "参考配图"}
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        className="block max-h-[380px] w-full object-contain bg-neutral-50 dark:bg-[#0D0F18]"
      />
      {alt && (
        <figcaption className="border-t border-neutral-100 px-3 py-1.5 text-[11.5px] text-neutral-500 dark:border-white/[0.07] dark:text-[#8E94B0]">
          {alt}
        </figcaption>
      )}
    </figure>
  );
}

interface MarkdownRenderContextValue {
  isDark: boolean;
  streaming: boolean;
  sessionId: string | null;
  verifiedMediaUrls: ReadonlySet<string>;
}

const MarkdownRenderContext = createContext<MarkdownRenderContextValue>({
  isDark: false,
  streaming: false,
  sessionId: null,
  verifiedMediaUrls: new Set(),
});

function RichMarkdownPre({ children }: { children?: ReactNode }) {
  const { isDark, streaming, sessionId, verifiedMediaUrls } = useContext(
    MarkdownRenderContext,
  );
  let lang: string | undefined;
  let raw = "";
  if (isValidElement<{ className?: string; children?: ReactNode }>(children)) {
    const cls = String(children.props?.className ?? "");
    const match = /\blanguage-([^\s]+)/i.exec(cls);
    lang = match?.[1]?.toLowerCase();
    raw = extractNodeText(children.props?.children);
  } else {
    raw = extractNodeText(children);
  }

  if (lang === "chart" || lang === "viz") {
    return (
      <RichChartCard
        raw={raw}
        streaming={streaming}
        isDark={isDark}
        fallback={<CodeBlock code={raw} language="json" streaming={streaming} />}
      />
    );
  }
  if (lang === "gallery" || lang === "images") {
    return (
      <RichImageGallery
        raw={raw}
        streaming={streaming}
        sessionId={sessionId}
        verifiedMediaUrls={verifiedMediaUrls}
      />
    );
  }
  return <CodeBlock code={raw} language={lang} streaming={streaming} />;
}

function RichMarkdownTable({ children }: { children?: ReactNode }) {
  return (
    <div className="markdown-table-wrap my-3.5 overflow-x-auto">
      <table>{children}</table>
    </div>
  );
}

function RichMarkdownImg({ src, alt }: { src?: string; alt?: string }) {
  const { sessionId, verifiedMediaUrls } = useContext(MarkdownRenderContext);
  return (
    <VerifiedMarkdownImage
      src={src}
      alt={alt}
      sessionId={sessionId}
      verifiedMediaUrls={verifiedMediaUrls}
    />
  );
}

// 模块级常量，避免流式输出每个 token 重建 ReactMarkdown components 导致画廊/图表重复挂载与请求
const STATIC_MARKDOWN_COMPONENTS: Components = {
  pre: ({ children }) => <RichMarkdownPre>{children}</RichMarkdownPre>,
  table: ({ children }) => <RichMarkdownTable>{children}</RichMarkdownTable>,
  a: ({ href, title, children }) => (
    <RichMarkdownLink href={href} title={title}>
      {children}
    </RichMarkdownLink>
  ),
  img: ({ src, alt }) => <RichMarkdownImg src={src} alt={alt} />,
};

export function MessageBubble({
  message,
  sessionId = null,
  pendingApprovals = [],
  onApprove,
  handoffEcho = false,
}: MessageBubbleProps) {
  const { isDark } = useTheme();
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [copiedReply, setCopiedReply] = useState(false);
  const vendorSpec = findVendorSpec(message.provider);
  const vendorTrail = vendorSpec ? resolveVendorTrail(vendorSpec, isDark) : null;
  const verifiedMediaUrls = useMemo(
    () => collectVerifiedMediaUrls(message),
    // 仅在工具输出或已核验列表变化时重算，避免每个文本流式 token 触发上下文重建
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [message.verified_media_urls, message.tool_calls, message.images],
  );
  const markdownContextValue = useMemo<MarkdownRenderContextValue>(
    () => ({
      isDark,
      streaming: Boolean(message.streaming),
      sessionId,
      verifiedMediaUrls,
    }),
    [isDark, message.streaming, sessionId, verifiedMediaUrls],
  );
  const otherTools = message.tool_calls?.filter((trace) => trace.name !== "generate_image") ?? [];
  const imageTrace = message.tool_calls?.filter((trace) => trace.name === "generate_image").at(-1);
  const showImageProgress = imageTrace && !message.safety_blocked && !message.images?.length
    && (imageTrace.status === "error" || (message.streaming
      && (imageTrace.status === "running" || imageTrace.status === "completed")));

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[82%] rounded-[22px] bg-[#f4f4f4] px-4 py-2.5 text-[15px] leading-relaxed text-neutral-900 dark:border dark:border-white/[0.08] dark:bg-[#161826] dark:text-[#F1F3FA]">
          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {message.attachments.map((att, idx) => (
                <div
                  key={`${att.name}-${idx}`}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-white px-2.5 py-1 text-xs text-neutral-600 shadow-2xs dark:bg-white/[0.07] dark:text-[#D5D9EA]"
                >
                  {att.kind === "image" && att.data_base64 ? (
                    <img
                      src={`data:${att.mime_type};base64,${att.data_base64}`}
                      alt={att.name}
                      className="h-8 w-8 rounded-md object-cover"
                    />
                  ) : (
                    <FileCode2 className="h-3.5 w-3.5 shrink-0 text-neutral-500 dark:text-[#8E94B0]" />
                  )}
                  <span className="max-w-32 truncate">{att.name}</span>
                </div>
              ))}
            </div>
          )}
          <div className="whitespace-pre-wrap">{message.text}</div>
        </div>
      </div>
    );
  }

  const totalIn = message.usage?.input_len ?? 0;
  const totalOut = message.usage?.output_len ?? 0;
  const totalCache = message.usage?.cached_len ?? 0;

  const handleCopyReply = async () => {
    if (!message.text) return;
    try {
      await navigator.clipboard.writeText(message.text);
      setCopiedReply(true);
      setTimeout(() => setCopiedReply(false), 1600);
    } catch {
      // ignore
    }
  };

  return (
    <div
      data-handoff-anchor={handoffEcho ? "1" : undefined}
      className="group/turn relative space-y-2.5"
    >
      {/* 层次 3：首次转场接力后的 1 秒极克制厂商专属流光余韵（播完自动归零，绝不打扰阅读） */}
      {handoffEcho && vendorTrail && (
        <div
          aria-hidden="true"
          className="assistant-handoff-echo pointer-events-none h-[2px] w-48 max-w-[60%] rounded-full"
          style={{
            background: `linear-gradient(90deg, ${rgba(vendorTrail.head, 0.85)} 0%, ${rgba(vendorTrail.mid, 0.52)} 55%, ${rgba(vendorTrail.tail, 0)} 100%)`,
          }}
        />
      )}

      {/* Gemini-style subtle thinking disclosure */}
      {message.thinking && (
        <div>
          <button
            type="button"
            onClick={() => setThinkingOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full py-1 text-[13px] font-medium text-neutral-500 transition hover:text-neutral-900 dark:text-[#8E94B0] dark:hover:text-[#F3F4FA]"
          >
            <Sparkles
              className={`h-3.5 w-3.5 ${
                message.streaming && !message.text ? "animate-pulse" : "text-neutral-400 dark:text-[#747A96]"
              }`}
              style={
                message.streaming && !message.text && vendorTrail
                  ? { color: rgba(vendorTrail.head, 0.92) }
                  : undefined
              }
            />
            <span>
              {message.streaming && !message.text
                ? "正在思考推理..."
                : "查看思考过程"}
            </span>
            {thinkingOpen ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>

          {thinkingOpen && (
            <div className="mt-1.5 max-h-72 overflow-y-auto border-l-2 border-neutral-200 pl-3.5 text-[13px] leading-relaxed whitespace-pre-wrap text-neutral-500 dark:border-white/[0.14] dark:text-[#9CA2BC]">
              {message.thinking}
            </div>
          )}
        </div>
      )}

      {/* Codex-style compact tool execution timeline */}
      {otherTools.length > 0 && (
        <div className="space-y-0.5 py-0.5">
          {otherTools.map((trace) => (
            <CodexToolRow key={trace.call_id} trace={trace} />
          ))}
        </div>
      )}

      {/* Codex-style authoritative inline approval gate */}
      {pendingApprovals.length > 0 && onApprove && (
        <div className="space-y-2.5 py-1">
          {pendingApprovals.map((approval) => approval.tool_name === "generate_pptx" ? (
            <PresentationApproval key={approval.approval_id} approval={approval} onApprove={onApprove} />
          ) : (
            <div
              key={approval.approval_id}
              className="rounded-2xl border border-amber-200/90 bg-amber-50/55 p-4 dark:border-amber-400/25 dark:bg-amber-500/[0.09]"
            >
              <div className="flex items-start gap-2.5">
                <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                    <span>请求执行高危操作</span>
                    <code className="rounded-md bg-amber-100/90 px-1.5 py-0.5 font-mono text-xs text-amber-900 dark:bg-amber-400/[0.16] dark:text-amber-200">
                      {approval.tool_name}
                    </code>
                  </div>
                  <CodeBlock
                    code={JSON.stringify(approval.tool_arguments, null, 2)}
                    language="json"
                    compact
                    className="mt-2"
                  />
                  <div className="mt-1.5 truncate font-mono text-[10.5px] text-neutral-500 dark:text-[#858BA8]">
                    SHA-256 指纹：{approval.request_hash}
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      type="button"
                      disabled={approval.submitting}
                      onClick={() => onApprove(approval, true)}
                      className="rounded-full bg-neutral-950 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-800 disabled:opacity-50 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
                    >
                      允许本次执行
                    </button>
                    <button
                      type="button"
                      disabled={approval.submitting}
                      onClick={() => onApprove(approval, false)}
                      className="rounded-full border border-neutral-300 bg-white px-4 py-1.5 text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 disabled:opacity-50 dark:border-white/[0.14] dark:bg-white/[0.06] dark:text-[#DCE0F0] dark:hover:bg-white/[0.11]"
                    >
                      拒绝
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Safety block notice */}
      {message.safety_blocked && (
        <div className="flex items-start gap-2.5 rounded-2xl border border-rose-200 bg-rose-50/70 px-4 py-3 text-xs text-rose-800 dark:border-rose-400/25 dark:bg-rose-500/[0.1] dark:text-rose-200">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <div className="font-semibold">
              输出安全审核已拦截
              {message.safety_category ? `（${message.safety_category}）` : ""}
            </div>
            <div className="mt-0.5">{message.text}</div>
          </div>
        </div>
      )}

      {/* Borderless Editorial Markdown Body */}
      {!message.safety_blocked && message.text && (
        <div className="markdown-body">
          <MarkdownRenderContext.Provider value={markdownContextValue}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={STATIC_MARKDOWN_COMPONENTS}
            >
              {message.text}
            </ReactMarkdown>
          </MarkdownRenderContext.Provider>
        </div>
      )}

      {showImageProgress && <ImageGenerationProgress trace={imageTrace} />}

      {!message.safety_blocked && message.images?.map((image) => (
        <GeneratedImageCard key={image.id} image={image} />
      ))}

      {!message.safety_blocked && message.presentations?.map((artifact) => (
        <section
          key={artifact.id}
          className="max-w-md overflow-hidden rounded-2xl border border-neutral-200 bg-white dark:border-white/[0.1] dark:bg-[#121420]"
          aria-label={artifact.filename}
        >
          {artifact.thumbnail_url && (
            <img
              className="aspect-video w-full bg-neutral-50 object-contain dark:bg-[#080910]"
              src={artifact.thumbnail_url}
              alt={`${artifact.filename} 的首页预览`}
              loading="lazy"
            />
          )}
          <div className="space-y-3 p-4">
            <div className="flex items-center gap-3">
              <Presentation className="h-5 w-5 shrink-0 text-orange-600 dark:text-orange-400" aria-hidden="true" />
              <div className="min-w-0">
                <p className="break-words text-sm font-medium text-neutral-900 dark:text-[#F3F4FA]">{artifact.filename}</p>
                <p className="mt-0.5 text-xs text-neutral-500 dark:text-[#8E94B0]">PPTX · {artifact.slide_count} 页</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <a
                className="inline-flex items-center gap-1.5 rounded-lg bg-neutral-900 px-3 py-2 text-xs font-medium text-white hover:bg-neutral-700 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
                href={artifact.download_url}
                download={artifact.filename}
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                下载 PPT
              </a>
              {artifact.preview_url && (
                <a
                  className="rounded-lg border border-neutral-200 px-3 py-2 text-xs font-medium text-neutral-700 hover:bg-neutral-50 dark:border-white/[0.12] dark:text-[#D5D9EA] dark:hover:bg-white/[0.07]"
                  href={artifact.preview_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  预览全部页面
                </a>
              )}
            </div>
          </div>
        </section>
      ))}

      {/* Streaming waiting indicator before first token */}
      {message.streaming &&
        !message.text &&
        !message.thinking &&
        (!message.tool_calls || message.tool_calls.length === 0) && (
          <div className="flex items-center gap-2.5 py-1 text-xs text-neutral-500 dark:text-[#8E94B0]">
            <VendorMicroMark provider={message.provider} />
            <span>正在连接模型...</span>
          </div>
        )}

      {/* Error line */}
      {message.error && !message.safety_blocked && (
        <div className="rounded-xl border border-rose-200/80 bg-rose-50/60 px-3.5 py-2.5 text-xs text-rose-700 dark:border-rose-400/25 dark:bg-rose-500/[0.1] dark:text-rose-200">
          {message.error}
        </div>
      )}

      {/* Quiet ChatGPT / Gemini style action & telemetry footer */}
      {!message.streaming && (
        <div className="flex flex-wrap items-center gap-3 pt-1 text-[11px] text-neutral-400 dark:text-[#6E7490]">
          {message.text && !message.safety_blocked && (
            <button
              type="button"
              onClick={() => void handleCopyReply()}
              title="复制回答"
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 transition hover:bg-neutral-100 hover:text-neutral-700 dark:hover:bg-white/[0.07] dark:hover:text-[#D9DCEB]"
            >
              {copiedReply ? (
                <>
                  <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                  <span>已复制</span>
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5" />
                  <span>复制</span>
                </>
              )}
            </button>
          )}
          {message.model_name && (
            <span className="font-mono">{message.model_name}</span>
          )}
          {typeof message.turns === "number" && message.turns > 0 && (
            <span>{message.turns} 轮</span>
          )}
          {typeof message.duration_ms === "number" && message.duration_ms > 0 && (
            <span>{(message.duration_ms / 1000).toFixed(1)}s</span>
          )}
          {(totalIn > 0 || totalOut > 0) && (
            <span className="font-mono">
              {totalIn}↑ / {totalOut}↓
              {totalCache > 0 ? ` · 缓存 ${totalCache}` : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
