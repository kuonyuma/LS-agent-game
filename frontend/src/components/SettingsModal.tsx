import {
  ExternalLink,
  Image as ImageIcon,
  KeyRound,
  LogIn,
  LogOut,
  RefreshCw,
  Shield,
  Sliders,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { logoutChatGPT, startChatGPTLogin } from "../api";
import type { ImageSearchStatus, ProviderInfo, RunSettings, ToolInfo } from "../types";

interface SettingsModalProps {
  open: boolean;
  initialTab?: "model" | "tools";
  providers: ProviderInfo[];
  currentModelMaxTokens?: number;
  tools: ToolInfo[];
  imageSearchStatus?: ImageSearchStatus | null;
  settings: RunSettings;
  rememberKeys: boolean;
  onChange: (next: RunSettings) => void;
  onChatGPTAuthChanged?: (refreshModels?: boolean) => Promise<void> | void;
  onClose: () => void;
}

export function SettingsModal({
  open,
  initialTab = "model",
  providers,
  currentModelMaxTokens = 65536,
  tools,
  imageSearchStatus = null,
  settings,
  rememberKeys,
  onChange,
  onChatGPTAuthChanged,
  onClose,
}: SettingsModalProps) {
  const [tab, setTab] = useState<"model" | "tools">(initialTab);
  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthError, setOauthError] = useState("");
  const popupTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (open) {
      setTab(initialTab);
      setOauthError("");
    }
  }, [open, initialTab]);

  useEffect(() => {
    return () => {
      if (popupTimerRef.current !== null) {
        window.clearInterval(popupTimerRef.current);
      }
    };
  }, []);

  const handleStartChatGPTLogin = async () => {
    setOauthError("");
    setOauthBusy(true);
    try {
      const res = await startChatGPTLogin();
      const popup = window.open(
        res.authorize_url,
        "chatgpt_oauth_login",
        "width=560,height=740,noopener=no",
      );
      if (!popup) {
        window.location.href = res.authorize_url;
        return;
      }
      if (popupTimerRef.current !== null) {
        window.clearInterval(popupTimerRef.current);
      }
      popupTimerRef.current = window.setInterval(() => {
        if (popup.closed) {
          if (popupTimerRef.current !== null) {
            window.clearInterval(popupTimerRef.current);
            popupTimerRef.current = null;
          }
          setOauthBusy(false);
          void onChatGPTAuthChanged?.(true);
        }
      }, 600);
    } catch (err) {
      setOauthBusy(false);
      setOauthError(err instanceof Error ? err.message : "发起 ChatGPT 登录失败");
    }
  };

  const handleLogoutChatGPT = async () => {
    setOauthError("");
    setOauthBusy(true);
    try {
      await logoutChatGPT();
      await onChatGPTAuthChanged?.(false);
    } catch (err) {
      setOauthError(err instanceof Error ? err.message : "退出 ChatGPT 登录失败");
    } finally {
      setOauthBusy(false);
    }
  };

  const handleRefreshChatGPTModels = async () => {
    setOauthError("");
    setOauthBusy(true);
    try {
      await onChatGPTAuthChanged?.(true);
    } catch (err) {
      setOauthError(err instanceof Error ? err.message : "刷新可用模型失败");
    } finally {
      setOauthBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-[2px] dark:bg-black/65 dark:backdrop-blur-xs"
      onClick={onClose}
    >
      <div
        className="flex max-h-[84vh] w-full max-w-xl flex-col overflow-hidden rounded-3xl border border-neutral-200 bg-white shadow-[0_24px_64px_rgba(0,0,0,0.16)] dark:border-white/[0.11] dark:bg-[#141724] dark:shadow-[0_28px_84px_rgba(0,0,0,0.75)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4 dark:border-white/[0.08]">
          <div className="flex items-center gap-1 rounded-full bg-neutral-100 p-1 dark:bg-white/[0.06]">
            <button
              type="button"
              onClick={() => setTab("model")}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
                tab === "model"
                  ? "bg-white text-neutral-950 shadow-2xs dark:bg-white/[0.13] dark:text-[#F5F6FC]"
                  : "text-neutral-500 hover:text-neutral-900 dark:text-[#8E94B0] dark:hover:text-[#F3F4FA]"
              }`}
            >
              <Sliders className="h-3.5 w-3.5" />
              模型与安全
            </button>
            <button
              type="button"
              onClick={() => setTab("tools")}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
                tab === "tools"
                  ? "bg-white text-neutral-950 shadow-2xs dark:bg-white/[0.13] dark:text-[#F5F6FC]"
                  : "text-neutral-500 hover:text-neutral-900 dark:text-[#8E94B0] dark:hover:text-[#F3F4FA]"
              }`}
            >
              <Wrench className="h-3.5 w-3.5" />
              工具契约 ({tools.length})
            </button>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 text-neutral-400 transition hover:bg-neutral-100 hover:text-neutral-800 dark:text-[#7F85A3] dark:hover:bg-white/[0.08] dark:hover:text-[#F3F4FA]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {tab === "model" ? (
            <div className="space-y-5">
              <section className="space-y-2.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                  <KeyRound className="h-3.5 w-3.5 text-neutral-500 dark:text-[#8E94B0]" />
                  {rememberKeys ? "厂商 API Key（保存在本机浏览器）" : "厂商 API Key（仅本次页面有效）"}
                </div>
                {providers.map((p) => {
                  const isChatGPTOAuth = p.auth_mode === "chatgpt_oauth";
                  const ready = Boolean(
                    p.oauth_connected || p.env_configured || settings.apiKeys[p.id],
                  );
                  const statusLabel = p.oauth_connected
                    ? p.oauth_account
                      ? `已登录 ${p.oauth_account}`
                      : "ChatGPT 已登录"
                    : p.env_configured
                      ? `${p.env_key} 已配置`
                      : settings.apiKeys[p.id]
                        ? "已输入本地 Key"
                        : isChatGPTOAuth
                          ? "未登录 ChatGPT"
                          : `未配置 ${p.env_key}`;

                  return (
                    <div
                      key={p.id}
                      className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3 dark:border-white/[0.08] dark:bg-[#0E101B]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                          {p.label}
                        </span>
                        <span
                          className={`truncate text-[11px] ${
                            ready ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-300"
                          }`}
                        >
                          {statusLabel}
                        </span>
                      </div>

                      {isChatGPTOAuth && (
                        <div className="mt-2.5 space-y-2 rounded-xl border border-neutral-200/80 bg-white p-2.5 dark:border-white/[0.08] dark:bg-[#141724]">
                          <div className="flex flex-wrap items-center gap-2">
                            {!p.oauth_connected ? (
                              <button
                                type="button"
                                disabled={oauthBusy}
                                onClick={() => void handleStartChatGPTLogin()}
                                className="inline-flex items-center gap-1.5 rounded-full bg-neutral-950 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-800 disabled:opacity-50 dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
                              >
                                <LogIn className="h-3.5 w-3.5" />
                                {oauthBusy ? "等待浏览器授权..." : "使用 ChatGPT 登录"}
                              </button>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  disabled={oauthBusy}
                                  onClick={() => void handleRefreshChatGPTModels()}
                                  className="inline-flex items-center gap-1.5 rounded-full border border-neutral-200 bg-neutral-50 px-3 py-1 text-[11.5px] font-medium text-neutral-700 transition hover:bg-neutral-100 disabled:opacity-50 dark:border-white/[0.12] dark:bg-white/[0.06] dark:text-[#DCE0F0] dark:hover:bg-white/[0.11]"
                                >
                                  <RefreshCw className="h-3 w-3" />
                                  刷新可用模型
                                </button>
                                {p.oauth_usage_url && (
                                  <a
                                    href={p.oauth_usage_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="inline-flex items-center gap-1 rounded-full border border-neutral-200 bg-white px-3 py-1 text-[11.5px] font-medium text-neutral-700 transition hover:bg-neutral-50 dark:border-white/[0.12] dark:bg-white/[0.04] dark:text-[#DCE0F0] dark:hover:bg-white/[0.09]"
                                  >
                                    <span>额度管理</span>
                                    <ExternalLink className="h-3 w-3" />
                                  </a>
                                )}
                                <button
                                  type="button"
                                  disabled={oauthBusy}
                                  onClick={() => void handleLogoutChatGPT()}
                                  className="inline-flex items-center gap-1 rounded-full border border-rose-200 bg-rose-50/70 px-3 py-1 text-[11.5px] font-medium text-rose-700 transition hover:bg-rose-100 disabled:opacity-50 dark:border-rose-400/25 dark:bg-rose-500/[0.11] dark:text-rose-200 dark:hover:bg-rose-500/[0.18]"
                                >
                                  <LogOut className="h-3 w-3" />
                                  退出登录
                                </button>
                              </>
                            )}
                          </div>
                          <div className="text-[11px] leading-relaxed text-neutral-500 dark:text-[#8E94B0]">
                            通过官方 Sign in with ChatGPT 授权本机后端走 Responses API 调用套餐可用模型（令牌仅存本机后端，不经过浏览器存储）。
                          </div>
                          {(oauthError || p.oauth_models_error) && (
                            <div className="rounded-lg bg-rose-50 px-2.5 py-1.5 text-[11px] text-rose-700 dark:bg-rose-500/[0.12] dark:text-rose-200">
                              {oauthError || p.oauth_models_error}
                            </div>
                          )}
                        </div>
                      )}

                      <input
                        type="password"
                        value={settings.apiKeys[p.id] ?? ""}
                        onChange={(e) =>
                          onChange({
                            ...settings,
                            apiKeys: {
                              ...settings.apiKeys,
                              [p.id]: e.target.value,
                            },
                          })
                        }
                        placeholder={
                          isChatGPTOAuth
                            ? p.oauth_connected
                              ? "留空默认使用已登录的 ChatGPT 套餐（可选填入 API Key 覆盖）"
                              : `可选兜底：粘贴 ${p.env_key}（或点击上方使用 ChatGPT 登录）`
                            : p.env_configured
                              ? "留空默认读取服务端环境变量"
                              : `粘贴 ${p.label} API Key...`
                        }
                        className="mt-2 w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 font-mono text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85] dark:focus:border-violet-400/50"
                      />
                    </div>
                  );
                })}
              </section>

              <section className="space-y-2.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                    <ImageIcon className="h-3.5 w-3.5 text-neutral-500 dark:text-[#8E94B0]" />
                    真实配图检索源（可选）
                  </div>
                  <span className="text-[11px] text-neutral-400 dark:text-[#7F85A3]">
                    未配 Key 时自动免费回退 Wikimedia / Wikipedia
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  <div className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3 dark:border-white/[0.08] dark:bg-[#0E101B]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                        Serper Images
                      </span>
                      <span
                        className={`text-[11px] ${
                          imageSearchStatus?.serper_configured || settings.apiKeys.serper
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-neutral-400 dark:text-[#747A96]"
                        }`}
                      >
                        {imageSearchStatus?.serper_configured
                          ? "SERPER_API_KEY 已配置"
                          : settings.apiKeys.serper
                            ? "已输入本地 Key"
                            : "未配置（可选）"}
                      </span>
                    </div>
                    <input
                      type="password"
                      value={settings.apiKeys.serper ?? ""}
                      onChange={(e) =>
                        onChange({
                          ...settings,
                          apiKeys: {
                            ...settings.apiKeys,
                            serper: e.target.value,
                          },
                        })
                      }
                      placeholder={
                        imageSearchStatus?.serper_configured
                          ? "留空默认读取服务端环境变量"
                          : "粘贴 SERPER_API_KEY（优先 Google 搜图）"
                      }
                      className="mt-2 w-full rounded-xl border border-neutral-200 bg-white px-3 py-1.5 font-mono text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85] dark:focus:border-violet-400/50"
                    />
                  </div>
                  <div className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3 dark:border-white/[0.08] dark:bg-[#0E101B]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                        Tavily Images
                      </span>
                      <span
                        className={`text-[11px] ${
                          imageSearchStatus?.tavily_configured || settings.apiKeys.tavily
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-neutral-400 dark:text-[#747A96]"
                        }`}
                      >
                        {imageSearchStatus?.tavily_configured
                          ? "TAVILY_API_KEY 已配置"
                          : settings.apiKeys.tavily
                            ? "已输入本地 Key"
                            : "未配置（可选）"}
                      </span>
                    </div>
                    <input
                      type="password"
                      value={settings.apiKeys.tavily ?? ""}
                      onChange={(e) =>
                        onChange({
                          ...settings,
                          apiKeys: {
                            ...settings.apiKeys,
                            tavily: e.target.value,
                          },
                        })
                      }
                      placeholder={
                        imageSearchStatus?.tavily_configured
                          ? "留空默认读取服务端环境变量"
                          : "粘贴 TAVILY_API_KEY（次优搜图源）"
                      }
                      className="mt-2 w-full rounded-xl border border-neutral-200 bg-white px-3 py-1.5 font-mono text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85] dark:focus:border-violet-400/50"
                    />
                  </div>
                </div>
              </section>

              <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="flex cursor-pointer items-center justify-between rounded-2xl border border-neutral-200/80 p-3.5 dark:border-white/[0.08] dark:bg-[#0E101B]">
                  <div>
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                      <Shield className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                      异步输出安全审核
                    </div>
                    <div className="mt-0.5 text-[11px] text-neutral-400 dark:text-[#6E7490]">
                      RunCoordinator 并发内容审核
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.safetyEnabled}
                    onChange={(e) =>
                      onChange({
                        ...settings,
                        safetyEnabled: e.target.checked,
                      })
                    }
                    className="h-4 w-4 accent-neutral-950 dark:accent-violet-400"
                  />
                </label>

                <div className="rounded-2xl border border-neutral-200/80 p-3.5 dark:border-white/[0.08] dark:bg-[#0E101B]">
                  <div className="flex items-center justify-between gap-2">
                    <label className="block text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                      最大输出 Token
                    </label>
                    {settings.maxTokens !== null && (
                      <button
                        type="button"
                        onClick={() => onChange({ ...settings, maxTokens: null })}
                        className="text-[11px] font-medium text-blue-600 hover:underline dark:text-[#8AB4FF]"
                      >
                        恢复自动顶格
                      </button>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-neutral-400 dark:text-[#6E7490]">
                    留空即自动顶格（当前模型上限{" "}
                    {currentModelMaxTokens.toLocaleString()}）
                  </div>
                  <input
                    type="number"
                    min={256}
                    max={1048576}
                    step={4096}
                    value={settings.maxTokens ?? ""}
                    placeholder={`自动顶格（${currentModelMaxTokens.toLocaleString()}）`}
                    onChange={(e) => {
                      const raw = e.target.value.trim();
                      if (!raw) {
                        onChange({ ...settings, maxTokens: null });
                        return;
                      }
                      const parsed = Number(raw);
                      if (Number.isFinite(parsed)) {
                        onChange({
                          ...settings,
                          maxTokens: Math.max(256, Math.min(1048576, Math.floor(parsed))),
                        });
                      }
                    }}
                    className="mt-1.5 w-full rounded-xl border border-neutral-200 bg-white px-3 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400 focus:border-neutral-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85] dark:focus:border-violet-400/50"
                  />
                </div>
              </section>

              <section>
                <label className="block text-xs font-semibold text-neutral-800 dark:text-[#E5E8F5]">
                  系统提示词覆盖（可选）
                </label>
                <textarea
                  rows={3}
                  value={settings.systemPromptOverride}
                  onChange={(e) =>
                    onChange({
                      ...settings,
                      systemPromptOverride: e.target.value,
                    })
                  }
                  placeholder="留空则使用服务端默认系统提示词..."
                  className="mt-1.5 w-full rounded-2xl border border-neutral-200 bg-white p-3 text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none dark:border-white/[0.11] dark:bg-[#090A12] dark:text-[#E8EBF6] dark:placeholder:text-[#666B85] dark:focus:border-violet-400/50"
                />
              </section>
            </div>
          ) : (
            <div className="space-y-2">
              {tools.map((tool) => {
                const requiresApproval = tool.requires_approval ?? !tool.read_only;
                return (
                  <div
                    key={tool.name}
                    className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3.5 dark:border-white/[0.08] dark:bg-[#0E101B]"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs font-semibold text-neutral-900 dark:text-[#F3F4FA]">
                        {tool.name}
                      </span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10.5px] font-medium ${
                          requiresApproval
                            ? "bg-amber-50 text-amber-800 dark:bg-amber-500/[0.12] dark:text-amber-300"
                            : "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/[0.12] dark:text-emerald-300"
                        }`}
                      >
                        {requiresApproval ? "高危 · 逐次审批" : tool.read_only ? "只读" : "直接执行"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-neutral-500 dark:text-[#9CA2BC]">
                      {tool.description}
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex justify-end border-t border-neutral-100 px-5 py-3 dark:border-white/[0.08]">
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
