import { useEffect, useRef, useState } from "react";
import {
  CARD_BUFFER_H,
  CARD_BUFFER_W,
  DEFAULT_STUDIO,
  TAU,
  VENDORS,
  clamp,
  drawCardAmbientBg,
  easeInOutCubic,
  easeInOutQuart,
  easeOutCubic,
  easeOutQuint,
  easeSpringOut,
  lerp,
  mixRgb,
  rgba,
  smoothstep,
  type RgbColor,
  type VendorSpec,
} from "./vendorCatalog";

interface OpeningConstellationIntroProps {
  open: boolean;
  onReveal: () => void;
  onFinish: () => void;
}

const COUNT = VENDORS.length;
const ACT1_END = 2.55;
const SLOT_DUR = 1.68;
const ACT2_END = ACT1_END + COUNT * SLOT_DUR; // 17.67s
const REFORM_END = ACT2_END + 0.9; // 18.57s
const CONVERGE_END = REFORM_END + 1.28; // 19.85s
const HANDOFF_END = CONVERGE_END + 0.85; // 20.70s：平滑交接淡出进入真实工作台
const SPEEDS = [0.7, 1.0, 1.4] as const;

interface CardNodeState {
  index: number;
  vendor: VendorSpec;
  el: HTMLDivElement;
  sheen: HTMLDivElement;
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  screenX: number;
  screenY: number;
  prevX: number | null;
  prevY: number | null;
  velX: number;
  velY: number;
  scale: number;
  opacity: number;
  z: number;
  focus: number;
  depthNorm: number;
}

function createAmbientParticles() {
  const cols = 44;
  const rows = 23;
  return Array.from({ length: cols * rows }, (_, i) => ({
    x: (((i % cols) + Math.random()) / cols - 0.5) * 2.55,
    y: ((Math.floor(i / cols) + Math.random()) / rows - 0.5) * 2.7,
    seed: Math.random(),
    phase: Math.random() * TAU,
  }));
}

function arcLight(angle: number, center: number, width: number): number {
  const d = Math.abs(angle - center);
  const off = Math.min(d, TAU - d);
  return Math.exp(-((off / width) ** 2));
}

export function OpeningConstellationIntro({
  open,
  onReveal,
  onFinish,
}: OpeningConstellationIntroProps) {
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const fieldCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const cardsLayerRef = useRef<HTMLDivElement | null>(null);
  const dotButtonsRef = useRef<(HTMLButtonElement | null)[]>([]);

  const [playing, setPlaying] = useState(true);
  const [speedIdx, setSpeedIdx] = useState(1);

  const playingRef = useRef(playing);
  const speedIdxRef = useRef(speedIdx);
  const timelineRef = useRef(0);
  const onRevealRef = useRef(onReveal);
  const onFinishRef = useRef(onFinish);

  playingRef.current = playing;
  speedIdxRef.current = speedIdx;
  onRevealRef.current = onReveal;
  onFinishRef.current = onFinish;

  const jumpToVendor = (index: number) => {
    timelineRef.current = ACT1_END + index * SLOT_DUR + 0.32;
    setPlaying(true);
  };

  const replay = () => {
    timelineRef.current = 0;
    setPlaying(true);
  };

  const skipToWorkbench = () => {
    onRevealRef.current();
    onFinishRef.current();
  };

  useEffect(() => {
    if (!open) return;

    const overlay = overlayRef.current;
    const fieldCanvas = fieldCanvasRef.current;
    const cardsLayer = cardsLayerRef.current;
    const fieldCtx = fieldCanvas?.getContext("2d");
    if (!overlay || !fieldCanvas || !cardsLayer || !fieldCtx) return;

    const particles = createAmbientParticles();
    const pointer = { x: 0, y: 0 };
    const follow = { x: 0, y: 0 };

    let width = window.innerWidth;
    let height = window.innerHeight;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    let ambientTime = 0;
    let lastTimestamp = 0;
    let frameId: number | null = null;
    let revealed = false;
    let finished = false;
    timelineRef.current = 0;

    let curBgCenter: RgbColor = [...DEFAULT_STUDIO.bgCenter];
    let curBgEdge: RgbColor = [...DEFAULT_STUDIO.bgEdge];
    let curHaloA: RgbColor = [...DEFAULT_STUDIO.haloA];
    let curHaloB: RgbColor = [...DEFAULT_STUDIO.haloB];
    let curHaloAlpha = DEFAULT_STUDIO.haloAlpha;
    let curTint: RgbColor = [124, 58, 237];
    let curAccent: RgbColor = [216, 55, 126];

    // 初始化 9 张固定高清缓冲的 3D 卡片
    cardsLayer.innerHTML = "";
    const cardNodes: CardNodeState[] = [];
    for (let index = 0; index < COUNT; index++) {
      const vendor = VENDORS[index];
      const el = document.createElement("div");
      el.className = "intro-vendor-card";

      const cv = document.createElement("canvas");
      cv.width = Math.round(CARD_BUFFER_W * dpr);
      cv.height = Math.round(CARD_BUFFER_H * dpr);
      const ctx = cv.getContext("2d");
      if (!ctx) continue;

      const sheen = document.createElement("div");
      sheen.className = "intro-card-sheen";

      el.appendChild(cv);
      el.appendChild(sheen);
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        jumpToVendor(index);
      });
      cardsLayer.appendChild(el);

      cardNodes.push({
        index,
        vendor,
        el,
        sheen,
        canvas: cv,
        ctx,
        screenX: 0,
        screenY: 0,
        prevX: null,
        prevY: null,
        velX: 0,
        velY: 0,
        scale: 1,
        opacity: 0,
        z: 0,
        focus: 0,
        depthNorm: 0.5,
      });
    }

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      fieldCanvas.width = Math.max(1, Math.round(width * dpr));
      fieldCanvas.height = Math.max(1, Math.round(height * dpr));
      fieldCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    const onPointerMove = (e: PointerEvent) => {
      pointer.x = (e.clientX / Math.max(window.innerWidth, 1) - 0.5) * 2;
      pointer.y = (e.clientY / Math.max(window.innerHeight, 1) - 0.5) * 2;
    };
    const onPointerLeave = () => {
      pointer.x = 0;
      pointer.y = 0;
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "r" || e.key === "R") {
        timelineRef.current = 0;
        revealed = false;
        setPlaying(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        skipToWorkbench();
      }
    };

    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerleave", onPointerLeave);
    window.addEventListener("keydown", onKeyDown);

    const render = (now: number) => {
      if (finished) return;

      const dtMs = lastTimestamp ? Math.min(now - lastTimestamp, 45) : 16.67;
      lastTimestamp = now;
      const dt = dtMs / 1000;

      ambientTime += dt;
      if (playingRef.current) {
        timelineRef.current += dt * SPEEDS[speedIdxRef.current];
      }
      const T = timelineRef.current;

      // 当 9 道彗尾在中心汇合并爆发光核时，通知外层提前唤醒真实「智启 Agent」欢迎页
      if (T >= CONVERGE_END - 0.08 && !revealed) {
        revealed = true;
        onRevealRef.current();
      }
      if (T >= HANDOFF_END) {
        finished = true;
        onFinishRef.current();
        return;
      }

      const easeFollow = 1 - Math.exp(-dt * 6.5);
      follow.x += (pointer.x - follow.x) * easeFollow;
      follow.y += (pointer.y - follow.y) * easeFollow;

      // 编排阶段计算
      const assembleP = clamp(T / ACT1_END);
      const reformP = smoothstep(ACT2_END, REFORM_END, T);
      const convergeP = smoothstep(REFORM_END, CONVERGE_END, T);
      const handoffP = smoothstep(CONVERGE_END, HANDOFF_END, T);

      const baseStep = TAU / COUNT;
      let ringYaw = Math.PI / 2;
      let activeIndex = -1;
      let activeSlotT = 0;
      let activeFocusStrength = 0;

      if (T < ACT1_END) {
        ringYaw = Math.PI / 2 - 0.42 + easeInOutCubic(assembleP) * 0.42;
      } else if (T < ACT2_END) {
        const elapsed = T - ACT1_END;
        const slotIdx = Math.min(COUNT - 1, Math.floor(elapsed / SLOT_DUR));
        const local = (elapsed - slotIdx * SLOT_DUR) / SLOT_DUR;
        activeIndex = slotIdx;
        activeSlotT = local;

        const prevYaw = Math.PI / 2 - Math.max(0, slotIdx - 1) * baseStep;
        const targetYaw = Math.PI / 2 - slotIdx * baseStep;
        const turnT = slotIdx === 0 ? 1 : easeInOutQuart(clamp(local / 0.25));
        ringYaw = lerp(prevYaw, targetYaw, turnT);

        const enterSpring = easeSpringOut(clamp(local / 0.23));
        const exit = 1 - easeInOutCubic(clamp((local - 0.8) / 0.2));
        activeFocusStrength = clamp(Math.min(enterSpring, exit), 0, 1.045);
      } else {
        const endBaseYaw = Math.PI / 2 - (COUNT - 1) * baseStep;
        ringYaw = endBaseYaw - reformP * 0.85 - Math.pow(convergeP, 1.75) * 4.8;
      }

      const anyFocus = clamp(activeFocusStrength);
      const rollTilt =
        T < ACT1_END
          ? lerp(-0.24, -0.17, easeInOutCubic(assembleP))
          : T < ACT2_END
            ? lerp(-0.17, -0.09, anyFocus)
            : lerp(-0.17, -0.16, Math.max(reformP, convergeP));

      const pitchTilt =
        T < ACT1_END
          ? lerp(0.36, 0.5, easeInOutCubic(assembleP))
          : T < ACT2_END
            ? lerp(0.5, 0.4, anyFocus)
            : lerp(0.5, 0.55, convergeP);

      const dynamicFov = lerp(1360, 960, anyFocus);
      cardsLayer.style.perspective = `${Math.round(dynamicFov)}px`;

      // 平滑淡出交接给真实工作台（保留干净纯白光爆过渡）
      overlay.style.opacity = (1 - easeInOutCubic(handoffP)).toFixed(4);

      const spotlightIdx = activeFocusStrength > 0.08 ? activeIndex : -1;
      const targetStudio = spotlightIdx >= 0 ? VENDORS[spotlightIdx].studio : DEFAULT_STUDIO;
      const targetTint: RgbColor = spotlightIdx >= 0 ? VENDORS[spotlightIdx].primary : [124, 58, 237];
      const targetAccent: RgbColor = spotlightIdx >= 0 ? VENDORS[spotlightIdx].accent : [216, 55, 126];

      const gradeSpeed = 1 - Math.exp(-dt * 4.8);
      curBgCenter = mixRgb(curBgCenter, targetStudio.bgCenter, gradeSpeed);
      curBgEdge = mixRgb(curBgEdge, targetStudio.bgEdge, gradeSpeed);
      curHaloA = mixRgb(curHaloA, targetStudio.haloA, gradeSpeed);
      curHaloB = mixRgb(curHaloB, targetStudio.haloB, gradeSpeed);
      curHaloAlpha = lerp(curHaloAlpha, targetStudio.haloAlpha, gradeSpeed);
      curTint = mixRgb(curTint, targetTint, gradeSpeed);
      curAccent = mixRgb(curAccent, targetAccent, gradeSpeed);

      // 同步底部 9 个纯圆点高亮状态
      for (let i = 0; i < COUNT; i++) {
        const dot = dotButtonsRef.current[i];
        if (!dot) continue;
        const isAct = i === spotlightIdx;
        dot.dataset.active = isAct ? "1" : "0";
        dot.style.backgroundColor = isAct
          ? rgba(VENDORS[i].primary, 0.94)
          : "rgba(15, 23, 42, 0.26)";
      }

      const cx = width * 0.5;
      const cy = height * 0.465;
      const ringRadius = Math.min(width * 0.355, height * 0.375, 405);
      const cardW = Math.round(clamp(ringRadius * 0.44, 122, 174));
      const cardH = Math.round(cardW * 0.64);

      const cosRoll = Math.cos(rollTilt);
      const sinRoll = Math.sin(rollTilt);
      const cosPitch = Math.cos(pitchTilt);
      const sinPitch = Math.sin(pitchTilt);

      for (const node of cardNodes) {
        const i = node.index;
        let appear = 1;
        let fanRatio = 1;
        if (T < ACT1_END) {
          if (i === 0) {
            appear = easeOutCubic(clamp(T / 0.48));
            fanRatio = easeInOutCubic(clamp((T - 0.28) / 0.98));
          } else {
            const startT = 0.22 + (i - 1) * 0.155;
            appear = easeOutCubic(clamp((T - startT) / 0.36));
            fanRatio = easeOutQuint(clamp((T - startT) / 0.95));
          }
        }

        const slotAngle = i * baseStep - (1 - fanRatio) * 0.36;
        const worldAngle = ringYaw + slotAngle;

        const openRadiusScale =
          i === 0 && T < ACT1_END
            ? lerp(0.18, 1, easeInOutCubic(clamp((T - 0.28) / 0.9)))
            : lerp(0.76, 1, fanRatio);

        const collapseScale = 1 - easeInOutQuart(convergeP);
        const otherFocusAmt = i !== activeIndex ? anyFocus : 0;
        const rNow = ringRadius * openRadiusScale * collapseScale * (1 + otherFocusAmt * 0.17);

        const ox = Math.cos(worldAngle) * rNow;
        const oz = Math.sin(worldAngle) * rNow;
        const bob = Math.sin(ambientTime * 1.45 + i * 0.85) * 4.0 * (1 - convergeP);

        const py1 = bob * cosPitch + oz * sinPitch;
        const pz1 = -bob * sinPitch + oz * cosPitch;
        const px2 = ox * cosRoll - py1 * sinRoll;
        const py2 = ox * sinRoll + py1 * cosRoll;

        const persp = dynamicFov / (dynamicFov - pz1);
        const orbitScreenX = cx + px2 * persp + follow.x * 16 * (1 + pz1 / 540);
        const orbitScreenY = cy + py2 * persp + follow.y * 10 * (1 + pz1 / 540);
        const depthNorm = clamp((pz1 + ringRadius) / (2 * ringRadius));
        const orbitScale = persp * lerp(0.84, 1.05, depthNorm);

        const orbitRotY = -Math.cos(worldAngle) * 30 * (1 - convergeP);
        const orbitRotX = 8.5 * (1 - convergeP);
        const orbitRotZ = ((rollTilt * 180) / Math.PI) * 0.42;

        const focusRaw = i === activeIndex && activeFocusStrength > 0 ? activeFocusStrength : 0;
        const focus = clamp(focusRaw);
        const showcaseT = i === activeIndex ? activeSlotT : 0;

        const focusTargetX = cx + follow.x * 10;
        const focusTargetY = cy - 4 + follow.y * 8;
        const focusTargetScale = Math.min((width * 0.58) / cardW, (height * 0.48) / cardH, 2.26);

        const x = lerp(orbitScreenX, focusTargetX, focusRaw);
        const y = lerp(orbitScreenY, focusTargetY, focusRaw);
        const scale = lerp(
          orbitScale * (0.45 + 0.55 * appear) * (1 - convergeP * 0.86),
          focusTargetScale,
          focusRaw,
        );

        if (node.prevX === null || node.prevY === null || dt <= 0.0001) {
          node.velX = 0;
          node.velY = 0;
        } else {
          const instVx = clamp((x - node.prevX) / (dt * 1000), -2.2, 2.2);
          const instVy = clamp((y - node.prevY) / (dt * 1000), -2.2, 2.2);
          const vEase = 1 - Math.exp(-dt * 12);
          node.velX = lerp(node.velX, instVx, vEase);
          node.velY = lerp(node.velY, instVy, vEase);
        }
        node.prevX = x;
        node.prevY = y;

        const rotY = lerp(orbitRotY, follow.x * 7, focus) + clamp(node.velX * 7.5, -14, 14);
        const rotX = lerp(orbitRotX, -follow.y * 5.2, focus) + clamp(-node.velY * 5.5, -10, 10);
        const rotZ = lerp(orbitRotZ, 0, focus) + clamp(node.velX * 2.2, -5, 5);

        let opacity = appear * lerp(0.78, 1.0, depthNorm);
        if (otherFocusAmt > 0) opacity *= lerp(1, 0.22, otherFocusAmt);
        if (convergeP > 0) opacity *= 1 - smoothstep(0.66, 0.98, convergeP);
        if (focus > 0) opacity = Math.max(opacity, focus);

        const depthBlur = (1 - depthNorm) * 0.65;
        const blurPx =
          focus > 0.05 ? (1 - focus) * depthBlur : depthBlur + otherFocusAmt * 3.1;

        node.screenX = x;
        node.screenY = y;
        node.scale = scale;
        node.opacity = clamp(opacity);
        node.z = focus > 0.1 ? 900 : pz1;
        node.focus = focus;
        node.depthNorm = depthNorm;

        const el = node.el;
        el.style.width = `${cardW}px`;
        el.style.height = `${cardH}px`;
        el.style.opacity = node.opacity.toFixed(4);
        el.style.zIndex = String(Math.round(node.z + 500));
        el.style.pointerEvents = node.opacity > 0.12 ? "auto" : "none";
        el.style.filter = blurPx > 0.08 ? `blur(${blurPx.toFixed(2)}px)` : "none";
        el.style.transform =
          `translate3d(${(x - cardW / 2).toFixed(2)}px, ${(y - cardH / 2).toFixed(2)}px, 0) ` +
          `scale(${scale.toFixed(4)}) ` +
          `rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg) rotateZ(${rotZ.toFixed(2)}deg)`;

        const sheenShiftX = clamp(-rotY * 2.1 + follow.x * 16, -68, 68);
        const sheenShiftY = clamp(-rotX * 1.6 + follow.y * 10, -40, 40);
        const angleGlint = Math.exp(-Math.pow((rotY + 8) / 22, 2));
        const sheenAlpha = clamp(0.18 + angleGlint * 0.42 + focus * 0.18, 0.15, 0.78);
        node.sheen.style.transform = `translate3d(${sheenShiftX.toFixed(1)}%, ${sheenShiftY.toFixed(1)}%, 0)`;
        node.sheen.style.opacity = sheenAlpha.toFixed(3);

        if (focus > 0.04) {
          const pCol = node.vendor.primary;
          el.style.boxShadow =
            `0 1.5px 0 rgba(255,255,255,1) inset, ` +
            `0 0 0 1px rgba(${pCol[0]},${pCol[1]},${pCol[2]},${(0.16 + focus * 0.18).toFixed(3)}), ` +
            `0 40px 88px -24px rgba(15,23,42,${(0.18 + focus * 0.1).toFixed(3)}), ` +
            `0 20px 52px -14px rgba(${pCol[0]},${pCol[1]},${pCol[2]},${(focus * 0.24).toFixed(3)})`;
        } else {
          el.style.boxShadow = "";
        }

        if (node.opacity > 0.015) {
          const c = node.ctx;
          c.setTransform(
            node.canvas.width / CARD_BUFFER_W,
            0,
            0,
            node.canvas.height / CARD_BUFFER_H,
            0,
            0,
          );
          c.clearRect(0, 0, CARD_BUFFER_W, CARD_BUFFER_H);
          drawCardAmbientBg(c, CARD_BUFFER_W, CARD_BUFFER_H, node.vendor, focus);
          node.vendor.drawScene(c, CARD_BUFFER_W, CARD_BUFFER_H, ambientTime, focus, showcaseT);
        }
      }

      // 绘制动态色温影棚 + 轨道 + 接触投影 + 9 道螺线彗尾 + 中心融合脉冲 + 背景粒子呼吸
      fieldCtx.clearRect(0, 0, width, height);

      const bgGrad = fieldCtx.createRadialGradient(
        cx + follow.x * 36,
        cy * 0.92 + follow.y * 22,
        0,
        cx,
        cy,
        Math.max(width, height) * 0.82,
      );
      bgGrad.addColorStop(0, rgba(curBgCenter, 1));
      bgGrad.addColorStop(1, rgba(curBgEdge, 1));
      fieldCtx.fillStyle = bgGrad;
      fieldCtx.fillRect(0, 0, width, height);

      const rA = Math.max(width, height) * 0.56;
      const gA = fieldCtx.createRadialGradient(
        cx - width * 0.14 + follow.x * 30,
        cy - height * 0.06 + follow.y * 20,
        0,
        cx - width * 0.14,
        cy - height * 0.06,
        rA,
      );
      gA.addColorStop(0, rgba(curHaloA, curHaloAlpha * (0.75 + anyFocus * 0.45)));
      gA.addColorStop(0.55, rgba(curHaloA, curHaloAlpha * 0.28));
      gA.addColorStop(1, rgba(curHaloA, 0));
      fieldCtx.fillStyle = gA;
      fieldCtx.fillRect(0, 0, width, height);

      const gB = fieldCtx.createRadialGradient(
        cx + width * 0.18 - follow.x * 24,
        cy + height * 0.12 - follow.y * 16,
        0,
        cx + width * 0.18,
        cy + height * 0.12,
        rA * 0.85,
      );
      gB.addColorStop(0, rgba(curHaloB, curHaloAlpha * (0.58 + anyFocus * 0.42)));
      gB.addColorStop(1, rgba(curHaloB, 0));
      fieldCtx.fillStyle = gB;
      fieldCtx.fillRect(0, 0, width, height);

      const trackAlpha =
        (T < ACT1_END
          ? smoothstep(0.12, 0.82, assembleP)
          : 1 - smoothstep(0.2, 0.9, convergeP)) * lerp(1, 0.18, anyFocus);
      if (trackAlpha > 0.01) {
        fieldCtx.save();
        fieldCtx.translate(cx + follow.x * 10, cy + follow.y * 6);
        fieldCtx.rotate(rollTilt);
        const rx = ringRadius * (1 - easeInOutQuart(convergeP));
        const ry = rx * Math.sin(pitchTilt);
        if (rx > 8) {
          fieldCtx.strokeStyle = `rgba(15, 23, 42, ${(0.095 * trackAlpha).toFixed(4)})`;
          fieldCtx.lineWidth = 1.1;
          fieldCtx.beginPath();
          fieldCtx.ellipse(0, 0, rx, ry, 0, 0, TAU);
          fieldCtx.stroke();

          if (reformP > 0.01 && convergeP < 0.96) {
            const energy = reformP * (1 - convergeP * 0.55);
            const g = fieldCtx.createLinearGradient(-rx, 0, rx, 0);
            g.addColorStop(0, `rgba(66,133,244,${0.55 * energy})`);
            g.addColorStop(0.35, `rgba(124,58,237,${0.62 * energy})`);
            g.addColorStop(0.7, `rgba(217,119,87,${0.55 * energy})`);
            g.addColorStop(1, `rgba(20,184,166,${0.55 * energy})`);
            fieldCtx.strokeStyle = g;
            fieldCtx.lineWidth = 2.6 * energy;
            fieldCtx.beginPath();
            fieldCtx.ellipse(0, 0, rx, ry, 0, 0, TAU);
            fieldCtx.stroke();
          }
        }
        fieldCtx.restore();
      }

      for (const n of cardNodes) {
        if (n.opacity < 0.06) continue;
        const cardBottomY = n.screenY + cardH * n.scale * 0.5;
        const shY = cardBottomY + lerp(14, 24, n.depthNorm) * (1 + n.focus * 0.35);
        const shW = cardW * n.scale * 0.46;
        const shH = Math.max(7, cardH * n.scale * 0.11);
        const shAlpha = n.opacity * lerp(0.06, 0.16, n.depthNorm) * (1 + n.focus * 0.25);

        fieldCtx.save();
        fieldCtx.translate(n.screenX, shY);
        fieldCtx.scale(1, shH / Math.max(shW, 1));
        const rg = fieldCtx.createRadialGradient(0, 0, 0, 0, 0, shW);
        rg.addColorStop(0, `rgba(15, 23, 42, ${(shAlpha * 0.72).toFixed(4)})`);
        rg.addColorStop(0.45, rgba(n.vendor.primary, shAlpha * 0.52));
        rg.addColorStop(1, "rgba(255,255,255,0)");
        fieldCtx.fillStyle = rg;
        fieldCtx.beginPath();
        fieldCtx.arc(0, 0, shW, 0, TAU);
        fieldCtx.fill();
        fieldCtx.restore();
      }

      if (convergeP > 0.02 && handoffP < 0.95) {
        const trailStrength = Math.sin(clamp(convergeP) * Math.PI) * (1 - handoffP * 0.7);
        fieldCtx.save();
        fieldCtx.lineCap = "round";
        for (let i = 0; i < COUNT; i++) {
          const v = VENDORS[i];
          const samples = 24;
          const pts: { x: number; y: number }[] = [];
          for (let s = 0; s <= samples; s++) {
            const pS = clamp(convergeP - s * 0.026);
            const rS = ringRadius * (1 - easeInOutQuart(pS));
            const yawS =
              ringYaw +
              (Math.pow(convergeP, 1.75) - Math.pow(pS, 1.75)) * 4.8 +
              i * baseStep;
            const ox = Math.cos(yawS) * rS;
            const oy = Math.sin(yawS) * rS * sinPitch;
            pts.push({
              x: cx + ox * cosRoll - oy * sinRoll,
              y: cy + ox * sinRoll + oy * cosRoll,
            });
          }
          if (pts.length >= 2) {
            const head = pts[0];
            const tail = pts[pts.length - 1];
            const tg = fieldCtx.createLinearGradient(head.x, head.y, tail.x, tail.y);
            tg.addColorStop(0, rgba(v.trail.head, 0.9 * trailStrength));
            tg.addColorStop(0.5, rgba(v.trail.mid, 0.48 * trailStrength));
            tg.addColorStop(1, rgba(v.trail.tail, 0));
            fieldCtx.strokeStyle = tg;
            fieldCtx.lineWidth = 3.6 * trailStrength + 0.8;
            fieldCtx.beginPath();
            fieldCtx.moveTo(pts[0].x, pts[0].y);
            for (let s = 1; s < pts.length; s++) fieldCtx.lineTo(pts[s].x, pts[s].y);
            fieldCtx.stroke();

            fieldCtx.beginPath();
            fieldCtx.arc(head.x, head.y, 3.8 * trailStrength, 0, TAU);
            fieldCtx.fillStyle = rgba(v.trail.head, 0.92 * trailStrength);
            fieldCtx.fill();
          }
        }
        fieldCtx.restore();
      }

      if (convergeP > 0.52) {
        const peak = smoothstep(0.52, 0.98, convergeP) * (1 - smoothstep(0.2, 0.96, handoffP));
        const coreR = Math.min(width, height) * (0.05 + handoffP * 0.42);
        const cg = fieldCtx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
        cg.addColorStop(0, `rgba(255, 255, 255, ${0.98 * peak})`);
        cg.addColorStop(0.26, `rgba(124, 58, 237, ${0.52 * peak})`);
        cg.addColorStop(0.58, `rgba(59, 130, 246, ${0.22 * peak})`);
        cg.addColorStop(1, "rgba(255, 255, 255, 0)");
        fieldCtx.fillStyle = cg;
        fieldCtx.beginPath();
        fieldCtx.arc(cx, cy, coreR, 0, TAU);
        fieldCtx.fill();

        if (handoffP > 0.01) {
          const waveR = Math.min(width, height) * (0.06 + easeOutCubic(handoffP) * 0.48);
          fieldCtx.strokeStyle = `rgba(124, 58, 237, ${(1 - handoffP) * 0.5})`;
          fieldCtx.lineWidth = 2.4 * (1 - handoffP * 0.55);
          fieldCtx.beginPath();
          fieldCtx.arc(cx, cy, waveR, 0, TAU);
          fieldCtx.stroke();
        }
      }

      // 背景细腻空气粒子共振
      const fieldRx = width * 0.38;
      const fieldRy = Math.min(height * 0.32, Math.max(132, fieldRx * 0.54));
      const tiltCos = Math.cos(rollTilt);
      const tiltSin = Math.sin(rollTilt);
      const arcPhase = 1.33 + Math.sin(ambientTime * 0.27) * 0.44;
      const fx = follow.x * 0.075 + Math.sin(ambientTime * 0.47) * 0.032;
      const fy = follow.y * 0.075 + Math.sin(ambientTime * 0.63) * 0.025;
      const fieldMode = spotlightIdx >= 0 ? VENDORS[spotlightIdx].fieldMode : "none";

      fieldCtx.save();
      fieldCtx.lineCap = "round";
      const baseStr = rgba(curTint, 1);
      const accentStr = rgba(curAccent, 1);
      for (const p of particles) {
        const x = p.x + Math.sin(ambientTime * 0.92 + p.phase) * 0.018;
        const y = p.y + Math.cos(ambientTime * 0.78 + p.phase) * 0.022;
        const lx = x - fx;
        const ly = y - fy;
        const ang = Math.atan2(ly, lx);
        const dist = Math.hypot(lx, ly);
        const offset = dist - 0.88 + Math.sin(ang * 4 + ambientTime) * 0.022;
        const broad = Math.exp(-(((offset + 0.034) / 0.145) ** 2));
        const rim = Math.exp(-((offset / 0.058) ** 2));
        const arc = 0.56 + 0.44 * (0.5 + 0.5 * Math.cos(ang * 2 + arcPhase)) ** 2;
        const band = broad * 0.42 + rim * 0.58;
        const strength = band * arc;

        let highlight = 0;
        if (anyFocus > 0.05) {
          if (fieldMode === "glint") {
            highlight = band * arcLight(ang, -2.8 + ((ambientTime * 1.4) % TAU), 0.36) * anyFocus;
          } else if (fieldMode === "slash") {
            highlight =
              band *
              (arcLight(ang, -Math.PI / 4, 0.28) + arcLight(ang, (3 * Math.PI) / 4, 0.28)) *
              0.7 *
              anyFocus;
          } else {
            highlight = band * arcLight(ang, ambientTime * 0.9, 0.6) * 0.45 * anyFocus;
          }
        }
        if (strength < 0.06 && highlight < 0.05 && p.seed < 0.55) continue;

        const px = (fx + lx) * fieldRx;
        const py = (fy + ly) * fieldRy;
        const sx = cx + px * tiltCos - py * tiltSin;
        const sy = cy + px * tiltSin + py * tiltCos;
        const len = 0.8 + p.seed * 0.8 + strength * 2.2 + highlight * 4.2;
        const dir = Math.atan2(ly * fieldRy, lx * fieldRx) + rollTilt + Math.sin(p.phase + ambientTime) * 0.16;
        const tx = Math.cos(dir) * len * 0.5;
        const ty = Math.sin(dir) * len * 0.5;
        fieldCtx.globalAlpha = clamp((0.08 + strength * 0.34 + highlight * 0.46) * (1 - handoffP), 0, 0.85);
        fieldCtx.strokeStyle = rim * arc > 0.34 && p.seed > 0.3 ? accentStr : baseStr;
        fieldCtx.lineWidth = 0.8 + strength * 0.7;
        fieldCtx.beginPath();
        fieldCtx.moveTo(sx - tx, sy - ty);
        fieldCtx.lineTo(sx + tx, sy + ty);
        fieldCtx.stroke();
      }
      fieldCtx.restore();

      frameId = window.requestAnimationFrame(render);
    };

    frameId = window.requestAnimationFrame(render);

    return () => {
      if (frameId !== null) window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerleave", onPointerLeave);
      window.removeEventListener("keydown", onKeyDown);
      cardsLayer.innerHTML = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 overflow-hidden select-none bg-[#f4f4f6]"
    >
      <canvas
        ref={fieldCanvasRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
      />
      <div
        ref={cardsLayerRef}
        className="pointer-events-none absolute inset-0"
        style={{ perspective: "1360px", perspectiveOrigin: "50% 47%" }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 78% 68% at 50% 47%, rgba(255,255,255,0) 62%, rgba(186,192,206,0.24) 100%)",
        }}
      />

      {/* 极简纯图标控制微条（无任何文字干扰，可暂停/重播/跳看单家/变速/跳过） */}
      <nav
        aria-label="开场动画控制"
        className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-slate-900/10 bg-white/85 px-2.5 py-1.5 opacity-75 shadow-[0_12px_32px_-12px_rgba(15,23,42,0.18)] backdrop-blur-md transition hover:bg-white/95 hover:opacity-100"
      >
        <button
          type="button"
          onClick={() => setPlaying((p) => !p)}
          title={playing ? "暂停 (Space)" : "播放 (Space)"}
          aria-label={playing ? "暂停" : "播放"}
          className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-700 transition hover:bg-slate-900/6 hover:text-slate-950 active:scale-90"
        >
          {playing ? (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" rx="1.5" />
              <rect x="14" y="4" width="4" height="16" rx="1.5" />
            </svg>
          ) : (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <path d="M7 4.8c0-1.1 1.2-1.7 2.1-1.2l11.2 7.2c.9.5.9 1.9 0 2.4L9.1 20.4c-.9.5-2.1-.1-2.1-1.2V4.8z" />
            </svg>
          )}
        </button>

        <button
          type="button"
          onClick={replay}
          title="重播 (R)"
          aria-label="重播"
          className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-700 transition hover:bg-slate-900/6 hover:text-slate-950 active:scale-90"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.1"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
            <path d="M3 3v5h5" />
          </svg>
        </button>

        <div className="mx-0.5 h-3.5 w-px bg-slate-900/15" />

        <div className="flex items-center gap-1.5 px-1">
          {VENDORS.map((vendor, index) => (
            <button
              key={vendor.id}
              ref={(el) => {
                dotButtonsRef.current[index] = el;
              }}
              type="button"
              title={vendor.id}
              aria-label={vendor.id}
              onClick={() => jumpToVendor(index)}
              className="intro-dock-dot h-2 w-2 rounded-full bg-slate-900/25 transition-transform duration-200 hover:scale-150"
            />
          ))}
        </div>

        <div className="mx-0.5 h-3.5 w-px bg-slate-900/15" />

        <button
          type="button"
          onClick={() => setSpeedIdx((s) => (s + 1) % SPEEDS.length)}
          title={`切换倍速 (${SPEEDS[speedIdx]}x)`}
          aria-label="切换倍速"
          className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-700 transition hover:bg-slate-900/6 hover:text-slate-950 active:scale-90"
        >
          <span className="inline-flex h-2.5 items-end gap-[2px]">
            <span
              className={`w-[2.5px] rounded-full bg-current transition-opacity ${
                speedIdx >= 0 ? "h-[5px] opacity-95" : "h-[5px] opacity-25"
              }`}
            />
            <span
              className={`w-[2.5px] rounded-full bg-current transition-opacity ${
                speedIdx >= 1 ? "h-[8px] opacity-95" : "h-[8px] opacity-25"
              }`}
            />
            <span
              className={`w-[2.5px] rounded-full bg-current transition-opacity ${
                speedIdx >= 2 ? "h-[11px] opacity-95" : "h-[11px] opacity-25"
              }`}
            />
          </span>
        </button>

        <button
          type="button"
          onClick={skipToWorkbench}
          title="进入工作台 (Esc)"
          aria-label="跳过开场进入工作台"
          className="inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-700 transition hover:bg-slate-900/6 hover:text-slate-950 active:scale-90"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.1"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polygon points="5 4 15 12 5 20 5 4" fill="currentColor" stroke="none" />
            <line x1="19" y1="5" x2="19" y2="19" />
          </svg>
        </button>
      </nav>
    </div>
  );
}
