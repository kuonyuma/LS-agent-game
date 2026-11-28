import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { effortColor, resolveEffortAppearance } from "../effortAppearance";
import { useTheme } from "../theme";
import type { ReasoningEffort } from "../types";

interface EffortMeta {
  key: ReasoningEffort;
  shortLabel: string;
  title: string;
  subtitle: string;
  starCount: number;
}

const EFFORT_META: Record<ReasoningEffort, EffortMeta> = {
  off: {
    key: "off",
    shortLabel: "Off",
    title: "Off",
    subtitle: "关闭思考链，最快直出",
    starCount: 0,
  },
  low: {
    key: "low",
    shortLabel: "Low",
    title: "Low",
    subtitle: "轻量推理，低延迟快速响应",
    starCount: 5,
  },
  medium: {
    key: "medium",
    shortLabel: "Med",
    title: "Medium",
    subtitle: "均衡推理，兼顾代码速度与准确度",
    starCount: 8,
  },
  high: {
    key: "high",
    shortLabel: "High",
    title: "High",
    subtitle: "深度推理，推荐复杂工程与多步任务",
    starCount: 11,
  },
  xhigh: {
    key: "xhigh",
    shortLabel: "XHigh",
    title: "XHigh",
    subtitle: "超深推理，攻克高难度架构与疑难排错",
    starCount: 15,
  },
  max: {
    key: "max",
    shortLabel: "Max",
    title: "Max",
    subtitle: "极限推理，释放最大思考深度",
    starCount: 16,
  },
};

const STAR_PARTICLES = [
  { left: "9%", top: "30%", size: 2, delay: "0s", dur: "1.9s" },
  { left: "16%", top: "68%", size: 2.5, delay: "0.35s", dur: "2.3s" },
  { left: "24%", top: "36%", size: 2, delay: "0.8s", dur: "2.0s" },
  { left: "31%", top: "70%", size: 3, delay: "0.2s", dur: "2.5s" },
  { left: "38%", top: "28%", size: 2, delay: "1.1s", dur: "1.8s" },
  { left: "45%", top: "62%", size: 3, delay: "0.5s", dur: "2.2s" },
  { left: "52%", top: "32%", size: 2.5, delay: "1.3s", dur: "2.1s" },
  { left: "59%", top: "72%", size: 2, delay: "0.4s", dur: "2.4s" },
  { left: "66%", top: "30%", size: 3, delay: "0.9s", dur: "1.9s" },
  { left: "72%", top: "64%", size: 2.5, delay: "1.4s", dur: "2.2s" },
  { left: "79%", top: "38%", size: 3.5, delay: "0.6s", dur: "1.7s" },
  { left: "85%", top: "68%", size: 2, delay: "1.0s", dur: "2.3s" },
  { left: "20%", top: "52%", size: 2, delay: "1.5s", dur: "2.6s" },
  { left: "49%", top: "48%", size: 2.5, delay: "0.7s", dur: "2.0s" },
  { left: "63%", top: "50%", size: 2, delay: "1.2s", dur: "1.8s" },
  { left: "89%", top: "40%", size: 2.5, delay: "0.15s", dur: "2.1s" },
];

interface EffortSliderInlineProps {
  levels: ReasoningEffort[];
  value: ReasoningEffort;
  disabled?: boolean;
  onChange: (level: ReasoningEffort) => void;
  onPreview?: (ratio: number | null) => void;
  entryLevel?: ReasoningEffort;
  onEntry?: () => void;
}

interface EffortDrag {
  pointerId: number;
  index: number;
  playedEntry: boolean;
}

export function EffortSliderInline({
  levels,
  value,
  disabled = false,
  onChange,
  onPreview,
  entryLevel,
  onEntry,
}: EffortSliderInlineProps) {
  const { isDark } = useTheme();
  const [dragRatio, setDragRatio] = useState<number | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<EffortDrag | null>(null);

  const availableLevels = useMemo<ReasoningEffort[]>(() => {
    const valid = levels.filter((lv): lv is ReasoningEffort => lv in EFFORT_META);
    return valid.length > 0 ? valid : ["low", "medium", "high"];
  }, [levels]);

  const maxStep = availableLevels.length - 1;
  const selectedIdx = availableLevels.indexOf(value);
  const currentIdx = selectedIdx >= 0 ? selectedIdx : Math.min(maxStep, 1);
  const committedRatio = maxStep > 0 ? currentIdx / maxStep : 0;
  const activeRatio = dragRatio ?? committedRatio;
  const previewIdx = Math.round(activeRatio * maxStep);
  const appearance = resolveEffortAppearance(availableLevels, value, dragRatio, isDark);
  const previewMeta = EFFORT_META[appearance.level];
  const baseColor = effortColor(appearance.base);
  const accentColor = effortColor(appearance.accent);

  const commitIndex = useCallback(
    (idx: number) => {
      const clamped = Math.max(0, Math.min(idx, maxStep));
      onChange(availableLevels[clamped]);
    },
    [availableLevels, maxStep, onChange],
  );

  const ratioFromClientX = useCallback((clientX: number): number => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return 0;
    const pad = 13;
    const usable = Math.max(1, rect.width - pad * 2);
    const raw = (clientX - rect.left - pad) / usable;
    return Math.max(0, Math.min(1, raw));
  }, []);

  const updatePreview = (ratio: number | null) => {
    setDragRatio(ratio);
    onPreview?.(ratio);
  };

  const previewDrag = (gesture: EffortDrag, ratio: number) => {
    const index = Math.round(ratio * maxStep);
    if (index !== gesture.index && !gesture.playedEntry && availableLevels[index] === entryLevel) {
      gesture.playedEntry = true;
      onEntry?.();
    }
    gesture.index = index;
    updatePreview(ratio);
  };

  const handlePointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled || maxStep === 0 || e.button !== 0 || drag.current) return;
    const gesture = { pointerId: e.pointerId, index: currentIdx, playedEntry: false };
    drag.current = gesture;
    e.currentTarget.setPointerCapture(e.pointerId);
    previewDrag(gesture, ratioFromClientX(e.clientX));
  };

  const handlePointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (!gesture || gesture.pointerId !== e.pointerId || disabled) return;
    previewDrag(gesture, ratioFromClientX(e.clientX));
  };

  const handlePointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    drag.current = null;
    if (!disabled) {
      previewDrag(gesture, ratioFromClientX(e.clientX));
      commitIndex(gesture.index);
    }
    updatePreview(null);
  };

  const handlePointerCancel = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== e.pointerId) return;
    drag.current = null;
    updatePreview(null);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (disabled || drag.current) return;
    let nextIndex = currentIdx;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") {
      nextIndex = Math.min(currentIdx + 1, maxStep);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
      nextIndex = Math.max(currentIdx - 1, 0);
    } else {
      return;
    }
    e.preventDefault();
    if (nextIndex === currentIdx) return;
    if (availableLevels[nextIndex] === entryLevel) onEntry?.();
    commitIndex(nextIndex);
  };

  const thumbLeftStyle = `calc(13px + (100% - 26px) * ${activeRatio})`;
  const fillWidthStyle = `calc(26px + (100% - 26px) * ${activeRatio})`;
  const trackWidthClass =
    availableLevels.length >= 5
      ? "w-[196px] sm:w-[228px]"
      : availableLevels.length >= 4
        ? "w-[178px] sm:w-[206px]"
        : "w-[144px] sm:w-[168px]";

  return (
    <div
      title={`${previewMeta.title}：${previewMeta.subtitle}`}
      className={`inline-flex min-w-[82px] items-center gap-1.5 py-0.5 pl-1 pr-1.5 transition-opacity sm:gap-2 ${
        disabled ? "opacity-50" : ""
      }`}
    >
      <span className="w-8 shrink-0 select-none">{previewMeta.shortLabel}</span>

      {/* Inline draggable neon-aurora stardust track */}
      <div className="flex min-w-8 flex-col items-stretch">
        <div
          ref={trackRef}
          role="slider"
          tabIndex={disabled ? -1 : 0}
          aria-label="思考强度"
          aria-valuemin={0}
          aria-valuemax={maxStep}
          aria-valuenow={previewIdx}
          aria-valuetext={previewMeta.title}
          aria-disabled={disabled}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          onLostPointerCapture={handlePointerCancel}
          onKeyDown={handleKeyDown}
          className={`relative h-6 ${trackWidthClass} max-w-full cursor-pointer select-none touch-none rounded-full bg-neutral-200/75 ring-1 ring-inset ring-black/6 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 dark:bg-white/[0.09] dark:ring-white/[0.11]`}
        >
          {/* Animated flowing gradient energy bar + shimmer + stardust */}
          <div
            className={`effort-gradient-flow pointer-events-none absolute left-0 top-0 h-full overflow-hidden rounded-full ${
              dragRatio === null
                ? "transition-[width,box-shadow] duration-250 ease-[cubic-bezier(0.22,1,0.36,1)]"
                : ""
            }`}
            style={{
              width: fillWidthStyle,
              backgroundImage: `linear-gradient(90deg, ${baseColor}, ${accentColor})`,
              boxShadow: previewMeta.key === "off"
                ? "none"
                : `0 0 ${10 + appearance.trail * 7}px ${effortColor(appearance.accent, isDark ? 0.44 : 0.34)}`,
            }}
          >
            {previewMeta.key !== "off" && (
              <span className="effort-shimmer-sweep pointer-events-none absolute inset-0" />
            )}
            {STAR_PARTICLES.slice(0, previewMeta.starCount).map((star, idx) => (
              <span
                key={idx}
                className="effort-star absolute rounded-full bg-white shadow-[0_0_5px_rgba(255,255,255,0.95)]"
                style={
                  {
                    left: star.left,
                    top: star.top,
                    width: `${star.size}px`,
                    height: `${star.size}px`,
                    "--delay": star.delay,
                    "--dur": star.dur,
                  } as CSSProperties
                }
              />
            ))}
          </div>

          <div className="pointer-events-none absolute inset-0 flex items-center justify-between px-[13px]">
            {availableLevels.map((level, idx) => {
              const filled = idx <= previewIdx;
              const isCurrent = idx === previewIdx;
              return (
                <span
                  key={level}
                  className={`h-1.5 w-1.5 rounded-full transition-all duration-200 ${
                    isCurrent
                      ? "scale-0 opacity-0"
                      : filled
                        ? "bg-white/75"
                        : "bg-neutral-400/75 dark:bg-white/30"
                  }`}
                />
              );
            })}
          </div>

          {/* Glowing white magnetic thumb */}
          <div
            className={`pointer-events-none absolute top-1/2 flex h-[18px] w-[18px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white shadow-[0_2px_8px_rgba(0,0,0,0.24)] ring-1 ring-black/8 ${
              dragRatio === null
                ? "transition-[left,transform] duration-250 ease-[cubic-bezier(0.22,1,0.36,1)]"
                : "scale-110"
            }`}
            style={{ left: thumbLeftStyle }}
          >
            <span
              className="h-2 w-2 rounded-full transition-colors duration-200"
              style={{ backgroundColor: baseColor }}
            />
          </div>
        </div>
      </div>

    </div>
  );
}
