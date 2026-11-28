import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Globe,
  Images,
  TrendingDown,
  TrendingUp,
  X,
  ZoomIn,
} from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import type { UIMessage } from "../types";

/* ============================================================================
   1. 主题色板（同时适配浅色极简与 Astra Obsidian 暗夜模式）
   ============================================================================ */

type ChartTone = "blue" | "rose" | "emerald" | "amber" | "violet" | "slate";

interface TonePalette {
  bar: string;
  barDark: string;
  stroke: string;
  strokeDark: string;
  softBg: string;
  text: string;
}

const TONE_PALETTES: Record<ChartTone, TonePalette> = {
  blue: {
    bar: "#4c84c4",
    barDark: "#69a2e6",
    stroke: "#3b76bc",
    strokeDark: "#7ab3f5",
    softBg: "bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300",
    text: "text-blue-700 dark:text-blue-300",
  },
  rose: {
    bar: "#b85d4b",
    barDark: "#d87a67",
    stroke: "#b85d4b",
    strokeDark: "#e08775",
    softBg: "bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    text: "text-rose-700 dark:text-rose-300",
  },
  emerald: {
    bar: "#2f9468",
    barDark: "#45b884",
    stroke: "#25855a",
    strokeDark: "#53c994",
    softBg: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    text: "text-emerald-700 dark:text-emerald-300",
  },
  amber: {
    bar: "#c97b24",
    barDark: "#e69c45",
    stroke: "#c27018",
    strokeDark: "#f0ac59",
    softBg: "bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
    text: "text-amber-800 dark:text-amber-300",
  },
  violet: {
    bar: "#7358c9",
    barDark: "#967ef0",
    stroke: "#684cc2",
    strokeDark: "#a692f5",
    softBg: "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
    text: "text-violet-700 dark:text-violet-300",
  },
  slate: {
    bar: "#52525b",
    barDark: "#94a3b8",
    stroke: "#475569",
    strokeDark: "#a3b3c9",
    softBg: "bg-neutral-100 text-neutral-700 dark:bg-white/[0.08] dark:text-[#C5C9DE]",
    text: "text-neutral-700 dark:text-[#C5C9DE]",
  },
};

const SERIES_TONES: ChartTone[] = ["blue", "rose", "emerald", "amber", "violet", "slate"];

function resolveTone(raw: unknown, fallback: ChartTone): ChartTone {
  if (typeof raw === "string") {
    const key = raw.trim().toLowerCase() as ChartTone;
    if (key in TONE_PALETTES) return key;
  }
  return fallback;
}

/* ============================================================================
   2. Chart Schema 解析与校验
   ============================================================================ */

interface ChartDatum {
  label: string;
  value: number;
  display?: string;
  badge?: string;
  tone?: ChartTone;
  delta?: string;
  subtext?: string;
}

interface ChartSeries {
  name: string;
  tone: ChartTone;
  items: ChartDatum[];
}

interface ParsedChartSpec {
  type: "horizontal_bar" | "comparison_bar" | "bar" | "line" | "stat_grid";
  title: string;
  subtitle?: string;
  unit?: string;
  max?: number;
  tone: ChartTone;
  source?: string;
  note?: string;
  items: ChartDatum[];
  series?: ChartSeries[];
}

function toFiniteNumber(raw: unknown): number | null {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const cleaned = raw.replace(/[,%\s]/g, "");
    if (!cleaned) return null;
    const n = Number(cleaned);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function parseDatum(raw: unknown): ChartDatum | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const label = typeof obj.label === "string" ? obj.label.trim() : "";
  if (!label) return null;
  const value = toFiniteNumber(obj.value);
  const display =
    typeof obj.display === "string" && obj.display.trim()
      ? obj.display.trim()
      : undefined;
  if (value === null && !display) return null;
  return {
    label,
    value: value ?? 0,
    display,
    badge:
      typeof obj.badge === "string" && obj.badge.trim()
        ? obj.badge.trim().slice(0, 10)
        : undefined,
    tone:
      typeof obj.tone === "string" && obj.tone.trim().toLowerCase() in TONE_PALETTES
        ? (obj.tone.trim().toLowerCase() as ChartTone)
        : typeof obj.color === "string" && obj.color.trim().toLowerCase() in TONE_PALETTES
          ? (obj.color.trim().toLowerCase() as ChartTone)
          : undefined,
    delta:
      typeof obj.delta === "string" && obj.delta.trim()
        ? obj.delta.trim().slice(0, 24)
        : undefined,
    subtext:
      typeof obj.subtext === "string" && obj.subtext.trim()
        ? obj.subtext.trim().slice(0, 80)
        : undefined,
  };
}

function parseChartSpec(raw: string): ParsedChartSpec | null {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("{")) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const obj = parsed as Record<string, unknown>;

  const rawType = typeof obj.type === "string" ? obj.type.trim().toLowerCase() : "horizontal_bar";
  const typeMap: Record<string, ParsedChartSpec["type"]> = {
    horizontal_bar: "horizontal_bar",
    hbar: "horizontal_bar",
    progress: "horizontal_bar",
    ranking: "horizontal_bar",
    comparison_bar: "comparison_bar",
    compare: "comparison_bar",
    comparison: "comparison_bar",
    bar: "bar",
    column: "bar",
    vertical_bar: "bar",
    line: "line",
    area: "line",
    trend: "line",
    stat_grid: "stat_grid",
    kpi: "stat_grid",
    stats: "stat_grid",
  };
  const type = typeMap[rawType] ?? "horizontal_bar";
  const defaultTone: ChartTone = type === "comparison_bar" ? "rose" : "blue";
  const tone = resolveTone(obj.tone ?? obj.color, defaultTone);

  const rawItems = Array.isArray(obj.items)
    ? obj.items
    : Array.isArray(obj.data)
      ? obj.data
      : [];
  const items = rawItems.map(parseDatum).filter((item): item is ChartDatum => item !== null);

  let series: ChartSeries[] | undefined;
  if (Array.isArray(obj.series)) {
    const parsedSeries: ChartSeries[] = [];
    obj.series.forEach((entry, idx) => {
      if (!entry || typeof entry !== "object") return;
      const sObj = entry as Record<string, unknown>;
      const sItemsRaw = Array.isArray(sObj.items)
        ? sObj.items
        : Array.isArray(sObj.data)
          ? sObj.data
          : [];
      const sItems = sItemsRaw
        .map(parseDatum)
        .filter((item): item is ChartDatum => item !== null);
      if (sItems.length > 0) {
        parsedSeries.push({
          name:
            typeof sObj.name === "string" && sObj.name.trim()
              ? sObj.name.trim()
              : `系列 ${idx + 1}`,
          tone: resolveTone(sObj.tone ?? sObj.color, SERIES_TONES[idx % SERIES_TONES.length]),
          items: sItems,
        });
      }
    });
    if (parsedSeries.length > 0) {
      series = parsedSeries;
    }
  }

  if (items.length === 0 && (!series || series.length === 0)) {
    return null;
  }

  const maxVal = toFiniteNumber(obj.max);
  return {
    type,
    title: typeof obj.title === "string" ? obj.title.trim() : "",
    subtitle: typeof obj.subtitle === "string" ? obj.subtitle.trim() : undefined,
    unit: typeof obj.unit === "string" ? obj.unit.trim() : undefined,
    max: maxVal !== null && maxVal > 0 ? maxVal : undefined,
    tone,
    source: typeof obj.source === "string" ? obj.source.trim() : undefined,
    note: typeof obj.note === "string" ? obj.note.trim() : undefined,
    items,
    series,
  };
}

function formatDatumValue(datum: ChartDatum, unit?: string): string {
  if (datum.display) return datum.display;
  const formatted = Number.isInteger(datum.value)
    ? datum.value.toLocaleString()
    : Number(datum.value.toFixed(2)).toLocaleString();
  if (!unit) return formatted;
  return unit === "%" ? `${formatted}%` : `${formatted} ${unit}`;
}

function computeNiceScaleMax(values: number[], explicitMax?: number, unit?: string): number {
  const peak = Math.max(...values.map((v) => Math.max(0, v)), 0);
  if (explicitMax && explicitMax >= peak) return explicitMax;
  if (peak <= 0) return 100;
  if (unit === "%" && peak <= 100) {
    if (peak <= 25) return Math.ceil(peak / 5) * 5 + 5;
    if (peak <= 50) return Math.min(100, Math.ceil(peak / 10) * 10 + 10);
    return 100;
  }
  return peak * 1.12;
}

/* ============================================================================
   3. 五种图表渲染器
   ============================================================================ */

function ChartHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  if (!title && !subtitle) return null;
  return (
    <div className="mb-4">
      {title && (
        <h4 className="m-0 text-[15px] font-semibold tracking-tight text-neutral-900 dark:text-[#F3F4FA]">
          {title}
        </h4>
      )}
      {subtitle && (
        <p className="mt-0.5 mb-0 text-xs text-neutral-500 dark:text-[#8E94B0]">
          {subtitle}
        </p>
      )}
    </div>
  );
}

function ChartFooter({ source, note }: { source?: string; note?: string }) {
  if (!source && !note) return null;
  return (
    <div className="mt-4 space-y-1 border-t border-neutral-100 pt-2.5 text-[11.5px] leading-relaxed text-neutral-500 dark:border-white/[0.07] dark:text-[#858BA8]">
      {note && <p className="m-0">{note}</p>}
      {source && <p className="m-0">{source}</p>}
    </div>
  );
}

function HorizontalBarChart({
  spec,
  isDark,
}: {
  spec: ParsedChartSpec;
  isDark: boolean;
}) {
  const maxVal = computeNiceScaleMax(
    spec.items.map((i) => i.value),
    spec.max,
    spec.unit,
  );
  return (
    <div className="space-y-3.5">
      {spec.items.map((item, idx) => {
        const ratio = Math.max(0, Math.min(100, (item.value / maxVal) * 100));
        const widthPct = item.value > 0 ? Math.max(1.5, ratio) : 0;
        const palette = TONE_PALETTES[item.tone ?? spec.tone];
        return (
          <div key={`${item.label}-${idx}`}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3 text-[13.5px]">
              <div className="flex min-w-0 items-baseline gap-2">
                {item.badge && (
                  <span className="shrink-0 font-mono text-[11px] font-semibold tracking-wide text-neutral-400 dark:text-[#7E84A2]">
                    {item.badge}
                  </span>
                )}
                <span className="truncate font-medium text-neutral-800 dark:text-[#E5E8F5]">
                  {item.label}
                </span>
              </div>
              <div className="flex shrink-0 items-baseline gap-2">
                <span className="font-semibold tabular-nums text-neutral-900 dark:text-[#F3F4FA]">
                  {formatDatumValue(item, spec.unit)}
                </span>
                {item.delta && (
                  <span className="rounded-full bg-neutral-100 px-1.5 py-0.5 text-[10.5px] font-medium text-neutral-600 dark:bg-white/[0.08] dark:text-[#B9BED6]">
                    {item.delta}
                  </span>
                )}
              </div>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-neutral-100 dark:bg-white/[0.07]">
              <div
                className="h-full rounded-full transition-[width] duration-500 ease-out"
                style={{
                  width: `${widthPct}%`,
                  backgroundColor: isDark ? palette.barDark : palette.bar,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ComparisonBarChart({
  spec,
  isDark,
}: {
  spec: ParsedChartSpec;
  isDark: boolean;
}) {
  const maxVal = computeNiceScaleMax(
    spec.items.map((i) => i.value),
    spec.max,
    spec.unit,
  );
  return (
    <div className="space-y-2.5">
      {spec.items.map((item, idx) => {
        const ratio = Math.max(0, Math.min(100, (item.value / maxVal) * 100));
        const palette = TONE_PALETTES[item.tone ?? spec.tone];
        return (
          <div
            key={`${item.label}-${idx}`}
            className="grid grid-cols-[minmax(3.8rem,auto)_1fr_minmax(4.2rem,auto)] items-center gap-3"
          >
            <span className="truncate text-[13px] font-medium text-neutral-600 dark:text-[#9CA2BC]">
              {item.label}
            </span>
            <div className="h-[18px] w-full overflow-hidden rounded-[6px] bg-neutral-100 dark:bg-white/[0.07]">
              {item.value > 0 && (
                <div
                  className="h-full rounded-[6px] transition-[width] duration-500 ease-out"
                  style={{
                    width: `${ratio}%`,
                    minWidth: "3px",
                    backgroundColor: isDark ? palette.barDark : palette.bar,
                  }}
                />
              )}
            </div>
            <span className="text-right text-[13.5px] font-semibold tabular-nums text-neutral-900 dark:text-[#F3F4FA]">
              {formatDatumValue(item, spec.unit)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function VerticalBarChart({
  spec,
  isDark,
}: {
  spec: ParsedChartSpec;
  isDark: boolean;
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const items = spec.items.slice(0, 12);
  const maxVal = computeNiceScaleMax(
    items.map((i) => i.value),
    spec.max,
    spec.unit,
  );

  const width = 560;
  const height = 210;
  const padTop = 26;
  const padBottom = 38;
  const padLeft = 14;
  const padRight = 14;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;
  const slotW = plotW / Math.max(1, items.length);
  const barW = Math.min(44, Math.max(14, slotW * 0.54));

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full select-none overflow-visible"
        role="img"
        aria-label={spec.title || "柱状图"}
      >
        {[0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padTop + plotH * (1 - tick);
          return (
            <line
              key={tick}
              x1={padLeft}
              x2={width - padRight}
              y1={y}
              y2={y}
              stroke={isDark ? "rgba(255,255,255,0.08)" : "rgba(15,23,42,0.08)"}
              strokeDasharray="3 4"
            />
          );
        })}
        <line
          x1={padLeft}
          x2={width - padRight}
          y1={padTop + plotH}
          y2={padTop + plotH}
          stroke={isDark ? "rgba(255,255,255,0.16)" : "rgba(15,23,42,0.15)"}
        />

        {items.map((item, idx) => {
          const barH = Math.max(2, (Math.max(0, item.value) / maxVal) * plotH);
          const cx = padLeft + slotW * idx + slotW / 2;
          const x = cx - barW / 2;
          const y = padTop + plotH - barH;
          const palette = TONE_PALETTES[item.tone ?? spec.tone];
          const active = hoveredIdx === null || hoveredIdx === idx;
          const shortLabel =
            item.label.length > 8 ? `${item.label.slice(0, 7)}…` : item.label;

          return (
            <g
              key={`${item.label}-${idx}`}
              onMouseEnter={() => setHoveredIdx(idx)}
              onMouseLeave={() => setHoveredIdx(null)}
              className="cursor-default"
            >
              <rect
                x={x}
                y={y}
                width={barW}
                height={barH}
                rx={6}
                fill={isDark ? palette.barDark : palette.bar}
                opacity={active ? 1 : 0.42}
                className="transition-opacity duration-150"
              />
              <text
                x={cx}
                y={y - 7}
                textAnchor="middle"
                fontSize={11}
                fontWeight={600}
                fill={isDark ? "#E5E8F5" : "#171717"}
                opacity={hoveredIdx === idx ? 1 : 0.85}
              >
                {formatDatumValue(item, spec.unit)}
              </text>
              <text
                x={cx}
                y={padTop + plotH + 20}
                textAnchor="middle"
                fontSize={11}
                fill={isDark ? "#9CA2BC" : "#525252"}
              >
                {shortLabel}
              </text>
            </g>
          );
        })}
      </svg>
      {hoveredIdx !== null && items[hoveredIdx] && (
        <div className="mt-1 flex items-center justify-between rounded-lg bg-neutral-50 px-3 py-1.5 text-xs text-neutral-700 dark:bg-white/[0.05] dark:text-[#D5D9EA]">
          <span className="font-medium">{items[hoveredIdx].label}</span>
          <span className="font-semibold tabular-nums">
            {formatDatumValue(items[hoveredIdx], spec.unit)}
          </span>
        </div>
      )}
    </div>
  );
}

function TrendLineChart({
  spec,
  isDark,
}: {
  spec: ParsedChartSpec;
  isDark: boolean;
}) {
  const seriesList: ChartSeries[] = useMemo(() => {
    if (spec.series && spec.series.length > 0) return spec.series;
    return [{ name: spec.title || "数值", tone: spec.tone, items: spec.items }];
  }, [spec]);

  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const allValues = seriesList.flatMap((s) => s.items.map((i) => i.value));
  const minRaw = Math.min(...allValues, 0);
  const minVal = minRaw < 0 ? minRaw * 1.1 : 0;
  const maxVal = computeNiceScaleMax(allValues, spec.max, spec.unit);
  const span = Math.max(1e-6, maxVal - minVal);
  const primaryLabels = seriesList[0]?.items.map((i) => i.label) ?? [];
  const count = Math.max(2, primaryLabels.length);

  const width = 560;
  const height = 215;
  const padTop = 20;
  const padBottom = 34;
  const padLeft = 44;
  const padRight = 18;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const getX = (idx: number, total: number) =>
    total <= 1 ? padLeft + plotW / 2 : padLeft + (idx / (total - 1)) * plotW;
  const getY = (val: number) => padTop + plotH - ((val - minVal) / span) * plotH;

  const yTicks = [0, 0.5, 1].map((r) => minVal + span * r);
  const gradId = `line-area-${useId().replace(/:/g, "")}`;

  return (
    <div>
      {seriesList.length > 1 && (
        <div className="mb-2.5 flex flex-wrap items-center gap-3 text-xs text-neutral-600 dark:text-[#AAB0CD]">
          {seriesList.map((s) => {
            const p = TONE_PALETTES[s.tone];
            return (
              <span key={s.name} className="inline-flex items-center gap-1.5">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: isDark ? p.strokeDark : p.stroke }}
                />
                {s.name}
              </span>
            );
          })}
        </div>
      )}

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full select-none overflow-visible"
        role="img"
        aria-label={spec.title || "趋势折线图"}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop
              offset="0%"
              stopColor={
                isDark
                  ? TONE_PALETTES[seriesList[0].tone].strokeDark
                  : TONE_PALETTES[seriesList[0].tone].stroke
              }
              stopOpacity={isDark ? 0.24 : 0.16}
            />
            <stop
              offset="100%"
              stopColor={
                isDark
                  ? TONE_PALETTES[seriesList[0].tone].strokeDark
                  : TONE_PALETTES[seriesList[0].tone].stroke
              }
              stopOpacity={0}
            />
          </linearGradient>
        </defs>

        {yTicks.map((tick, idx) => {
          const y = getY(tick);
          const label = Number.isInteger(tick) ? String(tick) : tick.toFixed(1);
          return (
            <g key={idx}>
              <line
                x1={padLeft}
                x2={width - padRight}
                y1={y}
                y2={y}
                stroke={isDark ? "rgba(255,255,255,0.08)" : "rgba(15,23,42,0.08)"}
                strokeDasharray={idx === 0 ? undefined : "3 4"}
              />
              <text
                x={padLeft - 8}
                y={y + 4}
                textAnchor="end"
                fontSize={10.5}
                fill={isDark ? "#7F85A3" : "#737373"}
              >
                {spec.unit === "%" ? `${label}%` : label}
              </text>
            </g>
          );
        })}

        {seriesList.length === 1 && seriesList[0].items.length >= 2 && (() => {
          const pts = seriesList[0].items.map((item, idx) => ({
            x: getX(idx, seriesList[0].items.length),
            y: getY(item.value),
          }));
          const areaPath = [
            `M ${pts[0].x} ${padTop + plotH}`,
            ...pts.map((pt) => `L ${pt.x} ${pt.y}`),
            `L ${pts[pts.length - 1].x} ${padTop + plotH}`,
            "Z",
          ].join(" ");
          return <path d={areaPath} fill={`url(#${gradId})`} />;
        })()}

        {seriesList.map((s) => {
          const p = TONE_PALETTES[s.tone];
          const color = isDark ? p.strokeDark : p.stroke;
          const points = s.items
            .map((item, idx) => `${getX(idx, s.items.length)},${getY(item.value)}`)
            .join(" ");
          return (
            <g key={s.name}>
              <polyline
                fill="none"
                stroke={color}
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
                points={points}
              />
              {s.items.map((item, idx) => {
                const cx = getX(idx, s.items.length);
                const cy = getY(item.value);
                const isSelected = activeIdx === idx;
                return (
                  <g
                    key={`${s.name}-${idx}`}
                    onMouseEnter={() => setActiveIdx(idx)}
                    onMouseLeave={() => setActiveIdx(null)}
                  >
                    <circle cx={cx} cy={cy} r={12} fill="transparent" />
                    <circle
                      cx={cx}
                      cy={cy}
                      r={isSelected ? 4.8 : 3.2}
                      fill={isDark ? "#121420" : "#ffffff"}
                      stroke={color}
                      strokeWidth={2.2}
                    />
                  </g>
                );
              })}
            </g>
          );
        })}

        {primaryLabels.map((lbl, idx) => {
          const step = count > 8 ? Math.ceil(count / 6) : 1;
          if (idx % step !== 0 && idx !== primaryLabels.length - 1) return null;
          const x = getX(idx, primaryLabels.length);
          const shortLbl = lbl.length > 9 ? `${lbl.slice(0, 8)}…` : lbl;
          return (
            <text
              key={`${lbl}-${idx}`}
              x={x}
              y={padTop + plotH + 20}
              textAnchor="middle"
              fontSize={10.5}
              fill={isDark ? "#9CA2BC" : "#525252"}
            >
              {shortLbl}
            </text>
          );
        })}
      </svg>

      {activeIdx !== null && primaryLabels[activeIdx] && (
        <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-neutral-50 px-3 py-1.5 text-xs text-neutral-700 dark:bg-white/[0.05] dark:text-[#D5D9EA]">
          <span className="font-medium">{primaryLabels[activeIdx]}</span>
          <div className="flex flex-wrap items-center gap-3 tabular-nums">
            {seriesList.map((s) => {
              const item = s.items[activeIdx];
              if (!item) return null;
              return (
                <span key={s.name} className="font-semibold">
                  {seriesList.length > 1 ? `${s.name}: ` : ""}
                  {formatDatumValue(item, spec.unit)}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function StatGridCard({ spec }: { spec: ParsedChartSpec }) {
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
      {spec.items.map((item, idx) => {
        const deltaNegative = item.delta?.startsWith("-");
        const deltaPositive = item.delta?.startsWith("+");
        return (
          <div
            key={`${item.label}-${idx}`}
            className="rounded-xl border border-neutral-200/80 bg-neutral-50/70 p-3 dark:border-white/[0.08] dark:bg-white/[0.03]"
          >
            <div className="text-[11.5px] font-medium text-neutral-500 dark:text-[#8E94B0]">
              {item.label}
            </div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="text-lg font-semibold tracking-tight tabular-nums text-neutral-900 dark:text-[#F3F4FA]">
                {formatDatumValue(item, spec.unit)}
              </span>
              {item.delta && (
                <span
                  className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10.5px] font-medium ${
                    deltaPositive
                      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                      : deltaNegative
                        ? "bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300"
                        : "bg-neutral-200/70 text-neutral-700 dark:bg-white/[0.08] dark:text-[#C5C9DE]"
                  }`}
                >
                  {deltaPositive && <TrendingUp className="h-2.5 w-2.5" />}
                  {deltaNegative && <TrendingDown className="h-2.5 w-2.5" />}
                  {item.delta}
                </span>
              )}
            </div>
            {item.subtext && (
              <div className="mt-1 text-[11px] leading-snug text-neutral-500 dark:text-[#858BA8]">
                {item.subtext}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function RichChartCard({
  raw,
  streaming,
  isDark,
  fallback,
}: {
  raw: string;
  streaming: boolean;
  isDark: boolean;
  fallback: ReactNode;
}) {
  const spec = useMemo(() => parseChartSpec(raw), [raw]);

  if (!spec) {
    if (streaming) {
      return (
        <div
          role="status"
          aria-label="正在绘制数据图表"
          className="my-4 rounded-2xl border border-neutral-200/90 bg-white p-5 dark:border-white/[0.1] dark:bg-[#121420]"
        >
          <div className="flex items-center gap-2 text-xs font-medium text-neutral-500 dark:text-[#8E94B0]">
            <BarChart3 className="h-4 w-4 animate-pulse" />
            <span>正在绘制数据图表...</span>
          </div>
          <div className="mt-4 space-y-3">
            <div className="h-2.5 w-4/5 animate-pulse rounded-full bg-neutral-100 dark:bg-white/[0.07]" />
            <div className="h-2.5 w-3/5 animate-pulse rounded-full bg-neutral-100 dark:bg-white/[0.07]" />
            <div className="h-2.5 w-2/5 animate-pulse rounded-full bg-neutral-100 dark:bg-white/[0.07]" />
          </div>
        </div>
      );
    }
    return <>{fallback}</>;
  }

  return (
    <section
      className="my-4 rounded-2xl border border-neutral-200/90 bg-white p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)] dark:border-white/[0.1] dark:bg-[#121420]"
      aria-label={spec.title || "数据图表"}
    >
      <ChartHeader title={spec.title} subtitle={spec.subtitle} />
      {spec.type === "horizontal_bar" && (
        <HorizontalBarChart spec={spec} isDark={isDark} />
      )}
      {spec.type === "comparison_bar" && (
        <ComparisonBarChart spec={spec} isDark={isDark} />
      )}
      {spec.type === "bar" && <VerticalBarChart spec={spec} isDark={isDark} />}
      {spec.type === "line" && <TrendLineChart spec={spec} isDark={isDark} />}
      {spec.type === "stat_grid" && <StatGridCard spec={spec} />}
      <ChartFooter source={spec.source} note={spec.note} />
    </section>
  );
}

/* ============================================================================
   4. 真实配图画廊（严格校验 URL 白名单 + 后端代理 + 拼贴排版 + 全屏灯箱）
   ============================================================================ */

interface GalleryItem {
  url: string;
  renderUrl: string;
  caption: string;
  source?: string;
  sourceUrl?: string;
}

interface ParsedGallerySpec {
  title?: string;
  items: Array<{
    url: string;
    caption: string;
    source?: string;
    sourceUrl?: string;
  }>;
}

const TOOL_IMAGE_URL_RE = /图片URL:\s*`(https?:\/\/[^`\s]+)`/g;
const PAGE_IMAGE_SECTION_RE = /####\s*页面可引用配图[^\n]*\n((?:-\s+[^\n]+\n?)+)/g;
const BACKTICK_URL_RE = /`(https?:\/\/[^`\s]+)`/g;
const MD_IMG_URL_RE = /!\[[^\]]*\]\((https?:\/\/[^)\s]+)(?:\s+"[^"]*")?\)/g;

export function collectVerifiedMediaUrls(message: UIMessage): Set<string> {
  const verified = new Set<string>();
  for (const url of message.verified_media_urls ?? []) {
    if (typeof url === "string" && url.trim()) {
      verified.add(url.trim());
    }
  }
  for (const trace of message.tool_calls ?? []) {
    if (trace.is_error || !trace.result) continue;
    if (trace.name === "search_images" || trace.name === "subagent") {
      for (const m of trace.result.matchAll(TOOL_IMAGE_URL_RE)) {
        if (m[1]) verified.add(m[1].trim());
      }
    }
    if (trace.name === "fetch_web_page" || trace.name === "subagent") {
      for (const sec of trace.result.matchAll(PAGE_IMAGE_SECTION_RE)) {
        for (const m of sec[1].matchAll(BACKTICK_URL_RE)) {
          if (m[1]) verified.add(m[1].trim());
        }
      }
      if (trace.name === "fetch_web_page") {
        for (const m of trace.result.matchAll(MD_IMG_URL_RE)) {
          if (m[1]) verified.add(m[1].trim());
        }
      }
    }
  }
  for (const img of message.images ?? []) {
    if (img.preview_url) verified.add(img.preview_url);
  }
  return verified;
}

function parseGallerySpec(raw: string): ParsedGallerySpec | null {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }
  let rawItems: unknown[] = [];
  let title: string | undefined;
  if (Array.isArray(parsed)) {
    rawItems = parsed;
  } else if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>;
    title = typeof obj.title === "string" ? obj.title.trim() : undefined;
    if (Array.isArray(obj.items)) rawItems = obj.items;
    else if (Array.isArray(obj.images)) rawItems = obj.images;
  }
  const items: ParsedGallerySpec["items"] = [];
  for (const entry of rawItems) {
    if (!entry || typeof entry !== "object") continue;
    const obj = entry as Record<string, unknown>;
    const url = typeof obj.url === "string" ? obj.url.trim() : "";
    if (!url) continue;
    const caption =
      typeof obj.caption === "string" && obj.caption.trim()
        ? obj.caption.trim()
        : typeof obj.alt === "string" && obj.alt.trim()
          ? obj.alt.trim()
          : typeof obj.title === "string" && obj.title.trim()
            ? obj.title.trim()
            : "参考配图";
    const source =
      typeof obj.source === "string" && obj.source.trim()
        ? obj.source.trim()
        : undefined;
    const rawSourceUrl =
      typeof obj.source_url === "string"
        ? obj.source_url.trim()
        : typeof obj.sourceUrl === "string"
          ? obj.sourceUrl.trim()
          : "";
    const sourceUrl = /^https?:\/\//i.test(rawSourceUrl) ? rawSourceUrl : undefined;
    items.push({ url, caption, source, sourceUrl });
  }
  if (items.length === 0) return null;
  return { title, items };
}

function resolveRenderableImageUrl(
  rawUrl: string,
  sessionId: string | null,
  verifiedMediaUrls: ReadonlySet<string>,
): string | null {
  const trimmed = rawUrl.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("/api/sessions/")) {
    return trimmed;
  }
  if (!/^https?:\/\//i.test(trimmed)) return null;
  if (!sessionId || !verifiedMediaUrls.has(trimmed)) {
    return null;
  }
  return `/api/sessions/${encodeURIComponent(sessionId)}/media-proxy?url=${encodeURIComponent(trimmed)}`;
}

export function RichImageGallery({
  raw,
  streaming,
  sessionId,
  verifiedMediaUrls,
}: {
  raw: string;
  streaming: boolean;
  sessionId: string | null;
  verifiedMediaUrls: ReadonlySet<string>;
}) {
  const spec = useMemo(() => parseGallerySpec(raw), [raw]);
  const [failedUrls, setFailedUrls] = useState<Set<string>>(() => new Set());
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);

  const visibleItems: GalleryItem[] = useMemo(() => {
    if (!spec) return [];
    const seen = new Set<string>();
    const out: GalleryItem[] = [];
    for (const item of spec.items) {
      if (seen.has(item.url) || failedUrls.has(item.url)) continue;
      const renderUrl = resolveRenderableImageUrl(
        item.url,
        sessionId,
        verifiedMediaUrls,
      );
      if (!renderUrl) continue;
      seen.add(item.url);
      out.push({
        url: item.url,
        renderUrl,
        caption: item.caption,
        source: item.source,
        sourceUrl: item.sourceUrl,
      });
    }
    return out;
  }, [spec, failedUrls, sessionId, verifiedMediaUrls]);

  useEffect(() => {
    if (lightboxIdx === null) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setLightboxIdx(null);
      } else if (e.key === "ArrowLeft") {
        setLightboxIdx((prev) =>
          prev === null ? null : (prev - 1 + visibleItems.length) % visibleItems.length,
        );
      } else if (e.key === "ArrowRight") {
        setLightboxIdx((prev) =>
          prev === null ? null : (prev + 1) % visibleItems.length,
        );
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [lightboxIdx, visibleItems.length]);

  const markFailed = (url: string) => {
    setFailedUrls((prev) => {
      if (prev.has(url)) return prev;
      const next = new Set(prev);
      next.add(url);
      return next;
    });
  };

  if (!spec) {
    if (streaming) {
      return (
        <div
          role="status"
          aria-label="正在整理参考配图"
          className="my-4 overflow-hidden rounded-2xl border border-neutral-200/90 bg-white p-3 dark:border-white/[0.1] dark:bg-[#121420]"
        >
          <div className="mb-2.5 flex items-center gap-2 text-xs font-medium text-neutral-500 dark:text-[#8E94B0]">
            <Images className="h-4 w-4 animate-pulse" />
            <span>正在整理参考配图...</span>
          </div>
          <div className="grid grid-cols-1 gap-1.5 sm:h-52 sm:grid-cols-[1.4fr_1fr] sm:grid-rows-2">
            <div className="h-36 animate-pulse rounded-xl bg-neutral-100 dark:bg-white/[0.06] sm:row-span-2 sm:h-full" />
            <div className="h-24 animate-pulse rounded-xl bg-neutral-100 dark:bg-white/[0.06] sm:h-full" />
            <div className="h-24 animate-pulse rounded-xl bg-neutral-100 dark:bg-white/[0.06] sm:h-full" />
          </div>
        </div>
      );
    }
    return null;
  }

  if (visibleItems.length === 0) {
    return null;
  }

  const activeLightbox =
    lightboxIdx !== null && visibleItems[lightboxIdx]
      ? visibleItems[lightboxIdx]
      : null;

  const renderTile = (
    item: GalleryItem,
    index: number,
    className: string,
    extraBadge?: number,
  ) => (
    <button
      key={item.url}
      type="button"
      onClick={() => setLightboxIdx(index)}
      className={`group relative block w-full overflow-hidden bg-neutral-100 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500 dark:bg-[#0D0F18] ${className}`}
    >
      <img
        src={item.renderUrl}
        alt={item.caption}
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => markFailed(item.url)}
        className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.02]"
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 via-black/25 to-transparent px-3 pt-8 pb-2.5 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
        <p className="m-0 line-clamp-2 text-[11.5px] leading-snug font-medium text-white">
          {item.caption}
        </p>
        {item.source && (
          <p className="mt-0.5 mb-0 truncate text-[10.5px] text-white/75">
            {item.source}
          </p>
        )}
      </div>
      <span className="pointer-events-none absolute right-2.5 top-2.5 inline-flex h-7 w-7 items-center justify-center rounded-full bg-black/50 text-white opacity-0 backdrop-blur-xs transition group-hover:opacity-100">
        <ZoomIn className="h-3.5 w-3.5" />
      </span>
      {extraBadge && extraBadge > 0 && (
        <span className="absolute right-2.5 bottom-2.5 inline-flex items-center gap-1 rounded-full bg-black/70 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur-xs">
          <Images className="h-3.5 w-3.5" />
          <span>+{extraBadge}</span>
        </span>
      )}
    </button>
  );

  return (
    <figure className="my-4 overflow-hidden rounded-2xl border border-neutral-200/90 bg-white dark:border-white/[0.1] dark:bg-[#121420]">
      {visibleItems.length === 1 && (
        <div className="relative">
          {renderTile(visibleItems[0], 0, "max-h-[380px] aspect-[16/10]")}
        </div>
      )}

      {visibleItems.length === 2 && (
        <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
          {renderTile(visibleItems[0], 0, "h-56 sm:h-64")}
          {renderTile(visibleItems[1], 1, "h-56 sm:h-64")}
        </div>
      )}

      {visibleItems.length >= 3 && (
        <div className="grid grid-cols-1 gap-1 sm:h-[316px] sm:grid-cols-[1.45fr_1fr] sm:grid-rows-2">
          {renderTile(visibleItems[0], 0, "h-56 sm:row-span-2 sm:h-full")}
          {renderTile(visibleItems[1], 1, "h-40 sm:h-full")}
          {renderTile(
            visibleItems[2],
            2,
            "h-40 sm:h-full",
            visibleItems.length > 3 ? visibleItems.length - 3 : undefined,
          )}
        </div>
      )}

      <figcaption className="flex flex-wrap items-center justify-between gap-2 border-t border-neutral-100 px-3.5 py-2 text-[11.5px] text-neutral-500 dark:border-white/[0.07] dark:text-[#8E94B0]">
        <span className="truncate">
          {spec.title || visibleItems[0].caption}
        </span>
        <span className="shrink-0 text-[11px] text-neutral-400 dark:text-[#747A96]">
          共 {visibleItems.length} 张参考图 · 点击可放大浏览
        </span>
      </figcaption>

      {activeLightbox && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="图片放大预览"
          onClick={() => setLightboxIdx(null)}
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/88 p-4 backdrop-blur-xs"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="relative flex max-h-[92dvh] w-full max-w-5xl flex-col items-center"
          >
            <div className="mb-3 flex w-full items-center justify-between text-xs text-white/80">
              <span>
                {(lightboxIdx ?? 0) + 1} / {visibleItems.length}
              </span>
              <div className="flex items-center gap-2">
                {activeLightbox.sourceUrl && (
                  <a
                    href={activeLightbox.sourceUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center gap-1 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-white transition hover:bg-white/20"
                  >
                    <span>查看来源{activeLightbox.source ? ` · ${activeLightbox.source}` : ""}</span>
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
                <button
                  type="button"
                  onClick={() => setLightboxIdx(null)}
                  aria-label="关闭灯箱"
                  className="rounded-full bg-white/10 p-1.5 text-white transition hover:bg-white/20"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="relative flex w-full items-center justify-center">
              {visibleItems.length > 1 && (
                <button
                  type="button"
                  onClick={() =>
                    setLightboxIdx((prev) =>
                      prev === null
                        ? 0
                        : (prev - 1 + visibleItems.length) % visibleItems.length,
                    )
                  }
                  aria-label="上一张"
                  className="absolute left-2 z-10 rounded-full bg-black/55 p-2 text-white transition hover:bg-black/80"
                >
                  <ChevronLeft className="h-5 w-5" />
                </button>
              )}

              <img
                src={activeLightbox.renderUrl}
                alt={activeLightbox.caption}
                referrerPolicy="no-referrer"
                className="max-h-[76dvh] max-w-full rounded-xl object-contain shadow-2xl"
              />

              {visibleItems.length > 1 && (
                <button
                  type="button"
                  onClick={() =>
                    setLightboxIdx((prev) =>
                      prev === null ? 0 : (prev + 1) % visibleItems.length,
                    )
                  }
                  aria-label="下一张"
                  className="absolute right-2 z-10 rounded-full bg-black/55 p-2 text-white transition hover:bg-black/80"
                >
                  <ChevronRight className="h-5 w-5" />
                </button>
              )}
            </div>

            <div className="mt-3 max-w-2xl text-center text-sm text-white/90">
              {activeLightbox.caption}
              {activeLightbox.source && !activeLightbox.sourceUrl && (
                <span className="ml-2 text-xs text-white/60">
                  （来源：{activeLightbox.source}）
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </figure>
  );
}

/* ============================================================================
   5. 行内来源胶囊（Citation Pill）
   ============================================================================ */

function extractPlainText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractPlainText).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: ReactNode } }).props;
    return extractPlainText(props?.children);
  }
  return "";
}

function getHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function RichMarkdownLink({
  href,
  title,
  children,
}: {
  href?: string;
  title?: string;
  children?: ReactNode;
}) {
  const safeHref =
    href && /^(https?:\/\/|mailto:|\/api\/sessions\/)/i.test(href.trim())
      ? href.trim()
      : undefined;

  if (!safeHref) {
    return <span>{children}</span>;
  }

  const text = extractPlainText(children).trim();
  const isExternal = /^https?:\/\//i.test(safeHref);
  const explicitCite =
    (typeof title === "string" && title.trim().toLowerCase() === "cite") ||
    /^(?:cite|来源)[:：]/i.test(text);
  const cleanLabel = text.replace(/^(?:cite|来源)[:：]\s*/i, "").trim();
  const hostname = isExternal ? getHostname(safeHref) : "";

  // 显式 cite 标记，或类似 [1] / [Reuters] 这种短引用形式自动渲染为紧凑胶囊
  const isShortBracketCite =
    isExternal &&
    cleanLabel.length > 0 &&
    cleanLabel.length <= 24 &&
    !cleanLabel.startsWith("http") &&
    (/^\d{1,2}$/.test(cleanLabel) ||
      (hostname.length > 0 &&
        cleanLabel.toLowerCase().replace(/\s+/g, "") ===
          hostname.split(".")[0]?.toLowerCase()));

  if (isExternal && (explicitCite || isShortBracketCite) && cleanLabel) {
    return (
      <a
        href={safeHref}
        target="_blank"
        rel="noreferrer noopener"
        title={hostname ? `${cleanLabel} · ${hostname}` : safeHref}
        className="citation-pill mx-0.5 inline-flex align-baseline items-center gap-1 rounded-full border border-neutral-200/90 bg-neutral-100/85 px-2 py-[1px] text-[11.5px] leading-tight font-medium text-neutral-600 no-underline transition hover:border-neutral-300 hover:bg-neutral-200/80 hover:text-neutral-900 dark:border-white/[0.11] dark:bg-white/[0.07] dark:text-[#C5C9DE] dark:hover:border-white/[0.2] dark:hover:bg-white/[0.13] dark:hover:text-[#F3F4FA]"
      >
        <Globe className="h-2.5 w-2.5 shrink-0 opacity-70" aria-hidden="true" />
        <span className="max-w-[14rem] truncate">{cleanLabel}</span>
      </a>
    );
  }

  return (
    <a
      href={safeHref}
      title={title}
      target={isExternal ? "_blank" : undefined}
      rel={isExternal ? "noreferrer noopener" : undefined}
    >
      {children}
    </a>
  );
}
