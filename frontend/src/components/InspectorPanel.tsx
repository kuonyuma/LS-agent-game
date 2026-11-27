import {
  CheckCircle2,
  Circle,
  Clock,
  GitBranch,
  ListChecks,
  RefreshCcw,
  ShieldCheck,
  ShieldOff,
  SkipForward,
  X,
} from "lucide-react";
import type {
  AgentPhase,
  ExecutionPlan,
  SubagentTrace,
  UsageStats,
} from "../types";

interface InspectorPanelProps {
  phase: AgentPhase;
  plan: ExecutionPlan | null;
  subagents: SubagentTrace[];
  sessionUsage: UsageStats;
  safetyEnabled: boolean;
  isRunning: boolean;
  onClose: () => void;
}

const PHASE_META: Record<AgentPhase, { label: string; dot: string }> = {
  planning: { label: "规划探索 · Planning", dot: "bg-blue-500" },
  executing: { label: "自主执行 · Executing", dot: "bg-emerald-500" },
  reflecting: { label: "故障反思 · Reflecting", dot: "bg-amber-500" },
};

const STRATEGY_LABELS: Record<string, string> = {
  retry: "修正重试",
  replan: "动态重规划",
  verify: "补充验证",
  complete: "验证通过",
};

export function InspectorPanel({
  phase,
  plan,
  subagents,
  sessionUsage,
  safetyEnabled,
  isRunning,
  onClose,
}: InspectorPanelProps) {
  const phaseInfo = PHASE_META[phase];
  const steps = plan?.steps ?? [];
  const completedCount = steps.filter(
    (s) => s.status === "completed" || s.status === "skipped",
  ).length;
  const progress =
    steps.length > 0 ? Math.round((completedCount / steps.length) * 100) : 0;

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-l border-neutral-200/80 bg-[#f9f9f9]">
      <div className="flex h-[52px] items-center justify-between border-b border-neutral-200/70 px-4">
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              isRunning ? "animate-pulse bg-blue-600" : "bg-neutral-300"
            }`}
          />
          <span className="text-[13.5px] font-semibold text-neutral-900">
            运行追踪
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          title="关闭面板"
          className="rounded-lg p-1 text-neutral-400 transition hover:bg-neutral-200/70 hover:text-neutral-800"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {/* Phase & Safety */}
        <section className="space-y-2.5">
          <div className="text-[11px] font-medium text-neutral-400">
            状态机与安全治理
          </div>
          <div className="rounded-2xl border border-neutral-200/80 bg-white p-3.5">
            <div className="flex items-center gap-2 text-xs font-semibold text-neutral-900">
              <span className={`h-2 w-2 rounded-full ${phaseInfo.dot}`} />
              {phaseInfo.label}
            </div>
            <div className="mt-2.5 flex items-center justify-between border-t border-neutral-100 pt-2.5 text-xs">
              <span className="text-neutral-500">异步安全审核</span>
              {safetyEnabled ? (
                <span className="inline-flex items-center gap-1 font-medium text-emerald-700">
                  <ShieldCheck className="h-3.5 w-3.5" /> 开启
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 font-medium text-amber-600">
                  <ShieldOff className="h-3.5 w-3.5" /> 关闭
                </span>
              )}
            </div>
          </div>
        </section>

        {/* Session Usage */}
        <section className="space-y-2">
          <div className="text-[11px] font-medium text-neutral-400">
            累计 Token 用量
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-xl border border-neutral-200/80 bg-white px-2.5 py-2 text-center">
              <div className="text-[10px] text-neutral-400">Input</div>
              <div className="mt-0.5 font-mono text-xs font-semibold text-neutral-900">
                {(sessionUsage.input_len ?? 0).toLocaleString()}
              </div>
            </div>
            <div className="rounded-xl border border-neutral-200/80 bg-white px-2.5 py-2 text-center">
              <div className="text-[10px] text-neutral-400">Output</div>
              <div className="mt-0.5 font-mono text-xs font-semibold text-neutral-900">
                {(sessionUsage.output_len ?? 0).toLocaleString()}
              </div>
            </div>
            <div className="rounded-xl border border-neutral-200/80 bg-white px-2.5 py-2 text-center">
              <div className="text-[10px] text-emerald-600">Cached</div>
              <div className="mt-0.5 font-mono text-xs font-semibold text-emerald-700">
                {(sessionUsage.cached_len ?? 0).toLocaleString()}
              </div>
            </div>
          </div>
        </section>

        {/* Execution Plan */}
        <section className="space-y-2">
          <div className="flex items-center justify-between text-[11px] font-medium text-neutral-400">
            <span className="inline-flex items-center gap-1">
              <ListChecks className="h-3.5 w-3.5" />
              执行计划（ExecutionPlan）
            </span>
            {plan && steps.length > 0 && (
              <span className="font-mono text-neutral-500">
                {completedCount}/{steps.length}
              </span>
            )}
          </div>

          <div className="rounded-2xl border border-neutral-200/80 bg-white p-3.5">
            {!plan || steps.length === 0 ? (
              <p className="text-xs leading-relaxed text-neutral-400">
                当任务需要多步执行时，内核自动生成的计划与步骤状态将在此实时同步。
              </p>
            ) : (
              <div className="space-y-3">
                <div className="text-xs font-medium text-neutral-800">
                  {plan.goal}
                </div>
                <div className="h-1 w-full overflow-hidden rounded-full bg-neutral-100">
                  <div
                    className="h-full rounded-full bg-neutral-900 transition-all duration-300"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <ul className="space-y-2">
                  {steps.map((step) => (
                    <li
                      key={step.id}
                      className="flex items-start gap-2 text-xs"
                    >
                      {step.status === "completed" ? (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                      ) : step.status === "in_progress" ? (
                        <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-pulse text-blue-600" />
                      ) : step.status === "skipped" ? (
                        <SkipForward className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neutral-400" />
                      ) : (
                        <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-neutral-300" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div
                          className={
                            step.status === "completed"
                              ? "text-neutral-400 line-through"
                              : "font-medium text-neutral-800"
                          }
                        >
                          {step.title}
                        </div>
                        {step.notes && (
                          <div className="mt-0.5 text-[11px] text-neutral-500">
                            {step.notes}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>

                {plan.unverified_edits.length > 0 && (
                  <div className="rounded-xl bg-amber-50 px-2.5 py-2 text-[11px] text-amber-800">
                    待验证修改：{plan.unverified_edits.join(", ")}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Reflections */}
        {plan && plan.reflections.length > 0 && (
          <section className="space-y-2">
            <div className="flex items-center gap-1 text-[11px] font-medium text-neutral-400">
              <RefreshCcw className="h-3.5 w-3.5" />
              反思记录（{plan.reflections.length}）
            </div>
            <div className="space-y-2">
              {plan.reflections.map((ref) => (
                <div
                  key={ref.id}
                  className="rounded-2xl border border-neutral-200/80 bg-white p-3 text-xs"
                >
                  <div className="flex items-center justify-between font-semibold text-neutral-800">
                    <span className="font-mono text-[11px]">{ref.id}</span>
                    <span className="text-[11px] text-amber-700">
                      {STRATEGY_LABELS[ref.strategy] ?? ref.strategy}
                    </span>
                  </div>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-neutral-600">
                    {ref.diagnosis}
                  </p>
                  <p className="mt-1 text-[11px] text-neutral-400">
                    下一步：{ref.next_action}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Subagents */}
        <section className="space-y-2">
          <div className="flex items-center justify-between text-[11px] font-medium text-neutral-400">
            <span className="inline-flex items-center gap-1">
              <GitBranch className="h-3.5 w-3.5" />
              子代理协同
            </span>
            <span className="font-mono">{subagents.length}</span>
          </div>
          <div className="rounded-2xl border border-neutral-200/80 bg-white p-3.5">
            {subagents.length === 0 ? (
              <p className="text-xs leading-relaxed text-neutral-400">
                当任务需要并发探索或分工协作时，主代理会自主调用{" "}
                <code className="font-mono text-neutral-600">subagent</code>{" "}
                工具派发 Explorer / Worker / Monitor 子代理并在此展示状态。
              </p>
            ) : (
              <div className="space-y-2.5">
                {subagents.map((sa) => (
                  <div
                    key={sa.call_id}
                    className="border-b border-neutral-100 pb-2 last:border-none last:pb-0"
                  >
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono font-semibold text-neutral-800">
                        {sa.role}
                        {sa.agent_id ? ` · ${sa.agent_id}` : ""}
                      </span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          sa.status === "completed"
                            ? "bg-emerald-50 text-emerald-700"
                            : sa.status === "failed"
                              ? "bg-rose-50 text-rose-700"
                              : "bg-blue-50 text-blue-700"
                        }`}
                      >
                        {sa.status}
                      </span>
                    </div>
                    {sa.description && (
                      <div className="mt-1 text-[11.5px] text-neutral-500">
                        {sa.description}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}
