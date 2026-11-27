import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { effortColor, type EffortAppearance } from "../effortAppearance";
import type { EffortVisual } from "../types";
import {
  TAU,
  TRAIL_MOTES,
  VENDORS,
  clamp,
  easeOutBack,
  lerp,
  mixRgb,
  rgba,
  sampleTrailColor,
  smoothstep,
  type RgbColor,
  type VendorId,
  type VendorSpec,
} from "./intro/vendorCatalog";

export const WELCOME_WARP_DURATION = 780;

const EFFECT_COLORS: Record<EffortVisual["effect"], RgbColor> = {
  glint: [184, 100, 145],
  align: [152, 93, 170],
  double_arc: [191, 120, 153],
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
  });
  const requestPaintRef = useRef<(() => void) | null>(null);

  useLayoutEffect(() => {
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
    };
    requestPaintRef.current?.();
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
  ]);

  // 一次性绘制 9 枚高 DPI 官方微缩厂商标识到对应徽章 Canvas
  useEffect(() => {
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
      VENDORS[i].drawMark(c, 11.4, 0, 0);
    }
  }, []);

  useEffect(() => {
    const element = canvasRef.current;
    const parent = element?.parentElement;
    const drawingContext = element?.getContext("2d");
    if (!element || !parent || !drawingContext) return;
    const canvas = element;
    const area = parent;
    const context = drawingContext;

    const particles = createParticles();
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
    let warpSnapshots: WarpPlanetSnapshot[] | null = null;
    let lastOrbitSnapshots: WarpPlanetSnapshot[] = [];
    let displayedVisual = sceneRef.current.visual;
    let visualStrength = displayedVisual ? 1 : 0;
    let lastModelKey = sceneRef.current.modelKey;
    let lastEntry = sceneRef.current.entry;
    let lastRevealKey = sceneRef.current.revealKey;
    let lastActiveProvider = sceneRef.current.activeProvider;
    let lastTargetEffort = sceneRef.current.effortIntensity;
    let smoothedEffort = sceneRef.current.effortIntensity;
    let surgeStartedAt = -Infinity;
    const activeWeights = VENDORS.map((v) =>
      v.id === sceneRef.current.activeProvider ? 1 : 0,
    );
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
    ) {
      ctx.save();
      ctx.lineCap = "round";

      for (const b of states) {
        if (b.visible <= 0.02) continue;
        const trail = b.vendor.trail;
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

          // (1) 持续向外荡漾的双层专属色呼吸能量环（随推理强度增强速度、范围与亮度）
          const ringCount = ef > 0.38 ? 2 : 1;
          const pulseRate = 0.42 + ef * 0.58;
          for (let k = 0; k < ringCount; k++) {
            const p = (orbitTime * pulseRate + k / ringCount) % 1;
            const rr = 25 + p * (18 + ef * 32);
            const ringCol = sampleTrailColor(trail, p);
            const ringAlpha = Math.sin(p * Math.PI) * (0.28 + ef * 0.48) * aw;
            ctx.strokeStyle = rgba(ringCol, clamp(ringAlpha, 0, 0.8));
            ctx.lineWidth = 1.2 + (1 - p) * (0.8 + ef * 1.1);
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
            const spinSpeed = 1.35 + ef * 2.15;
            for (let si = 0; si < sparkCount; si++) {
              const frac = si / sparkCount;
              const isOuter = si % 2 === 1 && ef > 0.48;
              const a =
                orbitTime * (isOuter ? -spinSpeed * 0.78 : spinSpeed) + frac * TAU;
              const orbR = (isOuter ? 38 + ef * 12 : 30 + ef * 6) + Math.sin(orbitTime * 3.4 + si * 1.7) * 2.4;
              const ex = b.sx + Math.cos(a) * orbR;
              const ey = b.sy + Math.sin(a) * orbR * (isOuter ? 0.82 : 0.92);
              const tang = a + (isOuter ? -Math.PI * 0.5 : Math.PI * 0.5);
              const sparkLen = (2.8 + ef * 5.2) * (isOuter ? 0.85 : 1.05);
              const sCol = sampleTrailColor(trail, frac);
              const twinkle = 0.55 + 0.45 * Math.sin(orbitTime * 5.2 + si * 2.1);

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
      } = sceneRef.current;

      if (curRevealKey !== lastRevealKey) {
        lastRevealKey = curRevealKey;
        revealStartedAt = now;
      }

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
          // 捕获发送瞬间 9 颗行星各自的实时轨道坐标与切线，确保俯冲脱离轨道 C1 连续无跳变
          warpSnapshots =
            lastOrbitSnapshots.length === VENDORS.length
              ? lastOrbitSnapshots.map((item) => ({
                  ...item,
                  isActive: VENDORS[item.index].id === curProvider,
                }))
              : null;
        } else {
          warpSnapshots = null;
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
      const fieldX = reducedMotion ? 0 : follow.x + Math.sin(motionTime * 0.47) * 0.047;
      const fieldY = reducedMotion ? 0 : follow.y + Math.sin(motionTime * 0.63) * 0.036;
      const breathing =
        Math.sin(motionTime * 1.52) * 0.052 + Math.sin(motionTime * 2.7) * 0.019;
      const ringRadius = 1 + breathing * breathAmplitude;
      // 跃迁阶段改为克制顺滑的「短促向心微吸 → 轻盈化雾消散」，绝不炸得满屏乱飞
      const charge = Math.min(1, progress / 0.24);
      const release = Math.min(1, Math.max(0, (progress - 0.22) / 0.72));
      const spread = release * release * (3 - 2 * release);
      const displacement = -0.095 * charge + 0.34 * spread;

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
      const alignedArc = displayedVisual?.effect === "align" ? visualStrength * settled : 0;
      const outerArc = displayedVisual?.effect === "double_arc" ? visualStrength * settled : 0;
      const fade = activeWarp
        ? 1 - Math.pow(clamp((progress - 0.24) / 0.68), 1.35)
        : 1;
      const arcPhase = 1.33 + Math.sin(motionTime * 0.27) * 0.44;

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
        const orbitExpand = 1 + displacement * 0.92;
        const orbitRx = baseOrbitRx * orbitExpand;
        const orbitRy = baseOrbitRy * orbitExpand;
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

      // 1) 光晕轻铺在输入框外围，粒子环浮在其上。
      context.save();
      context.translate(centerX + fieldX * radiusX, centerY + fieldY * radiusY);
      context.rotate(tilt);
      const glowScale = 1 + displacement * 0.3;
      context.scale(radiusX * 1.4 * glowScale, radiusY * 1.42 * glowScale);
      const glow = context.createRadialGradient(-0.12, 0.12, 0, 0, 0, 1);
      glow.addColorStop(0, effortColor(palette.accent, 0.088));
      glow.addColorStop(0.56, effortColor(palette.base, 0.095));
      glow.addColorStop(1, effortColor(palette.base, 0));
      context.globalAlpha = fade;
      context.fillStyle = glow;
      context.fillRect(-1, -1, 2, 2);
      context.restore();

      // 2) 粒子星环（融合 9 颗行星 74px 局部物理微引力分流与专属色染色）
      context.lineCap = "round";
      const wakeRadius = 74;
      const wakeEnabled = planetStates.length === VENDORS.length && !activeWarp;

      for (const particle of particles) {
        const x = particle.x + Math.sin(motionTime * 0.95 + particle.phase) * 0.018;
        const y = particle.y + Math.cos(motionTime * 0.81 + particle.phase) * 0.024;
        const localX = x - fieldX;
        const localY = y - fieldY;
        const angle = Math.atan2(localY, localX);
        const distance = Math.hypot(localX, localY);
        const offset = distance - ringRadius + Math.sin(angle * 4 + motionTime) * 0.023;
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
        const highlight =
          ringBand * (movingLight + restingLight) + alignment * 0.38 + secondArc * 0.53;

        const radialAdjustment =
          alignment * (ringRadius - distance) * 0.88 +
          secondArc * (ringRadius + 0.17 - distance) * 0.82;
        const alignmentScale = 1 + radialAdjustment / Math.max(distance, 0.01);
        const push =
          alignmentScale *
          (1 + displacement * (0.22 + strength * (0.8 + particle.seed * 0.53)));
        const px = (fieldX + localX * push) * radiusX;
        const py = (fieldY + localY * push) * radiusY;
        let screenX = centerX + px * tiltCos - py * tiltSin;
        let screenY = centerY + px * tiltSin + py * tiltCos;

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
            const colorU = bestB.vendor.trail.spectrum
              ? (Math.atan2(bestDy, bestDx) / TAU + 1) % 1
              : clamp(1 - bestW);
            wakeRgb = sampleTrailColor(bestB.vendor.trail, colorU);
          }
        }

        if (strength < 0.06 && highlight < 0.04 && wakeWeight < 0.08 && particle.seed < 0.47) {
          continue;
        }

        const length =
          0.42 +
          particle.seed * 0.72 +
          strength * (3.25 + spread * 12) * palette.trail +
          highlight * 4 +
          wakeWeight * 5.2;
        let direction =
          Math.atan2(localY * radiusY, localX * radiusX) +
          tilt +
          Math.sin(particle.phase + motionTime) * 0.18;
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
            strength * (0.7 + progress * 0.38) +
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
      context.globalAlpha = 1;

      // 3) 常规待命态 vs 首次发送跃迁态（选中行星俯冲精准落入首条 AI 回复缩微星核 + 其余 8 颗温柔退幕）
      if (activeWarp && warpSnapshots && warpSnapshots.length === VENDORS.length) {
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
          const trail = vendor.trail;

          if (snap.isActive) {
            // 当前选中模型行星：沿二次贝塞尔弧线俯冲注入任务发射点！
            const rawDive = clamp(progress / 0.46);
            const diveP = rawDive * rawDive * (3 - 2 * rawDive);
            // 控制点沿原有轨道切线微微延伸，保证脱离轨道瞬间 C1 平滑无折角
            const ctrlX = (snap.sx + targetX) * 0.5 + snap.tx * 74;
            const ctrlY = (snap.sy + targetY) * 0.5 + snap.ty * 58;
            const curPt = quadBezierPoint(snap.sx, snap.sy, ctrlX, ctrlY, targetX, targetY, diveP);

            // 绘制浓缩璀璨的专属色俯冲彗星光弧（从身后跟随至当前点）
            const trailFade = 1 - smoothstep(0.36, 0.72, progress);
            if (trailFade > 0.01) {
              const tailStart = Math.max(0, diveP - 0.44);
              const segs = 18;
              context.save();
              context.lineCap = "round";
              for (let s = 0; s < segs; s++) {
                const u0 = s / segs;
                const u1 = (s + 1) / segs;
                const t0 = lerp(diveP, tailStart, u0);
                const t1 = lerp(diveP, tailStart, u1);
                const p0 = quadBezierPoint(snap.sx, snap.sy, ctrlX, ctrlY, targetX, targetY, t0);
                const p1 = quadBezierPoint(snap.sx, snap.sy, ctrlX, ctrlY, targetX, targetY, t1);
                const uMid = (u0 + u1) * 0.5;
                const col = sampleTrailColor(trail, uMid);
                const f = Math.pow(1 - uMid, 1.5) * trailFade;

                context.strokeStyle = rgba(col, clamp(0.24 * f, 0, 0.6));
                context.lineWidth = 14 * (1 - uMid * 0.75);
                context.beginPath();
                context.moveTo(p0.x, p0.y);
                context.lineTo(p1.x, p1.y);
                context.stroke();

                context.strokeStyle = rgba(col, clamp(0.88 * f, 0, 0.96));
                context.lineWidth = 3.4 * (1 - uMid * 0.78);
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
                34 * trailFade,
              );
              headGlow.addColorStop(0, rgba(trail.head, 0.42 * trailFade));
              headGlow.addColorStop(0.55, rgba(trail.mid, 0.16 * trailFade));
              headGlow.addColorStop(1, rgba(trail.tail, 0));
              context.fillStyle = headGlow;
              context.beginPath();
              context.arc(curPt.x, curPt.y, 34 * trailFade, 0, TAU);
              context.fill();
              context.restore();
            }

            // 抵达交汇点瞬间（0.34 ~ 0.84）：绽放一圈极克制的专属色能量接入波纹
            if (progress >= 0.34 && progress <= 0.88) {
              const rp = clamp((progress - 0.34) / 0.52);
              const easeR = 1 - Math.pow(1 - rp, 3);
              const r1 = 14 + easeR * 68;
              const a1 = (1 - rp) * 0.58;
              const col1 = sampleTrailColor(trail, rp * 0.55);

              context.save();
              context.strokeStyle = rgba(col1, a1);
              context.lineWidth = 2.0 * (1 - rp * 0.55);
              context.beginPath();
              context.arc(targetX, targetY, r1, 0, TAU);
              context.stroke();

              // 内层柔和光闪
              const flashR = 26 + easeR * 36;
              const fg = context.createRadialGradient(
                targetX,
                targetY,
                0,
                targetX,
                targetY,
                flashR,
              );
              fg.addColorStop(0, rgba(trail.head, (1 - rp) * 0.22));
              fg.addColorStop(1, rgba(trail.head, 0));
              context.fillStyle = fg;
              context.beginPath();
              context.arc(targetX, targetY, flashR, 0, TAU);
              context.fill();
              context.restore();
            }

            // DOM 选中行星徽章平滑俯冲并收束融入接入点
            if (btn) {
              const badgeOp = 1 - smoothstep(0.32, 0.52, progress);
              const badgeSc = lerp(1.16, 0.46, diveP);
              btn.style.opacity = badgeOp.toFixed(3);
              btn.style.pointerEvents = "none";
              btn.style.transform =
                `translate3d(${(curPt.x - 23).toFixed(2)}px, ${(curPt.y - 23).toFixed(2)}px, 0) ` +
                `scale(${badgeSc.toFixed(3)})`;
            }
          } else {
            // 未选中的 8 颗行星：温柔减速向外微飘并安静淡出退幕
            const exitP = clamp(progress / 0.46);
            const easeExit = 1 - Math.pow(1 - exitP, 3);
            const dx = snap.sx - centerX;
            const dy = snap.sy - centerY;
            const dist = Math.hypot(dx, dy) || 1;
            const driftX = snap.sx + (dx / dist) * 22 * easeExit + snap.tx * 12 * easeExit;
            const driftY = snap.sy + (dy / dist) * 16 * easeExit + snap.ty * 10 * easeExit;
            const op = (1 - exitP) * lerp(0.7, 0.86, snap.depth);
            const sc = lerp(0.95, 0.78, easeExit) * lerp(0.86, 1.05, snap.depth);

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
        // 持续刷新最新位置快照，供发送瞬间无缝接棒
        lastOrbitSnapshots = planetStates.map((st) => ({
          index: st.index,
          sx: st.sx,
          sy: st.sy,
          tx: st.tx,
          ty: st.ty,
          depth: st.depth,
          isActive: st.isActiveProvider,
        }));

        drawPlanetTrails(context, planetStates, fade, surgeProgress);
        for (const st of planetStates) {
          const btn = badgeButtonsRef.current[st.index];
          if (!btn) continue;
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
          btn.style.borderColor =
            st.isSelected || st.isHovered || st.activeWeight > 0.25
              ? rgba(st.vendor.trail.head, clamp(0.62 + st.effortBoost * 0.34, 0, 0.96))
              : rgba(st.vendor.trail.head, 0.22);
          btn.style.boxShadow =
            st.isSelected || st.isHovered
              ? `0 0 0 2px ${rgba(st.vendor.trail.head, 0.48)}, 0 14px 32px -6px ${rgba(st.vendor.trail.head, 0.38)}`
              : st.activeWeight > 0.15
                ? `0 0 0 ${(2 + st.effortBoost * 1.4).toFixed(1)}px ${rgba(st.vendor.trail.head, 0.32 + st.effortBoost * 0.3)}, 0 0 ${(14 + st.effortBoost * 18).toFixed(0)}px ${rgba(st.vendor.trail.head, 0.24 + st.effortBoost * 0.28)}, 0 12px 28px -6px ${rgba(st.vendor.trail.head, 0.36)}`
                : `0 0 0 1px ${rgba(st.vendor.trail.head, 0.14)}, 0 10px 22px -8px ${rgba(st.vendor.trail.head, 0.2)}`;
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
    scheduleFrame();

    return () => {
      requestPaintRef.current = null;
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
            className="absolute left-0 top-0 flex h-[46px] w-[46px] cursor-pointer items-center justify-center rounded-full border-[1.5px] border-white bg-white/95 opacity-0 backdrop-blur-md transition-[background-color] duration-200 hover:bg-white"
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
