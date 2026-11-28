export type VendorId =
  | "gemini"
  | "openai"
  | "claude"
  | "kimi"
  | "deepseek"
  | "qwen"
  | "grok"
  | "meta"
  | "glm";

export type RgbColor = readonly [number, number, number];

export interface TrailPalette {
  head: RgbColor;
  mid: RgbColor;
  tail: RgbColor;
  spectrum?: readonly RgbColor[];
}

export interface StudioTheme {
  bgCenter: RgbColor;
  bgEdge: RgbColor;
  haloA: RgbColor;
  haloB: RgbColor;
  haloAlpha: number;
}

export type FieldMode =
  | "glint"
  | "bloom"
  | "compose"
  | "double_arc"
  | "align"
  | "lattice"
  | "slash"
  | "flow"
  | "circuit";

export interface VendorSpec {
  id: VendorId;
  primary: RgbColor;
  secondary: RgbColor;
  accent: RgbColor;
  trail: TrailPalette;
  studio: StudioTheme;
  fieldMode: FieldMode;
  drawMark: (
    ctx: CanvasRenderingContext2D,
    r: number,
    phase?: number,
    emphasis?: number,
    dark?: boolean,
  ) => void;
  drawScene: (
    ctx: CanvasRenderingContext2D,
    w: number,
    h: number,
    t: number,
    focus: number,
    show: number,
    dark?: boolean,
  ) => void;
}

export interface TrailMote {
  u: number;
  spread: number;
  phase: number;
  size: number;
}

export const TAU = Math.PI * 2;
export const CARD_BUFFER_W = 460;
export const CARD_BUFFER_H = 294;

export const clamp = (v: number, min = 0, max = 1): number =>
  Math.max(min, Math.min(max, v));

export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

export const smoothstep = (e0: number, e1: number, x: number): number => {
  const t = clamp((x - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
};

export const easeOutCubic = (t: number): number => 1 - Math.pow(1 - clamp(t), 3);

export const easeInOutCubic = (t: number): number => {
  const x = clamp(t);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};

export const easeOutQuint = (t: number): number => 1 - Math.pow(1 - clamp(t), 5);

export const easeInOutQuart = (t: number): number => {
  const x = clamp(t);
  return x < 0.5 ? 8 * x * x * x * x : 1 - Math.pow(-2 * x + 2, 4) / 2;
};

export const easeSpringOut = (t: number): number => {
  const x = clamp(t);
  if (x === 0 || x === 1) return x;
  return 1 - Math.exp(-7.2 * x) * Math.cos(8.6 * x);
};

export const easeOutBack = (t: number, s = 1.05): number => {
  const x = clamp(t) - 1;
  return 1 + (s + 1) * x * x * x + s * x * x;
};

export function rgba(rgb: RgbColor, a = 1): string {
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${a})`;
}

export function mixRgb(a: RgbColor, b: RgbColor, t: number): [number, number, number] {
  const k = clamp(t);
  return [
    Math.round(a[0] + (b[0] - a[0]) * k),
    Math.round(a[1] + (b[1] - a[1]) * k),
    Math.round(a[2] + (b[2] - a[2]) * k),
  ];
}

export function roundedRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export function sampleTrailColor(
  trail: TrailPalette,
  u: number,
): [number, number, number] {
  const k = clamp(u);
  if (trail.spectrum && trail.spectrum.length >= 2) {
    const scaled = k * (trail.spectrum.length - 1);
    const idx = Math.min(trail.spectrum.length - 2, Math.floor(scaled));
    return mixRgb(trail.spectrum[idx], trail.spectrum[idx + 1], scaled - idx);
  }
  return k < 0.5
    ? mixRgb(trail.head, trail.mid, k * 2)
    : mixRgb(trail.mid, trail.tail, (k - 0.5) * 2);
}

// 确定性星尘微丝偏移，确保尾迹流畅自然无随机闪烁
export const TRAIL_MOTES: readonly TrailMote[] = Array.from({ length: 16 }, (_, idx) => {
  const u = (idx + 0.5) / 16;
  const hash1 = Math.sin(idx * 12.9898 + 4.1414) * 43758.5453;
  const hash2 = Math.sin(idx * 78.233 + 1.732) * 28461.1234;
  const r1 = hash1 - Math.floor(hash1);
  const r2 = hash2 - Math.floor(hash2);
  return {
    u,
    spread: (r1 - 0.5) * 2,
    phase: r2 * TAU,
    size: 0.72 + r1 * 0.78,
  };
});

export const DEFAULT_STUDIO: StudioTheme = {
  bgCenter: [248, 248, 250],
  bgEdge: [232, 234, 240],
  haloA: [124, 58, 237],
  haloB: [216, 55, 126],
  haloAlpha: 0.08,
};

export const DEFAULT_DARK_STUDIO: StudioTheme = {
  bgCenter: [16, 15, 30],
  bgEdge: [5, 6, 11],
  haloA: [104, 74, 232],
  haloB: [232, 166, 154],
  haloAlpha: 0.16,
};

const DARK_PRIMARY_OVERRIDES: Partial<Record<VendorId, RgbColor>> = {
  openai: [52, 211, 153],
  kimi: [56, 189, 248],
  qwen: [99, 132, 255],
  grok: [226, 232, 245],
  meta: [56, 152, 255],
  glm: [45, 212, 191],
};

const DARK_TRAIL_OVERRIDES: Partial<Record<VendorId, TrailPalette>> = {
  openai: {
    head: [52, 211, 153],
    mid: [110, 231, 183],
    tail: [203, 213, 225],
  },
  kimi: {
    head: [56, 189, 248],
    mid: [125, 211, 252],
    tail: [199, 210, 254],
  },
  qwen: {
    head: [99, 132, 255],
    mid: [147, 108, 250],
    tail: [196, 138, 252],
  },
  grok: {
    head: [226, 232, 245],
    mid: [148, 163, 184],
    tail: [56, 189, 248],
  },
  meta: {
    head: [56, 152, 255],
    mid: [56, 189, 248],
    tail: [165, 224, 255],
  },
  glm: {
    head: [45, 212, 191],
    mid: [56, 189, 248],
    tail: [165, 243, 252],
  },
};

const DARK_STUDIO_OVERRIDES: Record<VendorId, StudioTheme> = {
  gemini: {
    bgCenter: [15, 19, 34],
    bgEdge: [5, 6, 12],
    haloA: [66, 133, 244],
    haloB: [245, 182, 86],
    haloAlpha: 0.16,
  },
  openai: {
    bgCenter: [11, 22, 24],
    bgEdge: [5, 7, 11],
    haloA: [16, 185, 129],
    haloB: [94, 234, 212],
    haloAlpha: 0.15,
  },
  claude: {
    bgCenter: [24, 16, 18],
    bgEdge: [6, 6, 11],
    haloA: [217, 119, 87],
    haloB: [245, 178, 132],
    haloAlpha: 0.16,
  },
  kimi: {
    bgCenter: [12, 18, 34],
    bgEdge: [5, 6, 12],
    haloA: [38, 138, 255],
    haloB: [56, 189, 248],
    haloAlpha: 0.16,
  },
  deepseek: {
    bgCenter: [13, 17, 36],
    bgEdge: [5, 6, 12],
    haloA: [77, 107, 254],
    haloB: [14, 165, 233],
    haloAlpha: 0.17,
  },
  qwen: {
    bgCenter: [15, 16, 36],
    bgEdge: [5, 6, 12],
    haloA: [99, 132, 255],
    haloB: [168, 85, 247],
    haloAlpha: 0.16,
  },
  grok: {
    bgCenter: [16, 18, 28],
    bgEdge: [5, 6, 10],
    haloA: [186, 198, 224],
    haloB: [56, 189, 248],
    haloAlpha: 0.14,
  },
  meta: {
    bgCenter: [12, 19, 34],
    bgEdge: [5, 6, 12],
    haloA: [37, 134, 255],
    haloB: [56, 189, 248],
    haloAlpha: 0.16,
  },
  glm: {
    bgCenter: [11, 21, 28],
    bgEdge: [5, 7, 11],
    haloA: [20, 184, 166],
    haloB: [56, 189, 248],
    haloAlpha: 0.15,
  },
};

export function resolveVendorPrimary(vendor: VendorSpec, dark: boolean): RgbColor {
  if (!dark) return vendor.primary;
  return DARK_PRIMARY_OVERRIDES[vendor.id] ?? vendor.primary;
}

export function resolveVendorTrail(vendor: VendorSpec, dark: boolean): TrailPalette {
  if (!dark) return vendor.trail;
  return DARK_TRAIL_OVERRIDES[vendor.id] ?? vendor.trail;
}

export function resolveVendorStudio(vendor: VendorSpec, dark: boolean): StudioTheme {
  if (!dark) return vendor.studio;
  return DARK_STUDIO_OVERRIDES[vendor.id];
}

function sceneInk(dark: boolean, alpha: number): string {
  return dark
    ? `rgba(226, 232, 245, ${alpha})`
    : `rgba(15, 23, 42, ${alpha})`;
}

/* ============================================================================
   官方高精度纯矢量品牌标识
   ============================================================================ */

export function drawGeminiMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
): void {
  ctx.save();
  const pulse = 1 + Math.sin(phase * 3.0) * 0.03 * emphasis;
  ctx.scale(pulse, pulse);

  ctx.beginPath();
  ctx.moveTo(0, -r);
  ctx.quadraticCurveTo(0, 0, r, 0);
  ctx.quadraticCurveTo(0, 0, 0, r);
  ctx.quadraticCurveTo(0, 0, -r, 0);
  ctx.quadraticCurveTo(0, 0, 0, -r);
  ctx.closePath();
  ctx.clip();

  ctx.fillStyle = "#4285F4";
  ctx.fillRect(-r, -r, r * 2, r * 2);

  const gTop = ctx.createRadialGradient(-r * 0.08, -r * 0.85, 0, -r * 0.08, -r * 0.85, r * 1.15);
  gTop.addColorStop(0, "rgba(234, 67, 53, 1)");
  gTop.addColorStop(0.55, "rgba(234, 67, 53, 0.72)");
  gTop.addColorStop(1, "rgba(234, 67, 53, 0)");
  ctx.fillStyle = gTop;
  ctx.fillRect(-r, -r, r * 2, r * 2);

  const gLeft = ctx.createRadialGradient(-r * 0.88, 0, 0, -r * 0.88, 0, r * 1.08);
  gLeft.addColorStop(0, "rgba(251, 188, 4, 1)");
  gLeft.addColorStop(0.55, "rgba(251, 188, 4, 0.78)");
  gLeft.addColorStop(1, "rgba(251, 188, 4, 0)");
  ctx.fillStyle = gLeft;
  ctx.fillRect(-r, -r, r * 2, r * 2);

  const gBot = ctx.createRadialGradient(-r * 0.08, r * 0.88, 0, -r * 0.08, r * 0.88, r * 1.05);
  gBot.addColorStop(0, "rgba(52, 168, 83, 1)");
  gBot.addColorStop(0.55, "rgba(52, 168, 83, 0.72)");
  gBot.addColorStop(1, "rgba(52, 168, 83, 0)");
  ctx.fillStyle = gBot;
  ctx.fillRect(-r, -r, r * 2, r * 2);

  const gRight = ctx.createRadialGradient(r * 0.78, 0, 0, r * 0.78, 0, r * 1.05);
  gRight.addColorStop(0, "rgba(66, 133, 244, 0.98)");
  gRight.addColorStop(0.65, "rgba(66, 133, 244, 0.5)");
  gRight.addColorStop(1, "rgba(66, 133, 244, 0)");
  ctx.fillStyle = gRight;
  ctx.fillRect(-r, -r, r * 2, r * 2);
  ctx.restore();
}

export function drawOpenAIMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
  dark = false,
  revealPetals = 6,
): void {
  ctx.save();
  ctx.rotate(phase * 0.28 * (0.2 + emphasis));

  const c = r * 0.51;
  const d = r * 0.185;
  const p = c * 0.5 + d;
  const lw = Math.max(1.15, r * 0.112);

  const aTangent = (5 * Math.PI) / 6;
  const aMeet = Math.PI / 6 - Math.acos((c * 0.5) / p);
  const sqrt3 = Math.sqrt(3);
  const startX = -d / sqrt3;
  const startY = d;
  const tanX = p * Math.cos(aTangent);
  const tanY = -c + p * Math.sin(aTangent);

  ctx.strokeStyle = dark ? "#F1F3FA" : "#111827";
  ctx.lineWidth = lw;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  for (let i = 0; i < 6; i++) {
    const prog = clamp(revealPetals - i);
    if (prog <= 0.01) continue;
    ctx.save();
    ctx.rotate((i * TAU) / 6);
    ctx.globalAlpha = prog;
    ctx.beginPath();
    const lineP = clamp(prog / 0.42);
    ctx.moveTo(startX, startY);
    ctx.lineTo(lerp(startX, tanX, lineP), lerp(startY, tanY, lineP));
    if (prog > 0.42) {
      const arcP = (prog - 0.42) / 0.58;
      const endA = lerp(aTangent, TAU + aMeet, easeOutCubic(arcP));
      ctx.arc(0, -c, p, aTangent, endA, false);
    }
    ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
}

export function drawClaudeMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
): void {
  ctx.save();
  ctx.rotate(Math.sin(phase * 1.4) * 0.05 * emphasis);
  const rays = [
    { a: -1.57, len: 0.96, w: 0.165 },
    { a: -1.04, len: 0.90, w: 0.145 },
    { a: -0.50, len: 0.98, w: 0.16 },
    { a: 0.04, len: 0.88, w: 0.145 },
    { a: 0.56, len: 0.97, w: 0.165 },
    { a: 1.09, len: 0.89, w: 0.145 },
    { a: 1.62, len: 0.96, w: 0.16 },
    { a: 2.15, len: 0.88, w: 0.145 },
    { a: 2.68, len: 0.97, w: 0.165 },
    { a: 3.22, len: 0.90, w: 0.145 },
    { a: 3.74, len: 0.95, w: 0.16 },
    { a: 4.26, len: 0.88, w: 0.145 },
  ];
  ctx.strokeStyle = "#D97757";
  ctx.lineCap = "round";
  for (const ray of rays) {
    ctx.lineWidth = Math.max(1.75, r * ray.w);
    ctx.beginPath();
    ctx.moveTo(Math.cos(ray.a) * r * 0.12, Math.sin(ray.a) * r * 0.12);
    ctx.lineTo(Math.cos(ray.a) * r * ray.len, Math.sin(ray.a) * r * ray.len);
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.arc(0, 0, r * 0.25, 0, TAU);
  ctx.fillStyle = "#D97757";
  ctx.fill();
  ctx.restore();
}

export function drawKimiMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
  dark = false,
): void {
  ctx.save();
  const box = r * 1.66;
  const half = box * 0.5;
  roundedRectPath(ctx, -half, -half, box, box, box * 0.25);
  ctx.fillStyle = dark ? "#161926" : "#090B0E";
  ctx.fill();
  if (dark) {
    ctx.strokeStyle = "rgba(255, 255, 255, 0.22)";
    ctx.lineWidth = Math.max(0.85, r * 0.06);
    ctx.stroke();
  }

  const s = box / 44;
  ctx.scale(s, s);
  ctx.fillStyle = "#FFFFFF";
  ctx.fillRect(-10.5, -11.5, 4.3, 23.0);

  ctx.beginPath();
  ctx.moveTo(-6.5, 0.2);
  ctx.lineTo(3.6, -11.5);
  ctx.lineTo(8.8, -11.5);
  ctx.lineTo(-2.2, 1.2);
  ctx.closePath();
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(-4.2, -1.6);
  ctx.lineTo(-0.4, -3.5);
  ctx.lineTo(9.8, 11.5);
  ctx.lineTo(4.3, 11.5);
  ctx.closePath();
  ctx.fill();

  const dotPulse = 1 + Math.sin(phase * 4.0) * 0.18 * emphasis;
  ctx.beginPath();
  ctx.arc(11.6, -11.6, 2.8 * dotPulse, 0, TAU);
  ctx.fillStyle = "#1677FF";
  ctx.fill();
  if (emphasis > 0.1) {
    ctx.beginPath();
    ctx.arc(11.6, -11.6, 5.8 * dotPulse, 0, TAU);
    ctx.fillStyle = `rgba(22, 119, 255, ${0.36 * emphasis})`;
    ctx.fill();
  }
  ctx.restore();
}

export function drawDeepSeekMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
): void {
  ctx.save();
  const s = r / 26;
  ctx.scale(s, s);
  ctx.rotate(Math.sin(phase * 2.1) * 0.04 * emphasis);
  const blue = "#4D6BFE";

  ctx.beginPath();
  ctx.moveTo(-21, -2);
  ctx.bezierCurveTo(-21, -16, -6, -21, 5, -15);
  ctx.bezierCurveTo(10, -12, 13, -8, 16, -11);
  ctx.bezierCurveTo(15.5, -15.5, 16.5, -18.5, 19.5, -19.5);
  ctx.bezierCurveTo(19.5, -15.5, 20.5, -14.2, 22, -13.8);
  ctx.bezierCurveTo(24.5, -14.5, 26, -15.2, 26.8, -14.2);
  ctx.bezierCurveTo(26.5, -10.5, 23.5, -7.8, 19.5, -6.5);
  ctx.bezierCurveTo(18.5, 5, 11.5, 15, 0, 17);
  ctx.bezierCurveTo(-12.5, 18.5, -21, 10, -21, -2);
  ctx.closePath();
  ctx.fillStyle = blue;
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(-17.5, -1.5);
  ctx.bezierCurveTo(-13, -3.5, -6.5, -1.5, -1.5, 4.5);
  ctx.bezierCurveTo(2.5, 9.2, 7.5, 11.2, 11.8, 10.2);
  ctx.bezierCurveTo(7.8, 15.2, -0.5, 16.2, -7.8, 13.8);
  ctx.bezierCurveTo(-14.8, 11.4, -18.5, 5.2, -17.5, -1.5);
  ctx.closePath();
  ctx.fillStyle = "#FFFFFF";
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(-6.2, 3.2);
  ctx.bezierCurveTo(-4.8, 9.2, -0.5, 14.5, 6.2, 16.2);
  ctx.bezierCurveTo(2.8, 12.2, 0.8, 7.8, 0.5, 3.5);
  ctx.bezierCurveTo(-2.0, 2.2, -4.5, 2.2, -6.2, 3.2);
  ctx.closePath();
  ctx.fillStyle = blue;
  ctx.fill();

  ctx.beginPath();
  ctx.arc(-8.5, -7.2, 1.75, 0, TAU);
  ctx.fillStyle = "#FFFFFF";
  ctx.fill();
  ctx.restore();
}

export function drawQwenMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
  dark = false,
): void {
  ctx.save();
  ctx.rotate(phase * 0.25 * emphasis);
  const s = r / 24;
  ctx.scale(s, s);
  ctx.fillStyle = dark ? "#5A7DFF" : "#0A3EFF";

  for (let i = 0; i < 3; i++) {
    ctx.save();
    ctx.rotate((i * TAU) / 3);
    ctx.beginPath();
    ctx.moveTo(-10.2, -7.8);
    ctx.lineTo(-4.2, -18.2);
    ctx.quadraticCurveTo(-2.1, -21.8, 1.8, -19.8);
    ctx.lineTo(5.8, -17.5);
    ctx.quadraticCurveTo(8.3, -16.0, 6.9, -13.4);
    ctx.lineTo(5.6, -11.2);
    ctx.lineTo(14.8, -11.2);
    ctx.quadraticCurveTo(18.8, -11.2, 17.8, -7.4);
    ctx.lineTo(15.8, -3.9);
    ctx.quadraticCurveTo(14.6, -2.0, 11.6, -2.0);
    ctx.lineTo(-7.2, -2.0);
    ctx.quadraticCurveTo(-12.0, -2.0, -10.2, -7.8);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}

export function drawGrokMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
  dark = false,
): void {
  ctx.save();
  const s = r / 24;
  ctx.scale(s, s);
  ctx.fillStyle = dark ? "#F1F3FA" : "#0B0D12";

  ctx.beginPath();
  ctx.arc(0, 0, 15.6, Math.PI * 0.78, -Math.PI * 0.18, false);
  ctx.arc(0, 0, 10.6, -Math.PI * 0.18, Math.PI * 0.78, true);
  ctx.closePath();
  ctx.fill();

  ctx.beginPath();
  ctx.arc(0, 0, 15.6, -Math.PI * 0.04, Math.PI * 0.65, false);
  ctx.arc(0, 0, 10.6, Math.PI * 0.65, -Math.PI * 0.04, true);
  ctx.closePath();
  ctx.fill();

  const slashShift = Math.sin(phase * 3.0) * 0.55 * emphasis;
  ctx.beginPath();
  ctx.moveTo(-21.5 - slashShift, 21.5 + slashShift);
  ctx.quadraticCurveTo(-5, 3, 22.5 + slashShift, -22.5 - slashShift);
  ctx.quadraticCurveTo(3, -1, -13.5, 16.5);
  ctx.quadraticCurveTo(-16.5, 19.2, -21.5 - slashShift, 21.5 + slashShift);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

export function drawMetaMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
): void {
  ctx.save();
  const s = r / 24;
  ctx.scale(s, s);
  const pulse = 1 + Math.sin(phase * 2.2) * 0.02 * emphasis;
  ctx.scale(pulse, pulse);

  const grad = ctx.createLinearGradient(-22, -10, 22, 12);
  grad.addColorStop(0, "#0055D4");
  grad.addColorStop(0.5, "#0078F8");
  grad.addColorStop(1, "#0064E0");

  ctx.beginPath();
  ctx.moveTo(0, -1.5);
  ctx.bezierCurveTo(6.2, -12.8, 12.2, -16.2, 17.2, -11.2);
  ctx.bezierCurveTo(22.2, -6.2, 22.8, 6.8, 17.6, 12.2);
  ctx.bezierCurveTo(12.6, 17.2, 6.2, 11.4, 0, 1.5);
  ctx.bezierCurveTo(-6.2, -8.8, -12.2, -16.2, -17.2, -11.2);
  ctx.bezierCurveTo(-22.2, -6.2, -22.8, 6.8, -17.6, 12.2);
  ctx.bezierCurveTo(-12.6, 17.2, -6.2, 11.4, 0, -1.5);
  ctx.strokeStyle = grad;
  ctx.lineWidth = 4.6;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(-4.8, 5.6);
  ctx.bezierCurveTo(-1.8, 1.8, 1.8, -1.8, 4.8, -5.6);
  ctx.strokeStyle = "#0082FB";
  ctx.lineWidth = 4.8;
  ctx.stroke();
  ctx.restore();
}

export function drawGlmMark(
  ctx: CanvasRenderingContext2D,
  r: number,
  phase = 0,
  emphasis = 0,
  dark = false,
): void {
  ctx.save();
  const s = r / 22;
  ctx.scale(s, s);
  ctx.fillStyle = dark ? "#F1F3FA" : "#111827";

  ctx.beginPath();
  ctx.moveTo(-17, -15.5);
  ctx.lineTo(1.5, -15.5);
  ctx.lineTo(-2.5, -9.2);
  ctx.lineTo(-17, -9.2);
  ctx.closePath();
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(8.5, -15.5);
  ctx.lineTo(17.5, -15.5);
  ctx.lineTo(-7.2, 15.5);
  ctx.lineTo(-16.8, 15.5);
  ctx.closePath();
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(1.8, 9.2);
  ctx.lineTo(17, 9.2);
  ctx.lineTo(17, 15.5);
  ctx.lineTo(-2.2, 15.5);
  ctx.closePath();
  ctx.fill();

  if (emphasis > 0.1) {
    const glow = 1 + Math.sin(phase * 3.6) * 0.16 * emphasis;
    ctx.beginPath();
    ctx.arc(3.8, -12.4, 1.9 * emphasis * glow, 0, TAU);
    ctx.fillStyle = `rgba(14, 165, 233, ${0.92 * emphasis})`;
    ctx.fill();
  }
  ctx.restore();
}

/* ============================================================================
   九套故事级零文字微剧场
   ============================================================================ */

export function drawCardAmbientBg(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  vendor: VendorSpec,
  focus: number,
  dark = false,
): void {
  const primary = resolveVendorPrimary(vendor, dark);
  const g1 = ctx.createRadialGradient(
    w * 0.18,
    h * 0.18,
    0,
    w * 0.18,
    h * 0.18,
    Math.max(w, h) * 0.9,
  );
  g1.addColorStop(0, rgba(primary, dark ? 0.1 + focus * 0.11 : 0.055 + focus * 0.065));
  g1.addColorStop(0.55, rgba(vendor.secondary, dark ? 0.04 + focus * 0.05 : 0.02 + focus * 0.03));
  g1.addColorStop(1, dark ? "rgba(0,0,0,0)" : "rgba(255,255,255,0)");
  ctx.fillStyle = g1;
  ctx.fillRect(0, 0, w, h);

  const g2 = ctx.createRadialGradient(
    w * 0.85,
    h * 0.85,
    0,
    w * 0.85,
    h * 0.85,
    Math.max(w, h) * 0.65,
  );
  g2.addColorStop(0, rgba(vendor.accent, dark ? 0.075 + focus * 0.085 : 0.045 + focus * 0.06));
  g2.addColorStop(1, dark ? "rgba(0,0,0,0)" : "rgba(255,255,255,0)");
  ctx.fillStyle = g2;
  ctx.fillRect(0, 0, w, h);
}

// 1. Gemini【多模态】—— 声波穿过四色棱镜折射显影出图像/视频帧/代码
function drawGeminiScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const beamIn = smoothstep(0.05, 0.42, show);
  const refract = smoothstep(0.32, 0.82, show);
  const vis = 0.45 + focus * 0.55;

  ctx.save();
  const waveLeft = w * 0.1;
  const waveRight = cx - unit * 0.19;
  ctx.beginPath();
  const steps = 28;
  for (let i = 0; i <= steps; i++) {
    const u = i / steps;
    const x = lerp(waveLeft, waveRight, u);
    const env = Math.sin(u * Math.PI) * (1 - u * 0.45);
    const amp = unit * 0.14 * env * (0.45 + 0.55 * vis);
    const y = cy + Math.sin(u * 13 - t * 6.5) * amp;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = `rgba(66, 133, 244, ${0.86 * vis})`;
  ctx.lineWidth = 2.2;
  ctx.lineCap = "round";
  ctx.stroke();

  if (focus > 0.06) {
    const bx = lerp(waveLeft, cx - unit * 0.06, (t * 1.35) % 1);
    ctx.beginPath();
    ctx.arc(bx, cy, unit * 0.018, 0, TAU);
    ctx.fillStyle = `rgba(66, 133, 244, ${0.95 * focus})`;
    ctx.fill();
  }

  const outX = w * 0.77;
  const targets: readonly { y: number; col: RgbColor }[] = [
    { y: h * 0.25, col: [234, 67, 53] },
    { y: h * 0.5, col: [245, 158, 11] },
    { y: h * 0.75, col: [52, 168, 83] },
  ];
  const raySpread = focus > 0.05 ? refract : 0.65;
  for (const tg of targets) {
    const tx = lerp(cx + unit * 0.08, outX - unit * 0.12, raySpread);
    const ty = lerp(cy, tg.y, raySpread);
    const lg = ctx.createLinearGradient(cx + unit * 0.05, cy, tx, ty);
    lg.addColorStop(0, rgba(tg.col, 0.92 * vis));
    lg.addColorStop(1, rgba(tg.col, 0.44 * vis));
    ctx.strokeStyle = lg;
    ctx.lineWidth = 2.3;
    ctx.beginPath();
    ctx.moveTo(cx + unit * 0.05, cy);
    ctx.lineTo(tx, ty);
    ctx.stroke();
  }

  const reveal = focus > 0.05 ? easeOutCubic(refract) : 0.72;
  const boxW = unit * 0.22;
  const boxH = unit * 0.145;

  // 图像画框
  ctx.save();
  ctx.globalAlpha = vis * reveal;
  const ix = outX - boxW * 0.4;
  const iy = h * 0.25 - boxH * 0.5;
  roundedRectPath(ctx, ix, iy, boxW, boxH, 5);
  ctx.fillStyle = "rgba(234, 67, 53, 0.08)";
  ctx.fill();
  ctx.strokeStyle = "rgba(234, 67, 53, 0.72)";
  ctx.lineWidth = 1.3;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(ix + boxW * 0.28, iy + boxH * 0.34, boxH * 0.13, 0, TAU);
  ctx.fillStyle = "#FBBC04";
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(ix + boxW * 0.14, iy + boxH * 0.84);
  ctx.lineTo(ix + boxW * 0.45, iy + boxH * 0.46);
  ctx.lineTo(ix + boxW * 0.65, iy + boxH * 0.68);
  ctx.lineTo(ix + boxW * 0.84, iy + boxH * 0.44);
  ctx.lineTo(ix + boxW * 0.9, iy + boxH * 0.84);
  ctx.closePath();
  ctx.fillStyle = "rgba(234, 67, 53, 0.58)";
  ctx.fill();
  ctx.restore();

  // 视频播放帧
  ctx.save();
  ctx.globalAlpha = vis * reveal;
  const vx = outX - boxW * 0.32;
  const vy = h * 0.5 - boxH * 0.5;
  roundedRectPath(ctx, vx, vy, boxW, boxH, 5);
  ctx.fillStyle = "rgba(251, 188, 4, 0.09)";
  ctx.fill();
  ctx.strokeStyle = "rgba(234, 168, 4, 0.78)";
  ctx.lineWidth = 1.3;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(vx + boxW * 0.42, vy + boxH * 0.28);
  ctx.lineTo(vx + boxW * 0.66, vy + boxH * 0.5);
  ctx.lineTo(vx + boxW * 0.42, vy + boxH * 0.72);
  ctx.closePath();
  ctx.fillStyle = "#F59E0B";
  ctx.fill();
  ctx.restore();

  // 结构化代码行
  ctx.save();
  ctx.globalAlpha = vis * reveal;
  const cxLine = outX - boxW * 0.4;
  const cyLine = h * 0.75;
  const lens = [0.85, 0.55, 0.95];
  ctx.strokeStyle = "#34A853";
  ctx.lineWidth = 2.1;
  ctx.lineCap = "round";
  for (let k = 0; k < 3; k++) {
    const ly = cyLine + (k - 1) * boxH * 0.34;
    const indent = k === 1 ? boxW * 0.18 : 0;
    ctx.beginPath();
    ctx.moveTo(cxLine + indent, ly);
    ctx.lineTo(cxLine + indent + boxW * lens[k] * 0.75, ly);
    ctx.stroke();
  }
  ctx.restore();

  if (focus > 0.08 && refract > 0.02) {
    const pr = unit * (0.14 + refract * 0.32);
    ctx.strokeStyle = `rgba(66, 133, 244, ${(1 - refract) * 0.65 * focus})`;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.arc(cx, cy, pr, 0, TAU);
    ctx.stroke();
  }
  ctx.restore();

  ctx.save();
  ctx.translate(cx, cy);
  drawGeminiMark(ctx, unit * (0.195 + focus * 0.045 + Math.sin(beamIn * Math.PI) * 0.02), t, focus);
  ctx.restore();
}

// 2. OpenAI / GPT【最早从 0 到 1】—— 空心 0 坍缩奇点 → 绽放 6 瓣花环与创世射线
function drawOpenAIScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
  dark = false,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);

  const zeroPhase = focus > 0.08 ? smoothstep(0.0, 0.21, show) : 1;
  const ignitePhase = focus > 0.08 ? smoothstep(0.18, 0.31, show) : 1;
  const genesisPhase = focus > 0.08 ? smoothstep(0.2, 0.56, show) : 1;
  const radiatePhase = focus > 0.08 ? smoothstep(0.24, 0.92, show) : 1;

  ctx.save();
  if (focus > 0.08 && ignitePhase < 0.98) {
    const zeroR = unit * lerp(0.24, 0.02, easeInOutCubic(zeroPhase));
    const zeroA = (1 - ignitePhase) * focus;
    ctx.strokeStyle = sceneInk(dark, 0.8 * zeroA);
    ctx.lineWidth = 2.6;
    ctx.beginPath();
    ctx.arc(cx, cy, zeroR, 0, TAU);
    ctx.stroke();
  }

  if (genesisPhase > 0.02) {
    const wave = easeOutCubic(radiatePhase);
    const rays = 12;
    for (let i = 0; i < rays; i++) {
      const a = (i * TAU) / rays + t * 0.08;
      const r1 = unit * 0.285;
      const r2 = unit * lerp(0.29, i % 2 === 0 ? 0.45 : 0.39, wave);
      const alpha = (i % 2 === 0 ? 0.62 : 0.34) * (0.35 + focus * 0.65) * wave;
      ctx.strokeStyle = i % 2 === 0
        ? `rgba(16, 163, 127, ${alpha})`
        : sceneInk(dark, alpha);
      ctx.lineWidth = i % 2 === 0 ? 2.0 : 1.3;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      ctx.stroke();

      if (i % 2 === 0 && wave > 0.4) {
        ctx.beginPath();
        ctx.arc(
          cx + Math.cos(a) * (r2 + unit * 0.026),
          cy + Math.sin(a) * (r2 + unit * 0.026),
          unit * 0.013,
          0,
          TAU,
        );
        ctx.fillStyle = `rgba(16, 163, 127, ${0.72 * (0.3 + focus * 0.7) * wave})`;
        ctx.fill();
      }
    }

    for (let k = 0; k < 2; k++) {
      const p = (t * 0.48 + k * 0.5) % 1;
      const rr = unit * (0.26 + p * 0.23);
      ctx.strokeStyle = k === 0
        ? `rgba(16, 163, 127, ${(1 - p) * 0.45 * (0.3 + focus * 0.7) * wave})`
        : sceneInk(dark, (1 - p) * 0.28 * (0.3 + focus * 0.7) * wave);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(cx, cy, rr, 0, TAU);
      ctx.stroke();
    }

    if (focus > 0.08 && radiatePhase < 0.96) {
      const burstR = unit * (0.08 + wave * 0.46);
      const burstA = (1 - wave) * 0.78 * focus;
      ctx.strokeStyle = `rgba(16, 163, 127, ${burstA})`;
      ctx.lineWidth = 2.6 * (1 - wave * 0.5);
      ctx.beginPath();
      ctx.arc(cx, cy, burstR, 0, TAU);
      ctx.stroke();
    }

    if (focus > 0.12 && genesisPhase > 0.85) {
      const spark = smoothstep(0.85, 1, genesisPhase) * focus;
      ctx.strokeStyle = `rgba(16, 163, 127, ${0.92 * spark})`;
      ctx.lineWidth = 2.4;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(cx, cy - unit * 0.044);
      ctx.lineTo(cx, cy + unit * 0.044);
      ctx.stroke();
    }
  }
  ctx.restore();

  const petals = focus > 0.08 ? lerp(0.5, 6, easeOutCubic(genesisPhase)) : 6;
  if (petals > 0.05) {
    ctx.save();
    ctx.translate(cx, cy);
    const scaleIn = focus > 0.08 ? lerp(0.76, 1, easeSpringOut(genesisPhase)) : 1;
    ctx.scale(scaleIn, scaleIn);
    drawOpenAIMark(ctx, unit * (0.215 + focus * 0.04), t, focus, dark, petals);
    ctx.restore();
  }
}

// 3. Claude【目前编程巅峰】—— 错乱故障代码逐行重构对齐 + 绿勾零缺陷通过
function drawClaudeScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
  dark = false,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const scanP = focus > 0.08 ? smoothstep(0.06, 0.56, show) : 0.85;
  const passP = focus > 0.08 ? smoothstep(0.5, 0.78, show) : 0.75;
  const markX = lerp(cx, w * 0.245, focus * 0.94);

  ctx.save();
  const rows = [
    { indent: 0, len: 0.26, err: false, jitterX: -0.04, jitterA: -0.06, hi: true },
    { indent: 1, len: 0.21, err: true, jitterX: 0.07, jitterA: 0.09, hi: false },
    { indent: 2, len: 0.18, err: false, jitterX: -0.05, jitterA: 0.06, hi: true },
    { indent: 2, len: 0.14, err: true, jitterX: 0.06, jitterA: -0.08, hi: false },
    { indent: 1, len: 0.23, err: false, jitterX: -0.03, jitterA: 0.05, hi: false },
    { indent: 0, len: 0.19, err: false, jitterX: 0.04, jitterA: -0.04, hi: true },
  ];
  const baseX = w * 0.455;
  const startY = cy - unit * 0.23;
  const gapY = unit * 0.092;
  const indentStep = unit * 0.055;

  if (focus > 0.08) {
    const lineH = (rows.length - 1) * gapY * scanP;
    ctx.strokeStyle = `rgba(217, 119, 87, ${0.42 * focus})`;
    ctx.lineWidth = 1.4;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(baseX - unit * 0.035, startY);
    ctx.lineTo(baseX - unit * 0.035, startY + lineH);
    ctx.stroke();

    if (scanP > 0.35) {
      const subA = smoothstep(0.35, 0.75, scanP) * focus;
      ctx.strokeStyle = `rgba(217, 119, 87, ${0.32 * subA})`;
      ctx.beginPath();
      ctx.moveTo(baseX + indentStep * 0.55, startY + gapY);
      ctx.lineTo(baseX + indentStep * 0.55, startY + gapY * 4);
      ctx.stroke();
    }
  }

  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const rowFixed = easeOutCubic(clamp(scanP * 1.25 - i * 0.11));
    const disorder = (1 - rowFixed) * (focus > 0.08 ? 1 : 0.2);
    const x = baseX + r.indent * indentStep + r.jitterX * w * disorder;
    const y = startY + i * gapY;
    const bw = r.len * w;
    const bh = Math.max(4.2, unit * 0.034);

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(r.jitterA * disorder);
    roundedRectPath(ctx, 0, -bh * 0.5, bw, bh, bh * 0.5);
    if (r.err && rowFixed < 0.7) {
      ctx.fillStyle = `rgba(239, 68, 68, ${0.55 * (1 - rowFixed * 0.6)})`;
    } else {
      ctx.fillStyle = r.hi
        ? `rgba(217, 119, 87, ${0.34 + focus * 0.56})`
        : dark
          ? `rgba(218, 198, 186, ${0.22 + focus * 0.3})`
          : `rgba(100, 82, 74, ${0.24 + focus * 0.32})`;
    }
    ctx.fill();

    if (rowFixed > 0.5 && focus > 0.1) {
      roundedRectPath(ctx, bw + unit * 0.025, -bh * 0.5, unit * 0.055, bh, bh * 0.5);
      ctx.fillStyle = `rgba(217, 119, 87, ${0.26 * rowFixed * focus})`;
      ctx.fill();
    }
    ctx.restore();
  }

  if (focus > 0.12 && passP > 0.05) {
    const gx = w * 0.86;
    const gy = startY - unit * 0.01;
    const gr = unit * 0.048 * easeSpringOut(passP);
    ctx.beginPath();
    ctx.arc(gx, gy, gr, 0, TAU);
    ctx.fillStyle = `rgba(16, 185, 129, ${0.16 * passP * focus})`;
    ctx.fill();
    ctx.strokeStyle = `rgba(16, 185, 129, ${0.9 * passP * focus})`;
    ctx.lineWidth = 1.6;
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(gx - gr * 0.42, gy + gr * 0.02);
    ctx.lineTo(gx - gr * 0.1, gy + gr * 0.34);
    ctx.lineTo(gx + gr * 0.45, gy - gr * 0.3);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  if (focus > 0.08) {
    ctx.strokeStyle = `rgba(217, 119, 87, ${0.44 * focus})`;
    ctx.lineWidth = 1.8;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.arc(markX, cy, unit * 0.285, -Math.PI * 0.38, Math.PI * 0.38);
    ctx.stroke();
  }
  ctx.restore();

  ctx.save();
  ctx.translate(markX, cy);
  drawClaudeMark(ctx, unit * (0.195 + focus * 0.025), t, focus);
  ctx.restore();
}

// 4. Kimi【国内最智能】—— 高维复杂星网瞬间洞察最优捷径
function drawKimiScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
  dark = false,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const solveP = focus > 0.08 ? smoothstep(0.14, 0.78, show) : 0.75;
  const ahaP = focus > 0.08 ? smoothstep(0.62, 0.96, show) : 0.4;

  const nodes = [
    { x: 0.14, y: 0.24 }, { x: 0.12, y: 0.52 }, { x: 0.16, y: 0.78 },
    { x: 0.31, y: 0.19 }, { x: 0.28, y: 0.44 }, { x: 0.32, y: 0.74 },
    { x: 0.68, y: 0.22 }, { x: 0.72, y: 0.56 }, { x: 0.67, y: 0.81 },
    { x: 0.86, y: 0.26 }, { x: 0.88, y: 0.52 }, { x: 0.84, y: 0.76 },
  ];
  const meshEdges: readonly [number, number][] = [
    [0, 3], [0, 4], [1, 3], [1, 4], [1, 5], [2, 4], [2, 5],
    [3, 6], [4, 6], [4, 7], [5, 7], [5, 8],
    [6, 9], [6, 10], [7, 9], [7, 10], [7, 11], [8, 10], [8, 11],
  ];
  const insightPath = [
    { x: 0.12, y: 0.52 },
    { x: 0.31, y: 0.19 },
    { x: 0.5, y: 0.5 },
    { x: 0.68, y: 0.22 },
    { x: 0.88, y: 0.52 },
  ];

  ctx.save();
  const fogFade = 1 - solveP * 0.55;
  ctx.strokeStyle = dark
    ? `rgba(148, 175, 224, ${(0.14 + focus * 0.16) * fogFade})`
    : `rgba(15, 23, 42, ${(0.1 + focus * 0.12) * fogFade})`;
  ctx.lineWidth = 1;
  for (const [a, b] of meshEdges) {
    ctx.beginPath();
    ctx.moveTo(nodes[a].x * w, nodes[a].y * h);
    ctx.lineTo(nodes[b].x * w, nodes[b].y * h);
    ctx.stroke();
  }
  for (const n of nodes) {
    ctx.beginPath();
    ctx.arc(n.x * w, n.y * h, unit * 0.013, 0, TAU);
    ctx.fillStyle = dark
      ? `rgba(168, 186, 224, ${(0.36 + focus * 0.38) * fogFade})`
      : `rgba(100, 116, 139, ${(0.32 + focus * 0.35) * fogFade})`;
    ctx.fill();
  }

  const totalSegs = insightPath.length - 1;
  const progSeg = solveP * totalSegs;
  ctx.strokeStyle = dark
    ? `rgba(56, 189, 248, ${0.46 + focus * 0.52})`
    : `rgba(22, 119, 255, ${0.42 + focus * 0.55})`;
  ctx.lineWidth = 2.2 + focus * 0.8;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  for (let s = 0; s < totalSegs; s++) {
    const segT = clamp(progSeg - s);
    if (segT <= 0.01) continue;
    const p0 = insightPath[s];
    const p1 = insightPath[s + 1];
    ctx.beginPath();
    ctx.moveTo(p0.x * w, p0.y * h);
    ctx.lineTo(lerp(p0.x * w, p1.x * w, segT), lerp(p0.y * h, p1.y * h, segT));
    ctx.stroke();
  }

  for (let i = 0; i < insightPath.length; i++) {
    if (i === 2) continue;
    const lit = clamp(progSeg - i + 0.5);
    if (lit <= 0.05) continue;
    const pt = insightPath[i];
    const isFinal = i === insightPath.length - 1;
    ctx.beginPath();
    ctx.arc(pt.x * w, pt.y * h, unit * (isFinal ? 0.028 : 0.019) * lit, 0, TAU);
    ctx.fillStyle = dark ? "#38BDF8" : "#1677FF";
    ctx.fill();

    if (isFinal && ahaP > 0.05 && focus > 0.1) {
      ctx.beginPath();
      ctx.arc(pt.x * w, pt.y * h, unit * (0.03 + ahaP * 0.095), 0, TAU);
      ctx.strokeStyle = dark
        ? `rgba(56, 189, 248, ${(1 - ahaP) * 0.85 * focus})`
        : `rgba(22, 119, 255, ${(1 - ahaP) * 0.85 * focus})`;
      ctx.lineWidth = 1.8;
      ctx.stroke();
    }
  }
  ctx.restore();

  ctx.save();
  ctx.translate(cx, cy);
  drawKimiMark(ctx, unit * (0.195 + focus * 0.032), t, focus, dark);
  ctx.restore();
}

// 5. DeepSeek【最实惠】—— 左侧稀疏 3 核撬动右侧宏大深海能量浪
function drawDeepSeekScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const activateP = focus > 0.08 ? smoothstep(0.06, 0.45, show) : 0.85;
  const surgeP = focus > 0.08 ? smoothstep(0.32, 0.92, show) : 0.8;
  const vis = 0.42 + focus * 0.58;

  ctx.save();
  const gridX = w * 0.12;
  const cell = unit * 0.042;
  const gap = unit * 0.016;
  const gridSize = 4 * cell + 3 * gap;
  const gridY = cy - gridSize * 0.5;
  const activeCells = new Set([2, 5, 11]);

  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      const idx = r * 4 + c;
      const isLit = activeCells.has(idx);
      const px = gridX + c * (cell + gap);
      const py = gridY + r * (cell + gap);
      roundedRectPath(ctx, px, py, cell, cell, 3);
      ctx.fillStyle = isLit
        ? `rgba(77, 107, 254, ${(0.35 + 0.65 * activateP) * vis})`
        : `rgba(148, 163, 184, ${0.18 * vis})`;
      ctx.fill();
    }
  }

  const feedStartX = gridX + gridSize + unit * 0.025;
  const feedEndX = cx - unit * 0.18;
  const feedG = ctx.createLinearGradient(feedStartX, cy, feedEndX, cy);
  feedG.addColorStop(0, `rgba(77, 107, 254, ${0.35 * vis})`);
  feedG.addColorStop(1, `rgba(77, 107, 254, ${0.9 * vis * activateP})`);
  ctx.strokeStyle = feedG;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(feedStartX, cy);
  ctx.lineTo(lerp(feedStartX, feedEndX, activateP), cy);
  ctx.stroke();

  const waveOriginX = cx + unit * 0.16;
  const waveMaxW = w * 0.89 - waveOriginX;
  const waves = 5;
  for (let i = 0; i < waves; i++) {
    const wp = easeOutCubic(clamp(surgeP * 1.35 - i * 0.12));
    if (wp <= 0.01) continue;
    const x0 = waveOriginX + (i / (waves - 1)) * waveMaxW * 0.72 * wp;
    const arcSpan = (0.36 + i * 0.13) * Math.PI * 0.5;
    const rad = unit * (0.12 + i * 0.072) * wp;
    const alpha = (0.88 - i * 0.12) * vis * wp;

    ctx.strokeStyle = i % 2 === 0
      ? `rgba(77, 107, 254, ${alpha})`
      : `rgba(14, 165, 233, ${alpha})`;
    ctx.lineWidth = 2.4 + i * 0.35;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.arc(x0 - rad * 0.55, cy, rad, -arcSpan, arcSpan);
    ctx.stroke();
  }

  if (focus > 0.08 && surgeP > 0.15) {
    for (let k = 0; k < 10; k++) {
      const p = ((t * 0.95 + k * 0.13) % 1) * surgeP;
      const px = waveOriginX + p * waveMaxW;
      const spreadY = Math.sin(k * 2.7) * 0.36 * h * p;
      ctx.beginPath();
      ctx.arc(px, cy + spreadY, unit * (0.012 + (1 - p) * 0.01), 0, TAU);
      ctx.fillStyle = `rgba(14, 165, 233, ${(1 - p) * 0.85 * focus})`;
      ctx.fill();
    }
  }
  ctx.restore();

  ctx.save();
  ctx.translate(cx, cy);
  drawDeepSeekMark(ctx, unit * (0.195 + focus * 0.032), t, focus);
  ctx.restore();
}

// 6. Qwen【体验均衡】—— 六维能力雷达满格对称锁定
function drawQwenScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
  dark = false,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const eqP = focus > 0.08 ? easeInOutCubic(smoothstep(0.1, 0.78, show)) : 0.92;
  const lockFlash = focus > 0.08 ? smoothstep(0.74, 0.98, show) : 0;
  const maxR = unit * 0.42;
  const qwenRgb = dark ? "99, 132, 255" : "10, 62, 255";

  ctx.save();
  ctx.translate(cx, cy);

  for (let ring = 1; ring <= 3; ring++) {
    const rr = (maxR * ring) / 3;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = -Math.PI / 2 + (i * TAU) / 6;
      const px = Math.cos(a) * rr;
      const py = Math.sin(a) * rr;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.strokeStyle = `rgba(${qwenRgb}, ${dark ? 0.16 + focus * 0.14 : 0.09 + focus * 0.09})`;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  for (let i = 0; i < 6; i++) {
    const a = -Math.PI / 2 + (i * TAU) / 6;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(a) * maxR, Math.sin(a) * maxR);
    ctx.strokeStyle = `rgba(${qwenRgb}, ${dark ? 0.14 + focus * 0.12 : 0.08 + focus * 0.08})`;
    ctx.stroke();
  }

  const uneven = [0.56, 0.92, 0.5, 0.9, 0.58, 0.84];
  const pts: { x: number; y: number }[] = [];
  for (let i = 0; i < 6; i++) {
    const a = -Math.PI / 2 + (i * TAU) / 6;
    const jitter = Math.sin(t * 5.0 + i * 1.4) * 0.06 * (1 - eqP);
    const ratio = lerp(uneven[i] + jitter, 0.94, eqP);
    pts.push({ x: Math.cos(a) * maxR * ratio, y: Math.sin(a) * maxR * ratio });
  }

  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    if (i === 0) ctx.moveTo(pts[i].x, pts[i].y);
    else ctx.lineTo(pts[i].x, pts[i].y);
  }
  ctx.closePath();
  ctx.fillStyle = `rgba(${qwenRgb}, ${(dark ? 0.12 : 0.07) + focus * 0.09 + Math.sin(lockFlash * Math.PI) * 0.08})`;
  ctx.fill();
  ctx.strokeStyle = `rgba(${qwenRgb}, ${0.46 + focus * 0.48})`;
  ctx.lineWidth = 1.9;
  ctx.stroke();

  for (const pt of pts) {
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, unit * (0.017 + eqP * 0.008 * focus), 0, TAU);
    ctx.fillStyle = dark ? "#6384FF" : "#0A3EFF";
    ctx.fill();
    if (lockFlash > 0.05 && focus > 0.1) {
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, unit * (0.02 + lockFlash * 0.045), 0, TAU);
      ctx.strokeStyle = `rgba(96, 165, 250, ${(1 - lockFlash) * 0.75 * focus})`;
      ctx.lineWidth = 1.3;
      ctx.stroke();
    }
  }

  drawQwenMark(ctx, unit * (0.185 + focus * 0.03), t, focus, dark);
  ctx.restore();
}

// 7. Grok【最开放】—— 奇点斜刃斩破封闭围栏，自由星尘迸射
function drawGrokScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
  dark = false,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const slashP = focus > 0.08 ? smoothstep(0.16, 0.44, show) : 0.75;
  const breakP = focus > 0.08 ? easeOutCubic(smoothstep(0.32, 0.9, show)) : 0.65;

  ctx.save();
  ctx.translate(cx, cy);

  const boxS = unit * 0.34;
  const split = breakP * unit * 0.145;
  const cageAlpha = (0.78 - breakP * 0.32) * (0.45 + focus * 0.55);

  ctx.strokeStyle = sceneInk(dark, cageAlpha);
  ctx.lineWidth = 2.5;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  ctx.save();
  ctx.translate(-split * 0.82, -split * 0.82);
  ctx.rotate(-breakP * 0.15);
  ctx.beginPath();
  ctx.moveTo(-boxS, boxS * 0.45);
  ctx.lineTo(-boxS, -boxS);
  ctx.lineTo(boxS * 0.45, -boxS);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.translate(split * 0.82, split * 0.82);
  ctx.rotate(breakP * 0.15);
  ctx.beginPath();
  ctx.moveTo(boxS, -boxS * 0.45);
  ctx.lineTo(boxS, boxS);
  ctx.lineTo(-boxS * 0.45, boxS);
  ctx.stroke();
  ctx.restore();

  if (breakP > 0.05) {
    const sparks = 14;
    for (let i = 0; i < sparks; i++) {
      const a = (i / sparks) * TAU + 0.28;
      const d0 = unit * (0.21 + breakP * (0.17 + (i % 3) * 0.085));
      const d1 = d0 + unit * (0.065 + (1 - breakP) * 0.075);
      const sa = (0.45 + focus * 0.55) * (1 - breakP * 0.22);
      ctx.strokeStyle = i % 3 === 0
        ? `rgba(56, 189, 248, ${sa})`
        : sceneInk(dark, sa * 0.85);
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * d0, Math.sin(a) * d0);
      ctx.lineTo(Math.cos(a) * d1, Math.sin(a) * d1);
      ctx.stroke();
    }
  }

  if (focus > 0.08 && slashP > 0.02 && breakP < 0.92) {
    const ext = unit * 0.56 * slashP;
    const sA = Math.sin(clamp(slashP) * Math.PI) * (1 - breakP * 0.5) * focus;
    const sg = ctx.createLinearGradient(-ext, ext, ext, -ext);
    sg.addColorStop(0, sceneInk(dark, 0));
    sg.addColorStop(0.5, `rgba(56,189,248,${0.95 * sA})`);
    sg.addColorStop(1, sceneInk(dark, 0));
    ctx.strokeStyle = sg;
    ctx.lineWidth = 2.6;
    ctx.beginPath();
    ctx.moveTo(-ext, ext);
    ctx.lineTo(ext, -ext);
    ctx.stroke();
  }

  drawGrokMark(ctx, unit * (0.2 + focus * 0.032), t, focus, dark);
  ctx.restore();
}

// 8. Meta【国外开源】—— 开源权重自由 Fork 裂变生态树
function drawMetaScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const gen1 = focus > 0.08 ? smoothstep(0.06, 0.38, show) : 0.9;
  const gen2 = focus > 0.08 ? smoothstep(0.28, 0.66, show) : 0.8;
  const gen3 = focus > 0.08 ? smoothstep(0.52, 0.92, show) : 0.65;
  const vis = 0.42 + focus * 0.58;

  ctx.save();
  for (const dir of [-1, 1] as const) {
    const rootX = cx + dir * unit * 0.21;
    const rootY = cy;
    const lvl1 = [
      { x: cx + dir * w * 0.25, y: cy - h * 0.18 },
      { x: cx + dir * w * 0.25, y: cy + h * 0.18 },
    ];
    for (const p of lvl1) {
      const px = lerp(rootX, p.x, gen1);
      const py = lerp(rootY, p.y, gen1);
      ctx.strokeStyle = `rgba(0, 100, 224, ${0.72 * vis})`;
      ctx.lineWidth = 1.7;
      ctx.beginPath();
      ctx.moveTo(rootX, rootY);
      ctx.quadraticCurveTo((rootX + px) * 0.5, py, px, py);
      ctx.stroke();

      if (gen1 > 0.3) {
        ctx.beginPath();
        ctx.arc(px, py, unit * 0.019, 0, TAU);
        ctx.fillStyle = "#0064E0";
        ctx.fill();
      }

      if (gen2 > 0.02) {
        for (const oy of [-0.14, 0.14] as const) {
          const p2x = cx + dir * w * 0.39;
          const p2y = p.y + oy * h;
          const c2x = lerp(p.x, p2x, gen2);
          const c2y = lerp(p.y, p2y, gen2);
          ctx.strokeStyle = `rgba(0, 130, 251, ${0.55 * vis * gen2})`;
          ctx.lineWidth = 1.3;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.quadraticCurveTo((p.x + c2x) * 0.5, c2y, c2x, c2y);
          ctx.stroke();

          if (gen2 > 0.45) {
            ctx.beginPath();
            ctx.arc(c2x, c2y, unit * 0.015, 0, TAU);
            ctx.fillStyle = "#0EA5E9";
            ctx.fill();
          }

          if (gen3 > 0.1 && focus > 0.1) {
            ctx.beginPath();
            ctx.arc(p2x, p2y, unit * (0.016 + gen3 * 0.032), 0, TAU);
            ctx.strokeStyle = `rgba(56, 189, 248, ${(1 - gen3) * 0.72 * focus})`;
            ctx.lineWidth = 1.2;
            ctx.stroke();
          }
        }
      }
    }
  }
  ctx.restore();

  ctx.save();
  ctx.translate(cx, cy);
  drawMetaMark(ctx, unit * (0.205 + focus * 0.032), t, focus);
  ctx.restore();
}

// 9. GLM / 智谱【国内编程扎实落地】—— 左侧终端经 Z 引擎编排构建右侧模块矩阵
function drawGlmScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  focus: number,
  show: number,
  dark = false,
): void {
  const cx = w * 0.5;
  const cy = h * 0.5;
  const unit = Math.min(w, h);
  const typeP = focus > 0.08 ? smoothstep(0.05, 0.55, show) : 0.8;
  const buildP = focus > 0.08 ? smoothstep(0.32, 0.92, show) : 0.85;
  const vis = 0.42 + focus * 0.58;

  ctx.save();
  const termX = w * 0.095;
  const termW = w * 0.255;
  const termH = h * 0.44;
  const termY = cy - termH * 0.5;
  roundedRectPath(ctx, termX, termY, termW, termH, 7);
  ctx.fillStyle = sceneInk(dark, dark ? 0.07 + focus * 0.05 : 0.045 + focus * 0.04);
  ctx.fill();
  ctx.strokeStyle = sceneInk(dark, (dark ? 0.26 : 0.22) * vis);
  ctx.lineWidth = 1.2;
  ctx.stroke();

  const px0 = termX + termW * 0.14;
  const py0 = termY + termH * 0.28;
  ctx.strokeStyle = `rgba(14, 165, 233, ${0.9 * vis})`;
  ctx.lineWidth = 1.8;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(px0, py0 - 4);
  ctx.lineTo(px0 + 5, py0);
  ctx.lineTo(px0, py0 + 4);
  ctx.stroke();

  const cmdLens = [0.46, 0.62, 0.38];
  for (let i = 0; i < 3; i++) {
    const lp = clamp(typeP * 2.2 - i * 0.45);
    if (lp <= 0.01) continue;
    const ly = py0 + i * (termH * 0.22);
    const lx = i === 0 ? px0 + 10 : px0;
    ctx.strokeStyle = i === 0
      ? sceneInk(dark, 0.72 * vis)
      : `rgba(56, 189, 248, ${0.68 * vis})`;
    ctx.lineWidth = 2.0;
    ctx.beginPath();
    ctx.moveTo(lx, ly);
    ctx.lineTo(lx + termW * cmdLens[i] * lp, ly);
    ctx.stroke();
  }

  ctx.strokeStyle = `rgba(14, 165, 233, ${0.38 * vis})`;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(termX + termW + 4, cy);
  ctx.lineTo(w * 0.65, cy);
  ctx.stroke();
  ctx.setLineDash([]);

  if (focus > 0.08) {
    const pulseX = lerp(termX + termW + 4, w * 0.65, (t * 1.45) % 1);
    ctx.beginPath();
    ctx.arc(pulseX, cy, 2.8, 0, TAU);
    ctx.fillStyle = "#0EA5E9";
    ctx.fill();
  }

  const modX = w * 0.665;
  const modCellW = w * 0.098;
  const modCellH = h * 0.115;
  const modGap = 6;
  const modTotalH = 3 * modCellH + 2 * modGap;
  const modStartY = cy - modTotalH * 0.5;

  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 2; c++) {
      const idx = r * 2 + c;
      const mp = easeSpringOut(clamp(buildP * 2.1 - idx * 0.18));
      if (mp <= 0.01) continue;
      const bx = modX + c * (modCellW + modGap);
      const by = modStartY + r * (modCellH + modGap);
      ctx.save();
      ctx.translate(bx + modCellW * 0.5, by + modCellH * 0.5);
      ctx.scale(mp, mp);
      roundedRectPath(ctx, -modCellW * 0.5, -modCellH * 0.5, modCellW, modCellH, 4.5);
      const isReady = mp > 0.72;
      ctx.fillStyle = isReady
        ? `rgba(20, 184, 166, ${(0.18 + focus * 0.16) * vis})`
        : `rgba(14, 165, 233, ${0.14 * vis})`;
      ctx.fill();
      ctx.strokeStyle = isReady
        ? `rgba(20, 184, 166, ${0.82 * vis})`
        : `rgba(14, 165, 233, ${0.65 * vis})`;
      ctx.lineWidth = 1.3;
      ctx.stroke();
      ctx.restore();
    }
  }
  ctx.restore();

  ctx.save();
  ctx.translate(cx, cy);
  drawGlmMark(ctx, unit * (0.18 + focus * 0.03), t, focus, dark);
  ctx.restore();
}

export const VENDORS: readonly VendorSpec[] = [
  {
    id: "gemini",
    primary: [66, 133, 244],
    secondary: [234, 67, 53],
    accent: [52, 168, 83],
    trail: {
      head: [66, 133, 244],
      mid: [52, 168, 83],
      tail: [234, 67, 53],
      spectrum: [
        [66, 133, 244],
        [52, 168, 83],
        [251, 188, 4],
        [234, 67, 53],
      ],
    },
    studio: {
      bgCenter: [247, 250, 255],
      bgEdge: [228, 236, 249],
      haloA: [66, 133, 244],
      haloB: [251, 188, 4],
      haloAlpha: 0.16,
    },
    fieldMode: "glint",
    drawMark: drawGeminiMark,
    drawScene: drawGeminiScene,
  },
  {
    id: "openai",
    primary: [17, 24, 39],
    secondary: [16, 163, 127],
    accent: [16, 185, 129],
    trail: {
      head: [16, 163, 127],
      mid: [52, 211, 153],
      tail: [17, 24, 39],
    },
    studio: {
      bgCenter: [246, 252, 249],
      bgEdge: [225, 240, 234],
      haloA: [16, 163, 127],
      haloB: [15, 23, 42],
      haloAlpha: 0.15,
    },
    fieldMode: "bloom",
    drawMark: drawOpenAIMark,
    drawScene: drawOpenAIScene,
  },
  {
    id: "claude",
    primary: [217, 119, 87],
    secondary: [194, 92, 62],
    accent: [238, 158, 120],
    trail: {
      head: [217, 119, 87],
      mid: [245, 158, 11],
      tail: [234, 88, 12],
    },
    studio: {
      bgCenter: [253, 247, 242],
      bgEdge: [241, 226, 214],
      haloA: [217, 119, 87],
      haloB: [245, 178, 132],
      haloAlpha: 0.19,
    },
    fieldMode: "compose",
    drawMark: drawClaudeMark,
    drawScene: drawClaudeScene,
  },
  {
    id: "kimi",
    primary: [15, 23, 42],
    secondary: [22, 119, 255],
    accent: [56, 189, 248],
    trail: {
      head: [22, 119, 255],
      mid: [56, 189, 248],
      tail: [15, 23, 42],
    },
    studio: {
      bgCenter: [244, 248, 255],
      bgEdge: [221, 231, 247],
      haloA: [22, 119, 255],
      haloB: [15, 23, 42],
      haloAlpha: 0.17,
    },
    fieldMode: "double_arc",
    drawMark: drawKimiMark,
    drawScene: drawKimiScene,
  },
  {
    id: "deepseek",
    primary: [77, 107, 254],
    secondary: [37, 99, 235],
    accent: [14, 165, 233],
    trail: {
      head: [77, 107, 254],
      mid: [37, 99, 235],
      tail: [6, 182, 212],
    },
    studio: {
      bgCenter: [242, 247, 255],
      bgEdge: [216, 229, 252],
      haloA: [77, 107, 254],
      haloB: [14, 165, 233],
      haloAlpha: 0.19,
    },
    fieldMode: "align",
    drawMark: drawDeepSeekMark,
    drawScene: drawDeepSeekScene,
  },
  {
    id: "qwen",
    primary: [10, 62, 255],
    secondary: [99, 102, 241],
    accent: [168, 85, 247],
    trail: {
      head: [10, 62, 255],
      mid: [124, 58, 237],
      tail: [168, 85, 247],
    },
    studio: {
      bgCenter: [245, 247, 255],
      bgEdge: [224, 230, 250],
      haloA: [10, 62, 255],
      haloB: [99, 102, 241],
      haloAlpha: 0.16,
    },
    fieldMode: "lattice",
    drawMark: drawQwenMark,
    drawScene: drawQwenScene,
  },
  {
    id: "grok",
    primary: [15, 23, 42],
    secondary: [71, 85, 105],
    accent: [14, 165, 233],
    trail: {
      head: [15, 23, 42],
      mid: [71, 85, 105],
      tail: [148, 163, 184],
    },
    studio: {
      bgCenter: [243, 244, 247],
      bgEdge: [213, 217, 226],
      haloA: [15, 23, 42],
      haloB: [100, 116, 139],
      haloAlpha: 0.17,
    },
    fieldMode: "slash",
    drawMark: drawGrokMark,
    drawScene: drawGrokScene,
  },
  {
    id: "meta",
    primary: [0, 100, 224],
    secondary: [0, 130, 251],
    accent: [56, 189, 248],
    trail: {
      head: [0, 100, 224],
      mid: [14, 165, 233],
      tail: [125, 211, 252],
    },
    studio: {
      bgCenter: [243, 249, 255],
      bgEdge: [218, 235, 252],
      haloA: [0, 100, 224],
      haloB: [56, 189, 248],
      haloAlpha: 0.17,
    },
    fieldMode: "flow",
    drawMark: drawMetaMark,
    drawScene: drawMetaScene,
  },
  {
    id: "glm",
    primary: [17, 24, 39],
    secondary: [14, 165, 233],
    accent: [20, 184, 166],
    trail: {
      head: [13, 148, 136],
      mid: [20, 184, 166],
      tail: [14, 165, 233],
    },
    studio: {
      bgCenter: [244, 249, 250],
      bgEdge: [220, 234, 238],
      haloA: [14, 165, 233],
      haloB: [20, 184, 166],
      haloAlpha: 0.16,
    },
    fieldMode: "circuit",
    drawMark: drawGlmMark,
    drawScene: drawGlmScene,
  },
];

export function findVendorSpec(id: string | null | undefined): VendorSpec | null {
  if (!id) return null;
  return VENDORS.find((v) => v.id === id) ?? null;
}
