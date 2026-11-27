import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  FileCode2,
  Loader2,
  ShieldAlert,
  Sparkles,
  Terminal,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PendingApproval, ToolTrace, UIMessage } from "../types";
import { findVendorSpec, rgba } from "./intro/vendorCatalog";

interface MessageBubbleProps {
  message: UIMessage;
  pendingApprovals?: PendingApproval[];
  onApprove?: (approval: PendingApproval, approved: boolean) => void;
  handoffEcho?: boolean;
}

function VendorMicroMark({ provider }: { provider?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const spec = findVendorSpec(provider);

  useEffect(() => {
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
    spec.drawMark(ctx, 7.2, 0, 0.25);
  }, [spec]);

  if (!spec) {
    return <span className="h-2 w-2 animate-pulse rounded-full bg-neutral-900" />;
  }

  return (
    <span
      className="relative inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-white"
      style={{
        boxShadow: `0 0 0 1px ${rgba(spec.trail.head, 0.26)}, 0 0 12px ${rgba(spec.trail.head, 0.28)}`,
      }}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-[-2px] animate-ping rounded-full opacity-35"
        style={{ border: `1.2px solid ${rgba(spec.trail.head, 0.55)}` }}
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
    case "fetch_web_page":
      return { verb: "阅读网页", target: String(args.url ?? "").slice(0, 64) };
    case "search_knowledge_base":
      return { verb: "检索知识库", target: String(args.query ?? "") };
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

function CodexToolRow({ trace }: { trace: ToolTrace }) {
  const [open, setOpen] = useState(trace.is_error);
  const { verb, target } = summarizeToolCall(trace);

  const icon = {
    running: <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-neutral-500" />,
    awaiting_approval: (
      <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-600" />
    ),
    completed: <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />,
    error: <XCircle className="h-3.5 w-3.5 shrink-0 text-rose-600" />,
  }[trace.status];

  return (
    <div className="text-[13px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left transition hover:bg-neutral-100/90"
      >
        {icon}
        <span className="font-medium text-neutral-700">{verb}</span>
        {target && (
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-neutral-400">
            {target}
          </span>
        )}
        <span className="ml-auto text-neutral-300 transition group-hover:text-neutral-500">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </span>
      </button>

      {open && (
        <div className="mt-1 ml-5 space-y-2 border-l border-neutral-200 pl-3 pb-1 text-xs">
          <div>
            <div className="mb-1 text-[11px] text-neutral-400">参数</div>
            <pre className="max-h-40 overflow-auto rounded-lg bg-[#141414] p-2.5 font-mono text-[11px] text-neutral-200">
              {JSON.stringify(trace.arguments, null, 2)}
            </pre>
          </div>
          {trace.result && (
            <div>
              <div className="mb-1 text-[11px] text-neutral-400">输出</div>
              <pre
                className={`max-h-56 overflow-auto whitespace-pre-wrap rounded-lg p-2.5 font-mono text-[11px] ${
                  trace.is_error
                    ? "bg-rose-50 text-rose-800"
                    : "bg-neutral-100 text-neutral-700"
                }`}
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

function CopyablePre({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);

  const extractText = (node: ReactNode): string => {
    if (typeof node === "string" || typeof node === "number") return String(node);
    if (Array.isArray(node)) return node.map(extractText).join("");
    if (node && typeof node === "object" && "props" in node) {
      const props = (node as { props?: { children?: ReactNode } }).props;
      return extractText(props?.children);
    }
    return "";
  };

  const handleCopy = async () => {
    const raw = extractText(children);
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // ignore clipboard errors
    }
  };

  return (
    <div className="group relative my-3">
      <button
        type="button"
        onClick={() => void handleCopy()}
        className="absolute right-2.5 top-2.5 inline-flex items-center gap-1 rounded-md bg-neutral-800 px-2 py-1 text-[11px] text-neutral-300 opacity-0 transition hover:bg-neutral-700 hover:text-white group-hover:opacity-100"
      >
        {copied ? (
          <>
            <Check className="h-3 w-3 text-emerald-400" /> 已复制
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" /> 复制
          </>
        )}
      </button>
      <pre>{children}</pre>
    </div>
  );
}

export function MessageBubble({
  message,
  pendingApprovals = [],
  onApprove,
  handoffEcho = false,
}: MessageBubbleProps) {
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [copiedReply, setCopiedReply] = useState(false);
  const vendorSpec = findVendorSpec(message.provider);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[82%] rounded-[22px] bg-[#f4f4f4] px-4 py-2.5 text-[15px] leading-relaxed text-neutral-900">
          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {message.attachments.map((att, idx) => (
                <div
                  key={`${att.name}-${idx}`}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-white px-2.5 py-1 text-xs text-neutral-600 shadow-2xs"
                >
                  {att.kind === "image" && att.data_base64 ? (
                    <img
                      src={`data:${att.mime_type};base64,${att.data_base64}`}
                      alt={att.name}
                      className="h-8 w-8 rounded-md object-cover"
                    />
                  ) : (
                    <FileCode2 className="h-3.5 w-3.5 shrink-0 text-neutral-500" />
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
      {handoffEcho && vendorSpec && (
        <div
          aria-hidden="true"
          className="assistant-handoff-echo pointer-events-none h-[2px] w-48 max-w-[60%] rounded-full"
          style={{
            background: `linear-gradient(90deg, ${rgba(vendorSpec.trail.head, 0.85)} 0%, ${rgba(vendorSpec.trail.mid, 0.52)} 55%, ${rgba(vendorSpec.trail.tail, 0)} 100%)`,
          }}
        />
      )}

      {/* Gemini-style subtle thinking disclosure */}
      {message.thinking && (
        <div>
          <button
            type="button"
            onClick={() => setThinkingOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-full py-1 text-[13px] font-medium text-neutral-500 transition hover:text-neutral-900"
          >
            <Sparkles
              className={`h-3.5 w-3.5 ${
                message.streaming && !message.text ? "animate-pulse" : "text-neutral-400"
              }`}
              style={
                message.streaming && !message.text && vendorSpec
                  ? { color: rgba(vendorSpec.trail.head, 0.92) }
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
            <div className="mt-1.5 max-h-72 overflow-y-auto border-l-2 border-neutral-200 pl-3.5 text-[13px] leading-relaxed whitespace-pre-wrap text-neutral-500">
              {message.thinking}
            </div>
          )}
        </div>
      )}

      {/* Codex-style compact tool execution timeline */}
      {message.tool_calls && message.tool_calls.length > 0 && (
        <div className="space-y-0.5 py-0.5">
          {message.tool_calls.map((trace) => (
            <CodexToolRow key={trace.call_id} trace={trace} />
          ))}
        </div>
      )}

      {/* Codex-style authoritative inline approval gate */}
      {pendingApprovals.length > 0 && onApprove && (
        <div className="space-y-2.5 py-1">
          {pendingApprovals.map((approval) => (
            <div
              key={approval.approval_id}
              className="rounded-2xl border border-amber-200/90 bg-amber-50/55 p-4"
            >
              <div className="flex items-start gap-2.5">
                <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-neutral-900">
                    <span>请求执行高危操作</span>
                    <code className="rounded-md bg-amber-100/90 px-1.5 py-0.5 font-mono text-xs text-amber-900">
                      {approval.tool_name}
                    </code>
                  </div>
                  <pre className="mt-2 max-h-40 overflow-auto rounded-xl bg-[#141414] p-3 font-mono text-[11.5px] text-neutral-200">
                    {JSON.stringify(approval.tool_arguments, null, 2)}
                  </pre>
                  <div className="mt-1.5 truncate font-mono text-[10.5px] text-neutral-500">
                    SHA-256 指纹：{approval.request_hash}
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      type="button"
                      disabled={approval.submitting}
                      onClick={() => onApprove(approval, true)}
                      className="rounded-full bg-neutral-950 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-800 disabled:opacity-50"
                    >
                      允许本次执行
                    </button>
                    <button
                      type="button"
                      disabled={approval.submitting}
                      onClick={() => onApprove(approval, false)}
                      className="rounded-full border border-neutral-300 bg-white px-4 py-1.5 text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 disabled:opacity-50"
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
        <div className="flex items-start gap-2.5 rounded-2xl border border-rose-200 bg-rose-50/70 px-4 py-3 text-xs text-rose-800">
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
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              pre: ({ children }) => <CopyablePre>{children}</CopyablePre>,
            }}
          >
            {message.text}
          </ReactMarkdown>
        </div>
      )}

      {/* Streaming waiting indicator before first token */}
      {message.streaming &&
        !message.text &&
        !message.thinking &&
        (!message.tool_calls || message.tool_calls.length === 0) && (
          <div className="flex items-center gap-2.5 py-1 text-xs text-neutral-500">
            <VendorMicroMark provider={message.provider} />
            <span>正在连接模型...</span>
          </div>
        )}

      {/* Error line */}
      {message.error && !message.safety_blocked && (
        <div className="rounded-xl border border-rose-200/80 bg-rose-50/60 px-3.5 py-2.5 text-xs text-rose-700">
          {message.error}
        </div>
      )}

      {/* Quiet ChatGPT / Gemini style action & telemetry footer */}
      {!message.streaming && (
        <div className="flex flex-wrap items-center gap-3 pt-1 text-[11px] text-neutral-400">
          {message.text && !message.safety_blocked && (
            <button
              type="button"
              onClick={() => void handleCopyReply()}
              title="复制回答"
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 transition hover:bg-neutral-100 hover:text-neutral-700"
            >
              {copiedReply ? (
                <>
                  <Check className="h-3.5 w-3.5 text-emerald-600" />
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
