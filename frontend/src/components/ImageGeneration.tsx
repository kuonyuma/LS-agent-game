import { Download, Loader2, X, ZoomIn } from "lucide-react";
import { useRef } from "react";
import type { ImageArtifact, ToolTrace } from "../types";

export function ImageGenerationProgress({ trace }: { trace: ToolTrace }) {
  if (trace.status === "error") {
    return (
      <p role="status" className="max-w-xl rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:bg-rose-500/10 dark:text-rose-200">
        {trace.result || "图片生成未完成。"}
      </p>
    );
  }
  const aspectRatio = trace.arguments.aspect_ratio;
  const [width, height] = (typeof aspectRatio === "string" ? aspectRatio : "1:1").split(":").map(Number);
  const ratio = width > 0 && height > 0 ? width / height : 1;
  return (
    <div
      role="status"
      className="grid w-full place-items-center rounded-2xl border border-violet-200 bg-violet-50/65 dark:border-violet-400/20 dark:bg-violet-500/10"
      style={{ aspectRatio: ratio, maxWidth: `${Math.min(36, 26 * ratio)}rem` }}
    >
      <span className="flex items-center gap-2 text-sm font-medium text-violet-800 dark:text-violet-200">
        <Loader2 size={16} className="motion-safe:animate-spin" aria-hidden="true" />
        正在生成图片
      </span>
    </div>
  );
}

export function GeneratedImageCard({ image }: { image: ImageArtifact }) {
  const preview = useRef<HTMLDialogElement>(null);
  return (
    <figure className="w-fit max-w-full overflow-hidden rounded-2xl border border-neutral-200 bg-white dark:border-white/10 dark:bg-[#121420]">
      <button
        type="button"
        aria-label="放大生成图片"
        onClick={() => preview.current?.showModal()}
        className="group relative block w-full cursor-zoom-in focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-violet-500"
      >
        <img
          className="block h-auto max-h-[34rem] w-auto max-w-full object-contain"
          src={image.preview_url}
          width={image.width}
          height={image.height}
          alt="生成的图片"
          loading="lazy"
        />
        <ZoomIn size={18} className="absolute right-3 top-3 rounded-full bg-neutral-950/60 p-1 text-white opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden="true" />
      </button>
      <figcaption className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs text-neutral-600 dark:text-[#AAB0CD]">
        <span>PNG · {image.width} × {image.height}</span>
        <a href={image.download_url} download={image.filename} className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 font-medium text-violet-700 hover:bg-violet-50 dark:text-violet-300 dark:hover:bg-violet-500/15">
          <Download size={14} aria-hidden="true" /> 下载原图
        </a>
      </figcaption>
      <dialog
        ref={preview}
        aria-label="生成图片预览"
        className="m-auto max-h-[94dvh] w-fit max-w-[94vw] overflow-auto rounded-2xl border border-white/15 bg-neutral-950 p-2 text-white backdrop:bg-neutral-950/80"
      >
        <div className="mb-1 flex justify-end">
          <button
            type="button"
            onClick={() => preview.current?.close()}
            className="rounded-full p-2 hover:bg-white/15"
            aria-label="关闭图片预览"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <img className="block max-h-[82dvh] max-w-full rounded-lg object-contain" src={image.preview_url} alt="生成的图片" loading="lazy" />
      </dialog>
    </figure>
  );
}
