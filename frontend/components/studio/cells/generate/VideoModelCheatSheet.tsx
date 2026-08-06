"use client";

import {
  BookOpen, ChevronDown, Clock3, DollarSign, Film, Image as ImageIcon,
  Mic2, Route, ShieldAlert, Sparkles, Users,
} from "lucide-react";
import type { ReactNode } from "react";
import type { ModelsConfig, VideoModel } from "@/lib/types";

function Pill({ children, tone = "neutral", title }: {
  children: ReactNode;
  tone?: "neutral" | "yes" | "warn" | "no" | "accent";
  title?: string;
}) {
  const colors = {
    neutral: "bg-white/5 text-zinc-400 border-white/10",
    yes: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
    warn: "bg-amber-500/10 text-amber-300 border-amber-500/25",
    no: "bg-zinc-800/70 text-zinc-500 border-white/5",
    accent: "bg-violet-500/12 text-violet-300 border-violet-500/25",
  }[tone];
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] ${colors}`}>
      {children}
    </span>
  );
}

function durationLabel(model: VideoModel) {
  const min = Math.min(...model.durations);
  const max = Math.max(...model.durations);
  const contiguous = model.durations.length === max - min + 1;
  if (min === max) return `${min}s`;
  return contiguous ? `${min}-${max}s` : model.durations.map((value) => `${value}s`).join(" / ");
}

function perMinuteLabels(model: VideoModel) {
  return model.resolutions.map((resolution) => {
    const rate = model.pricing[resolution]?.without_audio;
    return rate == null ? `${resolution} -` : `${resolution} $${(rate * 60).toFixed(2)}/min`;
  });
}

function audioPerMinuteLabels(model: VideoModel) {
  const resolutions = model.audio_resolutions || Object.keys(model.audio_pricing || {});
  return resolutions.map((resolution) => {
    const rate = model.audio_pricing?.[resolution];
    return rate == null
      ? `${resolution} -`
      : `${resolution} $${(rate * 60).toFixed(2)}/min`;
  });
}

function guardrailTone(level?: VideoModel["face_guardrail"]) {
  if (level === "high") return "warn" as const;
  if (level === "low") return "yes" as const;
  return "neutral" as const;
}

export default function VideoModelCheatSheet({
  models, selectedModel, onSelect, disabled, sceneDurations = [],
}: {
  models: ModelsConfig;
  selectedModel: string;
  onSelect: (modelKey: string, resolution: string) => void;
  disabled?: boolean;
  sceneDurations?: number[];
}) {
  return (
    <details className="group overflow-hidden rounded-xl border border-violet-500/20 bg-gradient-to-b from-violet-500/[0.06] to-surface-2">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-xs text-zinc-300 hover:bg-white/[0.03] [&::-webkit-details-marker]:hidden">
        <BookOpen className="h-3.5 w-3.5 text-violet-300" />
        <span className="font-medium">Video model selector & cheat sheet</span>
        <span className="text-[10px] text-zinc-600">costs, references, audio and face filters</span>
        <ChevronDown className="ml-auto h-3.5 w-3.5 text-zinc-500 transition-transform group-open:rotate-180" />
      </summary>

      <div className="space-y-3 border-t border-white/5 p-3">
        <div className="grid gap-2 text-[10px] text-zinc-400 sm:grid-cols-2">
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] p-2">
            <div className="mb-0.5 flex items-center gap-1 font-medium text-amber-300">
              <ImageIcon className="h-3 w-3" /> Frames and character refs are separate modes
            </div>
            Exact first/last frames take priority on OpenRouter. If both are sent, separate character references are ignored.
          </div>
          <div className="rounded-lg border border-fuchsia-500/20 bg-fuchsia-500/[0.05] p-2">
            <div className="mb-0.5 flex items-center gap-1 font-medium text-fuchsia-300">
              <Mic2 className="h-3 w-3" /> Reference audio is not generated audio
            </div>
            Only an uploaded song segment can drive lips and movement. “Native audio” models merely invent their own sound.
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1 text-[9px] text-zinc-600">
          <span>Face filter:</span>
          <Pill tone="yes">low = comparatively permissive</Pill>
          <Pill>medium = normal moderation</Pill>
          <Pill tone="warn">high = realistic faces often restricted</Pill>
          <span className="ml-auto">Prices are 60-second equivalents; models render shorter clips.</span>
        </div>

        <div className="flex flex-wrap items-center gap-1 text-[9px] text-zinc-500">
          <Route className="h-3 w-3 text-sky-400" />
          <span className="font-medium text-zinc-400">Provider labels:</span>
          <Pill tone="accent">OpenRouter standard</Pill>
          <span>normal text/frame/character generation</span>
          <span className="mx-1 text-zinc-700">·</span>
          <Pill tone="warn">fal audio</Pill>
          <span>song-driven audio-sync mode, where available</span>
        </div>

        <div className="grid gap-2 lg:grid-cols-2">
          {Object.entries(models.video).map(([key, model]) => {
            const selected = key === selectedModel;
            const audio = model.audio_reference_support || "none";
            const incompatibleDurations = Array.from(new Set(
              sceneDurations.filter((duration) => !model.durations.includes(duration))
            )).sort((a, b) => a - b);
            const incompatible = incompatibleDurations.length > 0;
            return (
              <article
                key={key}
                className={`rounded-lg border p-2.5 transition-colors ${
                  selected
                    ? "border-violet-400/60 bg-violet-500/10 ring-1 ring-violet-500/20"
                    : "border-white/10 bg-black/10 hover:border-white/20"
                }`}
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <h4 className="text-xs font-semibold text-white">{model.name}</h4>
                      <Pill tone={model.tier === "premium" ? "warn" : model.tier === "mid" ? "accent" : model.tier === "cheap" ? "yes" : "neutral"}>
                        {model.tier}
                      </Pill>
                      {selected && <Pill tone="accent">selected</Pill>}
                      {incompatible && (
                        <Pill tone="warn" title={`Unsupported scene lengths: ${incompatibleDurations.join(", ")}s`}>
                          incompatible with plan
                        </Pill>
                      )}
                    </div>
                    <p className="mt-0.5 text-[10px] leading-4 text-zinc-500">{model.tagline}</p>
                  </div>
                  <button
                    type="button"
                    disabled={disabled || selected || incompatible}
                    title={incompatible
                      ? `Cannot use for all scenes. Unsupported lengths: ${incompatibleDurations.join(", ")}s.`
                      : undefined}
                    onClick={() => onSelect(key, model.resolutions[0])}
                    className="shrink-0 rounded-md border border-violet-500/30 bg-violet-500/10 px-2 py-1 text-[9px] font-medium text-violet-300 hover:bg-violet-500/20 disabled:cursor-default disabled:opacity-40"
                  >
                    {selected ? "In use" : incompatible ? "Wrong length" : "Use for all"}
                  </button>
                </div>

                <div className="mt-2 flex flex-wrap gap-1">
                  <Pill tone="accent" title="Normal video generation for this model is submitted through OpenRouter.">
                    <Route className="h-2.5 w-2.5" /> OpenRouter · standard
                  </Pill>
                  {(model.fal_audio_model_id || model.fal_r2v_model_id) && (
                    <Pill tone="warn" title="When the scene mic toggle is enabled, generation is submitted through fal instead of OpenRouter.">
                      <Mic2 className="h-2.5 w-2.5" /> fal · audio sync
                    </Pill>
                  )}
                  <Pill><Clock3 className="h-2.5 w-2.5" />{durationLabel(model)}</Pill>
                  {perMinuteLabels(model).map((label) => (
                    <Pill key={`openrouter-${label}`} tone="yes" title="OpenRouter video-only route">
                      <DollarSign className="h-2.5 w-2.5" />OR {label}
                    </Pill>
                  ))}
                  {audioPerMinuteLabels(model).map((label) => (
                    <Pill key={`fal-${label}`} tone="warn" title="fal reference-audio route">
                      <DollarSign className="h-2.5 w-2.5" />fal audio {label}
                    </Pill>
                  ))}
                  <Pill><Film className="h-2.5 w-2.5" />{model.aspects.join(" · ")}</Pill>
                </div>

                <div className="mt-2 grid grid-cols-2 gap-1.5 text-[9px]">
                  <div className="rounded-md bg-black/15 p-1.5">
                    <div className="mb-1 text-zinc-600">FRAME CONTROL</div>
                    <div className="flex flex-wrap gap-1">
                      <Pill tone={model.supports_first_frame ? "yes" : "no"}>first {model.supports_first_frame ? "yes" : "no"}</Pill>
                      <Pill tone={model.supports_last_frame ? "yes" : "no"}>last {model.supports_last_frame ? "yes" : "no"}</Pill>
                    </div>
                  </div>
                  <div className="rounded-md bg-black/15 p-1.5">
                    <div className="mb-1 text-zinc-600">CHARACTER REFERENCES</div>
                    <Pill tone={model.supports_reference_images ? "accent" : "no"} title={model.reference_note}>
                      <Users className="h-2.5 w-2.5" />{model.supports_reference_images ? "available*" : "not available"}
                    </Pill>
                  </div>
                  <div className="rounded-md bg-black/15 p-1.5">
                    <div className="mb-1 text-zinc-600">REFERENCE AUDIO</div>
                    <Pill tone={audio === "app" ? "yes" : audio === "provider" ? "warn" : "no"} title={model.audio_note}>
                      <Mic2 className="h-2.5 w-2.5" />
                      {audio === "app" ? "works in app" : audio === "provider" ? "provider only" : "not available"}
                    </Pill>
                  </div>
                  <div className="rounded-md bg-black/15 p-1.5">
                    <div className="mb-1 text-zinc-600">REALISTIC FACE FILTER</div>
                    <Pill tone={guardrailTone(model.face_guardrail)} title={model.face_guardrail_note}>
                      <ShieldAlert className="h-2.5 w-2.5" />{model.face_guardrail || "unknown"}
                    </Pill>
                  </div>
                </div>

                <div className="mt-2 space-y-1 border-t border-white/5 pt-2 text-[9px] leading-4 text-zinc-500">
                  {model.reference_note && <p><Sparkles className="mr-1 inline h-2.5 w-2.5 text-violet-400" />{model.reference_note}</p>}
                  {model.audio_note && <p><Mic2 className="mr-1 inline h-2.5 w-2.5 text-fuchsia-400" />{model.audio_note}</p>}
                  {model.face_guardrail_note && <p><ShieldAlert className="mr-1 inline h-2.5 w-2.5 text-amber-400" />{model.face_guardrail_note}</p>}
                  {model.note && <p className="text-zinc-600">{model.note}</p>}
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </details>
  );
}
