import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { effortColor, type EffortAppearance } from "../effortAppearance";
import { useTheme } from "../theme";
import type { EffortVisual } from "../types";
import {
  createDeepSpaceStars,
  createForegroundBokehStreaks,
  createGalaxyRingStars,
  createWormholeStars,
  getCosmicSpriteAtlas,
  stampBokehStreak,
  stampCosmicStar,
  stampCosmicStreak,
} from "./cosmicStarfield";
import {
  TAU,
  TRAIL_MOTES,
  VENDORS,
  clamp,
  easeOutBack,
  lerp,
  mixRgb,
  resolveVendorTrail,
  rgba,
  sampleTrailColor,
  smoothstep,
  type RgbColor,
  type VendorId,
  type VendorSpec,
} from "./intro/vendorCatalog";

export const WELCOME_WARP_DURATION = 1080;
const WARP_Z_TRAVEL = 3.85;

// 阶段 1：短促引力蓄压咬合（0 ~ 145ms），从 0 丝滑抬压到峰值再回落至 0，衔接待机零跳变
function warpIntakePulse(p: number): number {
  const t = clamp(p / 0.135);
  if (t <= 0 || t >= 1) return 0;
  return Math.pow(Math.sin(t * Math.PI), 1.15);
}

// 阶段 2 & 3：厚重推背加速曲线（先慢起破惯性 → 180~440ms 加速度持续攀升至峰值 → 破壁惯性滑出）
function warpForwardProgress(p: number): number {
  const u = clamp((p - 0.075) / 0.925);
  if (u <= 0) return 0;
  if (u >= 1) return 1;
  const a = Math.pow(u, 2.65);
  const b = 0.11 * Math.pow(1 - u, 1.95);
  return a / (a + b);
}

// 复合相机深度推进：蓄压微吸（-0.18）无缝滑入深层持续加压推进（+3.85）
function warpDepthAt(p: number): number {
  return -0.18 * warpIntakePulse(p) + warpForwardProgress(p) * WARP_Z_TRAVEL;
}

// 近似瞬时推进速率（归一化 0..1），用于驱动加速度峰值时的光流能量攀升
function warpSpeedNorm(p: number): number {
  const eps = 0.015;
  const v = (warpForwardProgress(p + eps) - warpForwardProgress(Math.max(0, p - eps))) / (2 * eps);
  return clamp(v / 2.85, 0, 1);
}

interface ProjectedWarpRay {
  tailX: number;
  tailY: number;
  headX: number;
  headY: number;
  headScale: number;
  visibility: number;
}

// 统一 3D 透视投影：t=0 时 head/tail 均与初始像素坐标精确重合（零跳变），随推进拉出真实远近差与径向长尾
function projectWormholeRay(
  dx0: number,
  dy0: number,
  z0: number,
  dzHead: number,
  dzTail: number,
  centerX: number,
  centerY: number,
  width: number,
  height: number,
  maxRayReach: number,
  clearWave: number,
): ProjectedWarpRay | null {
  const zTail = z0 - dzTail;
  if (zTail <= 0.07) return null;

  const baseDist = Math.hypot(dx0, dy0);
  if (baseDist < 0.5) return null;

  const mTail = z0 / zTail;
  const zHead = z0 - dzHead;
  // 精确求射线与外扩 72px 视口边界的交点倍数，让冲出画框的近景星头恰好落在边外，保证亮尾饱满扫过屏缘
  const edgeX = dx0 > 1e-4 ? (width + 72 - centerX) / dx0 : dx0 < -1e-4 ? (-72 - centerX) / dx0 : Infinity;
  const edgeY = dy0 > 1e-4 ? (height + 72 - centerY) / dy0 : dy0 < -1e-4 ? (-72 - centerY) / dy0 : Infinity;
  const edgeScale = Math.min(edgeX, edgeY, maxRayReach / baseDist);
  const maxScale = Math.max(mTail + 0.01, edgeScale * 1.12);
  const rawHeadScale = zHead > 0.05 ? z0 / zHead : maxScale;
  const mHead = Math.min(rawHeadScale, maxScale);

  const tailX = centerX + dx0 * mTail;
  const tailY = centerY + dy0 * mTail;
  const headX = centerX + dx0 * mHead;
  const headY = centerY + dy0 * mHead;

  // 线段 AABB 视口裁剪：只要尾迹仍有部分横跨屏幕就保留绘制，完全出框即剔除
  const minX = tailX < headX ? tailX : headX;
  const maxX = tailX > headX ? tailX : headX;
  const minY = tailY < headY ? tailY : headY;
  const maxY = tailY > headY ? tailY : headY;
  if (maxX < -36 || minX > width + 36 || maxY < -36 || minY > height + 36) {
    return null;
  }

  // 中央清场走廊：首条消息开始上浮后由中心向外优先淡出滞留远星，保证正文背后通透干净
  let visibility = 1;
  if (clearWave > 0.001) {
    const normTailR = Math.hypot(
      (tailX - centerX) / Math.max(width * 0.48, 1),
      (tailY - centerY) / Math.max(height * 0.48, 1),
    );
    const clearRadius = clearWave * 0.98;
    const corridor = smoothstep(clearRadius - 0.34, clearRadius + 0.18, normTailR);
    visibility = lerp(1, corridor, clearWave);
    if (visibility <= 0.015) return null;
  }

  return {
    tailX,
    tailY,
    headX,
    headY,
    headScale: clamp(mHead, 1, 3.3),
    visibility,
  };
}

const EFFECT_COLORS: Record<EffortVisual["effect"], RgbColor> = {
  glint: [184, 100, 145],
  bloom: [16, 163, 127],
  align: [152, 93, 170],
  double_arc: [191, 120, 153],
  lattice: [99, 102, 241],
  circuit: [20, 184, 166],
};

interface WelcomeParticleFieldProps {
  composerRef: RefObject<HTMLDivElement | null>;
  warpOrigin: DOMRectReadOnly | null;
  appearance: EffortAppearance;
  modelKey: string;
  visual: EffortVisual | null;
  entry: number | null;
  activeProvider?: string;
  effortIntensity?: number;
  inspectedVendorId?: VendorId | null;
  planetsVisible?: boolean;
  revealKey?: number;
  onSelectVendor?: (vendorId: VendorId) => void;
}

interface PlanetOrbitState {
  index: number;
  vendor: VendorSpec;
  angle: number;
  sx: number;
  sy: number;
  tx: number;
  ty: number;
  depth: number;
  stagger: number;
  visible: number;
  isSelected: boolean;
  isHovered: boolean;
  isActiveProvider: boolean;
  activeWeight: number;
  effortBoost: number;
  dirSign: number;
  speedExtra: number;
  orbitRx: number;
  orbitRy: number;
  cosT: number;
  sinT: number;
  anchorX: number;
  anchorY: number;
}

function createParticles() {
  // 每格放一个带随机偏移的粒子，背景自然稀疏，环带也有足够的采样点。
  const columns = 46;
  const rows = 24;
  return Array.from({ length: columns * rows }, (_, index) => ({
    x: (((index % columns) + Math.random()) / columns - 0.5) * 2.64,
    y: ((Math.floor(index / columns) + Math.random()) / rows - 0.5) * 2.82,
    seed: Math.random(),
    phase: Math.random() * TAU,
  }));
}

function arcLight(angle: number, center: number, width: number): number {
  const distance = Math.abs(angle - center);
  const offset = Math.min(distance, TAU - distance);
  return Math.exp(-((offset / width) ** 2));
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      "textarea, input, button, a, [role='dialog'], [role='group'], .overflow-y-auto",
    ),
  );
}

interface WarpPlanetSnapshot {
  index: number;
  sx: number;
  sy: number;
  tx: number;
  ty: number;
  depth: number;
  isActive: boolean;
}

function quadBezierPoint(
  x0: number,
  y0: number,
  cx: number,
  cy: number,
  x1: number,
  y1: number,
  t: number,
) {
  const mt = 1 - t;
  return {
    x: mt * mt * x0 + 2 * mt * t * cx + t * t * x1,
    y: mt * mt * y0 + 2 * mt * t * cy + t * t * y1,
  };
}

// 轻微超椭圆映射（exp=0.84）：视觉仍是丝滑椭圆，但四角自然外舒以彻底避开居中输入框圆角
function superEllipsePoint(angle: number, rx: number, ry: number, exp = 0.84) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const lx = Math.sign(c) * Math.pow(Math.abs(c), exp) * rx;
  const ly = Math.sign(s) * Math.pow(Math.abs(s), exp) * ry;
  return { lx, ly };
}

function superEllipseTangent(angle: number, rx: number, ry: number, dirSign: number, exp = 0.84) {
  const eps = 0.015;
  const p0 = superEllipsePoint(angle - eps * dirSign, rx, ry, exp);
  const p1 = superEllipsePoint(angle + eps * dirSign, rx, ry, exp);
  return { dlx: p1.lx - p0.lx, dly: p1.ly - p0.ly };
}

export function WelcomeParticleField({
  composerRef,
  warpOrigin,
  appearance,
  modelKey,
  visual,
  entry,
  activeProvider = "",
  effortIntensity = 0.5,
  inspectedVendorId = null,
  planetsVisible = true,
  revealKey = 0,
  onSelectVendor,
}: WelcomeParticleFieldProps) {
  const { isDark } = useTheme();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const badgeButtonsRef = useRef<(HTMLButtonElement | null)[]>([]);
  const badgeCanvasesRef = useRef<(HTMLCanvasElement | null)[]>([]);
  const hoveredIdxRef = useRef<number>(-1);
  const onSelectVendorRef = useRef(onSelectVendor);
  onSelectVendorRef.current = onSelectVendor;

  const sceneRef = useRef({
    warpOrigin,
    appearance,
    modelKey,
    visual,
    entry,
    activeProvider,
    effortIntensity,
    inspectedVendorId,
    planetsVisible,
    revealKey,
    isDark,
  });
  const requestPaintRef = useRef<(() => void) | null>(null);
  const paintNowRef = useRef<(() => void) | null>(null);
  const prevThemeRef = useRef(isDark);

  useLayoutEffect(() => {
    const themeChanged = prevThemeRef.current !== isDark;
    prevThemeRef.current = isDark;
    sceneRef.current = {
      warpOrigin,
      appearance,
      modelKey,
      visual,
      entry,
      activeProvider,
      effortIntensity,
      inspectedVendorId,
      planetsVisible,
      revealKey,
      isDark,
    };
    if (themeChanged) {
      // 主题切换瞬间同步重绘 Canvas，确保 View Transitions API 截帧即呈现新主题星海，零撕裂
      paintNowRef.current?.();
    } else {
      requestPaintRef.current?.();
    }
  }, [
    warpOrigin,
    appearance,
    modelKey,
    visual,
    entry,
    activeProvider,
    effortIntensity,
    inspectedVendorId,
    planetsVisible,
    revealKey,
    isDark,
  ]);

  // 同步绘制 9 枚高 DPI 官方微缩厂商标识（使用 useLayoutEffect 保证 View Transitions 截帧前即完成反白）
  useLayoutEffect(() => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    for (let i = 0; i < VENDORS.length; i++) {
      const cv = badgeCanvasesRef.current[i];
      if (!cv) continue;
      cv.width = Math.round(28 * dpr);
      cv.height = Math.round(28 * dpr);
      const c = cv.getContext("2d");
      if (!c) continue;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, 28, 28);
      c.translate(14, 14);
      VENDORS[i].drawMark(c, 11.4, 0, 0, isDark);
    }
  }, [isDark]);

  useEffect(() => {
    const element = canvasRef.current;
    const parent = element?.parentElement;
    const drawingContext = element?.getContext("2d");
    if (!element || !parent || !drawingContext) return;
    const canvas = element;
    const area = parent;
    const context = drawingContext;

    const particles = createParticles();
    const deepStars = createDeepSpaceStars(170);
    const galaxyStars = createGalaxyRingStars(1520);
    const wormholeStars = createWormholeStars(420);
    const bokehStreaks = createForegroundBokehStreaks(42);
    const starAtlas = getCosmicSpriteAtlas();
    const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const pointer = { x: 0, y: 0 };
    const follow = { x: 0, y: 0 };
    const tilt = -0.16;
    const tiltCos = Math.cos(tilt);
    const tiltSin = Math.sin(tilt);

    let frameId: number | null = null;
    let previousFrame = 0;
    let time = 0;
    let orbitTime = 0;
    let userOrbitOffset = 0;
    let userOrbitVel = 0;
    let isDragging = false;
    let lastDragX = 0;
    let activeWarp: DOMRectReadOnly | null = null;
    let warpStartedAt = 0;
    let warpFreezeTime = 0;
    let warpFreezeOrbitTime = 0;
    let warpFreezeUserOffset = 0;
    let warpFreezeFollowX = 0;
    let warpFreezeFollowY = 0;
    let warpSnapshots: WarpPlanetSnapshot[] | null = null;
    let lastOrbitSnapshots: WarpPlanetSnapshot[] = [];
    let warpPlanetStates: PlanetOrbitState[] | null = null;
    let lastPlanetStates: PlanetOrbitState[] = [];
    let displayedVisual = sceneRef.current.visual;
    let visualStrength = displayedVisual ? 1 : 0;
    let lastModelKey = sceneRef.current.modelKey;
    let lastEntry = sceneRef.current.entry;
    let lastRevealKey = sceneRef.current.revealKey;
    let lastActiveProvider = sceneRef.current.activeProvider;
    let lastTargetEffort = sceneRef.current.effortIntensity;
    let lastDarkMode = sceneRef.current.isDark;
    let darkAwakenAt = -Infinity;
    let smoothedEffort = sceneRef.current.effortIntensity;
    let surgeStartedAt = -Infinity;
    const activeWeights = VENDORS.map((v) =>
      v.id === sceneRef.current.activeProvider ? 1 : 0,
    );
    const lastBorderColors = new Array<string>(VENDORS.length).fill("");
    const lastBoxShadows = new Array<string>(VENDORS.length).fill("");
    let revealStartedAt = performance.now();
    let entryStartedAt = -Infinity;
    let breathAmplitude = sceneRef.current.appearance.breath;

    function scheduleFrame() {
      if (frameId === null && !document.hidden) {
        frameId = window.requestAnimationFrame(draw);
      }
    }

    function hideAllBadges() {
      for (let i = 0; i < VENDORS.length; i++) {
        const btn = badgeButtonsRef.current[i];
        if (!btn) continue;
        btn.style.opacity = "0";
        btn.style.pointerEvents = "none";
      }
    }

    function drawPlanetTrails(
      ctx: CanvasRenderingContext2D,
      states: PlanetOrbitState[],
      warpFade: number,
      surgeP: number,
      dark: boolean,
    ) {
      ctx.save();
      ctx.lineCap = "round";

      for (const b of states) {
        if (b.visible <= 0.02) continue;
        const trail = resolveVendorTrail(b.vendor, dark);
        const actBoost = b.activeWeight * (0.28 + b.effortBoost * 0.62);
        const boost = (b.isHovered || b.isSelected ? 1.32 : 1.0) + actBoost;
        const depthAlpha = lerp(0.72, 1.0, b.depth) * b.visible * warpFade;
        // 选中厂商增大推理强度时，专属星尘彗尾丝滑拉长至最高 2 倍以上！
        const effortTailExtra = b.activeWeight * (0.08 + b.effortBoost * 0.42);
        const arcSpan = (0.33 + b.speedExtra + effortTailExtra) * (b.isHovered ? 1.18 : 1.0);
        const segs = 22;

        const pts: { x: number; y: number; u: number }[] = [];
        for (let s = 0; s <= segs; s++) {
          const u = s / segs;
          const a = b.angle - b.dirSign * u * arcSpan;
          const { lx, ly } = superEllipsePoint(a, b.orbitRx, b.orbitRy);
          pts.push({
            x: b.anchorX + lx * b.cosT - ly * b.sinT,
            y: b.anchorY + lx * b.sinT + ly * b.cosT,
            u,
          });
        }

        for (let s = 0; s < segs; s++) {
          const p0 = pts[s];
          const p1 = pts[s + 1];
          const uMid = (p0.u + p1.u) * 0.5;
          const fade = Math.pow(1 - uMid, 1.85);
          const col = sampleTrailColor(trail, uMid);

          ctx.strokeStyle = rgba(col, clamp(0.15 * fade * depthAlpha * boost, 0, 0.55));
          ctx.lineWidth =
            (11.0 + b.activeWeight * b.effortBoost * 6.5) *
            (1 - uMid * 0.72) *
            lerp(0.85, 1.08, b.depth);
          ctx.beginPath();
          ctx.moveTo(p0.x, p0.y);
          ctx.lineTo(p1.x, p1.y);
          ctx.stroke();

          ctx.strokeStyle = rgba(col, clamp(0.56 * fade * depthAlpha * boost, 0, 0.94));
          ctx.lineWidth =
            (2.4 + b.activeWeight * b.effortBoost * 1.45) *
            (1 - uMid * 0.78) *
            lerp(0.88, 1.12, b.depth);
          ctx.beginPath();
          ctx.moveTo(p0.x, p0.y);
          ctx.lineTo(p1.x, p1.y);
          ctx.stroke();
        }

        for (const mote of TRAIL_MOTES) {
          const u = (mote.u + orbitTime * (0.28 + b.activeWeight * b.effortBoost * 0.24)) % 1;
          const a = b.angle - b.dirSign * (0.04 + u * arcSpan * 1.16);
          const { lx, ly } = superEllipsePoint(a, b.orbitRx, b.orbitRy);
          const bx = b.anchorX + lx * b.cosT - ly * b.sinT;
          const by = b.anchorY + lx * b.sinT + ly * b.cosT;

          const { dlx, dly } = superEllipseTangent(a, b.orbitRx, b.orbitRy, b.dirSign);
          const tx0 = dlx * b.cosT - dly * b.sinT;
          const ty0 = dlx * b.sinT + dly * b.cosT;
          const tLen = Math.hypot(tx0, ty0) || 1;
          const tx = tx0 / tLen;
          const ty = ty0 / tLen;
          const nx = -ty;
          const ny = tx;

          const drift =
            mote.spread * (6 + u * (15 + b.activeWeight * b.effortBoost * 8)) +
            Math.sin(orbitTime * 2.4 + mote.phase) * 2.4 * u;
          const mx = bx + nx * drift;
          const my = by + ny * drift;
          const tailLen =
            (2.3 + (1 - u) * 5.0 + b.speedExtra * 8 + b.activeWeight * b.effortBoost * 4.5) *
            mote.size;
          const fade = Math.pow(1 - u, 1.32) * smoothstep(0.0, 0.12, u);
          const col = sampleTrailColor(trail, u);

          ctx.strokeStyle = rgba(col, clamp(0.82 * fade * depthAlpha * boost, 0, 0.98));
          ctx.lineWidth = (0.92 + (1 - u) * 1.08 + b.activeWeight * b.effortBoost * 0.45) * mote.size;
          ctx.beginPath();
          ctx.moveTo(mx - tx * tailLen * 0.5, my - ty * tailLen * 0.5);
          ctx.lineTo(mx + tx * tailLen * 0.5, my + ty * tailLen * 0.5);
          ctx.stroke();
        }

        // 柔和星光底座晕（当前选中厂商+高推理强度时显著扩宽发光）
        const haloR =
          (b.isHovered || b.isSelected
            ? 46
            : 36 + b.activeWeight * (14 + b.effortBoost * 24)) *
          lerp(0.88, 1.08, b.depth);
        const hg = ctx.createRadialGradient(b.sx, b.sy, 10, b.sx, b.sy, haloR);
        hg.addColorStop(0, rgba(trail.head, clamp((0.24 + b.activeWeight * (0.14 + b.effortBoost * 0.16)) * depthAlpha * boost, 0, 0.56)));
        hg.addColorStop(0.52, rgba(trail.mid, clamp((0.1 + b.activeWeight * (0.08 + b.effortBoost * 0.12)) * depthAlpha * boost, 0, 0.34)));
        hg.addColorStop(1, rgba(trail.tail, 0));
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(b.sx, b.sy, haloR, 0, TAU);
        ctx.fill();

        // 当前选中厂商的「推理强度强调粒子层」：脉动能量波纹 + 贴身高速环绕星火 + 充能冲击波
        if (b.activeWeight > 0.04) {
          const aw = b.activeWeight * depthAlpha;
          const ef = b.effortBoost;

          // (1) 持续向外荡漾的双层专属色呼吸能量环（暗夜模式放慢节奏降低眼部刺激）
          const ringCount = ef > 0.38 ? 2 : 1;
          const pulseRate = dark ? 0.22 + ef * 0.2 : 0.42 + ef * 0.58;
          for (let k = 0; k < ringCount; k++) {
            const p = (orbitTime * pulseRate + k / ringCount) % 1;
            const rr = 25 + p * (18 + ef * (dark ? 24 : 32));
            const ringCol = sampleTrailColor(trail, p);
            const ringAlpha = Math.sin(p * Math.PI) * ((dark ? 0.2 : 0.28) + ef * (dark ? 0.28 : 0.48)) * aw;
            ctx.strokeStyle = rgba(ringCol, clamp(ringAlpha, 0, dark ? 0.52 : 0.8));
            ctx.lineWidth = 1.15 + (1 - p) * (0.7 + ef * (dark ? 0.75 : 1.1));
            ctx.beginPath();
            ctx.arc(b.sx, b.sy, rr, 0, TAU);
            ctx.stroke();
          }

          // (2) 增大推理强度或切换厂商瞬间爆发的「充能冲击波」
          if (surgeP > 0 && surgeP < 1) {
            const sr = 24 + (1 - Math.pow(1 - surgeP, 3)) * (48 + ef * 34);
            const sa = (1 - surgeP) * (0.65 + ef * 0.3) * aw;
            const sCol = sampleTrailColor(trail, surgeP * 0.6);
            ctx.strokeStyle = rgba(sCol, clamp(sa, 0, 0.92));
            ctx.lineWidth = 2.4 * (1 - surgeP * 0.55);
            ctx.beginPath();
            ctx.arc(b.sx, b.sy, sr, 0, TAU);
            ctx.stroke();
          }

          // (3) 随推理强度递增的高能贴身环绕星火微粒（off=0 颗，low~max 渐增至 12 颗，每家各自色系！）
          if (ef > 0.06) {
            const sparkCount = Math.round(3 + ef * 9);
            const spinSpeed = dark ? 0.95 + ef * 1.05 : 1.35 + ef * 2.15;
            for (let si = 0; si < sparkCount; si++) {
              const frac = si / sparkCount;
              const isOuter = si % 2 === 1 && ef > 0.48;
              const a =
                orbitTime * (isOuter ? -spinSpeed * 0.78 : spinSpeed) + frac * TAU;
              const orbR = (isOuter ? 38 + ef * 12 : 30 + ef * 6) + Math.sin(orbitTime * (dark ? 1.8 : 3.4) + si * 1.7) * 2.0;
              const ex = b.sx + Math.cos(a) * orbR;
              const ey = b.sy + Math.sin(a) * orbR * (isOuter ? 0.82 : 0.92);
              const tang = a + (isOuter ? -Math.PI * 0.5 : Math.PI * 0.5);
              const sparkLen = (2.8 + ef * 5.2) * (isOuter ? 0.85 : 1.05);
              const sCol = sampleTrailColor(trail, frac);
              const twinkle = dark
                ? 0.68 + 0.32 * Math.sin(orbitTime * 2.4 + si * 2.1)
                : 0.55 + 0.45 * Math.sin(orbitTime * 5.2 + si * 2.1);

              ctx.strokeStyle = rgba(sCol, clamp((0.45 + ef * 0.5) * twinkle * aw, 0, 0.98));
              ctx.lineWidth = 1.25 + ef * 1.15;
              ctx.beginPath();
              ctx.moveTo(ex - Math.cos(tang) * sparkLen * 0.5, ey - Math.sin(tang) * sparkLen * 0.5);
              ctx.lineTo(ex + Math.cos(tang) * sparkLen * 0.5, ey + Math.sin(tang) * sparkLen * 0.5);
              ctx.stroke();

              // 高推理档位 (high / xhigh / max) 附加微小星芒高光核
              if (ef > 0.55 && si % 2 === 0) {
                ctx.beginPath();
                ctx.arc(ex, ey, 1.3 + ef * 0.9, 0, TAU);
                ctx.fillStyle = rgba(sCol, clamp(0.92 * twinkle * aw, 0, 1));
                ctx.fill();
              }
            }
          }
        }
      }
      ctx.restore();
    }

    function draw(now: number) {
      frameId = null;
      const bounds = canvas.getBoundingClientRect();
      const { width, height } = bounds;
      if (width <= 0 || height <= 0) return;

      const ratio = Math.min(window.devicePixelRatio || 1, 1.5, 2000 / Math.max(width, height));
      const pixelWidth = Math.max(1, Math.round(width * ratio));
      const pixelHeight = Math.max(1, Math.round(height * ratio));
      if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
      if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
      context.setTransform(pixelWidth / width, 0, 0, pixelHeight / height, 0, 0);
      context.clearRect(0, 0, width, height);

      const delta = previousFrame ? Math.min(now - previousFrame, 40) : 0;
      previousFrame = now;
      const dt = delta / 1000;
      const reducedMotion = motionPreference.matches;
      const {
        warpOrigin: nextWarp,
        appearance: palette,
        modelKey: nextModelKey,
        visual: nextVisual,
        entry: nextEntry,
        activeProvider: curProvider,
        effortIntensity: targetEffort,
        inspectedVendorId: curInspected,
        planetsVisible: showPlanetsFlag,
        revealKey: curRevealKey,
        isDark: dark,
      } = sceneRef.current;

      if (curRevealKey !== lastRevealKey) {
        lastRevealKey = curRevealKey;
        revealStartedAt = now;
      }

      // 由白天切入暗夜瞬间立即开启星光微苏醒（全程保持实时连续运行，绝不暂停）
      if (dark && !lastDarkMode && !reducedMotion) {
        darkAwakenAt = now;
      } else if (!dark) {
        darkAwakenAt = -Infinity;
      }
      lastDarkMode = dark;

      // 切换厂商或大幅提高推理强度时，立刻触发一圈清脆的充能冲击波
      if (
        !reducedMotion &&
        (curProvider !== lastActiveProvider || targetEffort - lastTargetEffort > 0.08)
      ) {
        surgeStartedAt = now;
      }
      lastActiveProvider = curProvider;
      lastTargetEffort = targetEffort;

      const smoothFactor = reducedMotion ? 1 : 1 - Math.exp(-delta / 130);
      smoothedEffort += (clamp(targetEffort) - smoothedEffort) * smoothFactor;
      for (let i = 0; i < VENDORS.length; i++) {
        const targetW = VENDORS[i].id === curProvider ? 1 : 0;
        activeWeights[i] += (targetW - activeWeights[i]) * smoothFactor;
      }
      const surgeProgress = reducedMotion ? 1 : clamp((now - surgeStartedAt) / 760);

      if (!reducedMotion) {
        time += (delta * palette.drift) / 1000;
        orbitTime += dt;
        if (!isDragging) {
          userOrbitOffset += userOrbitVel * dt;
          userOrbitVel *= Math.exp(-dt * 4.2);
          if (Math.abs(userOrbitVel) < 0.001) userOrbitVel = 0;
        }
      }
      const motionTime = reducedMotion ? 0 : time;

      if (activeWarp !== nextWarp) {
        activeWarp = nextWarp;
        warpStartedAt = now;
        if (nextWarp) {
          // 冻结跃迁起手瞬间的星图相位与鼠标视差，确保每颗星从原坐标沿直线射出，零首帧跳变
          warpFreezeTime = motionTime;
          warpFreezeOrbitTime = orbitTime;
          warpFreezeUserOffset = userOrbitOffset;
          warpFreezeFollowX = follow.x;
          warpFreezeFollowY = follow.y;
          // 捕获发送瞬间 9 颗行星各自的实时轨道坐标、切线与彗尾状态，确保待机到蓄压零跳变衔接
          warpSnapshots =
            lastOrbitSnapshots.length === VENDORS.length
              ? lastOrbitSnapshots.map((item) => ({
                  ...item,
                  isActive: VENDORS[item.index].id === curProvider,
                }))
              : null;
          warpPlanetStates =
            lastPlanetStates.length === VENDORS.length
              ? lastPlanetStates.map((st) => ({ ...st }))
              : null;
        } else {
          warpSnapshots = null;
          warpPlanetStates = null;
        }
      }
      const paletteEasing = 1 - Math.exp(-delta / 145);
      const newEntry = nextEntry !== lastEntry;
      if (
        nextModelKey !== lastModelKey ||
        activeWarp ||
        reducedMotion ||
        (newEntry && nextEntry === null)
      ) {
        displayedVisual = nextVisual;
        visualStrength = nextVisual ? 1 : 0;
        entryStartedAt = -Infinity;
      } else if (nextVisual && (newEntry || nextVisual !== displayedVisual)) {
        displayedVisual = nextVisual;
        entryStartedAt = newEntry ? now : -Infinity;
        visualStrength = newEntry ? 0 : 1;
      }
      lastModelKey = nextModelKey;
      lastEntry = nextEntry;
      visualStrength += ((nextVisual ? 1 : 0) - visualStrength) * paletteEasing;
      breathAmplitude += (palette.breath - breathAmplitude) * paletteEasing;

      const progress = activeWarp ? (now - warpStartedAt) / WELCOME_WARP_DURATION : 0;
      if (progress >= 1 || (activeWarp && reducedMotion)) {
        hideAllBadges();
        return;
      }

      const anchor = activeWarp ?? composerRef.current?.getBoundingClientRect();
      const centerX = anchor ? anchor.left + anchor.width / 2 - bounds.left : width / 2;
      const centerY = anchor ? anchor.top + anchor.height / 2 - bounds.top : height * 0.49;
      const radiusX = width * 0.43;
      const radiusY = Math.min(height * 0.39, Math.max(150, radiusX * 0.62));

      const easing = 1 - Math.exp(-delta / 190);
      if (!activeWarp && !reducedMotion) {
        follow.x += (pointer.x - follow.x) * easing;
        follow.y += (pointer.y - follow.y) * easing;
      }
      const effTime = activeWarp ? warpFreezeTime : motionTime;
      const effFollowX = activeWarp ? warpFreezeFollowX : follow.x;
      const effFollowY = activeWarp ? warpFreezeFollowY : follow.y;
      const fieldX = reducedMotion ? 0 : effFollowX + Math.sin(effTime * 0.47) * 0.047;
      const fieldY = reducedMotion ? 0 : effFollowY + Math.sin(effTime * 0.63) * 0.036;
      const breathing =
        Math.sin(effTime * 1.52) * 0.052 + Math.sin(effTime * 2.7) * 0.019;
      const ringRadius = 1 + breathing * breathAmplitude;

      // 厚重推背虫洞参数：0~140ms 蓄压咬合（衔接待机）→ 140~480ms 持续加压冲刺 → 480~1080ms 破壁清场滑出
      const warpIntake = activeWarp ? warpIntakePulse(progress) : 0;
      const warpFwd = activeWarp ? warpForwardProgress(progress) : 0;
      const warpSpeed = activeWarp ? warpSpeedNorm(progress) : 0;
      const warpDzHead = activeWarp ? warpDepthAt(progress) : 0;
      // 随瞬时速度自动拉长曝光快门，蓄压回拉期保持头尾合一（零倒刺），加速峰值期拉出绵密长尾
      const warpShutterLag = lerp(0.088, 0.132, warpSpeed) * (1 - 0.34 * smoothstep(0.56, 0.96, progress));
      const warpDzTail = activeWarp
        ? warpDzHead <= 0
          ? warpDzHead
          : Math.max(0, warpDepthAt(Math.max(0, progress - warpShutterLag)))
        : 0;
      const warpClearWave = activeWarp ? smoothstep(0.24, 0.56, progress) : 0;
      const warpTunnelIntensity = activeWarp
        ? smoothstep(0.04, 0.28, progress) * (1 - smoothstep(0.48, 0.88, progress))
        : 0;
      const warpAlignLock = activeWarp ? smoothstep(0.0, 0.125, progress) : 0;
      const warpMaxReach = Math.hypot(width, height) * 1.18;

      const entryProgress = displayedVisual
        ? Math.min(1, (now - entryStartedAt) / displayedVisual.duration_ms)
        : 1;
      const settled = entryProgress * entryProgress * (3 - 2 * entryProgress);
      const isGlint = displayedVisual?.effect === "glint";
      const glintAngle = -2.98 + settled * 2.83;
      const sweep =
        isGlint && entryProgress < 1
          ? Math.sin(Math.PI * entryProgress) ** 0.65 * visualStrength
          : 0;
      const halo = isGlint ? visualStrength * Math.max(0, (entryProgress - 0.6) / 0.4) : 0;
      const isBloom = displayedVisual?.effect === "bloom";
      const bloomStrength = isBloom ? visualStrength * settled : 0;
      const bloomPulse =
        isBloom && entryProgress < 1
          ? Math.sin(Math.PI * entryProgress) ** 0.7 * visualStrength
          : 0;
      const alignedArc = displayedVisual?.effect === "align" ? visualStrength * settled : 0;
      const outerArc = displayedVisual?.effect === "double_arc" ? visualStrength * settled : 0;
      const latticeStrength = displayedVisual?.effect === "lattice" ? visualStrength * settled : 0;
      const isCircuit = displayedVisual?.effect === "circuit";
      const circuitStrength = isCircuit ? visualStrength * settled : 0;
      const circuitAngle = -2.4 + settled * Math.PI * 1.45;
      const circuitSweep =
        isCircuit && entryProgress < 1
          ? Math.sin(Math.PI * entryProgress) ** 0.7 * visualStrength
          : 0;
      const fade = activeWarp
        ? 1 - Math.pow(smoothstep(0.46, 0.97, progress), 1.38)
        : 1;
      const arcPhase = 1.33 + Math.sin(effTime * 0.27) * 0.44;

      // 计算 9 枚行星的宽敞超椭圆安全包络轨（360° 全程环绕标题、输入框与建议按钮外围，0 遮挡）
      const canShowPlanets = showPlanetsFlag && width >= 760 && height >= 520;
      const revealP = reducedMotion ? 1 : clamp((now - revealStartedAt) / 920);
      const planetStates: PlanetOrbitState[] = [];

      if (canShowPlanets) {
        const compW = anchor?.width ?? Math.min(width * 0.66, 768);
        const compH = anchor?.height ?? 150;
        const maxSafeRx = Math.max(120, Math.min(centerX - 42, width - centerX - 42));
        const maxSafeRy = Math.max(120, Math.min(centerY - 40, height - centerY - 40));
        const baseOrbitRx = Math.min(
          maxSafeRx,
          Math.max(radiusX * 1.02, compW * 0.5 + 64),
        );
        const baseOrbitRy = Math.min(
          maxSafeRy,
          Math.max(radiusY * 1.05, compH * 0.5 + 178),
        );
        const orbitRx = baseOrbitRx;
        const orbitRy = baseOrbitRy;
        // 轻微随鼠标视差漂移（限制幅度防止触边）
        const anchorX = centerX + fieldX * Math.min(radiusX * 0.42, 26);
        const anchorY = centerY + fieldY * Math.min(radiusY * 0.42, 18);
        const angularVel = 0.14 + userOrbitVel;
        const dirSign = angularVel >= 0 ? 1 : -1;
        const speedExtra = Math.min(0.45, Math.abs(userOrbitVel) * 0.085);

        for (let i = 0; i < VENDORS.length; i++) {
          const vendor = VENDORS[i];
          const angle =
            (reducedMotion ? 0 : orbitTime * 0.14) + userOrbitOffset + (i * TAU) / VENDORS.length;
          const { lx, ly } = superEllipsePoint(angle, orbitRx, orbitRy);
          const sx = anchorX + lx * tiltCos - ly * tiltSin;
          const sy = anchorY + lx * tiltSin + ly * tiltCos;

          const { dlx, dly } = superEllipseTangent(angle, orbitRx, orbitRy, dirSign);
          const tx0 = dlx * tiltCos - dly * tiltSin;
          const ty0 = dlx * tiltSin + dly * tiltCos;
          const tMag = Math.hypot(tx0, ty0) || 1;

          const depth = Math.sin(angle) * 0.5 + 0.5;
          const stagger = reducedMotion
            ? 1
            : easeOutBack(clamp((revealP - i * 0.04) / 0.6), 0.85);
          const isSelected = curInspected === vendor.id;
          const isHovered = hoveredIdxRef.current === i && !activeWarp;
          const isActiveProvider = curProvider === vendor.id;
          const activeWeight = activeWeights[i];
          const hasAnyActive = VENDORS.some((v) => v.id === curProvider);
          // 选中某厂商时，其他非激活厂商柔和微降一点亮度以拉出明确主次层次
          const nonActiveSoft =
            hasAnyActive && !isActiveProvider && !isHovered && !isSelected ? 0.78 : 1;
          const dimmed = (curInspected && !isSelected ? 0.34 : 1) * nonActiveSoft;
          const visible = clamp(stagger) * dimmed;

          planetStates.push({
            index: i,
            vendor,
            angle,
            sx,
            sy,
            tx: tx0 / tMag,
            ty: ty0 / tMag,
            depth,
            stagger,
            visible,
            isSelected,
            isHovered,
            isActiveProvider,
            activeWeight,
            effortBoost: smoothedEffort,
            dirSign,
            speedExtra,
            orbitRx,
            orbitRy,
            cosT: tiltCos,
            sinT: tiltSin,
            anchorX,
            anchorY,
          });
        }
      }

      // 1) 待命态柔和光晕无缝跨淡入跃迁态（蓄压微缩凝聚 → 虫洞星云壁厚与暗角压迫 → 破壁扩散光环）
      const idleGlowFade = activeWarp ? 1 - smoothstep(0.0, 0.2, progress) : 1;
      if (idleGlowFade > 0.01) {
        context.save();
        const glowFollowScale = dark ? 0.75 : 1;
        const glowPinch = 1 - warpIntake * 0.09 + warpFwd * 0.35;
        context.translate(
          centerX + fieldX * radiusX * glowFollowScale,
          centerY + fieldY * radiusY * glowFollowScale,
        );
        context.rotate(tilt);
        context.scale(radiusX * 1.4 * glowPinch, radiusY * 1.42 * glowPinch);
        const glow = context.createRadialGradient(-0.12, 0.12, 0, 0, 0, 1);
        glow.addColorStop(0, effortColor(palette.accent, dark ? 0.06 : 0.088));
        glow.addColorStop(0.56, effortColor(palette.base, dark ? 0.055 : 0.095));
        glow.addColorStop(1, effortColor(palette.base, 0));
        context.globalAlpha = idleGlowFade * fade;
        context.fillStyle = glow;
        context.fillRect(-1, -1, 2, 2);
        context.restore();
      }

      if (activeWarp) {
        const diagR = Math.hypot(width * 0.5, height * 0.5);

        // 1A) 虫洞空间压迫与隧道“壁厚”雾带（暗夜为深空暗角+冷靛/暖金星云隧道壁；浅色为清透边缘折射光幕）
        if (warpTunnelIntensity > 0.01) {
          context.save();
          if (dark) {
            // 外围压迫深空暗角，凸显前方亮核与隧道纵深
            const vig = context.createRadialGradient(
              centerX,
              centerY,
              diagR * 0.22,
              centerX,
              centerY,
              diagR * 1.08,
            );
            vig.addColorStop(0, "rgba(4, 5, 11, 0)");
            vig.addColorStop(0.62, `rgba(4, 6, 14, ${(0.22 * warpTunnelIntensity).toFixed(3)})`);
            vig.addColorStop(1, `rgba(2, 3, 8, ${(0.56 * warpTunnelIntensity).toFixed(3)})`);
            context.fillStyle = vig;
            context.fillRect(0, 0, width, height);

            // 8 条低饱和深空星云隧道暗流，沿径向随推背被拉长，制造厚重介质穿越感
            context.globalCompositeOperation = "lighter";
            const nebulaSpecs: readonly { a: number; rgb: RgbColor; reach: number; w: number }[] = [
              { a: -2.72, rgb: [74, 116, 236], reach: 0.72, w: 0.26 },
              { a: -1.88, rgb: [112, 82, 226], reach: 0.66, w: 0.24 },
              { a: -0.96, rgb: [82, 138, 244], reach: 0.76, w: 0.28 },
              { a: -0.22, rgb: [212, 126, 76], reach: 0.68, w: 0.22 },
              { a: 0.44, rgb: [88, 124, 240], reach: 0.74, w: 0.26 },
              { a: 1.32, rgb: [126, 86, 232], reach: 0.64, w: 0.25 },
              { a: 2.12, rgb: [198, 118, 82], reach: 0.7, w: 0.22 },
              { a: 2.86, rgb: [78, 132, 242], reach: 0.74, w: 0.26 },
            ];
            for (const neb of nebulaSpecs) {
              const stretch = lerp(0.55, 1.38, warpFwd);
              const dist = diagR * neb.reach * stretch;
              const rx = diagR * (0.42 + warpSpeed * 0.34);
              const ry = diagR * neb.w * (1 - warpSpeed * 0.16);
              const cx = centerX + Math.cos(neb.a) * dist;
              const cy = centerY + Math.sin(neb.a) * dist;
              context.save();
              context.translate(cx, cy);
              context.rotate(neb.a);
              context.scale(rx, ry);
              const ng = context.createRadialGradient(0, 0, 0, 0, 0, 1);
              ng.addColorStop(0, rgba(neb.rgb, 0.145 * warpTunnelIntensity));
              ng.addColorStop(0.52, rgba(neb.rgb, 0.065 * warpTunnelIntensity));
              ng.addColorStop(1, rgba(neb.rgb, 0));
              context.fillStyle = ng;
              context.beginPath();
              context.arc(0, 0, 1, 0, TAU);
              context.fill();
              context.restore();
            }
          } else {
            // 浅色清透边缘折射光幕：中央保持纯白清爽，四周叠一层柔和紫粉折射压迫光晕
            const veil = context.createRadialGradient(
              centerX,
              centerY,
              diagR * 0.28,
              centerX,
              centerY,
              diagR * 1.06,
            );
            veil.addColorStop(0, effortColor(palette.base, 0));
            veil.addColorStop(0.62, effortColor(palette.base, 0.045 * warpTunnelIntensity));
            veil.addColorStop(0.88, effortColor(palette.accent, 0.095 * warpTunnelIntensity));
            veil.addColorStop(1, effortColor(palette.base, 0.12 * warpTunnelIntensity));
            context.fillStyle = veil;
            context.fillRect(0, 0, width, height);
          }
          context.restore();
        }

        // 1B) 蓄压奇点凝聚焦核（0 ~ 280ms）+ 随加速度扩张的破壁引力冲击光环
        const coreP = clamp(progress / 0.28);
        const shockP = clamp((progress - 0.08) / 0.48);
        if (coreP < 1 || shockP < 1) {
          context.save();
          if (dark) context.globalCompositeOperation = "lighter";

          if (coreP < 1) {
            const corePulse = Math.pow(Math.sin(coreP * Math.PI), 0.9);
            // 蓄压阶段收缩凝实，随后推背开启向深处点状收束
            const coreR = lerp(136, 26, coreP) * (1 - warpIntake * 0.18);
            const coreGrad = context.createRadialGradient(
              centerX,
              centerY,
              0,
              centerX,
              centerY,
              coreR,
            );
            if (dark) {
              coreGrad.addColorStop(0, `rgba(226, 238, 255, ${(0.28 * corePulse).toFixed(3)})`);
              coreGrad.addColorStop(0.32, effortColor(palette.accent, 0.16 * corePulse));
              coreGrad.addColorStop(0.66, effortColor(palette.base, 0.1 * corePulse));
              coreGrad.addColorStop(1, "rgba(110, 148, 255, 0)");
            } else {
              coreGrad.addColorStop(0, effortColor(palette.accent, 0.16 * corePulse));
              coreGrad.addColorStop(0.45, effortColor(palette.base, 0.08 * corePulse));
              coreGrad.addColorStop(1, effortColor(palette.base, 0));
            }
            context.fillStyle = coreGrad;
            context.beginPath();
            context.arc(centerX, centerY, coreR, 0, TAU);
            context.fill();
          }

          if (shockP > 0 && shockP < 1) {
            // 冲击环随 warpForward 同步由慢到快冲破视口，内外双柔肩避免生硬圆圈边
            const shockMove = clamp(warpFwd / 0.82);
            const shockPulse = smoothstep(0, 0.28, shockP) * (1 - smoothstep(0.42, 1, shockP));
            const outerR = lerp(Math.min(radiusX, radiusY) * 0.36, diagR * 1.44, shockMove);
            const portalGrad = context.createRadialGradient(
              centerX,
              centerY,
              outerR * 0.16,
              centerX,
              centerY,
              outerR,
            );
            if (dark) {
              portalGrad.addColorStop(0, "rgba(130, 168, 255, 0)");
              portalGrad.addColorStop(0.32, effortColor(palette.base, 0.055 * shockPulse));
              portalGrad.addColorStop(0.56, effortColor(palette.accent, 0.16 * shockPulse));
              portalGrad.addColorStop(0.76, rgba([168, 208, 255], 0.075 * shockPulse));
              portalGrad.addColorStop(1, "rgba(110, 148, 255, 0)");
            } else {
              portalGrad.addColorStop(0, effortColor(palette.base, 0));
              portalGrad.addColorStop(0.36, effortColor(palette.base, 0.04 * shockPulse));
              portalGrad.addColorStop(0.6, effortColor(palette.accent, 0.105 * shockPulse));
              portalGrad.addColorStop(0.82, effortColor(palette.base, 0.035 * shockPulse));
              portalGrad.addColorStop(1, effortColor(palette.base, 0));
            }
            context.fillStyle = portalGrad;
            context.beginPath();
            context.arc(centerX, centerY, outerR, 0, TAU);
            context.fill();
          }
          context.restore();
        }
      }

      // 2) 粒子星环（浅色走细腻磁力短线；暗夜切入多光谱深空星尘 + 旋臂星团精灵）
      const wakeRadius = 74;
      const wakeEnabled = planetStates.length === VENDORS.length && !activeWarp;

      if (dark) {
        context.save();
        context.globalCompositeOperation = "lighter";
        const calmTime = reducedMotion ? 0 : activeWarp ? warpFreezeOrbitTime : orbitTime;
        const effUserOrbit = activeWarp ? warpFreezeUserOffset : userOrbitOffset;
        // 切入暗夜时 580ms 内从 0.76 单调平滑升亮至 1.0，随径向揭幕实时点亮星空
        const awakenP =
          reducedMotion || darkAwakenAt === -Infinity
            ? 1
            : clamp((now - darkAwakenAt) / 580);
        const awakenEase = 1 - Math.pow(1 - awakenP, 3);
        const awakenScale = 1 + (1 - awakenEase) * 0.012;
        // 暗夜星海采用极克制的 15 秒慢潮汐微呼吸（±1.1%），彻底消除高档位高频胀缩造成的视觉疲劳
        const cosmicRingRadius = (1 + Math.sin(calmTime * 0.42) * 0.011) * awakenScale;
        // 黑夜模式鼠标视差跟随幅度减少 25%（0.75x），观感更沉稳深邃
        const cosmicFieldX = reducedMotion
          ? 0
          : effFollowX * 0.75 + Math.sin(calmTime * 0.24) * 0.014;
        const cosmicFieldY = reducedMotion
          ? 0
          : effFollowY * 0.75 + Math.sin(calmTime * 0.31) * 0.01;
        // 高思考强度改为「望远镜长曝光感」：星光通透度与旋臂丝滑公转温和提升，而非高频抖动
        const effortGain = 0.74 + smoothedEffort * 0.42;
        const orbitSpeedScale = 0.75 + smoothedEffort * 0.45;

        // 2A) 全视口深空远景散星（跃迁态按真实深度参与虫洞远近差推进）
        for (const star of deepStars) {
          const sx0 =
            width * 0.5 +
            star.nx * width * 0.5 +
            effFollowX * width * 0.056 * star.depth;
          const sy0 =
            height * 0.5 +
            star.ny * height * 0.5 +
            effFollowY * height * 0.056 * star.depth;
          const starAwaken =
            awakenP >= 1 ? 1 : 0.74 + 0.26 * smoothstep(star.depth * 0.25, 0.25 + star.depth * 0.75, awakenP);
          const twinkle =
            1 -
            star.twinkleDepth *
              0.55 *
              (0.5 - 0.5 * Math.sin(calmTime * star.twinkleSpeed * 0.48 + star.twinklePhase));

          if (activeWarp) {
            const depthNorm = clamp((star.depth - 0.18) / 0.77);
            const z0 = lerp(7.4, 1.24, Math.pow(depthNorm, 0.85));
            const ray = projectWormholeRay(
              sx0 - centerX,
              sy0 - centerY,
              z0,
              warpDzHead,
              warpDzTail,
              centerX,
              centerY,
              width,
              height,
              warpMaxReach,
              warpClearWave,
            );
            if (!ray) continue;
            const radiusBoost = 1 + (ray.headScale - 1) * 0.15;
            const alpha =
              star.luminance *
              twinkle *
              starAwaken *
              (1 + warpIntake * 0.16 + warpSpeed * 0.48) *
              ray.visibility *
              fade;
            stampCosmicStreak(
              context,
              starAtlas,
              star.spectral,
              ray.tailX,
              ray.tailY,
              ray.headX,
              ray.headY,
              star.drawRadius * radiusBoost,
              alpha,
              star.hasFlare,
            );
          } else {
            if (sx0 < -24 || sx0 > width + 24 || sy0 < -24 || sy0 > height + 24) continue;
            stampCosmicStar(
              context,
              starAtlas,
              star.spectral,
              sx0,
              sy0,
              star.drawRadius * (0.9 + 0.1 * starAwaken),
              star.luminance * twinkle * starAwaken * fade,
              star.hasFlare,
            );
          }
        }

        // 2B) 仅跃迁态激活的多波次虫洞核心锥星尘（蓄压阶段轻盈浮现，加速段连续 3 波涌出撑住厚重推背）
        if (activeWarp) {
          const coreFadeIn = smoothstep(0.02, 0.14, progress);
          const wormholeBaseR = Math.min(width, height) * 0.64;
          for (const star of wormholeStars) {
            const dx0 = Math.cos(star.angle) * star.radial * wormholeBaseR * 1.18;
            const dy0 = Math.sin(star.angle) * star.radial * wormholeBaseR * 0.86;
            const ray = projectWormholeRay(
              dx0,
              dy0,
              star.z0,
              warpDzHead,
              warpDzTail,
              centerX,
              centerY,
              width,
              height,
              warpMaxReach,
              warpClearWave,
            );
            if (!ray) continue;
            const alpha =
              star.luminance *
              coreFadeIn *
              (0.88 + warpSpeed * 0.62) *
              ray.visibility *
              fade;
            const headR = star.drawRadius * (0.9 + (ray.headScale - 1) * 0.16);
            stampCosmicStreak(
              context,
              starAtlas,
              star.spectral,
              ray.tailX,
              ray.tailY,
              ray.headX,
              ray.headY,
              headR,
              alpha,
              star.hasFlare,
            );
          }

          // 2B-2) 近景贴镜失焦散景大光带：沿四边角落分波次呼啸掠过，赋予镜头真实物理体量感
          const bokehFadeIn = smoothstep(0.11, 0.24, progress);
          if (bokehFadeIn > 0.01) {
            for (const bk of bokehStreaks) {
              const dx0 = Math.cos(bk.angle) * bk.radial * wormholeBaseR * 1.24;
              const dy0 = Math.sin(bk.angle) * bk.radial * wormholeBaseR * 0.92;
              const ray = projectWormholeRay(
                dx0,
                dy0,
                bk.z0,
                warpDzHead,
                warpDzTail,
                centerX,
                centerY,
                width,
                height,
                warpMaxReach,
                warpClearWave,
              );
              if (!ray) continue;
              const bkAlpha =
                bk.luminance *
                bokehFadeIn *
                (0.55 + warpSpeed * 0.95) *
                ray.visibility *
                fade;
              const thick = bk.thickness * (0.85 + (ray.headScale - 1) * 0.34);
              stampBokehStreak(
                context,
                starAtlas,
                bk.spectral,
                ray.tailX,
                ray.tailY,
                ray.headX,
                ray.headY,
                thick,
                bkAlpha,
              );
            }
          }
        }

        // 2C) 环绕工作区的多旋臂星团与星尘带（待命态平稳公转；蓄压期沿原旋臂方向轻微旋紧再逐层爆发）
        const spiralCoil = activeWarp ? warpIntake * 0.032 : 0;
        for (const star of galaxyStars) {
          const angle =
            star.angle +
            calmTime * star.orbitSpeed * orbitSpeedScale +
            effUserOrbit * 0.24 +
            spiralCoil +
            Math.sin(calmTime * 0.32 + star.wobblePhase) * 0.006;
          const rBase =
            star.radius * cosmicRingRadius +
            Math.cos(calmTime * 0.28 + star.wobblePhase) * 0.005;
          const offset = rBase - cosmicRingRadius + Math.sin(angle * 4 + calmTime * 0.36) * 0.01;
          const ringBand = Math.exp(-((offset / 0.16) ** 2));

          const movingLight = sweep > 0.01 ? sweep * arcLight(angle, glintAngle, 0.36) : 0;
          const restingLight = halo > 0.015 ? halo * arcLight(angle, -0.67, 0.42) * 0.34 : 0;
          const alignment =
            alignedArc > 0.015 ? alignedArc * arcLight(angle, 2, 0.86) * Math.sqrt(ringBand) : 0;
          const secondArc =
            outerArc > 0.015
              ? outerArc *
                arcLight(angle, -1.06, 0.78) *
                Math.exp(-(((offset - 0.17) / 0.07) ** 2))
              : 0;
          const latticeNode =
            latticeStrength > 0.015
              ? latticeStrength *
                Math.pow(0.5 + 0.5 * Math.cos(angle * 6 + calmTime * 0.28), 3) *
                Math.sqrt(ringBand)
              : 0;
          const circuitInner =
            circuitStrength > 0.015 ? Math.exp(-(((offset + 0.1) / 0.055) ** 2)) : 0;
          const circuitOuter =
            circuitStrength > 0.015 ? Math.exp(-(((offset - 0.12) / 0.055) ** 2)) : 0;
          const circuitTrack =
            circuitStrength > 0.015
              ? circuitStrength *
                (circuitInner * (0.35 + 0.65 * Math.pow(0.5 + 0.5 * Math.cos(angle * 4 - 0.45), 2)) +
                  circuitOuter * (0.28 + 0.72 * Math.pow(0.5 + 0.5 * Math.sin(angle * 4 + 0.45), 2))) *
                0.52
              : 0;
          const circuitSignal =
            circuitSweep > 0.01
              ? circuitSweep *
                (circuitInner * arcLight(angle, circuitAngle, 0.36) +
                  circuitOuter * arcLight(angle, -circuitAngle + Math.PI * 0.6, 0.36))
              : 0;
          const bloomPetal =
            bloomStrength > 0.015
              ? (bloomStrength * 0.42 + bloomPulse * 0.48) *
                Math.pow(0.5 + 0.5 * Math.cos(angle * 6 - calmTime * 0.32), 2.2) *
                Math.sqrt(ringBand)
              : 0;
          const highlight =
            ringBand * (movingLight + restingLight) +
            bloomPetal * 0.45 +
            alignment * 0.35 +
            secondArc * 0.46 +
            latticeNode * 0.42 +
            circuitTrack * 0.38 +
            circuitSignal * 0.62;

          // 暗夜模式下把径向拉扯控制在极轻微幅度，主要以星光增亮体现模型特性，杜绝整环反复膨胀收缩
          const bloomTarget =
            cosmicRingRadius + (0.025 + bloomPulse * 0.02) * Math.cos(angle * 6 - calmTime * 0.32);
          const latticeTarget = cosmicRingRadius + 0.02 * Math.cos(angle * 6 + calmTime * 0.28);
          const radialAdjustment =
            bloomPetal * (bloomTarget - rBase) * 0.22 +
            alignment * (cosmicRingRadius - rBase) * 0.28 +
            secondArc * (cosmicRingRadius + 0.14 - rBase) * 0.28 +
            latticeNode * (latticeTarget - rBase) * 0.24 +
            circuitStrength *
              (circuitOuter * (cosmicRingRadius + 0.1 - rBase) * 0.24 +
                circuitInner * (cosmicRingRadius - 0.08 - rBase) * 0.22);

          const rFinal = rBase + radialAdjustment;
          const px = (cosmicFieldX + Math.cos(angle) * rFinal) * radiusX;
          const py = (cosmicFieldY + Math.sin(angle) * rFinal) * radiusY;
          let screenX = centerX + px * tiltCos - py * tiltSin;
          let screenY = centerY + px * tiltSin + py * tiltCos;

          const starAwaken =
            awakenP >= 1 ? 1 : 0.76 + 0.24 * smoothstep(star.seed * 0.28, 0.28 + star.seed * 0.72, awakenP);
          const twinkle =
            1 -
            star.twinkleDepth *
              0.52 *
              (0.5 - 0.5 * Math.sin(calmTime * star.twinkleSpeed * 0.46 + star.twinklePhase));

          if (activeWarp) {
            const nearness = clamp(star.luminance * 0.52 + star.seed * 0.48);
            const z0 = lerp(6.0, 1.18, Math.pow(nearness, 0.88));
            const ray = projectWormholeRay(
              screenX - centerX,
              screenY - centerY,
              z0,
              warpDzHead,
              warpDzTail,
              centerX,
              centerY,
              width,
              height,
              warpMaxReach,
              warpClearWave,
            );
            if (!ray) continue;

            const starRadius =
              star.drawRadius *
              (0.88 + star.knotBoost * 0.12 + highlight * 0.2) *
              (0.92 + 0.08 * starAwaken) *
              (1 + warpIntake * 0.12 + (ray.headScale - 1) * 0.14);
            const starAlpha =
              (star.luminance * twinkle * effortGain * starAwaken + highlight * 0.34) *
              (1 + warpIntake * 0.18 + warpSpeed * 0.52) *
              ray.visibility *
              fade;

            stampCosmicStreak(
              context,
              starAtlas,
              star.spectral,
              ray.tailX,
              ray.tailY,
              ray.headX,
              ray.headY,
              starRadius,
              starAlpha,
              star.hasFlare || (highlight > 0.48 && star.seed > 0.86),
            );
            continue;
          }

          // 行星局部引力微扰 + 专属星光晕染
          let wakeWeight = 0;
          let wakeRgb: [number, number, number] | null = null;
          if (wakeEnabled) {
            let bestW = 0;
            let bestB: PlanetOrbitState | null = null;
            let bestDx = 0;
            let bestDy = 0;
            let bestD = 0;
            for (const b of planetStates) {
              if (b.visible < 0.05) continue;
              const dx = screenX - b.sx;
              const dy = screenY - b.sy;
              const d = Math.hypot(dx, dy);
              const effortScale = 1 + b.activeWeight * (0.16 + b.effortBoost * 0.44);
              const rInf = wakeRadius * effortScale * (b.isHovered || b.isSelected ? 1.18 : 1);
              if (d < rInf) {
                const rawW = (1 - d / rInf) * b.visible * (1 + b.activeWeight * b.effortBoost * 0.26);
                if (rawW > bestW) {
                  bestW = rawW;
                  bestB = b;
                  bestDx = dx;
                  bestDy = dy;
                  bestD = d;
                }
              }
            }
            if (bestB && bestW > 0.01) {
              const smoothW = Math.pow(bestW, 1.35);
              wakeWeight = smoothW;
              const invD = 1 / Math.max(bestD, 1);
              const nx = bestDx * invD;
              const ny = bestDy * invD;
              const wakePush = Math.sin(clamp(bestW) * Math.PI) * 14.5 * (bestB.isHovered ? 1.25 : 1);
              screenX += nx * wakePush - bestB.tx * smoothW * 4.5;
              screenY += ny * wakePush - bestB.ty * smoothW * 4.5;

              const bestTrail = resolveVendorTrail(bestB.vendor, true);
              const colorU = bestTrail.spectrum
                ? (Math.atan2(bestDy, bestDx) / TAU + 1) % 1
                : clamp(1 - bestW);
              wakeRgb = sampleTrailColor(bestTrail, colorU);
            }
          }

          if (screenX < -28 || screenX > width + 28 || screenY < -28 || screenY > height + 28) {
            continue;
          }

          const starRadius =
            star.drawRadius *
            (0.92 + star.knotBoost * 0.16 + highlight * 0.24 + wakeWeight * 0.24) *
            (0.92 + 0.08 * starAwaken);
          const starAlpha =
            (star.luminance * twinkle * effortGain * starAwaken + highlight * 0.34 + wakeWeight * 0.22) *
            fade;

          stampCosmicStar(
            context,
            starAtlas,
            star.spectral,
            screenX,
            screenY,
            starRadius,
            starAlpha,
            star.hasFlare || (highlight > 0.48 && star.seed > 0.86),
          );

          // 被行星引力场掠过的星点附加一层极柔的厂商专属色微光晕
          if (wakeRgb && wakeWeight > 0.16) {
            context.globalAlpha = clamp(wakeWeight * 0.55 * fade, 0, 0.72);
            context.fillStyle = rgba(wakeRgb, 1);
            context.beginPath();
            context.arc(screenX, screenY, Math.max(1.1, starRadius * 0.38), 0, TAU);
            context.fill();
          }
        }
        context.restore();
        context.globalAlpha = 1;
      } else {
        context.lineCap = "round";
        for (const particle of particles) {
          const x = particle.x + Math.sin(effTime * 0.95 + particle.phase) * 0.018;
          const y = particle.y + Math.cos(effTime * 0.81 + particle.phase) * 0.024;
          const localX = x - fieldX;
          const localY = y - fieldY;
          const angle = Math.atan2(localY, localX);
          const distance = Math.hypot(localX, localY);
          const offset = distance - ringRadius + Math.sin(angle * 4 + effTime) * 0.023;
          const broadRing = Math.exp(-(((offset + 0.034) / 0.142) ** 2));
          const rim = Math.exp(-((offset / 0.052) ** 2));
          const arc = 0.34 + 0.66 * (0.5 + 0.5 * Math.cos(angle * 2 + arcPhase)) ** 2;
          const ringBand = broadRing * 0.41 + rim * 0.59;
          const strength = ringBand * arc;
          const movingLight = sweep > 0.01 ? sweep * arcLight(angle, glintAngle, 0.34) : 0;
          const restingLight = halo > 0.015 ? halo * arcLight(angle, -0.67, 0.41) * 0.34 : 0;
          const alignment =
            alignedArc > 0.015 ? alignedArc * arcLight(angle, 2, 0.86) * Math.sqrt(ringBand) : 0;
          const secondArc =
            outerArc > 0.015
              ? outerArc *
                arcLight(angle, -1.06, 0.78) *
                Math.exp(-(((offset - 0.17) / 0.058) ** 2))
              : 0;
          const latticeNode =
            latticeStrength > 0.015
              ? latticeStrength *
                Math.pow(0.5 + 0.5 * Math.cos(angle * 6 + effTime * 0.42), 3) *
                Math.sqrt(ringBand)
              : 0;
          const circuitInner =
            circuitStrength > 0.015
              ? Math.exp(-(((offset + 0.1) / 0.048) ** 2))
              : 0;
          const circuitOuter =
            circuitStrength > 0.015
              ? Math.exp(-(((offset - 0.12) / 0.048) ** 2))
              : 0;
          const circuitTrack =
            circuitStrength > 0.015
              ? circuitStrength *
                (circuitInner * (0.35 + 0.65 * Math.pow(0.5 + 0.5 * Math.cos(angle * 4 - 0.45), 2)) +
                  circuitOuter * (0.28 + 0.72 * Math.pow(0.5 + 0.5 * Math.sin(angle * 4 + 0.45), 2))) *
                0.52
              : 0;
          const circuitSignal =
            circuitSweep > 0.01
              ? circuitSweep *
                (circuitInner * arcLight(angle, circuitAngle, 0.34) +
                  circuitOuter * arcLight(angle, -circuitAngle + Math.PI * 0.6, 0.34))
              : 0;
          const bloomPetal =
            bloomStrength > 0.015
              ? (bloomStrength * 0.48 + bloomPulse * 0.62) *
                Math.pow(0.5 + 0.5 * Math.cos(angle * 6 - effTime * 0.62), 2.2) *
                Math.sqrt(ringBand)
              : 0;
          const highlight =
            ringBand * (movingLight + restingLight) +
            bloomPetal * 0.54 +
            alignment * 0.38 +
            secondArc * 0.53 +
            latticeNode * 0.46 +
            circuitTrack * 0.42 +
            circuitSignal * 0.72;

          const bloomTarget =
            ringRadius + (0.065 + bloomPulse * 0.05) * Math.cos(angle * 6 - effTime * 0.62);
          const latticeTarget = ringRadius + 0.045 * Math.cos(angle * 6 + effTime * 0.42);
          const radialAdjustment =
            bloomPetal * (bloomTarget - distance) * 0.72 +
            alignment * (ringRadius - distance) * 0.88 +
            secondArc * (ringRadius + 0.17 - distance) * 0.82 +
            latticeNode * (latticeTarget - distance) * 0.78 +
            circuitStrength *
              (circuitOuter * (ringRadius + 0.12 - distance) * 0.65 +
                circuitInner * (ringRadius - 0.1 - distance) * 0.55);
          const alignmentScale = 1 + radialAdjustment / Math.max(distance, 0.01);
          const px = (fieldX + localX * alignmentScale) * radiusX;
          const py = (fieldY + localY * alignmentScale) * radiusY;
          let screenX = centerX + px * tiltCos - py * tiltSin;
          let screenY = centerY + px * tiltSin + py * tiltCos;

          if (activeWarp) {
            if (strength < 0.06 && highlight < 0.04 && particle.seed < 0.47) continue;
            const z0 = lerp(6.0, 1.2, Math.pow(particle.seed, 0.86));
            const ray = projectWormholeRay(
              screenX - centerX,
              screenY - centerY,
              z0,
              warpDzHead,
              warpDzTail,
              centerX,
              centerY,
              width,
              height,
              warpMaxReach,
              warpClearWave,
            );
            if (!ray) continue;

            const baseRgb: RgbColor =
              highlight > 0.06
                ? displayedVisual
                  ? EFFECT_COLORS[displayedVisual.effect]
                  : palette.accent
                : rim * arc > 0.33 && particle.seed > 0.3
                  ? palette.accent
                  : palette.base;
            const segLen = Math.hypot(ray.headX - ray.tailX, ray.headY - ray.tailY);
            const idleLen =
              0.42 +
              particle.seed * 0.72 +
              strength * 3.25 * palette.trail +
              highlight * 4;
            // 衔接白模式待机态：蓄压期将所有磁力短线从待机倾角丝滑锁正指向虫洞中心（零首帧偏折）
            let idleDir =
              Math.atan2(localY * radiusY, localX * radiusX) +
              tilt +
              Math.sin(particle.phase + effTime) * 0.18;
            if (highlight > 0.03) {
              const tangent = Math.atan2(localX * radiusY, -localY * radiusX) + tilt;
              const turn = tangent - idleDir;
              const shortestTurn = Math.atan2(Math.sin(2 * turn), Math.cos(2 * turn)) / 2;
              idleDir += shortestTurn * Math.min(1, highlight * 2.7);
            }
            const radialDir = Math.atan2(ray.headY - centerY, ray.headX - centerX);
            const deltaDir = radialDir - idleDir;
            const lockedDir =
              idleDir +
              (Math.atan2(Math.sin(2 * deltaDir), Math.cos(2 * deltaDir)) / 2) * warpAlignLock;

            const baseAlpha = Math.min(
              0.94,
              (0.12 + strength * 0.72 + highlight * 0.64 + secondArc * 0.14) *
                (1 + warpIntake * 0.2 + warpSpeed * 0.46) *
                ray.visibility *
                fade,
            );
            const lw =
              (0.68 + strength * 0.85) *
              (1 + warpIntake * 0.22 + (ray.headScale - 1) * 0.24);

            if (segLen < Math.max(4.2, idleLen * 1.15)) {
              const halfL = Math.max(idleLen, segLen) * 0.5;
              context.globalAlpha = baseAlpha;
              context.strokeStyle = rgba(baseRgb, 1);
              context.lineWidth = lw;
              context.beginPath();
              context.moveTo(
                ray.headX - Math.cos(lockedDir) * halfL,
                ray.headY - Math.sin(lockedDir) * halfL,
              );
              context.lineTo(
                ray.headX + Math.cos(lockedDir) * halfL,
                ray.headY + Math.sin(lockedDir) * halfL,
              );
              context.stroke();
            } else {
              // 浅色双段细锐棱镜光针：柔和长尾 + 凝实前段，零渐变对象分配
              const midX = lerp(ray.tailX, ray.headX, 0.58);
              const midY = lerp(ray.tailY, ray.headY, 0.58);
              context.strokeStyle = rgba(baseRgb, 1);
              context.globalAlpha = baseAlpha * 0.4;
              context.lineWidth = lw * 0.9;
              context.beginPath();
              context.moveTo(ray.tailX, ray.tailY);
              context.lineTo(ray.headX, ray.headY);
              context.stroke();

              context.globalAlpha = baseAlpha;
              context.lineWidth = lw * 1.24;
              context.beginPath();
              context.moveTo(midX, midY);
              context.lineTo(ray.headX, ray.headY);
              context.stroke();
            }
            continue;
          }

          // 局部物理微引力场：周围粒子顺流绕开行星并染上该家专属色
          let wakeWeight = 0;
          let wakeRgb: [number, number, number] | null = null;
          let wakeDir: number | null = null;
          if (wakeEnabled) {
            let bestW = 0;
            let bestB: PlanetOrbitState | null = null;
            let bestDx = 0;
            let bestDy = 0;
            let bestD = 0;
            for (const b of planetStates) {
              if (b.visible < 0.05) continue;
              const dx = screenX - b.sx;
              const dy = screenY - b.sy;
              const d = Math.hypot(dx, dy);
              // 当前激活厂商随推理强度扩大微引力场范围（74px → 最高 118px），抽引大片星河粒子变色共振
              const effortScale = 1 + b.activeWeight * (0.16 + b.effortBoost * 0.44);
              const rInf = wakeRadius * effortScale * (b.isHovered || b.isSelected ? 1.18 : 1);
              if (d < rInf) {
                const rawW = (1 - d / rInf) * b.visible * (1 + b.activeWeight * b.effortBoost * 0.26);
                if (rawW > bestW) {
                  bestW = rawW;
                  bestB = b;
                  bestDx = dx;
                  bestDy = dy;
                  bestD = d;
                }
              }
            }
            if (bestB && bestW > 0.01) {
              const smoothW = Math.pow(bestW, 1.35);
              wakeWeight = smoothW;
              const invD = 1 / Math.max(bestD, 1);
              const nx = bestDx * invD;
              const ny = bestDy * invD;
              const wakePush = Math.sin(clamp(bestW) * Math.PI) * 14.5 * (bestB.isHovered ? 1.25 : 1);
              screenX += nx * wakePush - bestB.tx * smoothW * 4.5;
              screenY += ny * wakePush - bestB.ty * smoothW * 4.5;

              const side = bestDx * bestB.ty - bestDy * bestB.tx >= 0 ? 1 : -1;
              wakeDir = Math.atan2(bestB.ty + ny * 0.42 * side, bestB.tx + nx * 0.42 * side);
              const bestTrail = resolveVendorTrail(bestB.vendor, false);
              const colorU = bestTrail.spectrum
                ? (Math.atan2(bestDy, bestDx) / TAU + 1) % 1
                : clamp(1 - bestW);
              wakeRgb = sampleTrailColor(bestTrail, colorU);
            }
          }

          if (strength < 0.06 && highlight < 0.04 && wakeWeight < 0.08 && particle.seed < 0.47) {
            continue;
          }

          const length =
            0.42 +
            particle.seed * 0.72 +
            strength * 3.25 * palette.trail +
            highlight * 4 +
            wakeWeight * 5.2;
          let direction =
            Math.atan2(localY * radiusY, localX * radiusX) +
            tilt +
            Math.sin(particle.phase + effTime) * 0.18;
          if (highlight > 0.03) {
            const tangent = Math.atan2(localX * radiusY, -localY * radiusX) + tilt;
            const turn = tangent - direction;
            const shortestTurn = Math.atan2(Math.sin(2 * turn), Math.cos(2 * turn)) / 2;
            direction += shortestTurn * Math.min(1, highlight * 2.7);
          }
          if (wakeDir !== null) {
            const turnW = wakeDir - direction;
            const shortW = Math.atan2(Math.sin(2 * turnW), Math.cos(2 * turnW)) / 2;
            direction += shortW * clamp(wakeWeight * 1.15);
          }

          const tailX = (Math.cos(direction) * length) / 2;
          const tailY = (Math.sin(direction) * length) / 2;
          context.globalAlpha = Math.min(
            1,
            (0.12 +
              strength * 0.7 +
              highlight * 0.64 +
              secondArc * 0.14 +
              wakeWeight * 0.42) *
              fade,
          );

          const baseRgb: RgbColor =
            highlight > 0.06
              ? displayedVisual
                ? EFFECT_COLORS[displayedVisual.effect]
                : palette.accent
              : rim * arc > 0.33 && particle.seed > 0.3
                ? palette.accent
                : palette.base;
          const finalRgb = wakeRgb ? mixRgb(baseRgb, wakeRgb, clamp(wakeWeight * 1.35)) : baseRgb;

          context.strokeStyle = rgba(finalRgb, 1);
          context.lineWidth = 0.68 + strength * 0.85 + wakeWeight * 0.8;
          context.beginPath();
          context.moveTo(screenX - tailX, screenY - tailY);
          context.lineTo(screenX + tailX, screenY + tailY);
          context.stroke();
        }

        // 浅色模式补充多波次中心棱镜光针 + 周边柔雾散景棱镜宽光带，保持纯净白底与厚重推背兼得
        if (activeWarp) {
          const coreFadeIn = smoothstep(0.02, 0.14, progress);
          const wormholeBaseR = Math.min(width, height) * 0.64;
          for (let i = 0; i < wormholeStars.length; i += 2) {
            const star = wormholeStars[i];
            const dx0 = Math.cos(star.angle) * star.radial * wormholeBaseR * 1.18;
            const dy0 = Math.sin(star.angle) * star.radial * wormholeBaseR * 0.86;
            const ray = projectWormholeRay(
              dx0,
              dy0,
              star.z0,
              warpDzHead,
              warpDzTail,
              centerX,
              centerY,
              width,
              height,
              warpMaxReach,
              warpClearWave,
            );
            if (!ray) continue;
            const rgb = star.seed > 0.45 ? palette.accent : palette.base;
            const alpha =
              star.luminance *
              0.62 *
              coreFadeIn *
              (0.88 + warpSpeed * 0.56) *
              ray.visibility *
              fade;
            const segLen = Math.hypot(ray.headX - ray.tailX, ray.headY - ray.tailY);
            const lw = (0.72 + star.drawRadius * 0.19) * (0.92 + (ray.headScale - 1) * 0.25);
            context.strokeStyle = rgba(rgb, 1);
            if (segLen < 3.2) {
              context.globalAlpha = alpha;
              context.fillStyle = rgba(rgb, 1);
              context.beginPath();
              context.arc(ray.headX, ray.headY, Math.max(0.9, lw * 0.72), 0, TAU);
              context.fill();
            } else {
              const midX = lerp(ray.tailX, ray.headX, 0.56);
              const midY = lerp(ray.tailY, ray.headY, 0.56);
              context.globalAlpha = alpha * 0.36;
              context.lineWidth = lw * 0.85;
              context.beginPath();
              context.moveTo(ray.tailX, ray.tailY);
              context.lineTo(ray.headX, ray.headY);
              context.stroke();

              context.globalAlpha = alpha;
              context.lineWidth = lw * 1.24;
              context.beginPath();
              context.moveTo(midX, midY);
              context.lineTo(ray.headX, ray.headY);
              context.stroke();
            }
          }

          // 浅色近景失焦棱镜光带（使用预烘焙高斯羽化贴图，彻底消除圆角胶囊边，纯白清爽且有厚重体量）
          const bokehFadeIn = smoothstep(0.11, 0.24, progress);
          if (bokehFadeIn > 0.01) {
            for (let i = 0; i < bokehStreaks.length; i += 2) {
              const bk = bokehStreaks[i];
              const dx0 = Math.cos(bk.angle) * bk.radial * wormholeBaseR * 1.24;
              const dy0 = Math.sin(bk.angle) * bk.radial * wormholeBaseR * 0.92;
              const ray = projectWormholeRay(
                dx0,
                dy0,
                bk.z0,
                warpDzHead,
                warpDzTail,
                centerX,
                centerY,
                width,
                height,
                warpMaxReach,
                warpClearWave,
              );
              if (!ray) continue;
              const bkAlpha =
                bk.luminance *
                0.78 *
                bokehFadeIn *
                (0.55 + warpSpeed * 0.92) *
                ray.visibility *
                fade;
              const thick = bk.thickness * (0.85 + (ray.headScale - 1) * 0.3);
              stampBokehStreak(
                context,
                starAtlas,
                bk.spectral,
                ray.tailX,
                ray.tailY,
                ray.headX,
                ray.headY,
                thick,
                bkAlpha,
                true,
              );
            }
          }
        }
        context.globalAlpha = 1;
      }

      // 3) 常规待命态 vs 首次发送跃迁态（蓄压承接待机彗尾 → 选中行星随推背峰值俯冲落入 AI 回复，其余 8 颗蓄压微收后重甩出画框）
      if (activeWarp && warpSnapshots && warpSnapshots.length === VENDORS.length) {
        // 蓄压期（0 ~ 170ms）保留并柔收待机态行星彗尾与环绕能量环，确保待机到跃迁首帧 100% 零突兀断层
        const idleTrailFade = 1 - smoothstep(0.02, 0.16, progress);
        if (idleTrailFade > 0.01 && warpPlanetStates && warpPlanetStates.length === VENDORS.length) {
          const intakeScale = 1 - warpIntake * 0.045;
          const intakeStates = warpPlanetStates.map((st) => ({
            ...st,
            orbitRx: st.orbitRx * intakeScale,
            orbitRy: st.orbitRy * intakeScale,
            sx: centerX + (st.sx - centerX) * intakeScale,
            sy: centerY + (st.sy - centerY) * intakeScale,
          }));
          drawPlanetTrails(context, intakeStates, idleTrailFade, surgeProgress, dark);
        }

        // 精准读取首条 AI 回复的真实锚点位置，让俯冲行星严丝合缝落进左上角缩微厂商星核！
        const handoffEl = area.querySelector<HTMLElement>("[data-handoff-anchor='1']");
        const handoffRect = handoffEl?.getBoundingClientRect();
        const fallbackLeft = Math.max(48, (width - Math.min(width - 48, 768)) / 2 + 12);
        const targetX = handoffRect
          ? handoffRect.left + 12 - bounds.left
          : fallbackLeft;
        const targetY = handoffRect
          ? handoffRect.top + 16 - bounds.top
          : 112;

        for (const snap of warpSnapshots) {
          const btn = badgeButtonsRef.current[snap.index];
          const vendor = VENDORS[snap.index];
          const trail = resolveVendorTrail(vendor, dark);

          if (snap.isActive) {
            // 蓄压期（0 ~ 9%）随星海轻微向心锁轨凝光，随后沿原二次贝塞尔弧线带惯性俯冲接入 AI 回复！
            const intakePull = 1 - warpIntake * 0.045;
            const startX = centerX + (snap.sx - centerX) * intakePull;
            const startY = centerY + (snap.sy - centerY) * intakePull;
            const rawDive = clamp((progress - 0.085) / 0.44);
            // 先蓄力抬速再顺滑制动落位（cubic in-out 偏后段稳刹），与虫洞破壁同频
            const diveP =
              rawDive < 0.5
                ? 4 * rawDive * rawDive * rawDive
                : 1 - Math.pow(-2 * rawDive + 2, 3) / 2;
            // 控制点沿原有轨道切线微微延伸，保证脱离轨道瞬间 C1 平滑无折角
            const ctrlX = (startX + targetX) * 0.5 + snap.tx * 78;
            const ctrlY = (startY + targetY) * 0.5 + snap.ty * 60;
            const curPt = quadBezierPoint(startX, startY, ctrlX, ctrlY, targetX, targetY, diveP);

            // 绘制浓缩璀璨的专属色俯冲彗星光弧（随脱离轨道无缝接棒）
            const trailIn = smoothstep(0.06, 0.14, progress);
            const trailFade = trailIn * (1 - smoothstep(0.46, 0.78, progress));
            if (trailFade > 0.01 && diveP > 0.005) {
              const tailStart = Math.max(0, diveP - 0.46);
              const segs = 18;
              context.save();
              context.lineCap = "round";
              for (let s = 0; s < segs; s++) {
                const u0 = s / segs;
                const u1 = (s + 1) / segs;
                const t0 = lerp(diveP, tailStart, u0);
                const t1 = lerp(diveP, tailStart, u1);
                const p0 = quadBezierPoint(startX, startY, ctrlX, ctrlY, targetX, targetY, t0);
                const p1 = quadBezierPoint(startX, startY, ctrlX, ctrlY, targetX, targetY, t1);
                const uMid = (u0 + u1) * 0.5;
                const col = sampleTrailColor(trail, uMid);
                const f = Math.pow(1 - uMid, 1.5) * trailFade;

                context.strokeStyle = rgba(col, clamp(0.26 * f, 0, 0.62));
                context.lineWidth = 15 * (1 - uMid * 0.75);
                context.beginPath();
                context.moveTo(p0.x, p0.y);
                context.lineTo(p1.x, p1.y);
                context.stroke();

                context.strokeStyle = rgba(col, clamp(0.9 * f, 0, 0.96));
                context.lineWidth = 3.5 * (1 - uMid * 0.78);
                context.beginPath();
                context.moveTo(p0.x, p0.y);
                context.lineTo(p1.x, p1.y);
                context.stroke();
              }

              // 俯冲光核晕
              const headGlow = context.createRadialGradient(
                curPt.x,
                curPt.y,
                2,
                curPt.x,
                curPt.y,
                36 * trailFade,
              );
              headGlow.addColorStop(0, rgba(trail.head, 0.46 * trailFade));
              headGlow.addColorStop(0.55, rgba(trail.mid, 0.18 * trailFade));
              headGlow.addColorStop(1, rgba(trail.tail, 0));
              context.fillStyle = headGlow;
              context.beginPath();
              context.arc(curPt.x, curPt.y, 36 * trailFade, 0, TAU);
              context.fill();
              context.restore();
            }

            // 抵达交汇点瞬间（0.41 ~ 0.90）：绽放一圈极克制的专属色能量接入波纹，接住破壁余势
            if (progress >= 0.41 && progress <= 0.9) {
              const rp = clamp((progress - 0.41) / 0.49);
              const easeR = 1 - Math.pow(1 - rp, 3);
              const r1 = 14 + easeR * 72;
              const a1 = (1 - rp) * 0.6;
              const col1 = sampleTrailColor(trail, rp * 0.55);

              context.save();
              context.strokeStyle = rgba(col1, a1);
              context.lineWidth = 2.1 * (1 - rp * 0.55);
              context.beginPath();
              context.arc(targetX, targetY, r1, 0, TAU);
              context.stroke();

              // 内层柔和光闪
              const flashR = 28 + easeR * 38;
              const fg = context.createRadialGradient(
                targetX,
                targetY,
                0,
                targetX,
                targetY,
                flashR,
              );
              fg.addColorStop(0, rgba(trail.head, (1 - rp) * 0.24));
              fg.addColorStop(1, rgba(trail.head, 0));
              context.fillStyle = fg;
              context.beginPath();
              context.arc(targetX, targetY, flashR, 0, TAU);
              context.fill();
              context.restore();
            }

            // DOM 选中行星徽章蓄压微胀凝光后平滑俯冲收束融入接入点
            if (btn) {
              const badgeOp = 1 - smoothstep(0.4, 0.58, progress);
              const badgeSc = lerp(1.16 + warpIntake * 0.08, 0.46, diveP);
              btn.style.opacity = badgeOp.toFixed(3);
              btn.style.pointerEvents = "none";
              btn.style.transform =
                `translate3d(${(curPt.x - 23).toFixed(2)}px, ${(curPt.y - 23).toFixed(2)}px, 0) ` +
                `scale(${badgeSc.toFixed(3)})`;
            }
          } else {
            // 未选中的 8 颗行星：蓄压期随星环微向心拉紧，推背开启后带惯性由慢到快重重甩出画框
            const dx = snap.sx - centerX;
            const dy = snap.sy - centerY;
            const dist = Math.hypot(dx, dy) || 1;
            const nx = dx / dist;
            const ny = dy / dist;
            const exitP = clamp((progress - 0.08) / 0.36);
            const heavyFling = Math.pow(exitP, 1.95);
            const intakePull = -20 * warpIntake;
            const radialOffset = intakePull + lerp(150, 360, snap.depth) * heavyFling;
            const driftX = snap.sx + nx * radialOffset + snap.tx * 24 * heavyFling;
            const driftY = snap.sy + ny * radialOffset + snap.ty * 18 * heavyFling;
            const op = (1 - smoothstep(0.14, 0.44, progress)) * lerp(0.74, 0.92, snap.depth);
            const targetSc =
              (1 - warpIntake * 0.04) *
              (snap.depth > 0.48 ? lerp(1.0, 1.32, heavyFling) : lerp(0.94, 0.72, heavyFling));
            const sc = targetSc * lerp(0.86, 1.05, snap.depth);

            if (btn) {
              btn.style.opacity = Math.max(0, op).toFixed(3);
              btn.style.pointerEvents = "none";
              btn.style.transform =
                `translate3d(${(driftX - 23).toFixed(2)}px, ${(driftY - 23).toFixed(2)}px, 0) ` +
                `scale(${sc.toFixed(3)})`;
            }
          }
        }
      } else if (planetStates.length === VENDORS.length && !activeWarp) {
        // 持续刷新最新位置与轨道彗尾快照，供发送瞬间 100% 零断层接棒
        lastPlanetStates = planetStates;
        lastOrbitSnapshots = planetStates.map((st) => ({
          index: st.index,
          sx: st.sx,
          sy: st.sy,
          tx: st.tx,
          ty: st.ty,
          depth: st.depth,
          isActive: st.isActiveProvider,
        }));

        drawPlanetTrails(context, planetStates, fade, surgeProgress, dark);
        for (const st of planetStates) {
          const btn = badgeButtonsRef.current[st.index];
          if (!btn) continue;
          const trail = resolveVendorTrail(st.vendor, dark);
          const depthScale = lerp(0.86, 1.08, st.depth);
          const depthAlpha = lerp(0.84, 1.0, st.depth);
          const activeScaleBoost = st.activeWeight * (0.1 + st.effortBoost * 0.1);
          const emphasisScale = st.isSelected
            ? 1.18
            : st.isHovered
              ? 1.13 + activeScaleBoost * 0.3
              : 1.0 + activeScaleBoost;
          const finalOpacity = st.visible * depthAlpha * fade;

          btn.style.opacity = finalOpacity.toFixed(3);
          btn.style.pointerEvents = finalOpacity > 0.35 ? "auto" : "none";
          btn.style.transform =
            `translate3d(${(st.sx - 23).toFixed(2)}px, ${(st.sy - 23).toFixed(2)}px, 0) ` +
            `scale(${(clamp(st.stagger, 0, 1.15) * depthScale * emphasisScale).toFixed(3)})`;
          // 量化并缓存 borderColor / boxShadow，避免 9 颗 backdrop-blur 行星按钮每帧重复触发昂贵重绘
          const efQ = Math.round(st.effortBoost * 50) / 50;
          const nextBorderColor =
            st.isSelected || st.isHovered || st.activeWeight > 0.25
              ? rgba(trail.head, Number(clamp(0.62 + efQ * 0.34, 0, 0.96).toFixed(2)))
              : rgba(trail.head, dark ? 0.34 : 0.22);
          if (lastBorderColors[st.index] !== nextBorderColor) {
            lastBorderColors[st.index] = nextBorderColor;
            btn.style.borderColor = nextBorderColor;
          }
          const innerRim = dark ? "0 1px 0 rgba(255,255,255,0.16) inset, " : "";
          const nextBoxShadow =
            st.isSelected || st.isHovered
              ? `${innerRim}0 0 0 2px ${rgba(trail.head, dark ? 0.56 : 0.48)}, 0 14px 32px -6px ${rgba(trail.head, dark ? 0.48 : 0.38)}`
              : st.activeWeight > 0.15
                ? `${innerRim}0 0 0 ${(2 + efQ * 1.4).toFixed(1)}px ${rgba(trail.head, Number((0.36 + efQ * 0.3).toFixed(2)))}, 0 0 ${(16 + efQ * 20).toFixed(0)}px ${rgba(trail.head, Number(((dark ? 0.34 : 0.24) + efQ * 0.28).toFixed(2)))}, 0 12px 28px -6px ${rgba(trail.head, dark ? 0.46 : 0.36)}`
                : `${innerRim}0 0 0 1px ${rgba(trail.head, dark ? 0.2 : 0.14)}, 0 10px 24px -8px ${rgba(trail.head, dark ? 0.34 : 0.2)}`;
          if (lastBoxShadows[st.index] !== nextBoxShadow) {
            lastBoxShadows[st.index] = nextBoxShadow;
            btn.style.boxShadow = nextBoxShadow;
          }
        }
      } else {
        hideAllBadges();
      }

      if (!reducedMotion) scheduleFrame();
    }

    function movePointer(event: PointerEvent) {
      if (event.pointerType !== "mouse" || motionPreference.matches) return;
      const bounds = area.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / Math.max(bounds.width, 1) - 0.5) * 0.31;
      pointer.y = ((event.clientY - bounds.top) / Math.max(bounds.height, 1) - 0.5) * 0.29;

      if (isDragging) {
        const dx = event.clientX - lastDragX;
        lastDragX = event.clientX;
        userOrbitOffset += dx * 0.0052;
        userOrbitVel = dx * 0.24;
      }
    }

    function onPointerDown(event: PointerEvent) {
      if (
        event.button !== 0 ||
        motionPreference.matches ||
        sceneRef.current.warpOrigin ||
        !sceneRef.current.planetsVisible ||
        sceneRef.current.inspectedVendorId
      ) {
        return;
      }
      if (isInteractiveTarget(event.target)) return;
      isDragging = true;
      lastDragX = event.clientX;
    }

    function onPointerUp() {
      isDragging = false;
    }

    function onWheel(event: WheelEvent) {
      if (
        motionPreference.matches ||
        sceneRef.current.warpOrigin ||
        !sceneRef.current.planetsVisible ||
        sceneRef.current.inspectedVendorId
      ) {
        return;
      }
      if (isInteractiveTarget(event.target)) return;
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      userOrbitVel += delta * 0.0028;
    }

    function resetPointer() {
      pointer.x = 0;
      pointer.y = 0;
      isDragging = false;
    }

    function updateVisibility() {
      if (document.hidden) {
        if (frameId !== null) window.cancelAnimationFrame(frameId);
        frameId = null;
        previousFrame = 0;
      } else {
        scheduleFrame();
      }
    }

    const observer = new ResizeObserver(scheduleFrame);
    observer.observe(canvas);
    if (composerRef.current) observer.observe(composerRef.current);
    area.addEventListener("pointermove", movePointer, { passive: true });
    area.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointerup", onPointerUp);
    area.addEventListener("wheel", onWheel, { passive: true });
    area.addEventListener("pointerleave", resetPointer);
    motionPreference.addEventListener("change", scheduleFrame);
    document.addEventListener("visibilitychange", updateVisibility);
    requestPaintRef.current = scheduleFrame;
    paintNowRef.current = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
        frameId = null;
      }
      previousFrame = 0;
      draw(performance.now());
    };
    scheduleFrame();

    return () => {
      requestPaintRef.current = null;
      paintNowRef.current = null;
      if (frameId !== null) window.cancelAnimationFrame(frameId);
      observer.disconnect();
      area.removeEventListener("pointermove", movePointer);
      area.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointerup", onPointerUp);
      area.removeEventListener("wheel", onWheel);
      area.removeEventListener("pointerleave", resetPointer);
      motionPreference.removeEventListener("change", scheduleFrame);
      document.removeEventListener("visibilitychange", updateVisibility);
    };
  }, [composerRef]);

  return (
    <>
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
      />
      <div className="pointer-events-none absolute inset-0 z-[15] overflow-hidden">
        {VENDORS.map((vendor, index) => (
          <button
            key={vendor.id}
            ref={(el) => {
              badgeButtonsRef.current[index] = el;
            }}
            type="button"
            aria-label={vendor.id}
            title={`查看 ${vendor.id} 模型特性动效`}
            onPointerEnter={() => {
              hoveredIdxRef.current = index;
            }}
            onPointerLeave={() => {
              if (hoveredIdxRef.current === index) hoveredIdxRef.current = -1;
            }}
            onClick={(e) => {
              e.stopPropagation();
              onSelectVendorRef.current?.(vendor.id);
            }}
            className="absolute left-0 top-0 flex h-[46px] w-[46px] cursor-pointer items-center justify-center rounded-full border-[1.5px] border-white bg-white/95 opacity-0 backdrop-blur-md transition-[background-color] duration-200 hover:bg-white dark:border-white/15 dark:bg-[#121522]/82 dark:hover:bg-[#191D30]/95"
            style={{ willChange: "transform, opacity" }}
          >
            <canvas
              ref={(el) => {
                badgeCanvasesRef.current[index] = el;
              }}
              className="block h-[28px] w-[28px]"
            />
          </button>
        ))}
      </div>
    </>
  );
}
