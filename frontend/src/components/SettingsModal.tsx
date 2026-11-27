import { KeyRound, Shield, Sliders, Wrench, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ProviderInfo, RunSettings, ToolInfo } from "../types";

interface SettingsModalProps {
  open: boolean;
  initialTab?: "model" | "tools";
  providers: ProviderInfo[];
  currentModelMaxTokens?: number;
  tools: ToolInfo[];
  settings: RunSettings;
  onChange: (next: RunSettings) => void;
  onClose: () => void;
}

export function SettingsModal({
  open,
  initialTab = "model",
  providers,
  currentModelMaxTokens = 65536,
  tools,
  settings,
  onChange,
  onClose,
}: SettingsModalProps) {
  const [tab, setTab] = useState<"model" | "tools">(initialTab);

  useEffect(() => {
    if (open) {
      setTab(initialTab);
    }
  }, [open, initialTab]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="flex max-h-[84vh] w-full max-w-xl flex-col overflow-hidden rounded-3xl border border-neutral-200 bg-white shadow-[0_24px_64px_rgba(0,0,0,0.16)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4">
          <div className="flex items-center gap-1 rounded-full bg-neutral-100 p-1">
            <button
              type="button"
              onClick={() => setTab("model")}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
                tab === "model"
                  ? "bg-white text-neutral-950 shadow-2xs"
                  : "text-neutral-500 hover:text-neutral-900"
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
                  ? "bg-white text-neutral-950 shadow-2xs"
                  : "text-neutral-500 hover:text-neutral-900"
              }`}
            >
              <Wrench className="h-3.5 w-3.5" />
              工具契约 ({tools.length})
            </button>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 text-neutral-400 transition hover:bg-neutral-100 hover:text-neutral-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {tab === "model" ? (
            <div className="space-y-5">
              <section className="space-y-2.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-neutral-800">
                  <KeyRound className="h-3.5 w-3.5 text-neutral-500" />
                  厂商 API Key（仅存本地浏览器，不写入仓库或服务端）
                </div>
                {providers.map((p) => (
                  <div
                    key={p.id}
                    className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-neutral-800">
                        {p.label}
                      </span>
                      <span
                        className={`text-[11px] ${
                          p.env_configured || settings.apiKeys[p.id]
                            ? "text-emerald-600"
                            : "text-amber-600"
                        }`}
                      >
                        {p.env_configured
                          ? `${p.env_key} 已配置`
                          : settings.apiKeys[p.id]
                            ? "已输入本地 Key"
                            : `未配置 ${p.env_key}`}
                      </span>
                    </div>
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
                        p.env_configured
                          ? "留空默认读取服务端环境变量"
                          : `粘贴 ${p.label} API Key...`
                      }
                      className="mt-2 w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 font-mono text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none"
                    />
                  </div>
                ))}
              </section>

              <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="flex cursor-pointer items-center justify-between rounded-2xl border border-neutral-200/80 p-3.5">
                  <div>
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-neutral-800">
                      <Shield className="h-3.5 w-3.5 text-emerald-600" />
                      异步输出安全审核
                    </div>
                    <div className="mt-0.5 text-[11px] text-neutral-400">
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
                    className="h-4 w-4 accent-neutral-950"
                  />
                </label>

                <div className="rounded-2xl border border-neutral-200/80 p-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <label className="block text-xs font-semibold text-neutral-800">
                      最大输出 Token
                    </label>
                    {settings.maxTokens !== null && (
                      <button
                        type="button"
                        onClick={() => onChange({ ...settings, maxTokens: null })}
                        className="text-[11px] font-medium text-blue-600 hover:underline"
                      >
                        恢复自动顶格
                      </button>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-neutral-400">
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
                    className="mt-1.5 w-full rounded-xl border border-neutral-200 px-3 py-1 font-mono text-xs text-neutral-800 placeholder:text-neutral-400 focus:border-neutral-400 focus:outline-none"
                  />
                </div>
              </section>

              <section>
                <label className="block text-xs font-semibold text-neutral-800">
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
                  className="mt-1.5 w-full rounded-2xl border border-neutral-200 p-3 text-xs text-neutral-800 focus:border-neutral-400 focus:outline-none"
                />
              </section>
            </div>
          ) : (
            <div className="space-y-2">
              {tools.map((tool) => (
                <div
                  key={tool.name}
                  className="rounded-2xl border border-neutral-200/80 bg-[#fafafa] p-3.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-semibold text-neutral-900">
                      {tool.name}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10.5px] font-medium ${
                        tool.read_only
                          ? "bg-emerald-50 text-emerald-700"
                          : "bg-amber-50 text-amber-800"
                      }`}
                    >
                      {tool.read_only ? "只读" : "高危 · 逐次审批"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-neutral-500">
                    {tool.description}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end border-t border-neutral-100 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full bg-neutral-950 px-5 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-800"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  );
}
