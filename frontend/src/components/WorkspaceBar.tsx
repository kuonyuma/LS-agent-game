import {
  Check,
  Copy,
  Folder,
  FolderOpen,
  FolderSearch,
  Laptop,
  Loader2,
  MoreHorizontal,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface WorkspaceBarProps {
  workspace: string;
  recentWorkspaces: string[];
  disabled?: boolean;
  pickingNative?: boolean;
  onSelectWorkspace: (path: string) => void;
  onPickNative: () => void;
  onOpenBrowser: () => void;
}

export function basenameOfPath(fullPath: string): string {
  if (!fullPath) return "未选择目录";
  const normalized = fullPath.replace(/[\\/]+$/, "");
  if (!normalized) return fullPath;
  const parts = normalized.split(/[\\/]/);
  return parts[parts.length - 1] || fullPath;
}

export function WorkspaceBar({
  workspace,
  recentWorkspaces,
  disabled = false,
  pickingNative = false,
  onSelectWorkspace,
  onPickNative,
  onOpenBrowser,
}: WorkspaceBarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onOutside = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onOutside);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onOutside);
      document.removeEventListener("keydown", onEsc);
    };
  }, [menuOpen]);

  const folderName = basenameOfPath(workspace);
  const uniqueRecents = Array.from(
    new Set([workspace, ...recentWorkspaces].filter(Boolean)),
  ).slice(0, 8);

  const handleCopyPath = async () => {
    if (!workspace) return;
    try {
      await navigator.clipboard.writeText(workspace);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // ignore
    }
  };

  return (
    <div ref={rootRef} className="relative mx-4 sm:mx-5">
      {/* Codex-style docked rounded top shelf right above the composer */}
      <div className="flex items-center justify-between gap-2 rounded-t-[18px] border border-b-0 border-neutral-200/85 bg-[#f3f3f4]/90 px-3.5 py-2 text-[12.5px] text-neutral-700 backdrop-blur-xs dark:border-white/[0.09] dark:bg-[#0D0F19]/92 dark:text-[#C8CCE0]">
        <div className="flex min-w-0 items-center gap-1.5 sm:gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={onOpenBrowser}
            title="浏览本机目录"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-0.5 font-medium text-neutral-700 transition hover:bg-neutral-200/75 hover:text-neutral-950 disabled:opacity-50 dark:text-[#C8CCE0] dark:hover:bg-white/[0.08] dark:hover:text-[#F3F4FA]"
          >
            <Laptop className="h-3.5 w-3.5 text-neutral-500 dark:text-[#858BA8]" />
            <span>此计算机</span>
          </button>

          <button
            type="button"
            disabled={disabled}
            onClick={() => setMenuOpen((v) => !v)}
            title={workspace || "点击切换工作目录"}
            className="inline-flex min-w-0 items-center gap-1.5 rounded-lg px-2 py-0.5 font-medium text-neutral-800 transition hover:bg-neutral-200/75 hover:text-neutral-950 disabled:opacity-50 dark:text-[#E5E8F5] dark:hover:bg-white/[0.08] dark:hover:text-white"
          >
            <Folder className="h-3.5 w-3.5 shrink-0 text-neutral-500 dark:text-[#858BA8]" />
            <span className="max-w-[180px] truncate sm:max-w-[280px]">
              {folderName}
            </span>
          </button>

          <button
            type="button"
            disabled={disabled}
            onClick={() => setMenuOpen((v) => !v)}
            title="更多工作区选项"
            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-neutral-500 transition hover:bg-neutral-200/75 hover:text-neutral-900 disabled:opacity-50 dark:text-[#858BA8] dark:hover:bg-white/[0.08] dark:hover:text-[#F3F4FA]"
          >
            {pickingNative ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <MoreHorizontal className="h-4 w-4" />
            )}
          </button>
        </div>

        <span
          title={workspace}
          className="hidden max-w-[240px] truncate font-mono text-[11px] text-neutral-400 dark:text-[#666B85] md:inline"
        >
          {workspace}
        </span>
      </div>

      {/* Dropdown Popover for Recent Workspaces + Native Picker + Browser */}
      {menuOpen && (
        <div className="effort-popover-enter absolute left-2 bottom-full z-40 mb-2 w-80 overflow-hidden rounded-2xl border border-neutral-200/90 bg-white p-1.5 shadow-[0_14px_40px_rgba(0,0,0,0.13)] dark:border-white/[0.11] dark:bg-[#171A29] dark:shadow-[0_18px_48px_rgba(0,0,0,0.68)]">
          <div className="px-2.5 pt-1.5 pb-1 text-[11px] font-medium text-neutral-400 dark:text-[#6E7490]">
            选择工作目录
          </div>

          <div className="space-y-0.5 pb-1.5">
            <button
              type="button"
              disabled={disabled || pickingNative}
              onClick={() => {
                setMenuOpen(false);
                onPickNative();
              }}
              className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-neutral-100 disabled:opacity-50 dark:text-[#E5E8F5] dark:hover:bg-white/[0.06]"
            >
              <FolderOpen className="h-4 w-4 shrink-0 text-blue-600 dark:text-[#7DA8FF]" />
              <div>
                <div>打开系统文件夹选择器...</div>
                <div className="text-[10.5px] font-normal text-neutral-400 dark:text-[#6E7490]">
                  调用 Windows 原生目录选择窗口
                </div>
              </div>
            </button>

            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                setMenuOpen(false);
                onOpenBrowser();
              }}
              className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs font-medium text-neutral-800 transition hover:bg-neutral-100 disabled:opacity-50 dark:text-[#E5E8F5] dark:hover:bg-white/[0.06]"
            >
              <FolderSearch className="h-4 w-4 shrink-0 text-neutral-500 dark:text-[#8E94B0]" />
              <div>
                <div>浏览本机目录 / 粘贴路径...</div>
                <div className="text-[10.5px] font-normal text-neutral-400 dark:text-[#6E7490]">
                  按盘符逐级浏览或直接输入绝对路径
                </div>
              </div>
            </button>

            {workspace && (
              <button
                type="button"
                onClick={() => void handleCopyPath()}
                className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-1.5 text-left text-xs text-neutral-600 transition hover:bg-neutral-100 hover:text-neutral-900 dark:text-[#A9AEC6] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5 shrink-0 text-neutral-400 dark:text-[#747A96]" />
                )}
                <span>{copied ? "已复制当前路径" : "复制当前工作区路径"}</span>
              </button>
            )}
          </div>

          {uniqueRecents.length > 0 && (
            <div className="border-t border-neutral-100 pt-1.5 dark:border-white/[0.08]">
              <div className="px-2.5 py-1 text-[11px] font-medium text-neutral-400 dark:text-[#6E7490]">
                最近使用的目录
              </div>
              <div className="max-h-48 space-y-0.5 overflow-y-auto">
                {uniqueRecents.map((item) => {
                  const active = item === workspace;
                  return (
                    <button
                      key={item}
                      type="button"
                      disabled={disabled}
                      onClick={() => {
                        setMenuOpen(false);
                        onSelectWorkspace(item);
                      }}
                      className={`flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-1.5 text-left transition ${
                        active
                          ? "bg-neutral-100 text-neutral-950 dark:bg-white/[0.09] dark:text-[#F5F6FC]"
                          : "text-neutral-700 hover:bg-neutral-50 dark:text-[#C5C9DE] dark:hover:bg-white/[0.05]"
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium">
                          {basenameOfPath(item)}
                        </div>
                        <div className="truncate font-mono text-[10.5px] text-neutral-400 dark:text-[#6E7490]">
                          {item}
                        </div>
                      </div>
                      {active && (
                        <Check className="h-3.5 w-3.5 shrink-0 text-neutral-900 dark:text-[#F3F4FA]" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
