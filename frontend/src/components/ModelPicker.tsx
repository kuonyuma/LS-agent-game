import { Check, ChevronDown, KeyRound } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ProviderInfo } from "../types";

interface ModelPickerProps {
  providers: ProviderInfo[];
  selectedProvider: string;
  selectedModel: string;
  apiKeys: Record<string, string>;
  disabled?: boolean;
  onSelect: (providerId: string, modelId: string) => void;
  onOpenSettings: () => void;
}

export function ModelPicker({
  providers,
  selectedProvider,
  selectedModel,
  apiKeys,
  disabled,
  onSelect,
  onOpenSettings,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [menuSpace, setMenuSpace] = useState<{ maxHeight: number; maxWidth: number }>();
  const containerRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    if (!open || disabled) return;
    const updateMenuSpace = () => {
      const anchor = containerRef.current?.getBoundingClientRect();
      if (!anchor) return;
      // 给触发按钮与菜单之间、菜单与视口边缘之间留出间距。
      setMenuSpace({
        maxHeight: Math.max(0, anchor.top - 16),
        maxWidth: Math.max(0, window.innerWidth - anchor.left - 8),
      });
    };
    updateMenuSpace();
    window.addEventListener("resize", updateMenuSpace);
    return () => window.removeEventListener("resize", updateMenuSpace);
  }, [open, disabled]);

  useEffect(() => {
    if (!open) return;
    if (disabled) {
      setOpen(false);
      return;
    }
    const onDocClick = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, disabled]);

  const activeProvider = providers.find((p) => p.id === selectedProvider);
  const activeModel = activeProvider?.models.find(
    (m) => m.id === selectedModel,
  );
  const activeLabel = activeModel?.label || selectedModel || "选择模型";
  const activeReady = Boolean(
    activeProvider?.oauth_connected ||
      activeProvider?.env_configured ||
      apiKeys[selectedProvider],
  );

  return (
    <div ref={containerRef} className="relative min-w-0 max-w-full">
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open && !disabled}
        onClick={() => setOpen((v) => !v)}
        title={`${activeLabel} · ${activeReady ? "密钥已就绪" : "待配置 API Key"}`}
        className="inline-flex h-7 max-w-full items-center gap-1 rounded-full pl-2.5 pr-1.5 transition-colors hover:bg-black/5 hover:text-neutral-800 aria-expanded:bg-black/5 disabled:opacity-50 dark:hover:bg-white/[0.08] dark:hover:text-[#F3F4FA] dark:aria-expanded:bg-white/[0.08]"
      >
        <span className="truncate">{activeLabel}</span>
        <ChevronDown className="h-3 w-3 shrink-0 text-neutral-500 dark:text-[#8E94B0]" />
      </button>

      {open && !disabled && (
        <div
          style={menuSpace}
          className="absolute bottom-full left-0 z-40 mb-2 flex w-72 flex-col overflow-hidden rounded-2xl border border-neutral-200/90 bg-white p-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.12)] dark:border-white/[0.11] dark:bg-[#171A29] dark:shadow-[0_18px_48px_rgba(0,0,0,0.68)]"
        >
          <div className="min-h-0 max-h-96 space-y-2 overflow-y-auto p-1">
            {providers.map((provider) => {
              const ready = Boolean(
                provider.oauth_connected ||
                  provider.env_configured ||
                  apiKeys[provider.id],
              );
              const badgeText = provider.oauth_connected
                ? "ChatGPT 已登录"
                : ready
                  ? "已就绪"
                  : provider.auth_mode === "chatgpt_oauth"
                    ? "待登录 ChatGPT"
                    : "未配置 Key";
              return (
                <div key={provider.id}>
                  <div className="flex items-center justify-between px-2.5 py-1">
                    <span className="text-[11px] font-medium text-neutral-400 dark:text-[#6E7490]">
                      {provider.label}
                    </span>
                    <span
                      className={`text-[10px] ${
                        ready ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-300"
                      }`}
                    >
                      {badgeText}
                    </span>
                  </div>
                  <div className="space-y-0.5">
                    {provider.models.map((model) => {
                      const isSelected =
                        provider.id === selectedProvider &&
                        model.id === selectedModel;
                      return (
                        <button
                          key={model.id}
                          type="button"
                          onClick={() => {
                            onSelect(provider.id, model.id);
                            setOpen(false);
                          }}
                          className={`flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-left transition ${
                            isSelected
                              ? "bg-neutral-100 text-neutral-950 dark:bg-white/[0.09] dark:text-[#F5F6FC]"
                              : "text-neutral-700 hover:bg-neutral-50 dark:text-[#C8CCE0] dark:hover:bg-white/[0.05]"
                          }`}
                        >
                          <span className="truncate text-[13.5px] font-medium">
                            {model.label}
                          </span>
                          {isSelected && (
                            <Check className="h-4 w-4 shrink-0 text-neutral-900 dark:text-[#F3F4FA]" />
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-1 shrink-0 border-t border-neutral-100 pt-1 dark:border-white/[0.08]">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onOpenSettings();
              }}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium text-neutral-600 transition hover:bg-neutral-100 hover:text-neutral-900 dark:text-[#9EA4BF] dark:hover:bg-white/[0.06] dark:hover:text-[#F3F4FA]"
            >
              <KeyRound className="h-3.5 w-3.5" />
              配置 API Key / ChatGPT 登录与参数
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
