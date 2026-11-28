import { Check, Copy } from "lucide-react";
import {
  Fragment,
  memo,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ThemedToken } from "shiki/core";
import {
  ensureLanguageLoaded,
  formatLanguageLabel,
  isLanguageLoaded,
  resolveLanguageId,
  tokenizeCodeSync,
} from "./shikiHighlighter";

interface CodeBlockProps {
  code: string;
  language?: string | null;
  streaming?: boolean;
  /** 紧凑模式：用于工具参数 / 审批卡片内的 JSON 预览 */
  compact?: boolean;
  className?: string;
}

function normalizeCodeText(raw: string): string {
  return raw.replace(/\r\n/g, "\n").replace(/\n$/, "");
}

/**
 * 自动探测代码块缩进步长（2 空格或 4 空格）：
 * Python / C / Java / Rust 等默认 4 格，JSON / YAML / JSX / TS 常见 2 格。
 */
function detectIndentSize(text: string): number {
  const lines = text.split("\n");
  let countTwoMod = 0;
  let countFour = 0;
  let minPositive = Infinity;

  for (const line of lines) {
    if (!line || line[0] === "\t") continue;
    const match = /^( +)\S/.exec(line);
    if (!match) continue;
    const spaces = match[1].length;
    if (spaces < minPositive) minPositive = spaces;
    if (spaces % 4 === 0) {
      countFour += 1;
    } else if (spaces % 2 === 0) {
      countTwoMod += 1;
    }
  }

  if (countTwoMod > 0 && minPositive === 2) return 2;
  if (countFour === 0 && countTwoMod > 0) return 2;
  return 4;
}

function renderIndentSpans(ws: string, indentSize: number, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let idx = 0;
  let seg = 0;

  while (idx < ws.length) {
    if (ws[idx] === "\t") {
      nodes.push(
        <span key={`${keyPrefix}-t-${seg++}`} className="code-indent-guide">
          {"\t"}
        </span>,
      );
      idx += 1;
      continue;
    }

    let end = idx;
    while (end < ws.length && ws[end] === " ") end += 1;
    let run = end - idx;

    while (run >= indentSize) {
      nodes.push(
        <span key={`${keyPrefix}-s-${seg++}`} className="code-indent-guide">
          {" ".repeat(indentSize)}
        </span>,
      );
      run -= indentSize;
    }
    if (run > 0) {
      nodes.push(
        <Fragment key={`${keyPrefix}-r-${seg++}`}>{" ".repeat(run)}</Fragment>,
      );
    }
    idx = end;
  }

  return nodes;
}

function renderPlainLine(line: string, indentSize: number, lineIdx: number): ReactNode {
  if (!line) return null;
  const match = /^([ \t]+)(.*)$/.exec(line);
  if (!match) return line;
  const [, ws, rest] = match;
  return (
    <>
      {renderIndentSpans(ws, indentSize, `p-${lineIdx}`)}
      {rest}
    </>
  );
}

function tokenFontStyle(fontStyle?: number): {
  fontWeight?: number;
  fontStyle?: "italic";
  textDecoration?: string;
} {
  if (!fontStyle) return {};
  return {
    fontStyle: fontStyle & 1 ? "italic" : undefined,
    fontWeight: fontStyle & 2 ? 600 : undefined,
    textDecoration: fontStyle & 4 ? "underline" : undefined,
  };
}

function renderTokenizedLine(
  lineTokens: ThemedToken[],
  indentSize: number,
  lineIdx: number,
): ReactNode {
  if (lineTokens.length === 0) return null;

  // 提取行首空白（可能横跨多个 token 或位于首个字符串/注释 token 开头），保证缩进参考线精准对齐且不破坏复制内容
  let leadingWs = "";
  let firstContentIdx = lineTokens.length;
  let remainderFirstToken: ThemedToken | null = null;

  for (let i = 0; i < lineTokens.length; i += 1) {
    const tok = lineTokens[i];
    if (/^[ \t]*$/.test(tok.content)) {
      leadingWs += tok.content;
      continue;
    }
    const wsMatch = /^([ \t]+)(.+)$/s.exec(tok.content);
    if (wsMatch) {
      leadingWs += wsMatch[1];
      remainderFirstToken = { ...tok, content: wsMatch[2] };
      firstContentIdx = i + 1;
    } else {
      firstContentIdx = i;
    }
    break;
  }

  const parts: ReactNode[] = [];
  if (leadingWs) {
    parts.push(...renderIndentSpans(leadingWs, indentSize, `l-${lineIdx}`));
  }

  if (remainderFirstToken) {
    parts.push(
      <span
        key={`tok-${lineIdx}-rem`}
        style={{
          color: remainderFirstToken.color,
          ...tokenFontStyle(remainderFirstToken.fontStyle),
        }}
      >
        {remainderFirstToken.content}
      </span>,
    );
  }

  for (let i = firstContentIdx; i < lineTokens.length; i += 1) {
    const tok = lineTokens[i];
    if (!tok.content) continue;
    parts.push(
      <span
        key={`tok-${lineIdx}-${i}`}
        style={{
          color: tok.color,
          ...tokenFontStyle(tok.fontStyle),
        }}
      >
        {tok.content}
      </span>,
    );
  }

  return parts;
}

export const CodeBlock = memo(function CodeBlock({
  code,
  language,
  streaming = false,
  compact = false,
  className = "",
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [loadedLangVersion, setLoadedLangVersion] = useState(0);

  const normalizedCode = useMemo(() => normalizeCodeText(code), [code]);
  // 流式输出时使用 useDeferredValue 保持主线程高帧率；流式结束后立即采用最新值
  const deferredCode = useDeferredValue(normalizedCode);
  const activeCode = streaming && normalizedCode.length > 1800 ? deferredCode : normalizedCode;

  const langId = useMemo(() => resolveLanguageId(language), [language]);
  const langLabel = useMemo(() => formatLanguageLabel(language), [language]);

  useEffect(() => {
    if (!langId || isLanguageLoaded(langId)) return;
    let cancelled = false;
    void ensureLanguageLoaded(langId).then((loaded) => {
      if (!cancelled && loaded) {
        setLoadedLangVersion((v) => v + 1);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [langId]);

  const indentSize = useMemo(() => detectIndentSize(activeCode), [activeCode]);

  const tokenLines = useMemo(() => {
    void loadedLangVersion;
    if (!langId) return null;
    return tokenizeCodeSync(activeCode, langId);
  }, [activeCode, langId, loadedLangVersion]);

  const plainLines = useMemo(() => {
    if (tokenLines) return null;
    return activeCode.split("\n");
  }, [activeCode, tokenLines]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(normalizedCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // 忽略剪贴板权限异常
    }
  };

  const hasHeaderSpace = !compact && Boolean(langLabel);

  return (
    <div
      className={`obsidian-code-block group relative overflow-hidden ${
        compact ? "obsidian-code-compact" : "my-3.5"
      } ${className}`}
    >
      {/* Obsidian 风格右上角：常态展示低调语言标签，悬浮/聚焦时平滑切换为极简复制按钮 */}
      {!compact && langLabel && (
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute right-3.5 top-2.5 select-none font-sans text-[11.5px] font-medium tracking-[0.01em] text-[var(--code-badge)] transition-opacity duration-150 ${
            copied
              ? "opacity-0"
              : "opacity-100 group-hover:opacity-0 group-focus-within:opacity-0"
          }`}
        >
          {langLabel}
        </span>
      )}

      <button
        type="button"
        onClick={() => void handleCopy()}
        aria-label={copied ? "已复制代码" : "复制代码"}
        className={`obsidian-code-copy absolute ${
          compact ? "right-2 top-2" : "right-2.5 top-2"
        } z-10 inline-flex items-center gap-1 rounded-md px-2 py-1 font-sans text-[11px] font-medium transition-all duration-150 ${
          copied
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
        }`}
      >
        {copied ? (
          <>
            <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
            <span>已复制</span>
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" />
            <span>复制</span>
          </>
        )}
      </button>

      <pre
        className={`obsidian-code-pre ${
          compact
            ? "obsidian-code-pre--compact"
            : hasHeaderSpace
              ? "obsidian-code-pre--titled"
              : "obsidian-code-pre--plain"
        }`}
      >
        <code className="obsidian-code-content">
          {tokenLines
            ? tokenLines.map((lineTokens, lineIdx) => (
                <Fragment key={lineIdx}>
                  {renderTokenizedLine(lineTokens, indentSize, lineIdx)}
                  {lineIdx < tokenLines.length - 1 ? "\n" : null}
                </Fragment>
              ))
            : plainLines?.map((line, lineIdx) => (
                <Fragment key={lineIdx}>
                  {renderPlainLine(line, indentSize, lineIdx)}
                  {lineIdx < plainLines.length - 1 ? "\n" : null}
                </Fragment>
              ))}
        </code>
      </pre>
    </div>
  );
});
