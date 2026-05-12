"use client";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Wand2, Loader2, Plus, ChevronDown, Pencil, Trash2, Cpu } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Song, Scene } from "@/lib/types";
import ModelTag from "../ModelTag";

export default function StepPlanCell({
  project, song, scenes,
}: { project: Project; song?: Song; scenes: Scene[] }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [planOpts, setPlanOpts] = useState({
    duration: 8,
    plan_llm: "gemini-3-flash-preview",
    expand_llm: "gemini-3-flash-preview",
    story_seed: "",
  });
  const [expandedScene, setExpandedScene] = useState<number | null>(null);

  const { data: models } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const refresh = () => qc.invalidateQueries({ queryKey: ["project", project.id] });

  const autoPlan = useMutation({
    mutationFn: () =>
      api.scenes.autoPlan({
        project_id: project.id,
        song_id: song!.id,
        target_scene_duration: planOpts.duration,
        replace_existing: true,
        llm_model: models?.llm?.[planOpts.plan_llm]?.model_id || "google/gemini-3-flash-preview",
        story_seed: planOpts.story_seed.trim() || undefined,
      }),
    onSuccess: refresh,
  });
  const planError = autoPlan.error instanceof Error ? autoPlan.error.message : null;

  const expandLlmId = models?.llm?.[planOpts.expand_llm]?.model_id || "google/gemini-3-flash-preview";

  const expandAll = useMutation({
    mutationFn: () =>
      api.scenes.expandAll({
        project_id: project.id,
        llm_model: expandLlmId,
        only_empty: false,
      }),
    onSuccess: refresh,
  });
  const expandAllError = expandAll.error instanceof Error ? expandAll.error.message : null;

  const addScene = useMutation({
    mutationFn: (data: any) => api.scenes.create(data),
    onSuccess: refresh,
  });

  if (!song || song.status !== "ready") {
    return (
      <div className="pt-4 text-sm text-zinc-500">
        Add and analyze a song first to enable scene planning.
      </div>
    );
  }

  return (
    <div className="space-y-4 pt-4">
      {/* Auto-plan controls */}
      <div className="bg-surface-2 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3">
          <Wand2 className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-semibold">Auto-Plan with AI</h3>
        </div>
        <p className="text-xs text-zinc-500 mb-2">
          Claude reads lyrics, beats, sections, song theme, your story seed, and the cast — then designs a scene-by-scene plan.
        </p>
        <div className="text-[11px] text-zinc-400 mb-4 bg-surface-3 border border-white/5 rounded-md p-2.5 leading-relaxed">
          <span className="text-accent font-medium">How it works:</span>{" "}
          Each scene is a self-contained shot. The plan picks the strongest single moment for each section of the song,
          gives it a still image (the opening frame) and a video (the motion that plays out within that one shot).
          Scenes are joined by hard cuts in the final assembly — no frame anchoring between adjacent clips. AI Expand
          later writes detailed image and motion prompts using neighbor descriptions only for narrative coherence.
        </div>

        <div className="mb-3">
          <label className="text-[10px] text-zinc-500 uppercase tracking-wide block mb-1">
            Story seed <span className="text-zinc-600 normal-case">(optional — gives the AI a narrative to anchor on)</span>
          </label>
          <textarea
            rows={2}
            placeholder="e.g. A wanderer journeys through a dying city, searching for a lost lover. Ends at a rooftop reunion at dawn."
            value={planOpts.story_seed}
            onChange={(e) => setPlanOpts({ ...planOpts, story_seed: e.target.value })}
            className="w-full bg-surface-3 border border-white/10 rounded-md px-2.5 py-2 text-xs text-white focus:outline-none focus:border-accent placeholder:text-zinc-600 resize-none"
          />
        </div>

        <div className="mb-3">
          <label className="text-[10px] text-zinc-500 uppercase tracking-wide block mb-1">
            Target Scene Length
          </label>
          <div className="flex items-center gap-2">
            <input
              type="range" min={3} max={15} step={1}
              value={planOpts.duration}
              onChange={(e) => setPlanOpts({ ...planOpts, duration: Number(e.target.value) })}
              className="flex-1 accent-accent"
            />
            <span className="text-sm font-medium w-10 text-right">{planOpts.duration}s</span>
          </div>
        </div>

        {/* LLM pickers — separate model for Auto-Plan vs AI Expand */}
        <div className="grid grid-cols-2 gap-2 mb-3">
          <div className="bg-surface-3 rounded-lg px-2.5 py-1.5">
            <div className="flex items-center gap-1.5 mb-1">
              <Cpu className="w-3 h-3 text-zinc-500" />
              <span className="text-[10px] text-zinc-500">Plan LLM</span>
            </div>
            <select
              value={planOpts.plan_llm}
              onChange={(e) => setPlanOpts({ ...planOpts, plan_llm: e.target.value })}
              className="w-full bg-surface-2 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-accent"
            >
              {models && Object.entries(models.llm).map(([key, m]) => (
                <option key={key} value={key}>{m.name}</option>
              ))}
              {!models && <option value={planOpts.plan_llm}>{planOpts.plan_llm}</option>}
            </select>
          </div>
          <div className="bg-surface-3 rounded-lg px-2.5 py-1.5">
            <div className="flex items-center gap-1.5 mb-1">
              <Cpu className="w-3 h-3 text-zinc-500" />
              <span className="text-[10px] text-zinc-500">AI Expand LLM</span>
            </div>
            <select
              value={planOpts.expand_llm}
              onChange={(e) => setPlanOpts({ ...planOpts, expand_llm: e.target.value })}
              className="w-full bg-surface-2 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-accent"
            >
              {models && Object.entries(models.llm).map(([key, m]) => (
                <option key={key} value={key}>{m.name}</option>
              ))}
              {!models && <option value={planOpts.expand_llm}>{planOpts.expand_llm}</option>}
            </select>
          </div>
        </div>
        {models && models.llm[planOpts.plan_llm]?.note && (
          <p className="text-[10px] text-zinc-600 -mt-2 mb-3">
            <span className="text-zinc-500">Plan: </span>{models.llm[planOpts.plan_llm].note}
          </p>
        )}

        {scenes.length > 0 && (
          <p className="text-[11px] text-warning mb-3">
            ⚠ Auto-plan replaces all {scenes.length} existing scenes
          </p>
        )}

        <button
          onClick={() => autoPlan.mutate()}
          disabled={autoPlan.isPending}
          className="w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
        >
          {autoPlan.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
          {autoPlan.isPending ? "Generating plan… (can take 30–90s for long songs)" : scenes.length ? "Re-plan Scenes" : "Auto-Plan Scenes"}
        </button>
        {planError && (
          <div className="mt-2 bg-red-900/20 border border-red-800/40 rounded-md px-2.5 py-2 text-[11px] text-red-300">
            <span className="font-medium">Auto-plan failed: </span>{planError.length > 400 ? planError.slice(0, 400) + "…" : planError}
          </div>
        )}

        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          <ModelTag
            label="Plan"
            model={models?.llm?.[planOpts.plan_llm]?.name || planOpts.plan_llm}
            hint="Reads lyrics + beats + sections and writes scene-by-scene plan"
          />
          <ModelTag
            label="AI Expand"
            model={models?.llm?.[planOpts.expand_llm]?.name || planOpts.expand_llm}
            hint="Per-scene prompt expansion"
          />
        </div>
      </div>

      {/* Scenes list */}
      {scenes.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                {scenes.length} Scenes
              </h3>
              <DurationSum scenes={scenes} song={song} />
            </div>
            <div className="flex items-center gap-3 ml-auto">
              <button
                onClick={async () => {
                  if (await confirm({
                    title: "AI Expand all scenes",
                    message: `Run AI Expand on all ${scenes.length} scenes? Estimated cost ~$${(0.005 * scenes.length).toFixed(2)}.`,
                    confirmLabel: "Expand all",
                  })) {
                    expandAll.mutate();
                  }
                }}
                disabled={expandAll.isPending}
                className="text-[11px] text-accent hover:text-accent-hover flex items-center gap-1 disabled:opacity-50"
                title="Re-write video/image prompts for every scene with full narrative context (previous + next scene + duration)"
              >
                {expandAll.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" />}
                {expandAll.isPending ? "Expanding..." : "AI Expand all"}
              </button>
              <button
                onClick={() =>
                  addScene.mutate({
                    project_id: project.id,
                    order: scenes.length + 1,
                    audio_start: scenes.length ? scenes[scenes.length - 1].audio_end : 0,
                    audio_end: scenes.length ? scenes[scenes.length - 1].audio_end + 8 : 8,
                    description: "",
                  })
                }
                className="text-[11px] text-accent hover:text-accent-hover flex items-center gap-1"
              >
                <Plus className="w-3 h-3" /> Add Scene
              </button>
            </div>
          </div>
          {expandAllError && (
            <div className="bg-red-900/20 border border-red-800/40 rounded-md px-2.5 py-2 text-[11px] text-red-300">
              <span className="font-medium">AI Expand all failed: </span>{expandAllError.length > 400 ? expandAllError.slice(0, 400) + "…" : expandAllError}
            </div>
          )}
          <div className="space-y-1.5">
            {scenes.map((s) => (
              <ScenePlanRow
                key={s.id}
                scene={s}
                expanded={expandedScene === s.id}
                onToggle={() => setExpandedScene(expandedScene === s.id ? null : s.id)}
                onUpdate={refresh}
                expandLlm={expandLlmId}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ScenePlanRow({
  scene, expanded, onToggle, onUpdate, expandLlm,
}: {
  scene: Scene;
  expanded: boolean;
  onToggle: () => void;
  onUpdate: () => void;
  expandLlm: string;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    description: scene.description ?? "",
    video_prompt: scene.video_prompt ?? "",
    image_prompt: scene.image_prompt ?? "",
    audio_start: Math.round(scene.audio_start),
    audio_end: Math.round(scene.audio_end),
  });

  // Re-sync the form when the scene prop changes externally (e.g. after
  // AI Expand all rewrote prompts server-side, or another tab updated
  // the scene). Without this, row-level state stays frozen on whatever
  // the row was first mounted with.
  useEffect(() => {
    setForm({
      description: scene.description ?? "",
      video_prompt: scene.video_prompt ?? "",
      image_prompt: scene.image_prompt ?? "",
      audio_start: Math.round(scene.audio_start),
      audio_end: Math.round(scene.audio_end),
    });
  }, [scene.id, scene.description, scene.video_prompt, scene.image_prompt, scene.audio_start, scene.audio_end]);

  const update = useMutation({
    mutationFn: () => api.scenes.update(scene.id, form),
    onSuccess: onUpdate,
  });

  const expandPrompts = useMutation({
    mutationFn: () => api.scenes.expandPrompts(scene.id, expandLlm),
    onSuccess: (s) => {
      setForm((f) => ({ ...f, video_prompt: s.video_prompt ?? f.video_prompt, image_prompt: s.image_prompt ?? f.image_prompt }));
      onUpdate();
    },
  });

  const remove = useMutation({
    mutationFn: () => api.scenes.delete(scene.id),
    onSuccess: onUpdate,
  });

  return (
    <div className="bg-surface-2 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-surface-3 text-left"
      >
        <span className="text-[10px] font-mono text-zinc-500 w-6 text-center">{scene.order}</span>
        <span className="text-[11px] text-zinc-500 font-mono w-20 shrink-0">
          {fmt(scene.audio_start)}–{fmt(scene.audio_end)}
        </span>
        <span className="text-xs flex-1 truncate text-zinc-300">
          {scene.description || <span className="text-zinc-600 italic">No description</span>}
        </span>
        {scene.prompts_expanded ? (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0 flex items-center gap-0.5"
            title="AI Expand has run on this scene — bridging-aware prompts ready"
          >
            <Wand2 className="w-2.5 h-2.5" /> AI
          </span>
        ) : (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300/80 border border-amber-500/30 shrink-0"
            title="Prompts came from Auto-Plan only. Click 'AI Expand all' for richer bridging-aware prompts."
          >
            plan-only
          </span>
        )}
        <ChevronDown className={`w-3.5 h-3.5 text-zinc-500 transition-transform shrink-0 ${expanded ? "rotate-180" : ""}`} />
      </button>

      {expanded && (
        <div className="px-3 pb-3 pt-2 border-t border-white/5 space-y-2">
          {/* Duration slider — clamped 3-15s, whole seconds. Adjusts audio_end. */}
          <div>
            <label className="text-[10px] text-zinc-500 flex items-center justify-between">
              <span>Duration</span>
              <span className="font-mono text-zinc-400">
                {form.audio_end - form.audio_start}s
                <span className="text-zinc-600"> · ends at {form.audio_end}s</span>
              </span>
            </label>
            <input
              type="range" min={3} max={15} step={1}
              value={Math.max(3, Math.min(15, form.audio_end - form.audio_start))}
              onChange={(e) =>
                setForm({ ...form, audio_end: form.audio_start + Number(e.target.value) })
              }
              className="w-full accent-accent"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-zinc-500">Start (s)</label>
              <input type="number" step={1} value={form.audio_start}
                onChange={(e) => setForm({ ...form, audio_start: Math.round(Number(e.target.value)) })}
                className={inputCls} />
            </div>
            <div>
              <label className="text-[10px] text-zinc-500">End (s)</label>
              <input type="number" step={1} value={form.audio_end}
                onChange={(e) => setForm({ ...form, audio_end: Math.round(Number(e.target.value)) })}
                className={inputCls} />
            </div>
          </div>
          <textarea rows={2} value={form.description}
            placeholder="Scene description..."
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            className={inputCls + " resize-none"} />

          {/* Lyrics in this scene — auto-extracted from word timestamps and
              recomputed whenever the duration slider moves (after Save). */}
          {scene.lyrics_segment && (
            <div className="bg-surface-3 rounded-md px-2.5 py-1.5">
              <div className="text-[10px] text-zinc-500 uppercase tracking-wide mb-0.5">
                Lyrics in this scene
              </div>
              <p className="text-[11px] text-zinc-300 italic leading-snug">
                "{scene.lyrics_segment}"
              </p>
            </div>
          )}

          <details className="text-xs">
            <summary className="cursor-pointer text-zinc-500 hover:text-white py-1">Generation prompts</summary>
            <div className="space-y-2 pt-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-zinc-500">Video prompt</span>
                <button
                  onClick={() => expandPrompts.mutate()}
                  disabled={expandPrompts.isPending}
                  className="text-[10px] text-accent hover:text-accent-hover disabled:opacity-40"
                >
                  {expandPrompts.isPending ? "…" : "AI Expand"}
                </button>
              </div>
              <textarea rows={3} value={form.video_prompt}
                onChange={(e) => setForm({ ...form, video_prompt: e.target.value })}
                className={inputCls + " resize-none"} />
              <span className="text-[10px] text-zinc-500">Image prompt</span>
              <textarea rows={2} value={form.image_prompt}
                onChange={(e) => setForm({ ...form, image_prompt: e.target.value })}
                className={inputCls + " resize-none"} />
            </div>
          </details>
          <div className="flex gap-2 pt-1">
            <button
              onClick={() => remove.mutate()}
              className="text-[11px] text-zinc-600 hover:text-error transition-colors flex items-center gap-1 px-2"
            >
              <Trash2 className="w-3 h-3" /> Delete
            </button>
            <div className="flex-1" />
            <button
              onClick={() => update.mutate()}
              disabled={update.isPending}
              className="text-xs px-3 py-1.5 bg-accent/20 hover:bg-accent/40 text-accent border border-accent/30 rounded-lg transition-colors disabled:opacity-50"
            >
              {update.isPending ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function fmt(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function DurationSum({ scenes, song }: { scenes: Scene[]; song?: Song }) {
  const sceneTotal = Math.round(
    scenes.reduce((acc, s) => acc + (s.audio_end - s.audio_start), 0)
  );
  const songTotal = song?.duration ? Math.floor(song.duration) : 0;
  if (!songTotal) {
    return (
      <span className="text-[10px] font-mono text-zinc-500">
        {sceneTotal}s total
      </span>
    );
  }
  const diff = sceneTotal - songTotal;
  const matched = Math.abs(diff) <= 1;
  const tone = matched
    ? "text-green-400"
    : diff < 0 ? "text-amber-300" : "text-red-300";
  const label = matched
    ? "matches song"
    : diff < 0 ? `${Math.abs(diff)}s short` : `${diff}s over`;
  return (
    <span className={`text-[10px] font-mono ${tone}`} title={`Scenes total: ${sceneTotal}s — Song: ${songTotal}s`}>
      {sceneTotal}s / {songTotal}s · {label}
    </span>
  );
}

const inputCls = "w-full bg-surface-3 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent text-white placeholder:text-zinc-600";
