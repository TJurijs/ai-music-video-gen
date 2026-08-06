"use client";
import { Image as ImageIcon, Video } from "lucide-react";
import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import type { Scene, Character } from "@/lib/types";
import { fmt, textMentionsCharacter } from "./shared";

export default function DescriptionWithPromptTooltip({
  scene,
  characters,
  videoModelLabel,
  videoModelUsesRefs,
  audioSyncActive,
  audioUsesFrame,
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
  // Whether audio-sync is active on this scene (toggle + model supports it).
  // When true, the request routes through fal Seedance R2V instead of
  // OpenRouter I2V; no first_frame is sent, audio reference IS sent.
  audioSyncActive?: boolean;
  // Wan audio I2V keeps an exact first frame; Seedance audio R2V does not.
  audioUsesFrame?: boolean;
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

  // Compute which characters will actually be passed to the video model.
  // Mirror backend's `_find_character_references` EXACTLY — checking all
  // three fields (video_prompt + image_prompt + description) and matching
  // either the full name or any single-token of a multi-word name.
  //
  // Why this matters: when a user (or our own prompt rewrites) replaces
  // names with "the trio" / "they" / "she" in the motion prompt, the
  // image_prompt usually still names the characters in the still
  // composition. Matching only against video_prompt would drop those
  // refs and the audio-sync (R2V) route would have no identity anchor.
  const haystack = [
    scene.video_prompt || "",
    scene.image_prompt || "",
    scene.description || "",
  ].join(" ").toLowerCase();
  const characterReferenceMode = (
    !audioSyncActive
    && videoModelUsesRefs === true
    && scene.video_reference_mode === "character"
  );
  const referencesActive = (!!audioSyncActive && !audioUsesFrame) || characterReferenceMode;
  const charsActuallyPassed = (characters || []).filter((c) => {
    if (!referencesActive) return false;
    if (!c.reference_image_url) return false;
    const name = (c.name || "").toLowerCase().trim();
    return textMentionsCharacter(haystack, name);
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
      <div className={hasPrompts ? "cursor-help" : ""}>
        <p className="text-xs text-zinc-300 truncate">
          {scene.description || <span className="text-zinc-600 italic">no description</span>}
        </p>
        <p className="text-[10px] text-amber-200/65 truncate mt-0.5">
          {scene.lyrics_segment ? `♪ ${scene.lyrics_segment}` : "♪ Instrumental / no timestamped lyrics"}
        </p>
      </div>
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
          {/* Honest summary of what the video call will include. Three routes:
                - OpenRouter I2V (default): first_frame OR character refs.
                - fal Seedance R2V: audio + refs, NO first_frame.
                - fal Wan I2V: audio + exact first_frame, NO character refs.
              The text changes per route so the user knows what's actually sent. */}
          <div className={`mb-3 text-[10px] ${audioSyncActive ? "text-fuchsia-200" : "text-zinc-400"} ${audioSyncActive ? "bg-fuchsia-500/10 border-fuchsia-500/30" : "bg-zinc-500/10 border-zinc-500/30"} border rounded px-2 py-1.5`}>
            <div className="font-semibold mb-1" style={{ color: audioSyncActive ? "rgb(244 114 182)" : "rgb(212 212 216)" }}>
              {audioSyncActive
                ? `Sent to fal ${videoModelLabel || "video model"} (${audioUsesFrame ? "audio-driven I2V" : "audio-sync R2V"}):`
                : `Sent to ${videoModelLabel || "OpenRouter"} (image-to-video route):`}
            </div>
            <ul className="space-y-0.5 leading-snug">
              <li>· video_prompt (verbatim, below)</li>
              {audioSyncActive ? (
                <>
                  <li>
                    · {audioUsesFrame ? "audio_url" : "audio_urls[0]"} ={" "}
                    <span className="text-fuchsia-200">
                      song slice {fmt(scene.audio_start)}–{fmt(scene.audio_end)}
                      {!audioUsesFrame && " (trimmed ~150ms under video duration)"}
                    </span>
                  </li>
                  {audioUsesFrame ? (
                    <>
                      <li>
                        · image_url ={" "}
                        {scene.chain_from_prev
                          ? <span className="text-emerald-300">prev scene's extracted last frame (exact first frame)</span>
                          : scene.reference_image_url
                            ? <span className="text-fuchsia-100">this scene's generated still (exact first frame)</span>
                            : <span className="text-red-300">none — REQUIRED; the app will generate a still first</span>}
                      </li>
                      <li className="text-zinc-500 italic">· no separate character portraits — Wan audio I2V uses the exact frame as its identity anchor.</li>
                    </>
                  ) : (
                    <>
                      <li>
                        · image_urls ={" "}
                        {(() => {
                          const frameSource = scene.chain_from_prev
                            ? "prev scene's extracted last frame"
                            : scene.reference_image_url
                              ? "this scene's generated still"
                              : null;
                          const items: React.ReactNode[] = [];
                          if (frameSource) items.push(<span key="f" className="text-fuchsia-100">{frameSource}</span>);
                          if (charsActuallyPassed.length > 0) {
                            items.push(<span key="c" className="text-fuchsia-100">{charsActuallyPassed.map((c) => c.name).join(", ")} ({charsActuallyPassed.length} portrait{charsActuallyPassed.length === 1 ? "" : "s"})</span>);
                          }
                          if (items.length === 0) return <span className="text-red-300">none — REQUIRED. Generate a still or mention a cast character with a portrait.</span>;
                          return items.reduce((acc, el, i) => i === 0 ? [el] : [...acc as any, <span key={`s${i}`} className="text-zinc-500"> + </span>, el], [] as React.ReactNode[]);
                        })()}
                      </li>
                      <li className="text-zinc-500 italic">· no first_frame — Seedance treats all images as compositional/style/identity references.</li>
                    </>
                  )}
                </>
              ) : (
                <>
                  <li>
                    · first_frame ={" "}
                    {characterReferenceMode
                      ? <span className="text-zinc-500 italic">none (character-reference mode)</span>
                      : scene.chain_from_prev
                      ? <span className="text-emerald-300">prev scene's extracted last frame (chained)</span>
                      : scene.reference_image_url
                        ? <span className="text-zinc-300">this scene's generated still</span>
                        : <span className="text-amber-300">none (no still generated yet)</span>}
                  </li>
                  <li>
                    · input_references ={" "}
                    {videoModelUsesRefs === false
                      ? <span className="text-zinc-500 italic">none (model doesn't use refs — skipped)</span>
                      : !characterReferenceMode
                        ? <span className="text-zinc-500 italic">none (frame mode)</span>
                      : charsActuallyPassed.length === 0
                        ? <span className="text-zinc-500 italic">none</span>
                        : (
                          <span className="text-zinc-300">
                            {charsActuallyPassed.map((c) => c.name).join(", ")}
                            {" "}({charsActuallyPassed.length} portrait{charsActuallyPassed.length === 1 ? "" : "s"})
                          </span>
                        )}
                  </li>
                </>
              )}
            </ul>
            <div className="mt-1 text-zinc-500">
              {audioSyncActive
                ? audioUsesFrame
                  ? "Wan audio mode: the uploaded song segment drives lip/action timing while the scene still or chained frame remains the exact visual start. Character-reference mode is not combined with this route."
                  : "Audio-sync route: Seedance R2V composes the shot using the audio + all image_urls as combined references. Character portraits provide identity anchoring; the scene still adds composition without being a strict first frame."
                : videoModelUsesRefs === false
                  ? `${videoModelLabel || "This model"} doesn't accept input_references on the OpenRouter route — character identity comes entirely from the first_frame. Switch to a Seedance variant if you need character-portrait identity anchoring.`
                  : characterReferenceMode
                    ? "Character-reference mode: named cast portraits are the identity anchors. No exact first/last frame is sent because OpenRouter treats frame images and character references as mutually exclusive inputs."
                    : "Frame mode: the scene still or chained final frame is used as the exact first frame. Separate character portraits are not sent."}
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
