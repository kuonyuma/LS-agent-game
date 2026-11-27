import type { ReasoningEffort } from "./types";

type Color = readonly [number, number, number];

interface EffortPalette {
  base: Color;
  accent: Color;
  breath: number;
  drift: number;
  trail: number;
}

export interface EffortAppearance extends EffortPalette {
  level: ReasoningEffort;
}

const PALETTES: Record<ReasoningEffort, EffortPalette> = {
  off: { base: [107, 99, 119], accent: [153, 126, 151], breath: 0.2, drift: 0.35, trail: 0.45 },
  low: { base: [124, 85, 180], accent: [181, 108, 166], breath: 0.5, drift: 0.6, trail: 0.62 },
  medium: { base: [124, 58, 237], accent: [198, 78, 151], breath: 1, drift: 1, trail: 1 },
  high: { base: [109, 40, 217], accent: [216, 55, 126], breath: 1.22, drift: 1.12, trail: 1.23 },
  xhigh: { base: [123, 45, 219], accent: [219, 39, 119], breath: 1.48, drift: 1.26, trail: 1.48 },
  max: { base: [134, 35, 201], accent: [225, 34, 108], breath: 1.63, drift: 1.36, trail: 1.63 },
};

function mixColor(from: Color, to: Color, fraction: number): Color {
  return [
    Math.round(from[0] + (to[0] - from[0]) * fraction),
    Math.round(from[1] + (to[1] - from[1]) * fraction),
    Math.round(from[2] + (to[2] - from[2]) * fraction),
  ];
}

export function effortColor(color: Color, opacity = 1): string {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${opacity})`;
}

export function resolveEffortAppearance(
  levels: readonly ReasoningEffort[],
  value: ReasoningEffort,
  previewRatio: number | null,
): EffortAppearance {
  if (!levels.length) return { level: "medium", ...PALETTES.medium };

  const lastIndex = levels.length - 1;
  const selected = levels.indexOf(value);
  const position = previewRatio === null
    ? (selected >= 0 ? selected : Math.min(1, lastIndex))
    : previewRatio * lastIndex;
  const leftIndex = Math.floor(position);
  const from = PALETTES[levels[leftIndex]];
  const to = PALETTES[levels[Math.ceil(position)]];
  const fraction = position - leftIndex;
  return {
    level: levels[Math.round(position)],
    base: mixColor(from.base, to.base, fraction),
    accent: mixColor(from.accent, to.accent, fraction),
    breath: from.breath + (to.breath - from.breath) * fraction,
    drift: from.drift + (to.drift - from.drift) * fraction,
    trail: from.trail + (to.trail - from.trail) * fraction,
  };
}
