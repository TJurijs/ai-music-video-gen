"use client";
import { Image as ImageIcon, Video } from "lucide-react";
import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import type { Scene } from "@/lib/types";

export default function DescriptionWithPromptTooltip({ scene }: { scene: Scene }) {
  // Portal-rendered tooltip — necessary because the parent scene card uses
  // overflow-hidden (for rounded corners on the inner divider), which clips
  // any absolutely-positioned descendant. Portaling to document.body
  // escapes that overflow context. Position is computed from the trigger's
  // bounding rect on every open + on scroll/resize.
  const triggerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; placement: "below" | "above" }>(
    { left: 0, top: 0, placement: "below" }
  );
  const closeTimerRef = useRef<number | null>(null);

  const TOOLTIP_W = 720;
  const TOOLTIP_MAX_H = Math.min(480, typeof window !== "undefined" ? window.innerHeight * 0.6 : 480);

  const compute = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(TOOLTIP_W, vw - 32);
    // Try anchor left to trigger; flip if would overflow right edge.
    let left = rect.left;
    if (left + width > vw - 16) left = Math.max(16, vw - width - 16);
    // Try placement below; flip above if would overflow bottom edge.
    let top = rect.bottom + 8;
    let placement: "below" | "above" = "below";
    if (top + TOOLTIP_MAX_H > vh - 16) {
      const aboveTop = rect.top - 8 - TOOLTIP_MAX_H;
      if (aboveTop >= 16) {
        top = aboveTop;
        placement = "above";
      } else {
        // Doesn't fit either way — clamp to viewport.
        top = Math.max(16, vh - TOOLTIP_MAX_H - 16);
      }
    }
    setPos({ left, top, placement });
  }, [TOOLTIP_MAX_H]);

  const cancelClose = () => {
    if (closeTimerRef.current !== null) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => setOpen(false), 120);
  };

  const onEnter = () => {
    cancelClose();
    compute();
    setOpen(true);
  };

  // Reposition on scroll/resize while open.
  useEffect(() => {
    if (!open) return;
    const handler = () => compute();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    return () => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    };
  }, [open, compute]);

  const hasPrompts = !!(scene.video_prompt || scene.image_prompt);

  return (
    <div
      ref={triggerRef}
      className="relative flex-1 min-w-0"
      onMouseEnter={hasPrompts ? onEnter : undefined}
      onMouseLeave={hasPrompts ? scheduleClose : undefined}
    >
      <p className={`text-xs text-zinc-300 truncate ${hasPrompts ? "cursor-help" : ""}`}>
        {scene.description || <span className="text-zinc-600 italic">no description</span>}
      </p>
      {hasPrompts && open && typeof document !== "undefined" && createPortal(
        <div
          className="fixed z-[100] bg-surface-2 border border-white/10 rounded-lg shadow-2xl p-4 overflow-y-auto"
          style={{
            left: pos.left,
            top: pos.top,
            width: `min(${TOOLTIP_W}px, calc(100vw - 2rem))`,
            maxHeight: `${TOOLTIP_MAX_H}px`,
          }}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        >
          {scene.video_prompt && (
            <div className={scene.image_prompt ? "mb-4" : ""}>
              <div className="text-[9px] uppercase tracking-wider text-accent font-semibold mb-1.5 flex items-center gap-1">
                <Video className="w-2.5 h-2.5" /> Video Prompt
              </div>
              <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                {scene.video_prompt}
              </pre>
            </div>
          )}
          {scene.image_prompt && (
            <div>
              <div className="text-[9px] uppercase tracking-wider text-blue-300 font-semibold mb-1.5 flex items-center gap-1">
                <ImageIcon className="w-2.5 h-2.5" /> Image Prompt
              </div>
              <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                {scene.image_prompt}
              </pre>
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
