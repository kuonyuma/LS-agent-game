import {
  Activity,
  Orbit,
  PanelLeftOpen,
  Settings,
  SquarePen,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  createSession,
  deleteSession,
  fetchHealth,
  fetchKnowledgeBases,
  fetchProviders,
  fetchSessionDetail,
  fetchSessions,
  fetchTools,
  pickNativeWorkspace,
  stopSessionRun,
  streamSessionRun,
  submitApproval,
  updateSession,
} from "./api";
import { AccountMenu } from "./components/AccountMenu";
import { Composer } from "./components/Composer";
import { InspectorPanel } from "./components/InspectorPanel";
import { KnowledgeBaseModal } from "./components/KnowledgeBaseModal";
import { MessageBubble } from "./components/MessageBubble";
import { ModelPicker } from "./components/ModelPicker";
import { SettingsModal } from "./components/SettingsModal";
import { Sidebar } from "./components/Sidebar";
import {
  WelcomeParticleField,
  WELCOME_WARP_DURATION,
} from "./components/WelcomeParticleField";
import { OpeningConstellationIntro } from "./components/intro/OpeningConstellationIntro";
import { VendorSpotlightModal } from "./components/intro/VendorSpotlightModal";
import type { VendorId } from "./components/intro/vendorCatalog";
import { ThemeToggleButton } from "./components/ThemeToggleButton";
import { WorkspaceBrowserModal } from "./components/WorkspaceBrowserModal";
import { resolveEffortAppearance } from "./effortAppearance";
import { useTheme } from "./theme";
import type {
  AgentPhase,
  AttachmentPreview,
  EmbeddingDefaults,
  ExecutionPlan,
  ImageArtifact,
  ImageOptions,
  ImageSearchStatus,
  KnowledgeBaseSummary,
  ModelOption,
  PendingApproval,
  PresentationArtifact,
  ProviderInfo,
  ReasoningEffort,
  RunSettings,
  SessionSummary,
  SubagentTrace,
  ToolInfo,
  ToolTrace,
  UIMessage,
  UsageStats,
  WebAccount,
} from "./types";

const STORAGE_KEY = "ls_agent_studio_settings_v4";
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB
const BINARY_DOCUMENT_TYPES: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

const DEFAULT_SETTINGS: RunSettings = {
  apiKeys: {},
  effortByModel: {},
  safetyEnabled: true,
  maxTokens: null,
  systemPromptOverride: "",
  recentWorkspaces: [],
};

const SUGGESTION_CHIPS = [
  {
    label: "探索当前工作区架构",
    prompt: "请列出当前工作区的核心目录结构，并简要总结关键模块职责。",
  },
  {
    label: "核查项目配置与依赖",
    prompt: "请检查当前工作区的主要配置文件和依赖清单，总结技术栈与入口。",
  },
  {
    label: "多子代理协同调研",
    prompt:
      "请派发 explorer 子代理并发调研当前工作区的核心逻辑与边界约束，并汇总一份简明报告。",
  },
  {
    label: "演示高危写入审批",
    prompt:
      "请帮我在当前工作区新建一个名为 demo_hello.md 的简短测试笔记，验证人工审批流程。",
  },
];
const SHARED_SUGGESTION_CHIPS = [
  { label: "总结上传的资料", prompt: "请总结我提供的资料，提炼主要观点和待确认的问题。" },
];
const PPT_EXAMPLE_CHIP = {
  label: "试做一份 PPT",
  prompt: "请根据以下示例资料，为大一新生制作一份 6 页中文 PPT，主题是「校园反诈第一课」。示例资料：遇到自称老师的人在群里催缴费用，先通过学校公布的渠道核实。兼职刷单和索要验证码都是常见风险。发现可疑情况时先暂停转账，保留记录，通过正式渠道求助。请加入一道情境选择题，生成可下载的 PPT 文件。",
};

function loadSavedSettings(): RunSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // 迁移旧版本配置，并将旧固定上限（4096 / 32768）切为 null（按当前模型自动顶格）
      const legacyRaw =
        localStorage.getItem("ls_agent_studio_settings_v3") ??
        localStorage.getItem("ls_agent_studio_settings_v2") ??
        localStorage.getItem("ls_agent_studio_settings_v1");
      if (legacyRaw) {
        const legacy = JSON.parse(legacyRaw) as Partial<RunSettings>;
        const legacyMax =
          typeof legacy.maxTokens === "number" &&
          legacy.maxTokens !== 4096 &&
          legacy.maxTokens !== 32768
            ? legacy.maxTokens
            : null;
        return {
          ...DEFAULT_SETTINGS,
          apiKeys: legacy.apiKeys ?? {},
          effortByModel: legacy.effortByModel ?? {},
          safetyEnabled: legacy.safetyEnabled ?? true,
          maxTokens: legacyMax,
          systemPromptOverride: legacy.systemPromptOverride ?? "",
          recentWorkspaces: Array.isArray(legacy.recentWorkspaces)
            ? legacy.recentWorkspaces
            : [],
        };
      }
      return DEFAULT_SETTINGS;
    }
    const parsed = JSON.parse(raw) as Partial<RunSettings>;
    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      apiKeys: parsed.apiKeys ?? {},
      effortByModel: parsed.effortByModel ?? {},
      maxTokens:
        typeof parsed.maxTokens === "number" && parsed.maxTokens >= 256
          ? parsed.maxTokens
          : null,
      recentWorkspaces: Array.isArray(parsed.recentWorkspaces)
        ? parsed.recentWorkspaces
        : [],
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function modelEffortKey(providerId: string, modelId: string): string {
  return `${providerId}:${modelId}`;
}

function resolveModelEffort(
  model: ModelOption | undefined,
  saved: ReasoningEffort | undefined,
): ReasoningEffort {
  const levels = model?.thinking_levels ?? [];
  if (levels.length === 0) return "off";
  if (saved && levels.includes(saved)) return saved;
  if (
    model?.default_thinking_level &&
    levels.includes(model.default_thinking_level)
  ) {
    return model.default_thinking_level;
  }
  return levels[Math.min(levels.length - 1, 1)] ?? levels[0];
}

function readAsBase64(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const str = String(reader.result || "");
      const comma = str.indexOf(",");
      resolve(comma >= 0 ? str.slice(comma + 1) : str);
    };
    reader.onerror = () => reject(new Error(`读取文件失败：${file.name}`));
    reader.readAsDataURL(file);
  });
}

function readAsText(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error(`读取文本文件失败：${file.name}`));
    reader.readAsText(file, "utf-8");
  });
}

export default function App({ account, onLogout }: {
  account: WebAccount;
  onLogout: () => Promise<void>;
}) {
  const { isDark } = useTheme();
  const localMode = account.mode === "local";
  const [workspace, setWorkspace] = useState("");
  const [workspaceBrowserOpen, setWorkspaceBrowserOpen] = useState(false);
  const [pickingNativeWorkspace, setPickingNativeWorkspace] = useState(false);

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [imageSearchStatus, setImageSearchStatus] =
    useState<ImageSearchStatus | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [defaultEmbedding, setDefaultEmbedding] =
    useState<EmbeddingDefaults | null>(null);
  const [selectedKnowledgeBaseIds, setSelectedKnowledgeBaseIds] = useState<
    string[]
  >([]);
  const [knowledgeModalOpen, setKnowledgeModalOpen] = useState(false);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [sessionUsage, setSessionUsage] = useState<UsageStats>({});

  const [selectedProvider, setSelectedProvider] = useState("gemini");
  const [selectedModel, setSelectedModel] = useState("gemini-4-hydrogen");

  const [runSettings, setRunSettings] = useState<RunSettings>(() => localMode ? loadSavedSettings() : DEFAULT_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"model" | "tools">("model");

  const [leftOpen, setLeftOpen] = useState(() => window.matchMedia("(min-width: 640px)").matches);
  const [rightOpen, setRightOpen] = useState(false);

  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<AttachmentPreview[]>([]);
  const [imageOptions, setImageOptions] = useState<ImageOptions | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [phase, setPhase] = useState<AgentPhase>("executing");
  const [activePlan, setActivePlan] = useState<ExecutionPlan | null>(null);
  const [subagents, setSubagents] = useState<SubagentTrace[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [bannerError, setBannerError] = useState("");
  const [warpOrigin, setWarpOrigin] = useState<DOMRectReadOnly | null>(null);
  const [effortPreviewRatio, setEffortPreviewRatio] = useState<number | null>(null);
  const [effortEntry, setEffortEntry] = useState<number | null>(null);

  // 开场 3D 多厂商模型星环动画与终态行星特写状态
  const [introOpen, setIntroOpen] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  const [welcomeRevealed, setWelcomeRevealed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  const [welcomeRevealKey, setWelcomeRevealKey] = useState(0);
  const [inspectedVendorId, setInspectedVendorId] = useState<VendorId | null>(null);
  const [handoffMsgId, setHandoffMsgId] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const stopPromiseRef = useRef<Promise<unknown> | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const welcomeComposerRef = useRef<HTMLDivElement | null>(null);
  const dockedComposerRef = useRef<HTMLDivElement | null>(null);
  const messagesListRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!localMode) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(runSettings));
    } catch {
      // ignore
    }
  }, [localMode, runSettings]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const currentProviderInfo = useMemo(
    () => providers.find((p) => p.id === selectedProvider) ?? providers[0],
    [providers, selectedProvider],
  );

  const currentModelInfo = useMemo(
    () =>
      currentProviderInfo?.models.find((m) => m.id === selectedModel) ??
      currentProviderInfo?.models[0],
    [currentProviderInfo, selectedModel],
  );

  const currentThinkingLevels = useMemo<ReasoningEffort[]>(
    () => currentModelInfo?.thinking_levels ?? [],
    [currentModelInfo],
  );
  const effortModelKey = modelEffortKey(selectedProvider, selectedModel);
  const effortVisual = currentModelInfo?.effort_visual ?? null;

  const activeEffort = useMemo<ReasoningEffort>(
    () =>
      resolveModelEffort(
        currentModelInfo,
        runSettings.effortByModel[effortModelKey],
      ),
    [currentModelInfo, runSettings.effortByModel, effortModelKey],
  );
  const particleAppearance = useMemo(
    () => resolveEffortAppearance(currentThinkingLevels, activeEffort, effortPreviewRatio, isDark),
    [currentThinkingLevels, activeEffort, effortPreviewRatio, isDark],
  );

  // 将当前推理强度（含实时拖动推杆预览）连续映射为 0..1，用于强化当前选中厂商行星的粒子特效
  const effortIntensity = useMemo(() => {
    const levelWeights: Record<ReasoningEffort, number> = {
      off: 0,
      low: 0.24,
      medium: 0.48,
      high: 0.72,
      xhigh: 0.94,
      max: 1,
    };
    if (effortPreviewRatio !== null && currentThinkingLevels.length > 1) {
      const pos = effortPreviewRatio * (currentThinkingLevels.length - 1);
      const lo = Math.floor(pos);
      const hi = Math.min(currentThinkingLevels.length - 1, Math.ceil(pos));
      const frac = pos - lo;
      const wLo = levelWeights[currentThinkingLevels[lo]];
      const wHi = levelWeights[currentThinkingLevels[hi]];
      return wLo + (wHi - wLo) * frac;
    }
    return levelWeights[activeEffort];
  }, [currentThinkingLevels, activeEffort, effortPreviewRatio]);

  const supportsVision = currentModelInfo?.supports_vision ?? true;

  const hasApiKey = Boolean(
    currentProviderInfo?.oauth_connected ||
      currentProviderInfo?.env_configured ||
      runSettings.apiKeys[selectedProvider],
  );
  const canMakePpt = tools.some((tool) => tool.name === "generate_pptx");
  const canGenerateImage = tools.some((tool) => tool.name === "generate_image");
  const baseSuggestions = localMode ? SUGGESTION_CHIPS : SHARED_SUGGESTION_CHIPS;
  const showPptSuggestion = canMakePpt && !input.trim() && !attachments.length && !selectedKnowledgeBaseIds.length;
  const suggestions = canGenerateImage && imageOptions ? [] : showPptSuggestion
    ? [PPT_EXAMPLE_CHIP, ...baseSuggestions] : baseSuggestions;

  const refreshProviders = useCallback(
    async (refreshModels = false) => {
      const provRes = await fetchProviders(refreshModels);
      setProviders(provRes.providers);
      const matched = provRes.providers.find((p) => p.id === selectedProvider);
      if (
        matched &&
        matched.models.length > 0 &&
        !matched.models.some((m) => m.id === selectedModel)
      ) {
        setSelectedModel(matched.default_model || matched.models[0].id);
      }
    },
    [selectedProvider, selectedModel],
  );

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; ok?: boolean; message?: string } | null;
      if (!data || data.type !== "chatgpt-oauth-complete") return;
      if (data.ok) {
        setBannerError("");
        void refreshProviders(true).catch(() => undefined);
      } else if (data.message) {
        setBannerError(data.message);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [refreshProviders]);

  const geminiEnvConfigured = useMemo(
    () =>
      providers.find((p) => p.id === "gemini")?.env_configured ?? false,
    [providers],
  );

  const refreshKnowledgeBases = useCallback(async () => {
    const res = await fetchKnowledgeBases();
    setKnowledgeBases(res.knowledge_bases);
    setDefaultEmbedding(res.default_embedding);
    const validIds = new Set(res.knowledge_bases.map((kb) => kb.kb_id));
    setSelectedKnowledgeBaseIds((prev) =>
      prev.filter((id) => validIds.has(id)),
    );
    return res.knowledge_bases;
  }, []);

  const planStepsTotal = activePlan?.steps.length ?? 0;
  const planStepsDone =
    activePlan?.steps.filter(
      (s) => s.status === "completed" || s.status === "skipped",
    ).length ?? 0;

  const recordRecentWorkspace = useCallback((wsPath: string) => {
    if (!wsPath) return;
    setRunSettings((prev) => {
      const next = [
        wsPath,
        ...prev.recentWorkspaces.filter((item) => item !== wsPath),
      ].slice(0, 8);
      return { ...prev, recentWorkspaces: next };
    });
  }, []);

  const refreshSessionsList = useCallback(async () => {
    const res = await fetchSessions();
    setSessions(res.sessions);
    return res.sessions;
  }, []);

  const applyWorkspace = useCallback(
    async (nextWorkspace: string) => {
      const cleaned = nextWorkspace.trim();
      if (!cleaned) return;
      setBannerError("");
      setWorkspace(cleaned);
      recordRecentWorkspace(cleaned);
      try {
        const toolsRes = await fetchTools(cleaned);
        setTools(toolsRes.tools);
      } catch (err) {
        setBannerError(
          err instanceof Error ? err.message : "切换工作目录失败",
        );
        return;
      }
      if (activeSessionId && !isRunning) {
        try {
          await updateSession(activeSessionId, { workspace: cleaned });
          await refreshSessionsList();
        } catch {
          // ignore
        }
      }
    },
    [activeSessionId, isRunning, recordRecentWorkspace, refreshSessionsList],
  );

  const handlePickNativeWorkspace = async () => {
    if (isRunning || pickingNativeWorkspace) return;
    setBannerError("");
    setPickingNativeWorkspace(true);
    try {
      const res = await pickNativeWorkspace(workspace || undefined);
      if (!res.cancelled && res.path) {
        await applyWorkspace(res.path);
      }
    } catch (err) {
      setBannerError(
        err instanceof Error ? err.message : "打开系统文件夹选择器失败",
      );
    } finally {
      setPickingNativeWorkspace(false);
    }
  };

  const loadSession = useCallback(
    async (sessionId: string) => {
      const detail = await fetchSessionDetail(sessionId);
      setWarpOrigin(null);
      setEffortPreviewRatio(null);
      setEffortEntry(null);
      setIntroOpen(false);
      setWelcomeRevealed(true);
      setInspectedVendorId(null);
      setHandoffMsgId(null);
      setActiveSessionId(detail.session_id);
      setImageOptions(null);
      setMessages(detail.messages);
      setSessionUsage(detail.usage ?? {});
      setSelectedKnowledgeBaseIds(detail.knowledge_base_ids ?? []);
      if (detail.workspace) {
        setWorkspace(detail.workspace);
        recordRecentWorkspace(detail.workspace);
        try {
          const toolsRes = await fetchTools(detail.workspace);
          setTools(toolsRes.tools);
        } catch {
          // ignore
        }
      }

      // 若旧历史会话使用了已下架模型，平滑回退到该厂商默认模型
      const prov =
        providers.find((p) => p.id === detail.provider) ?? providers[0];
      if (prov) {
        setSelectedProvider(prov.id);
        const validModel = prov.models.some((m) => m.id === detail.model_name)
          ? detail.model_name
          : prov.default_model;
        setSelectedModel(validModel);
      } else {
        setSelectedProvider(detail.provider);
        setSelectedModel(detail.model_name);
      }
      setPendingApprovals([]);

      const lastAssistant = [...detail.messages]
        .reverse()
        .find((m) => m.role === "assistant");
      setActivePlan(lastAssistant?.plan ?? null);
      setPhase(lastAssistant?.plan?.phase ?? "executing");
      setSubagents(lastAssistant?.subagents ?? []);
    },
    [providers, recordRecentWorkspace],
  );

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const [health, provRes, toolsRes, sessRes, kbRes] = await Promise.all([
          fetchHealth(),
          fetchProviders(),
          fetchTools(),
          fetchSessions(),
          fetchKnowledgeBases(),
        ]);
        if (!mounted) return;
        setWorkspace(health.workspace);
        setImageSearchStatus(health.image_search ?? null);
        recordRecentWorkspace(health.workspace);
        setProviders(provRes.providers);
        setTools(toolsRes.tools);
        setSessions(sessRes.sessions);
        setKnowledgeBases(kbRes.knowledge_bases);
        setDefaultEmbedding(kbRes.default_embedding);

        const defaultProv =
          provRes.providers.find((p) => p.id === provRes.default_provider) ?? provRes.providers[0];
        const initialProvider = localMode || defaultProv?.env_configured || defaultProv?.oauth_connected
          ? defaultProv
          : provRes.providers.find((p) => p.env_configured || p.oauth_connected) ?? defaultProv;
        if (initialProvider) {
          setSelectedProvider(initialProvider.id);
          setSelectedModel(initialProvider.default_model);
        }
      } catch (err) {
        if (mounted) {
          setBannerError(
            err instanceof Error ? `后端连接失败：${err.message}` : "后端连接失败",
          );
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, [localMode, recordRecentWorkspace]);

  useEffect(() => {
    if (messages.length > 0) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, pendingApprovals]);

  useLayoutEffect(() => {
    if (!warpOrigin) return;
    const finishWarp = () => setWarpOrigin(null);
    const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (motionPreference.matches) {
      finishWarp();
      return;
    }
    const dismissWarp = window.setTimeout(finishWarp, WELCOME_WARP_DURATION + 48);
    const updateMotion = (event: MediaQueryListEvent) => {
      if (event.matches) finishWarp();
    };
    motionPreference.addEventListener("change", updateMotion);

    const animations: Animation[] = [];

    // 1) 向下沉降：底部输入框 0ms 响应但采用沉稳带惯性阻尼的曲线，与虫洞蓄压→推背同频受力
    const dockedComposer = dockedComposerRef.current;
    const destination = dockedComposer?.getBoundingClientRect();
    if (dockedComposer && destination) {
      const x =
        warpOrigin.left - destination.left + (warpOrigin.width - destination.width) / 2;
      const y =
        warpOrigin.top - destination.top + (warpOrigin.height - destination.height) / 2;
      const flight = dockedComposer.animate?.(
        [
          { transform: `translate(${x}px, ${y}px) scale(1)`, offset: 0 },
          { transform: `translate(${x}px, ${(y * 0.96).toFixed(1)}px) scale(0.988)`, offset: 0.14 },
          { transform: "translate(0, 0) scale(1)", offset: 1 },
        ],
        {
          delay: 0,
          duration: 640,
          fill: "backwards",
          easing: "cubic-bezier(0.28, 0.08, 0.16, 1)",
        },
      );
      if (flight) animations.push(flight);
    }

    // 2) 向上镜像升空：随虫洞加压峰值与破壁清场，首条用户气泡优雅上浮，AI 首条紧接行星落点显现
    const listRoot = messagesListRef.current;
    if (listRoot) {
      const firstUser = listRoot.querySelector<HTMLElement>("[data-first-user='1']");
      if (firstUser) {
        const uRect = firstUser.getBoundingClientRect();
        const riseY = Math.max(52, Math.min(196, warpOrigin.top - uRect.top + 18));
        const userAnim = firstUser.animate?.(
          [
            { opacity: 0, transform: `translateY(${riseY}px) scale(0.955)` },
            { opacity: 1, transform: "translateY(0) scale(1)" },
          ],
          {
            delay: 220,
            duration: 500,
            fill: "backwards",
            easing: "cubic-bezier(0.18, 0.88, 0.24, 1)",
          },
        );
        if (userAnim) animations.push(userAnim);
      }

      const firstAssistant = listRoot.querySelector<HTMLElement>("[data-first-assistant='1']");
      if (firstAssistant) {
        const assistantAnim = firstAssistant.animate?.(
          [
            { opacity: 0, transform: "translateY(16px) scale(0.985)" },
            { opacity: 1, transform: "translateY(0) scale(1)" },
          ],
          {
            delay: 420,
            duration: 420,
            fill: "backwards",
            easing: "cubic-bezier(0.2, 0.9, 0.28, 1)",
          },
        );
        if (assistantAnim) animations.push(assistantAnim);
      }
    }

    return () => {
      for (const anim of animations) anim.cancel();
      window.clearTimeout(dismissWarp);
      motionPreference.removeEventListener("change", updateMotion);
    };
  }, [warpOrigin]);

  const handleNewChat = () => {
    if (isRunning) return;
    setWarpOrigin(null);
    setEffortPreviewRatio(null);
    setEffortEntry(null);
    setInspectedVendorId(null);
    setHandoffMsgId(null);
    setBannerError("");
    setActiveSessionId(null);
    setMessages([]);
    setSessionUsage({});
    setActivePlan(null);
    setPhase("executing");
    setSubagents([]);
    setPendingApprovals([]);
    setAttachments([]);
    setImageOptions(null);
  };

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await deleteSession(sessionId);
      const remaining = await refreshSessionsList();
      if (activeSessionId === sessionId) {
        if (remaining.length > 0) {
          await loadSession(remaining[0].session_id);
        } else {
          handleNewChat();
        }
      }
    } catch (err) {
      setBannerError(err instanceof Error ? err.message : "删除对话失败");
    }
  };

  const handleModelSelect = async (providerId: string, modelId: string) => {
    setEffortPreviewRatio(null);
    setEffortEntry(null);
    setInspectedVendorId(null);
    setSelectedProvider(providerId);
    setSelectedModel(modelId);
    if (activeSessionId && !isRunning) {
      try {
        await updateSession(activeSessionId, {
          provider: providerId,
          model_name: modelId,
        });
        await refreshSessionsList();
      } catch {
        // ignore
      }
    }
  };

  const handleChangeEffort = (effort: ReasoningEffort) => {
    setRunSettings((prev) => ({
      ...prev,
      effortByModel: {
        ...prev.effortByModel,
        [effortModelKey]: effort,
      },
    }));
  };

  const persistSessionKnowledgeBases = useCallback(
    (nextIds: string[]) => {
      if (!activeSessionId || isRunning) return;
      void updateSession(activeSessionId, { knowledge_base_ids: nextIds })
        .then(() => refreshSessionsList())
        .catch(() => undefined);
    },
    [activeSessionId, isRunning, refreshSessionsList],
  );

  const handleToggleKnowledgeBase = useCallback(
    (kbId: string) => {
      setSelectedKnowledgeBaseIds((prev) => {
        const next = prev.includes(kbId)
          ? prev.filter((id) => id !== kbId)
          : [...prev, kbId];
        persistSessionKnowledgeBases(next);
        return next;
      });
    },
    [persistSessionKnowledgeBases],
  );

  const handleSelectKnowledgeBase = useCallback(
    (kbId: string) => {
      setSelectedKnowledgeBaseIds((prev) => {
        if (prev.includes(kbId)) return prev;
        const next = [...prev, kbId];
        persistSessionKnowledgeBases(next);
        return next;
      });
    },
    [persistSessionKnowledgeBases],
  );

  const handleClearKnowledgeBases = useCallback(() => {
    setSelectedKnowledgeBaseIds([]);
    persistSessionKnowledgeBases([]);
  }, [persistSessionKnowledgeBases]);

  const handleAttachImages = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBannerError("");
    const next: AttachmentPreview[] = [];
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("image/")) continue;
      if (file.size > MAX_UPLOAD_BYTES) {
        setBannerError(`图片 ${file.name} 超过 10MB 上限，已跳过。`);
        continue;
      }
      try {
        const b64 = await readAsBase64(file);
        next.push({
          name: file.name,
          mime_type: file.type,
          kind: "image",
          size: file.size,
          data_base64: b64,
        });
      } catch (err) {
        setBannerError(err instanceof Error ? err.message : "读取图片失败");
      }
    }
    if (next.length > 0) {
      setAttachments((prev) => [...prev, ...next]);
    }
  };

  const handleAttachDocuments = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBannerError("");
    const next: AttachmentPreview[] = [];
    for (const file of Array.from(files)) {
      if (file.size > MAX_UPLOAD_BYTES) {
        setBannerError(`文件 ${file.name} 超过 10MB 上限，已跳过。`);
        continue;
      }
      try {
        const extension = file.name.toLowerCase().match(/\.([^.]+)$/)?.[1] ?? "";
        const binaryType = Object.hasOwn(BINARY_DOCUMENT_TYPES, extension)
          ? BINARY_DOCUMENT_TYPES[extension]
          : file.type === "application/pdf" ? file.type : "";
        if (!binaryType && file.type.startsWith("image/")) {
          if (!supportsVision) {
            setBannerError(`当前模型不支持图片输入，已跳过 ${file.name}。`);
            continue;
          }
          const b64 = await readAsBase64(file);
          next.push({
            name: file.name,
            mime_type: file.type,
            kind: "image",
            size: file.size,
            data_base64: b64,
          });
        } else if (binaryType) {
          const b64 = await readAsBase64(file);
          next.push({
            name: file.name,
            mime_type: binaryType,
            kind: "file",
            size: file.size,
            data_base64: b64,
          });
        } else {
          const text = await readAsText(file);
          next.push({
            name: file.name,
            mime_type: file.type || "text/plain",
            kind: "file",
            size: file.size,
            text_content: text,
          });
        }
      } catch (err) {
        setBannerError(err instanceof Error ? err.message : "读取文件失败");
      }
    }
    if (next.length > 0) {
      setAttachments((prev) => [...prev, ...next]);
    }
  };

  const handleApprove = async (approval: PendingApproval, approved: boolean) => {
    if (!activeSessionId) return;
    setPendingApprovals((prev) =>
      prev.map((item) =>
        item.approval_id === approval.approval_id
          ? { ...item, submitting: true }
          : item,
      ),
    );
    try {
      await submitApproval(
        activeSessionId,
        approval.approval_id,
        approved,
        approval.request_hash,
      );
      setPendingApprovals((prev) =>
        prev.filter((item) => item.approval_id !== approval.approval_id),
      );
      if (approved) {
        setMessages((prev) => prev.map((message) => !message.streaming ? message : {
          ...message,
          tool_calls: message.tool_calls?.map((trace) =>
            trace.call_id === approval.tool_call_id && trace.status === "awaiting_approval"
              ? { ...trace, status: "running" }
              : trace,
          ),
        }));
      }
    } catch (err) {
      setBannerError(err instanceof Error ? err.message : "提交审批失败");
      setPendingApprovals((prev) =>
        prev.map((item) =>
          item.approval_id === approval.approval_id
            ? { ...item, submitting: false }
            : item,
        ),
      );
    }
  };

  const handleStop = () => {
    const sid = activeSessionId;
    if (sid && !stopPromiseRef.current) {
      stopPromiseRef.current = stopSessionRun(sid)
        .catch(() => undefined)
        .finally(() => {
          stopPromiseRef.current = null;
        });
    }
    abortRef.current?.abort();
  };

  const handleSend = async (overrideText?: string) => {
    const queryText = (overrideText ?? input).trim();
    if (!queryText || isRunning) return;

    setBannerError("");
    const isFirstTurn = messages.length === 0;
    const source = welcomeComposerRef.current;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // 0ms 乐观起手：不等待后端建会话往返，按下回车当帧立即启动行星俯冲与反向镜像转场
    if (isFirstTurn && source && !reducedMotion) {
      setWarpOrigin(source.getBoundingClientRect());
    }
    setEffortPreviewRatio(null);
    setEffortEntry(null);
    setInspectedVendorId(null);

    const currentAttachments = [...attachments];
    setInput("");
    setAttachments([]);
    setIsRunning(true);
    setPendingApprovals([]);
    setPhase("executing");

    const userMsgId = `local_u_${Date.now()}`;
    const assistantMsgId = `local_a_${Date.now()}`;
    if (isFirstTurn) {
      setHandoffMsgId(assistantMsgId);
    }

    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: "user",
        text: queryText,
        attachments: currentAttachments,
      },
      {
        id: assistantMsgId,
        role: "assistant",
        provider: selectedProvider,
        model_name: selectedModel,
        thinking: "",
        text: "",
        tool_calls: [],
        subagents: [],
        streaming: true,
      },
    ]);

    const updateAssistant = (updater: (msg: UIMessage) => UIMessage) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantMsgId ? updater(m) : m)),
      );
    };

    let targetSessionId = activeSessionId;
    try {
      if (!targetSessionId) {
        const created = await createSession({
          title: queryText.slice(0, 28),
          provider: selectedProvider,
          model_name: selectedModel,
          workspace: workspace || undefined,
          knowledge_base_ids: selectedKnowledgeBaseIds,
        });
        targetSessionId = created.session_id;
        setActiveSessionId(created.session_id);
        void refreshSessionsList().catch(() => undefined);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "无法初始化对话";
      setBannerError(msg);
      updateAssistant((m) => ({ ...m, streaming: false, error: msg }));
      setIsRunning(false);
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamSessionRun(
        targetSessionId,
        {
          query: queryText,
          provider: selectedProvider,
          model_name: selectedModel,
          workspace: workspace || undefined,
          api_key: runSettings.apiKeys[selectedProvider] || undefined,
          search_api_key: runSettings.apiKeys.deepseek || undefined,
          serper_api_key: runSettings.apiKeys.serper || undefined,
          tavily_api_key: runSettings.apiKeys.tavily || undefined,
          embedding_api_key: runSettings.apiKeys.gemini || undefined,
          knowledge_base_ids: selectedKnowledgeBaseIds,
          thinking_enabled: activeEffort !== "off",
          reasoning_effort: activeEffort,
          safety_enabled: runSettings.safetyEnabled,
          plan_and_execute: false,
          max_tokens: runSettings.maxTokens ?? undefined,
          system_prompt_override:
            runSettings.systemPromptOverride.trim() || undefined,
          attachments: currentAttachments,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          image_options: canGenerateImage ? imageOptions ?? undefined : undefined,
        },
        (ev) => {
          const evType = String(ev.type ?? "");
          if (ev.phase) {
            setPhase(ev.phase as AgentPhase);
          }
          if (ev.plan) {
            const p = ev.plan as ExecutionPlan;
            setActivePlan(p);
            setPhase(p.phase);
            if (p.steps.length > 0 && window.innerWidth >= 1280) {
              setRightOpen(true);
            }
          }

          if (evType === "thinking.delta") {
            const delta = String(ev.text ?? "");
            updateAssistant((m) => ({
              ...m,
              thinking: (m.thinking ?? "") + delta,
            }));
          } else if (evType === "text.delta") {
            const delta = String(ev.text ?? "");
            updateAssistant((m) => ({ ...m, text: m.text + delta }));
          } else if (evType === "tool.requested") {
            const callId = String(ev.tool_call_id ?? "");
            const toolName = String(ev.tool_name ?? "");
            const args = (ev.tool_arguments as Record<string, unknown>) ?? {};
            const reqApproval = Boolean(ev.requires_approval);
            const trace: ToolTrace = {
              call_id: callId,
              name: toolName,
              arguments: args,
              requires_approval: reqApproval,
              request_hash: String(ev.request_hash ?? ""),
              status: reqApproval ? "awaiting_approval" : "running",
              result: "",
              is_error: false,
            };
            updateAssistant((m) => ({
              ...m,
              tool_calls: [...(m.tool_calls ?? []), trace],
            }));
            if (toolName === "subagent") {
              if (window.innerWidth >= 1280) {
                setRightOpen(true);
              }
              const sa: SubagentTrace = {
                call_id: callId,
                action: String(args.action ?? "spawn"),
                role: String(args.role ?? "explorer"),
                agent_id: String(args.agent_id ?? ""),
                description: String(args.description ?? args.task ?? "").slice(
                  0,
                  80,
                ),
                status: "running",
              };
              setSubagents((prev) => [...prev, sa]);
            }
          } else if (evType === "tool.approval_required") {
            const approval: PendingApproval = {
              approval_id: String(ev.approval_id ?? ""),
              tool_call_id: String(ev.tool_call_id ?? ""),
              tool_name: String(ev.tool_name ?? ""),
              tool_arguments:
                (ev.tool_arguments as Record<string, unknown>) ?? {},
              request_hash: String(ev.request_hash ?? ""),
            };
            setPendingApprovals((prev) => [...prev, approval]);
          } else if (evType === "tool.completed") {
            const callId = String(ev.tool_call_id ?? "");
            const toolName = String(ev.tool_name ?? "");
            const isErr = Boolean(ev.is_error);
            const resText = String(ev.result ?? "");
            const verifiedUrls = Array.isArray(ev.verified_media_urls)
              ? (ev.verified_media_urls as string[])
              : undefined;
            updateAssistant((m) => ({
              ...m,
              verified_media_urls: verifiedUrls ?? m.verified_media_urls,
              tool_calls: (m.tool_calls ?? []).map((t) =>
                t.call_id === callId
                  ? {
                      ...t,
                      status: isErr ? "error" : "completed",
                      result: resText,
                      is_error: isErr,
                    }
                  : t,
              ),
            }));
            if (toolName === "subagent") {
              setSubagents((prev) =>
                prev.map((sa) =>
                  sa.call_id === callId
                    ? {
                        ...sa,
                        status: isErr ? "failed" : "completed",
                        summary: resText.slice(0, 200),
                      }
                    : sa,
                ),
              );
            }
          } else if (evType === "safety.blocked") {
            updateAssistant((m) => ({
              ...m,
              safety_blocked: true,
              safety_category: String(ev.safety_category ?? ""),
              text: String(ev.text ?? "内容已触发安全审核拦截"),
            }));
          } else if (evType === "run.completed" || evType === "run.failed") {
            const usage = (ev.usage as UsageStats) ?? {};
            const verifiedUrls = Array.isArray(ev.verified_media_urls)
              ? (ev.verified_media_urls as string[])
              : undefined;
            updateAssistant((m) => ({
              ...m,
              presentations: (ev.presentations as PresentationArtifact[] | undefined) ?? m.presentations,
              images: (ev.images as ImageArtifact[] | undefined) ?? m.images,
              verified_media_urls: verifiedUrls ?? m.verified_media_urls,
              streaming: false,
              usage,
              reason: String(ev.reason ?? ""),
              turns: Number(ev.turns ?? 0),
              duration_ms: Number(ev.duration_ms ?? 0),
              error: ev.error ? String(ev.error) : undefined,
            }));
            setPendingApprovals([]);
          }
        },
        controller.signal,
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        const msg = err instanceof Error ? err.message : "流式运行异常中断";
        updateAssistant((m) => ({ ...m, streaming: false, error: msg }));
      } else {
        updateAssistant((m) => ({
          ...m,
          streaming: false,
          error: "运行已由用户手动中止。",
        }));
      }
    } finally {
      if (stopPromiseRef.current) {
        await stopPromiseRef.current;
      }
      setIsRunning(false);
      abortRef.current = null;
      setPendingApprovals([]);
      if (targetSessionId) {
        try {
          await refreshSessionsList();
          const refreshed = await fetchSessionDetail(targetSessionId);
          setSessionUsage(refreshed.usage ?? {});
        } catch {
          // ignore
        }
      }
    }
  };

  const isEmpty = messages.length === 0;
  const composer = (
    <Composer
      centered={isEmpty}
      placeholder={isEmpty && canMakePpt ? "输入你的任务，例如：帮我做一份介绍乐山的 PPT" : undefined}
      showWorkspace={localMode}
      value={input}
      attachments={attachments}
      workspace={workspace}
      recentWorkspaces={runSettings.recentWorkspaces}
      pickingNativeWorkspace={pickingNativeWorkspace}
      supportsVision={supportsVision}
      canGenerateImage={canGenerateImage}
      imageOptions={canGenerateImage ? imageOptions : null}
      onImageOptionsChange={setImageOptions}
      modelPicker={
        <ModelPicker
          providers={providers}
          selectedProvider={selectedProvider}
          selectedModel={selectedModel}
          apiKeys={runSettings.apiKeys}
          disabled={isRunning}
          onSelect={(pid, mid) => void handleModelSelect(pid, mid)}
          onOpenSettings={() => {
            setSettingsTab("model");
            setSettingsOpen(true);
          }}
        />
      }
      knowledgeBases={knowledgeBases}
      selectedKnowledgeBaseIds={selectedKnowledgeBaseIds}
      thinkingLevels={currentThinkingLevels}
      reasoningEffort={activeEffort}
      effortModelKey={effortModelKey}
      effortEntryLevel={isEmpty ? effortVisual?.level : undefined}
      isRunning={isRunning}
      onChange={setInput}
      onAttachImages={(files) => void handleAttachImages(files)}
      onAttachDocuments={(files) => void handleAttachDocuments(files)}
      onRemoveAttachment={(idx) =>
        setAttachments((prev) => prev.filter((_, i) => i !== idx))
      }
      onToggleKnowledgeBase={handleToggleKnowledgeBase}
      onClearKnowledgeBases={handleClearKnowledgeBases}
      onOpenKnowledgeModal={() => setKnowledgeModalOpen(true)}
      onChangeEffort={handleChangeEffort}
      onEffortPreview={isEmpty ? setEffortPreviewRatio : undefined}
      onEffortEntry={isEmpty ? () => setEffortEntry((last) => (last ?? 0) + 1) : undefined}
      onSelectWorkspace={(path) => void applyWorkspace(path)}
      onPickNativeWorkspace={() => void handlePickNativeWorkspace()}
      onOpenWorkspaceBrowser={() => setWorkspaceBrowserOpen(true)}
      onSend={() => void handleSend()}
      onStop={() => handleStop()}
    />
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-white text-neutral-900 dark:bg-[#0B0C14] dark:text-[#F3F4FA]">
      {/* Smooth animated left sidebar wrapper */}
      <div
        className={`h-full shrink-0 overflow-hidden transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
          leftOpen ? "w-64" : "w-0"
        }`}
      >
        <div
          className={`h-full w-64 transition-[transform,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
            leftOpen
              ? "translate-x-0 opacity-100"
              : "-translate-x-4 opacity-0 pointer-events-none"
          }`}
        >
          <Sidebar
            sessions={sessions}
            activeSessionId={activeSessionId}
            workspace={workspace}
            toolCount={tools.length}
            knowledgeBaseCount={knowledgeBases.length}
            hasApiKey={hasApiKey}
            onCreateSession={handleNewChat}
            onSelectSession={(id) => {
              if (!isRunning) void loadSession(id);
            }}
            onDeleteSession={(id) => void handleDeleteSession(id)}
            onCollapse={() => setLeftOpen(false)}
            onOpenSettings={(tab) => {
              setSettingsTab(tab);
              setSettingsOpen(true);
            }}
            onOpenKnowledgeModal={() => setKnowledgeModalOpen(true)}
            onOpenWorkspaceBrowser={localMode ? () => {
              if (!isRunning) setWorkspaceBrowserOpen(true);
            } : undefined}
          />
        </div>
      </div>

      {/* Main Canvas */}
      <main className="flex min-w-0 flex-1 flex-col bg-white dark:bg-[#0B0C14]">
        {/* Ultra-minimal 52px Top Bar */}
        <header className="relative z-30 flex h-[52px] shrink-0 items-center justify-between px-3 sm:px-4">
          <div className="flex items-center gap-1">
            <div
              className={`flex items-center gap-1 overflow-hidden transition-[width,opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
                leftOpen
                  ? "w-0 -translate-x-2 opacity-0 pointer-events-none"
                  : "w-[72px] translate-x-0 opacity-100"
              }`}
            >
              <button
                type="button"
                onClick={() => setLeftOpen(true)}
                title="展开侧边栏"
                className="rounded-lg p-1.5 text-neutral-500 transition hover:bg-neutral-100 hover:text-neutral-900 dark:text-[#8E94B0] dark:hover:bg-white/[0.07] dark:hover:text-[#F3F4FA]"
              >
                <PanelLeftOpen className="h-[18px] w-[18px]" />
              </button>
              <button
                type="button"
                disabled={isRunning}
                onClick={handleNewChat}
                title="发起新对话"
                className="rounded-lg p-1.5 text-neutral-500 transition hover:bg-neutral-100 hover:text-neutral-900 disabled:opacity-40 dark:text-[#8E94B0] dark:hover:bg-white/[0.07] dark:hover:text-[#F3F4FA]"
              >
                <SquarePen className="h-[18px] w-[18px]" />
              </button>
            </div>

          </div>

          <div className="flex items-center gap-1.5">
            {isEmpty && (
              <button
                type="button"
                onClick={() => {
                  setInspectedVendorId(null);
                  setWelcomeRevealed(false);
                  setIntroOpen(true);
                }}
                title="重播开场星环动画"
                aria-label="重播开场星环动画"
                className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-neutral-200/80 bg-white text-neutral-500 transition hover:border-violet-200 hover:bg-violet-50 hover:text-violet-700 dark:border-white/[0.11] dark:bg-white/[0.05] dark:text-[#A3A8C2] dark:hover:border-violet-400/35 dark:hover:bg-violet-500/[0.14] dark:hover:text-violet-200"
              >
                <Orbit className="h-[15px] w-[15px]" />
              </button>
            )}
            <button
              type="button"
              onClick={() => setRightOpen((v) => !v)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                rightOpen
                  ? "border-neutral-300 bg-neutral-100 text-neutral-950 dark:border-white/[0.18] dark:bg-white/[0.11] dark:text-[#F3F4FA]"
                  : "border-neutral-200/80 bg-white text-neutral-600 hover:bg-neutral-50 hover:text-neutral-900 dark:border-white/[0.11] dark:bg-white/[0.05] dark:text-[#C8CCE0] dark:hover:bg-white/[0.09] dark:hover:text-[#F3F4FA]"
              }`}
            >
              <Activity
                className={`h-3.5 w-3.5 ${
                  isRunning ? "animate-pulse text-blue-600 dark:text-[#7DA8FF]" : "text-neutral-400 dark:text-[#7C829E]"
                }`}
              />
              <span>运行追踪</span>
              {planStepsTotal > 0 && (
                <span className="rounded-full bg-neutral-200/80 px-1.5 py-0.2 font-mono text-[10.5px] text-neutral-700 dark:bg-white/[0.12] dark:text-[#E5E8F5]">
                  {planStepsDone}/{planStepsTotal}
                </span>
              )}
            </button>

            <ThemeToggleButton className="h-8 w-8" />

            <button
              type="button"
              onClick={() => {
                setSettingsTab("model");
                setSettingsOpen(true);
              }}
              title="模型设置与 API Key"
              className="relative rounded-full p-2 text-neutral-500 transition hover:bg-neutral-100 hover:text-neutral-900 dark:text-[#989EB8] dark:hover:bg-white/[0.07] dark:hover:text-[#F3F4FA]"
            >
              <Settings className="h-[17px] w-[17px]" />
              {!hasApiKey && (
                <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-amber-400 ring-2 ring-white dark:ring-[#0B0C14]" />
              )}
            </button>
            {!localMode && <AccountMenu account={account} onLogout={onLogout} />}
          </div>
        </header>

        {bannerError && (
          <div className="mx-auto mb-2 flex w-full max-w-3xl items-center justify-between rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-700 dark:border-rose-400/25 dark:bg-rose-500/[0.11] dark:text-rose-200">
            <span>{bannerError}</span>
            <button type="button" onClick={() => setBannerError("")}>
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        <div className="relative isolate flex min-h-0 flex-1 flex-col">
          {(isEmpty || warpOrigin) && (
            <div
              aria-hidden="true"
              className={`welcome-astra-backdrop pointer-events-none absolute inset-0 transition-opacity ${
                warpOrigin ? "duration-700 delay-200 opacity-0" : "duration-500 opacity-100"
              }`}
            />
          )}
          {(isEmpty || warpOrigin) && (
            <WelcomeParticleField
              composerRef={welcomeComposerRef}
              warpOrigin={warpOrigin}
              appearance={particleAppearance}
              modelKey={effortModelKey}
              visual={effortVisual?.level === particleAppearance.level ? effortVisual : null}
              entry={effortEntry}
              activeProvider={selectedProvider}
              effortIntensity={effortIntensity}
              inspectedVendorId={inspectedVendorId}
              planetsVisible={isEmpty && welcomeRevealed}
              revealKey={welcomeRevealKey}
              onSelectVendor={(vid) => setInspectedVendorId(vid)}
            />
          )}
          {isEmpty ? (
            <div className="pointer-events-none relative z-20 flex flex-1 flex-col items-center justify-center px-4 pb-12">
              <div
                key={welcomeRevealKey}
                className={`pointer-events-none w-full max-w-2xl transition-opacity duration-300 ${
                  welcomeRevealed ? "welcome-bloom-enter opacity-100" : "opacity-0"
                }`}
              >
                <div className="mb-6 text-center">
                  <h2 className="inline-block text-[26px] font-semibold tracking-tight text-neutral-900 dark:text-[#F3F4FA] sm:text-[30px]">
                    今天想推进什么任务？
                  </h2>
                </div>

                <div ref={welcomeComposerRef} className="pointer-events-auto">
                  {composer}
                </div>

                <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                  {suggestions.map((chip) => (
                    <button
                      key={chip.label}
                      type="button"
                      onClick={() => {
                        if (chip === PPT_EXAMPLE_CHIP && !hasApiKey) {
                          setBannerError(localMode ? "请先在模型设置中配置一个可用模型。" : "暂时没有可用的模型，请联系工作台管理员。");
                          return;
                        }
                        void handleSend(chip.prompt);
                      }}
                      className="pointer-events-auto rounded-full border border-neutral-200/90 bg-white px-3.5 py-1.5 text-xs text-neutral-600 transition hover:border-violet-200 hover:bg-violet-50 hover:text-violet-900 dark:border-white/[0.11] dark:bg-[#121420]/70 dark:text-[#C5C9DE] dark:backdrop-blur-md dark:hover:border-violet-400/35 dark:hover:bg-violet-500/[0.14] dark:hover:text-[#F3F4FA]"
                    >
                      {chip.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <>
              {/* 首次输入转场前 240ms 的标题与建议条柔雾上飘退场残影，彻底消灭 0ms 硬切瞬灭 */}
              {warpOrigin && (
                <div
                  aria-hidden="true"
                  className="welcome-chrome-exit pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center px-4 pb-12"
                >
                  <div className="w-full max-w-2xl">
                    <div className="mb-6 text-center">
                      <div className="inline-block text-[26px] font-semibold tracking-tight text-neutral-900 dark:text-[#F3F4FA] sm:text-[30px]">
                        今天想推进什么任务？
                      </div>
                    </div>
                    <div style={{ height: `${Math.round(warpOrigin.height)}px` }} />
                    <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                      {suggestions.map((chip) => (
                        <span
                          key={chip.label}
                          className="rounded-full border border-neutral-200/90 bg-white px-3.5 py-1.5 text-xs text-neutral-600 dark:border-white/[0.11] dark:bg-[#121420]/70 dark:text-[#C5C9DE]"
                        >
                          {chip.label}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              <div className="relative z-10 flex-1 overflow-y-auto px-4 pt-4 pb-8 sm:px-6">
                <div ref={messagesListRef} className="mx-auto max-w-3xl space-y-8">
                  {messages.map((msg, idx) => (
                    <div
                      key={msg.id}
                      data-first-user={warpOrigin && idx === 0 && msg.role === "user" ? "1" : undefined}
                      data-first-assistant={
                        warpOrigin && idx === 1 && msg.role === "assistant" ? "1" : undefined
                      }
                    >
                      <MessageBubble
                        message={msg}
                        sessionId={activeSessionId}
                        handoffEcho={msg.id === handoffMsgId}
                        pendingApprovals={
                          idx === messages.length - 1 ? pendingApprovals : []
                        }
                        onApprove={(approval, ok) => void handleApprove(approval, ok)}
                      />
                    </div>
                  ))}
                  <div ref={bottomRef} />
                </div>
              </div>
              <div
                className={`relative z-20 shrink-0 px-4 pb-4 pt-2 sm:px-6 ${
                  warpOrigin
                    ? "bg-transparent"
                    : "bg-gradient-to-t from-white via-white to-white/80 dark:from-[#0B0C14] dark:via-[#0B0C14] dark:to-[#0B0C14]/80"
                }`}
              >
                <div ref={dockedComposerRef} className="mx-auto max-w-3xl">
                  {composer}
                </div>
              </div>
            </>
          )}
        </div>
      </main>

      {/* Smooth animated right Inspector drawer */}
      <div
        className={`h-full shrink-0 overflow-hidden transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
          rightOpen ? "w-80" : "w-0"
        }`}
      >
        <div
          className={`h-full w-80 transition-[transform,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
            rightOpen
              ? "translate-x-0 opacity-100"
              : "translate-x-4 opacity-0 pointer-events-none"
          }`}
        >
          <InspectorPanel
            phase={phase}
            plan={activePlan}
            subagents={subagents}
            sessionUsage={sessionUsage}
            safetyEnabled={runSettings.safetyEnabled}
            isRunning={isRunning}
            onClose={() => setRightOpen(false)}
          />
        </div>
      </div>

      {/* Settings Modal */}
      <SettingsModal
        open={settingsOpen}
        initialTab={settingsTab}
        providers={providers}
        currentModelMaxTokens={currentModelInfo?.max_output_tokens ?? 65536}
        tools={tools}
        imageSearchStatus={imageSearchStatus}
        settings={runSettings}
        rememberKeys={localMode}
        onChange={setRunSettings}
        onChatGPTAuthChanged={refreshProviders}
        onClose={() => setSettingsOpen(false)}
      />

      {/* Server-side Workspace Browser Modal */}
      {localMode && (
        <WorkspaceBrowserModal
          open={workspaceBrowserOpen}
          currentWorkspace={workspace}
          onSelect={(path) => void applyWorkspace(path)}
          onClose={() => setWorkspaceBrowserOpen(false)}
        />
      )}

      {/* Local Vector Knowledge Base Modal */}
      <KnowledgeBaseModal
        open={knowledgeModalOpen}
        allowCustomEmbedding={localMode}
        knowledgeBases={knowledgeBases}
        selectedIds={selectedKnowledgeBaseIds}
        defaultEmbedding={defaultEmbedding}
        geminiApiKey={runSettings.apiKeys.gemini ?? ""}
        geminiEnvConfigured={geminiEnvConfigured}
        onToggleSelect={handleToggleKnowledgeBase}
        onSelectKb={handleSelectKnowledgeBase}
        onRefreshKnowledgeBases={refreshKnowledgeBases}
        onClose={() => setKnowledgeModalOpen(false)}
      />

      {/* 欢迎页点击星尘行星唤出的高清特写微剧场 */}
      <VendorSpotlightModal
        vendorId={inspectedVendorId}
        matchedProvider={
          inspectedVendorId
            ? (providers.find((p) => p.id === inspectedVendorId) ?? null)
            : null
        }
        isCurrentProvider={Boolean(
          inspectedVendorId && selectedProvider === inspectedVendorId,
        )}
        onSelectProvider={(prov) => {
          void handleModelSelect(prov.id, prov.default_model);
        }}
        onClose={() => setInspectedVendorId(null)}
      />

      {/* 首次进入或主动重播时的全屏 3D 多模型星环开场序幕 */}
      <OpeningConstellationIntro
        open={introOpen}
        onReveal={() => {
          setWelcomeRevealed(true);
          setWelcomeRevealKey((k) => k + 1);
        }}
        onFinish={() => {
          setWelcomeRevealed(true);
          setIntroOpen(false);
        }}
      />
    </div>
  );
}
