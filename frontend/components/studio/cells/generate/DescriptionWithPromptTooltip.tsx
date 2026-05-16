"use client";
import { Image as ImageIcon, Video } from "lucide-react";
import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import type { Scene, Character } from "@/lib/types";

export default function DescriptionWithPromptTooltip({
  scene,
  characters,
  videoModelLabel,
  videoModelUsesRefs,
}: {
  scene: Scene;
  characters?: Character[];
  // Human-readable name of the scene's video model (e.g. "Seedance 2.0").
  // Falls back to "OpenRouter" if unset. Used to label the "sent to ..."
  // summary line in the tooltip.
  videoModelLabel?: string;
  // Whether the selected video model actually uses input_references on
  // the OpenRouter route. Seedance variants: yes. Kling/Veo: no — refs
  // are dropped at the OpenRouter passthrough layer. Drives the
  // "Sent to ..." summary so we don't lie about what reaches the model.
  videoModelUsesRefs?: boolean;
}) {
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

  // Compute which characters will actually be passed to the video model as
  // `input_references` (OpenRouter's name for subject references). Mirror
  // backend's `_find_character_references` exactly: name (case-insensitive
  // substring) appears in the scene's prompt or description, AND the
  // character has an active portrait. The model receives these images as
  // visual references — NOT a hard identity lock, NOT addressed via tokens
  // (Seedance / Kling / Veo on OpenRouter don't use @Image tokens — that
  // convention was fal-only). They influence the output but don't override
  // what's in the first_frame.
  const haystack = `${scene.video_prompt || ""} ${scene.description || ""}`.toLowerCase();
  const charsActuallyPassed = (characters || []).filter((c) => {
    if (!c.reference_image_url) return false;
    return c.name.toLowerCase().split(/\s+/).some((part) => part && haystack.includes(part));
  });

  const swappedVideo = scene.video_prompt;
  const swappedImage = scene.image_prompt;

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
          {/* Honest summary of what the OpenRouter video call will include.
              Important context: character refs go in as `input_references`,
              which are SOFT references — they bias the rendered output but
              don't lock identity. If the first_frame doesn't show the
              character's face, the model improvises. */}
          <div className="mb-3 text-[10px] text-zinc-400 bg-zinc-500/10 border border-zinc-500/30 rounded px-2 py-1.5">
            <div className="font-semibold text-zinc-300 mb-1">
              Sent to {videoModelLabel || "OpenRouter"}:
            </div>
            <ul className="space-y-0.5 leading-snug">
              <li>· video_prompt (verbatim, below)</li>
              <li>
                · first_frame ={" "}
                {scene.chain_from_prev
                  ? <span className="text-emerald-300">prev scene's extracted last frame (chained)</span>
                  : scene.reference_image_url
                    ? <span className="text-zinc-300">this scene's generated still</span>
                    : <span className="text-amber-300">none (no still generated yet)</span>}
              </li>
              <li>
                · input_references ={" "}
                {videoModelUsesRefs === false
                  ? <span className="text-zinc-500 italic">none (model doesn't use refs — skipped)</span>
                  : charsActuallyPassed.length === 0
                    ? <span className="text-zinc-500 italic">none</span>
                    : (
                      <span className="text-zinc-300">
                        {charsActuallyPassed.map((c) => c.name).join(", ")}
                        {" "}({charsActuallyPassed.length} portrait{charsActuallyPassed.length === 1 ? "" : "s"})
                      </span>
                    )}
              </li>
            </ul>
            <div className="mt-1 text-zinc-500">
              {videoModelUsesRefs === false
                ? `${videoModelLabel || "This model"} doesn't accept input_references on the OpenRouter route — character identity comes entirely from the first_frame. Switch to a Seedance variant if you need character-portrait identity anchoring.`
                : scene.chain_from_prev || scene.reference_image_url
                  ? "Seedance is running in image-to-video mode (a first_frame is present), so first_frame pixels DOMINATE the output. input_references are a soft hint (~30% weight) — they nudge style/identity but don't lock the face. If the first_frame shows the character from behind, the model invents the face on turn-around and refs alone often won't keep it consistent. To get the strong ~70% identity anchor, drop the first_frame (disable chaining + clear the scene still) so Seedance runs in reference-to-video mode."
                  : "No first_frame attached — Seedance runs in reference-to-video mode. input_references are the primary identity anchor (~70% weight per ByteDance). Pose and composition come from the prompt; the model is free to invent them."}
            </div>
          </div>
          {scene.video_prompt && (
            <div className={scene.image_prompt ? "mb-4" : ""}>
              <div className="text-[9px] uppercase tracking-wider text-accent font-semibold mb-1.5 flex items-center gap-1">
                <Video className="w-2.5 h-2.5" /> Video Prompt
              </div>
              <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                {swappedVideo}
              </pre>
            </div>
          )}
          {scene.image_prompt && (
            <div>
              <div className="text-[9px] uppercase tracking-wider text-blue-300 font-semibold mb-1.5 flex items-center gap-1">
                <ImageIcon className="w-2.5 h-2.5" /> Image Prompt
              </div>
              <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                {swappedImage}
              </pre>
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
