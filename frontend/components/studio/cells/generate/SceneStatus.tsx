"use client";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Image as ImageIcon, AlertCircle, Check, Square, Wand2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Scene } from "@/lib/types";

export function Badge({ children, tone = "default" }: { children: React.ReactNode; tone?: "default" | "accent" | "warn" }) {
  const cls = {
    default: "bg-surface-3 text-zinc-400",
    accent:  "bg-accent/15 text-accent",
    warn:    "bg-amber-500/15 text-amber-300",
  }[tone];
  return <span className={`text-[9px] px-1.5 py-0.5 rounded ${cls}`}>{children}</span>;
}

export function SceneErrorBanner({ scene, onSoftened }: { scene: Scene; onSoftened: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const err = scene.error_message || "";
  // Heuristic: did the model reject for content-policy reasons?
  const isContentFilter = /content.*filter|content.*policy|safety|moderation|blocked.*content|refused.*content|completed with no output/i.test(err);
  // And which model side did the refusing — image gen or video gen? Image
  // gen errors carry "Image model" verbatim; video gen errors carry "video"
  // (Seedance/Veo/Kling). Default to video if no clear signal.
  const isImageFilter = /image model|image content filter|image prompt/i.test(err);
  const which = isImageFilter ? "image" : "video";

  const soften = useMutation({
    mutationFn: (field: "video_prompt" | "image_prompt") =>
      api.scenes.softenPrompt(scene.id, field),
    onSuccess: onSoftened,
  });
  const softenErr = soften.error instanceof Error ? soften.error.message : null;

  return (
    <div className="px-3 py-2 bg-red-900/15 border-b border-red-900/40 space-y-1">
      <div className="flex items-start gap-2">
        <AlertCircle className="w-3 h-3 text-red-400 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className={`text-[10px] text-red-300 ${expanded ? "" : "line-clamp-2"} leading-snug`}>
            {err}
          </p>
          {err.length > 100 && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-[9px] text-red-400/70 hover:text-red-300 mt-0.5"
            >
              {expanded ? "show less" : "show full"}
            </button>
          )}
        </div>
      </div>
      {isContentFilter && (
        <div className="bg-amber-900/20 border border-amber-700/40 rounded px-2 py-1.5 text-[10px] text-amber-200/90 space-y-1.5">
          <p>
            Content filter rejected the <span className="font-medium">{which} prompt</span>. Soften it (LLM rewrites without triggers) or pick a less strict model.
          </p>
          <div className="flex gap-2">
            {/* Primary action: soften the SIDE that actually failed.
                Show the other as a secondary option since sometimes both
                prompts share the same triggering phrase. */}
            <button
              onClick={() => soften.mutate(`${which}_prompt` as "video_prompt" | "image_prompt")}
              disabled={soften.isPending}
              className="text-[10px] px-2 py-0.5 bg-amber-500/20 hover:bg-amber-500/40 text-amber-100 border border-amber-500/40 rounded flex items-center gap-1 disabled:opacity-50"
              title={`Use the LLM to rewrite ${which}_prompt without filter triggers`}
            >
              {soften.isPending && soften.variables === `${which}_prompt` ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Wand2 className="w-2.5 h-2.5" />}
              Soften {which} prompt
            </button>
            <button
              onClick={() => soften.mutate(which === "video" ? "image_prompt" : "video_prompt")}
              disabled={soften.isPending}
              className="text-[10px] px-2 py-0.5 bg-amber-500/10 hover:bg-amber-500/25 text-amber-200/70 border border-amber-500/25 rounded flex items-center gap-1 disabled:opacity-50"
              title="Soften the other prompt too — useful when both share the triggering phrase"
            >
              {soften.isPending && soften.variables === (which === "video" ? "image_prompt" : "video_prompt") ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Wand2 className="w-2.5 h-2.5" />}
              Soften {which === "video" ? "image" : "video"} prompt
            </button>
          </div>
          {softenErr && (
            <p className="text-red-300 text-[9px]">Soften failed: {softenErr.slice(0, 200)}</p>
          )}
        </div>
      )}
    </div>
  );
}


// `ExpandedBadge` is now a no-op visually — the new batch generator always
// produces fully-expanded scenes, so the AI-expanded / plan-only distinction
// no longer carries useful information. Kept as a stub so existing callers
// don't need to change; will be deleted in a follow-up sweep.
export function ExpandedBadge({ expanded: _expanded }: { expanded: boolean }) {
  return null;
}


export function StatusPill({ status }: { status: string }) {
  const cfg: Record<string, { label: string; cls: string; icon?: React.ReactNode }> = {
    pending: { label: "Pending", cls: "bg-zinc-800 text-zinc-400" },
    generating_image: { label: "Image", cls: "bg-blue-900/40 text-blue-300", icon: <Loader2 className="w-2.5 h-2.5 animate-spin" /> },
    image_ready: { label: "Still ready", cls: "bg-blue-900/40 text-blue-300", icon: <ImageIcon className="w-2.5 h-2.5" /> },
    generating_video: { label: "Video", cls: "bg-purple-900/40 text-purple-300", icon: <Loader2 className="w-2.5 h-2.5 animate-spin" /> },
    done: { label: "Done", cls: "bg-green-900/40 text-green-400", icon: <Check className="w-2.5 h-2.5" /> },
    error: { label: "Error", cls: "bg-red-900/40 text-red-400", icon: <AlertCircle className="w-2.5 h-2.5" /> },
    cancelled: { label: "Cancelled", cls: "bg-zinc-700/50 text-zinc-300", icon: <Square className="w-2.5 h-2.5" /> },
  };
  const c = cfg[status] ?? cfg.pending;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium ${c.cls}`}>
      {c.icon}
      {c.label}
    </span>
  );
}
