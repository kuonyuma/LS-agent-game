import { Check, Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTheme } from "../../theme";
import type { ProviderInfo } from "../../types";
import {
  CARD_BUFFER_H,
  CARD_BUFFER_W,
  clamp,
  drawCardAmbientBg,
  easeSpringOut,
  findVendorSpec,
  resolveVendorPrimary,
  resolveVendorStudio,
  rgba,
  type VendorId,
} from "./vendorCatalog";

interface VendorSpotlightModalProps {
  vendorId: VendorId | null;
  matchedProvider?: ProviderInfo | null;
  isCurrentProvider?: boolean;
  onSelectProvider?: (provider: ProviderInfo) => void;
  onClose: () => void;
}

export function VendorSpotlightModal({
  vendorId,
  matchedProvider,
  isCurrentProvider = false,
  onSelectProvider,
  onClose,
}: VendorSpotlightModalProps) {
  const { isDark } = useTheme();
  const cardRef = useRef<HTMLDivElement | null>(null);
  const sheenRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const vendor = findVendorSpec(vendorId);

  useEffect(() => {
    if (!vendor) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [vendor, onClose]);

  useEffect(() => {
    if (!vendor) return;
    const canvas = canvasRef.current;
    const card = cardRef.current;
    const sheen = sheenRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !card || !sheen || !ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(CARD_BUFFER_W * dpr);
    canvas.height = Math.round(CARD_BUFFER_H * dpr);

    const pointer = { x: 0, y: 0 };
    const follow = { x: 0, y: 0 };
    let startedAt = 0;
    let frameId: number | null = null;
    let prevNow = 0;

    const onMove = (e: PointerEvent) => {
      pointer.x = (e.clientX / Math.max(window.innerWidth, 1) - 0.5) * 2;
      pointer.y = (e.clientY / Math.max(window.innerHeight, 1) - 0.5) * 2;
    };
    window.addEventListener("pointermove", onMove, { passive: true });

    const draw = (now: number) => {
      if (!startedAt) startedAt = now;
      const dt = prevNow ? Math.min((now - prevNow) / 1000, 0.045) : 0.016;
      prevNow = now;
      const elapsed = (now - startedAt) / 1000;

      const easeF = 1 - Math.exp(-dt * 7.5);
      follow.x += (pointer.x - follow.x) * easeF;
      follow.y += (pointer.y - follow.y) * easeF;

      const focus = clamp(easeSpringOut(clamp(elapsed / 0.48)), 0, 1.03);
      // 每 2.8 秒柔和循环演绎一次微剧场故事，让重温时始终生动
      const cycle = elapsed < 1.85 ? clamp((elapsed - 0.06) / 1.55) : ((elapsed - 1.85) % 2.9) / 2.6;
      const show = clamp(cycle);

      const rotY = follow.x * 7.5;
      const rotX = -follow.y * 5.5;
      card.style.transform = `scale(${(0.84 + focus * 0.16).toFixed(4)}) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg)`;

      const sx = clamp(-rotY * 2.2 + follow.x * 14, -62, 62);
      const sy = clamp(-rotX * 1.6 + follow.y * 10, -36, 36);
      sheen.style.transform = `translate3d(${sx.toFixed(1)}%, ${sy.toFixed(1)}%, 0)`;

      ctx.setTransform(canvas.width / CARD_BUFFER_W, 0, 0, canvas.height / CARD_BUFFER_H, 0, 0);
      ctx.clearRect(0, 0, CARD_BUFFER_W, CARD_BUFFER_H);
      drawCardAmbientBg(ctx, CARD_BUFFER_W, CARD_BUFFER_H, vendor, clamp(focus), isDark);
      vendor.drawScene(ctx, CARD_BUFFER_W, CARD_BUFFER_H, elapsed, clamp(focus), show, isDark);

      frameId = window.requestAnimationFrame(draw);
    };

    frameId = window.requestAnimationFrame(draw);
    return () => {
      if (frameId !== null) window.cancelAnimationFrame(frameId);
      window.removeEventListener("pointermove", onMove);
    };
  }, [vendor, isDark]);

  if (!vendor) return null;

  const studio = resolveVendorStudio(vendor, isDark);
  const primary = resolveVendorPrimary(vendor, isDark);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={vendor.id}
      onClick={onClose}
      className="fixed inset-0 z-40 flex flex-col items-center justify-center px-4 backdrop-blur-[10px] transition-opacity"
      style={{
        background: isDark
          ? `radial-gradient(ellipse 72% 64% at 50% 48%, ${rgba(studio.haloA, 0.2)} 0%, rgba(5, 6, 12, 0.84) 100%)`
          : `radial-gradient(ellipse 72% 64% at 50% 48%, ${rgba(studio.haloA, 0.12)} 0%, ${rgba(studio.bgEdge, 0.72)} 100%)`,
      }}
    >
      <div
        className="flex flex-col items-center gap-5"
        style={{ perspective: "1050px" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          ref={cardRef}
          className="intro-vendor-card relative h-[256px] w-[400px] max-w-[88vw] cursor-default sm:h-[278px] sm:w-[434px]"
          style={{
            position: "relative",
            boxShadow: isDark
              ? `0 1px 0 rgba(255,255,255,0.2) inset, 0 0 0 1px ${rgba(primary, 0.36)}, 0 40px 88px -24px rgba(0,0,0,0.78), 0 20px 56px -14px ${rgba(primary, 0.32)}`
              : `0 1.5px 0 rgba(255,255,255,1) inset, 0 0 0 1px ${rgba(primary, 0.24)}, 0 40px 88px -24px rgba(15,23,42,0.22), 0 20px 52px -14px ${rgba(primary, 0.24)}`,
          }}
        >
          <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
          <div ref={sheenRef} className="intro-card-sheen" />
        </div>

        {/* 若该行星对应项目已接入厂商，提供极简一键切换按钮 */}
        {matchedProvider && onSelectProvider && (
          <button
            type="button"
            disabled={isCurrentProvider}
            onClick={() => {
              onSelectProvider(matchedProvider);
              onClose();
            }}
            className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-xs font-medium shadow-sm backdrop-blur-md transition ${
              isCurrentProvider
                ? "border-emerald-200/90 bg-white/90 text-emerald-700 dark:border-emerald-400/30 dark:bg-[#121522]/90 dark:text-emerald-300"
                : "border-neutral-200/90 bg-white/92 text-neutral-800 hover:-translate-y-0.5 hover:border-neutral-300 hover:bg-white hover:shadow-md dark:border-white/[0.18] dark:bg-[#F4F5FA] dark:text-[#090A10] dark:hover:bg-white"
            }`}
          >
            {isCurrentProvider ? (
              <>
                <Check className="h-3.5 w-3.5" />
                <span>当前正在使用 {matchedProvider.label}</span>
              </>
            ) : (
              <>
                <Sparkles className="h-3.5 w-3.5 text-violet-600 dark:text-violet-700" />
                <span>切换到 {matchedProvider.label}</span>
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
