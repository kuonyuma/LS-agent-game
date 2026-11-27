import {
  ArrowUp,
  ChevronRight,
  Folder,
  FolderOpen,
  HardDrive,
  Home,
  Loader2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { browseWorkspace, validateWorkspace } from "../api";
import type { WorkspaceBrowseResult } from "../types";

interface WorkspaceBrowserModalProps {
  open: boolean;
  currentWorkspace: string;
  onSelect: (workspacePath: string) => void;
  onClose: () => void;
}

export function WorkspaceBrowserModal({
  open,
  currentWorkspace,
  onSelect,
  onClose,
}: WorkspaceBrowserModalProps) {
  const [data, setData] = useState<WorkspaceBrowseResult | null>(null);
  const [pathInput, setPathInput] = useState(currentWorkspace);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadPath = useCallback(async (targetPath: string) => {
    setLoading(true);
    setError("");
    try {
      const res = await browseWorkspace(targetPath);
      setData(res);
      setPathInput(res.current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取目录失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setPathInput(currentWorkspace);
    void loadPath(currentWorkspace);
  }, [open, currentWorkspace, loadPath]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const handleJumpInput = async () => {
    const trimmed = pathInput.trim();
    if (!trimmed) {
      await loadPath("");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const validated = await validateWorkspace(trimmed);
      await loadPath(validated.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : "路径无效");
      setLoading(false);
    }
  };

  const handleConfirm = async () => {
    const candidate = pathInput.trim() || data?.current || "";
    if (!candidate) {
      setError("请先选择一个具体的工作目录");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const validated = await validateWorkspace(candidate);
      onSelect(validated.path);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "路径无效");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="flex max-h-[82vh] w-full max-w-xl flex-col overflow-hidden rounded-3xl border border-neutral-200 bg-white shadow-[0_24px_64px_rgba(0,0,0,0.16)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3.5">
          <div className="flex items-center gap-2 text-sm font-semibold text-neutral-900">
            <FolderOpen className="h-4 w-4 text-neutral-600" />
            选择工作目录
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 text-neutral-400 transition hover:bg-neutral-100 hover:text-neutral-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Manual Path Input + Quick Navigation */}
        <div className="space-y-2.5 border-b border-neutral-100 bg-[#fafafa] px-5 py-3">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleJumpInput();
                }
              }}
              placeholder="输入或粘贴本地目录绝对路径，例如 A:\Root_Code\my-project"
              className="flex-1 rounded-xl border border-neutral-200 bg-white px-3 py-1.5 font-mono text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none"
            />
            <button
              type="button"
              disabled={loading}
              onClick={() => void handleJumpInput()}
              className="rounded-xl border border-neutral-200 bg-white px-3 py-1.5 text-xs font-medium text-neutral-700 transition hover:bg-neutral-100 disabled:opacity-50"
            >
              前往
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              disabled={loading}
              onClick={() => void loadPath("")}
              className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-[11px] font-medium text-neutral-600 ring-1 ring-neutral-200/80 transition hover:bg-neutral-100 hover:text-neutral-900"
            >
              <HardDrive className="h-3 w-3" />
              此计算机
            </button>
            {data?.home && (
              <button
                type="button"
                disabled={loading}
                onClick={() => void loadPath(data.home)}
                className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-[11px] font-medium text-neutral-600 ring-1 ring-neutral-200/80 transition hover:bg-neutral-100 hover:text-neutral-900"
              >
                <Home className="h-3 w-3" />
                主目录
              </button>
            )}
            {data?.current && (
              <button
                type="button"
                disabled={loading}
                onClick={() => void loadPath(data.parent)}
                className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-[11px] font-medium text-neutral-600 ring-1 ring-neutral-200/80 transition hover:bg-neutral-100 hover:text-neutral-900"
              >
                <ArrowUp className="h-3 w-3" />
                上一级
              </button>
            )}
          </div>

          {error && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-700">
              {error}
            </div>
          )}
        </div>

        {/* Directory List */}
        <div className="min-h-[260px] flex-1 overflow-y-auto px-3 py-2">
          {loading ? (
            <div className="flex h-48 items-center justify-center gap-2 text-xs text-neutral-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在读取目录...
            </div>
          ) : !data || data.directories.length === 0 ? (
            <div className="flex h-48 items-center justify-center text-xs text-neutral-400">
              当前目录下没有可进入的子文件夹
            </div>
          ) : (
            <ul className="space-y-0.5">
              {data.directories.map((dir) => (
                <li key={dir.path}>
                  <button
                    type="button"
                    onClick={() => void loadPath(dir.path)}
                    className="group flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-[13px] text-neutral-700 transition hover:bg-neutral-100 hover:text-neutral-950"
                  >
                    <span className="flex min-w-0 items-center gap-2.5">
                      <Folder className="h-4 w-4 shrink-0 text-neutral-400 group-hover:text-neutral-700" />
                      <span className="truncate font-medium">{dir.name}</span>
                    </span>
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-neutral-300 transition group-hover:text-neutral-600" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-neutral-100 px-5 py-3">
          <div
            title={data?.current || pathInput}
            className="min-w-0 flex-1 truncate font-mono text-[11px] text-neutral-500"
          >
            {data?.current || "请选择具体文件夹"}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-neutral-200 px-4 py-1.5 text-xs font-medium text-neutral-600 transition hover:bg-neutral-100"
            >
              取消
            </button>
            <button
              type="button"
              disabled={loading || (!data?.current && !pathInput.trim())}
              onClick={() => void handleConfirm()}
              className="rounded-full bg-neutral-950 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-800 disabled:opacity-40"
            >
              设为工作区
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
