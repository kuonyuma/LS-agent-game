import {
  BookOpen,
  Folder,
  PanelLeftClose,
  Settings,
  SquarePen,
  Trash2,
  Wrench,
} from "lucide-react";
import type { SessionSummary } from "../types";

interface SidebarProps {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  workspace: string;
  toolCount: number;
  knowledgeBaseCount: number;
  hasApiKey: boolean;
  onCreateSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onCollapse: () => void;
  onOpenSettings: (tab: "model" | "tools") => void;
  onOpenKnowledgeModal: () => void;
  onOpenWorkspaceBrowser?: () => void;
}

export function Sidebar({
  sessions,
  activeSessionId,
  workspace,
  toolCount,
  knowledgeBaseCount,
  hasApiKey,
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  onCollapse,
  onOpenSettings,
  onOpenKnowledgeModal,
  onOpenWorkspaceBrowser,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-neutral-200/80 bg-[#f9f9f9] dark:border-white/[0.07] dark:bg-[#080910]">
      {/* Top Row */}
      <div className="flex h-[52px] items-center justify-between px-3">
        <div className="flex items-center gap-2 px-1.5">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-neutral-950 text-[11px] font-bold text-white dark:bg-[#F4F5FA] dark:text-[#090A10]">
            智
          </span>
          <span className="text-[14px] font-semibold tracking-tight text-neutral-900 dark:text-[#F3F4FA]">
            智启 Agent
          </span>
        </div>
        <button
          type="button"
          onClick={onCollapse}
          title="收起侧边栏"
          className="rounded-lg p-1.5 text-neutral-500 transition hover:bg-neutral-200/70 hover:text-neutral-900 dark:text-[#8E94B0] dark:hover:bg-white/[0.07] dark:hover:text-[#F3F4FA]"
        >
          <PanelLeftClose className="h-[18px] w-[18px]" />
        </button>
      </div>

      {/* New Chat Button */}
      <div className="px-2.5 pb-2">
        <button
          type="button"
          onClick={onCreateSession}
          className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-[13.5px] font-medium text-neutral-800 transition hover:bg-neutral-200/65 dark:text-[#E2E5F2] dark:hover:bg-white/[0.06]"
        >
          <SquarePen className="h-4 w-4 text-neutral-600 dark:text-[#9CA2BC]" />
          发起新对话
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-2.5 py-1">
        {sessions.length > 0 && (
          <div className="px-2.5 pb-1.5 pt-2 text-[11px] font-medium text-neutral-400 dark:text-[#666B85]">
            最近对话
          </div>
        )}
        {sessions.length === 0 ? (
          <div className="px-2.5 py-6 text-xs text-neutral-400 dark:text-[#666B85]">
            还没有历史会话
          </div>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((item) => {
              const active = item.session_id === activeSessionId;
              return (
                <li key={item.session_id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelectSession(item.session_id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectSession(item.session_id);
                      }
                    }}
                    className={`group flex cursor-pointer items-center justify-between gap-1.5 rounded-xl px-2.5 py-2 text-[13.5px] transition ${
                      active
                        ? "bg-neutral-200/75 font-medium text-neutral-950 dark:bg-white/[0.09] dark:text-[#F5F6FC]"
                        : "text-neutral-700 hover:bg-neutral-200/45 hover:text-neutral-950 dark:text-[#A8ADC7] dark:hover:bg-white/[0.05] dark:hover:text-[#F3F4FA]"
                    }`}
                  >
                    <span className="min-w-0 flex-1 truncate">{item.title}</span>
                    <button
                      type="button"
                      title="删除对话"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSession(item.session_id);
                      }}
                      className="rounded-md p-1 text-neutral-400 opacity-0 transition hover:bg-neutral-300/60 hover:text-neutral-800 group-hover:opacity-100 dark:text-[#787E99] dark:hover:bg-white/[0.1] dark:hover:text-[#F3F4FA]"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Minimal Bottom Footer */}
      <div className="space-y-0.5 border-t border-neutral-200/70 p-2.5 dark:border-white/[0.07]">
        <button
          type="button"
          onClick={onOpenKnowledgeModal}
          className="flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-xs text-neutral-600 transition hover:bg-neutral-200/60 hover:text-neutral-950 dark:text-[#9CA2BC] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
        >
          <span className="flex items-center gap-2">
            <BookOpen className="h-3.5 w-3.5 text-neutral-500 dark:text-[#7C829E]" />
            向量知识库
          </span>
          <span className="font-mono text-[11px] text-neutral-400 dark:text-[#666B85]">
            {knowledgeBaseCount}
          </span>
        </button>

        <button
          type="button"
          onClick={() => onOpenSettings("tools")}
          className="flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-xs text-neutral-600 transition hover:bg-neutral-200/60 hover:text-neutral-950 dark:text-[#9CA2BC] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
        >
          <span className="flex items-center gap-2">
            <Wrench className="h-3.5 w-3.5 text-neutral-500 dark:text-[#7C829E]" />
            工具与权限契约
          </span>
          <span className="font-mono text-[11px] text-neutral-400 dark:text-[#666B85]">
            {toolCount}
          </span>
        </button>

        <button
          type="button"
          onClick={() => onOpenSettings("model")}
          className="flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-xs text-neutral-600 transition hover:bg-neutral-200/60 hover:text-neutral-950 dark:text-[#9CA2BC] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
        >
          <span className="flex items-center gap-2">
            <Settings className="h-3.5 w-3.5 text-neutral-500 dark:text-[#7C829E]" />
            模型设置与密钥
          </span>
          {!hasApiKey && (
            <span
              className="h-2 w-2 rounded-full bg-amber-400"
              title="未配置当前模型密钥"
            />
          )}
        </button>

        {onOpenWorkspaceBrowser && (
          <button
            type="button"
            onClick={onOpenWorkspaceBrowser}
            title={workspace || "点击选择工作目录"}
            className="flex w-full items-center gap-2 rounded-xl px-2.5 py-1.5 text-left text-[11px] text-neutral-400 transition hover:bg-neutral-200/60 hover:text-neutral-800 dark:text-[#6E7490] dark:hover:bg-white/[0.06] dark:hover:text-[#D9DCEB]"
          >
            <Folder className="h-3 w-3 shrink-0" />
            <span className="truncate font-mono">{workspace || "..."}</span>
          </button>
        )}
      </div>
    </aside>
  );
}
