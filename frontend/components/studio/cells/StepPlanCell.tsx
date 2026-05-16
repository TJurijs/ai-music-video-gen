"use client";
import { useEffect, useRef, useState } from "react";
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
    // Single LLM model used for both the batch generator and per-scene
    // re-expand. The two used to be configurable separately back when
    // planning and AI-expanding were distinct passes — now they're one
    // batch flow, so one knob is enough.
    plan_llm: "gemini-3-flash-preview",
    // Seed hydrates from the persisted project value so a refresh, a fresh
    // browser session, or another collaborator opening the project sees the
    // same narrative direction the last auto-plan ran with.
    story_seed: project.story_seed || "",
  });
  // If the project's stored seed changes after mount (e.g. a backend write
  // landed via auto-plan), bring the editable textarea in sync — but only
  // when the user hasn't started typing a new seed locally.
  useEffect(() => {
    setPlanOpts((opts) => {
      if (opts.story_seed.trim()) return opts;  // user has unsaved text — don't clobber
      return { ...opts, story_seed: project.story_seed || "" };
    });
  }, [project.story_seed]);
  const [expandedScene, setExpandedScene] = useState<number | null>(null);

  const { data: models } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const refresh = () => qc.invalidateQueries({ queryKey: ["project", project.id] });

  // Single LLM identifier used for both the batch generator and per-scene
  // re-expand. Resolves the user's preference to OpenRouter's full model_id.
  const llmId = models?.llm?.[planOpts.plan_llm]?.model_id || "google/gemini-3-flash-preview";

  // ─── Per-batch retry on transient errors ──────────────────────────────
  // Backend restart (uvicorn reload), network blip, or proxy upstream-
  // unreachable shouldn't abort the whole multi-batch generation. Each
  // batch retries up to 5 times with exponential backoff capped at 8s.
  const isTransientError = (error: any): boolean => {
    const msg: string = String((error as Error)?.message || "");
    return (
      /Backend unreachable/i.test(msg) ||
      /Network error/i.test(msg) ||
      /ECONNREFUSED|ECONNRESET|ETIMEDOUT/i.test(msg) ||
      /^5\d\d: Internal Server Error$/.test(msg)
    );
  };
  const TRANSIENT_RETRY_COUNT = 5;
  const transientRetryDelay = (attempt: number) => Math.min(1000 * 2 ** attempt, 8000);

  // ─── Batch generator — the only scene-planning flow ───────────────────
  // One LLM call per batch (default 3 scenes), runs sequentially, each
  // batch sees previously-generated scenes for continuity. Short calls →
  // no timeouts → user sees scenes appear as they're created.
  const BATCH_SIZE = 3;
  const [genTotal, setGenTotal] = useState<number | null>(null);
  const [genSoFar, setGenSoFar] = useState<number>(0);
  const [genRunning, setGenRunning] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  // When a transient error trips the per-batch retry loop, surface the
  // attempt number so the user sees "Retrying after blip…" instead of just
  // a frozen progress bar.
  const [genRetryAttempt, setGenRetryAttempt] = useState<number>(0);
  const genCancelRef = useRef(false);

  // Three modes, all using the same batch endpoint:
  //
  //  - oneBatch=false, startFrom=0:
  //      Full song. start_index=0 wipes any existing scenes on the backend,
  //      then loops batches of 3 until has_more=false.
  //
  //  - oneBatch=true, startFrom=0:
  //      Just scene 1 (when zero scenes exist). Backend wipes → plans 1.
  //      Useful for iterating on the story seed cheaply before committing
  //      to a full plan.
  //
  //  - oneBatch=true, startFrom=N (N >= 1):
  //      Add scene N+1 to an existing plan. start_index>0 does NOT wipe;
  //      backend reads existing scenes for continuity, plans 1 more at the
  //      next position. This is the iterative-build path: generate scene 1,
  //      render it, click "Add scene 2", run continuation-prompt on scene 2,
  //      render it, repeat.
  const runGenerateLoop = async ({
    oneBatch = false,
    startFrom = 0,
  }: { oneBatch?: boolean; startFrom?: number } = {}) => {
    setGenRunning(true);
    setGenError(null);
    setGenTotal(null);
    setGenSoFar(0);
    setGenRetryAttempt(0);
    genCancelRef.current = false;
    let start = startFrom;
    // One-batch mode uses batch_size=1 so we get exactly the scene we want
    // (no accidentally over-planning). Full-song mode uses BATCH_SIZE (3).
    const callBatchSize = oneBatch ? 1 : BATCH_SIZE;

    // Per-batch retry on transient errors (uvicorn restart, network blip,
    // proxy upstream-unreachable). Each batch is its own request, so a
    // single bad blip shouldn't abort the whole multi-minute generation.
    // Up to 5 retries per batch, exponential backoff capped at 8s.
    const callBatchWithRetry = async (startIdx: number) => {
      let lastErr: unknown = null;
      for (let attempt = 0; attempt <= TRANSIENT_RETRY_COUNT; attempt++) {
        if (genCancelRef.current) throw new Error("Generation cancelled");
        try {
          const r = await api.scenes.generateBatch({
            project_id: project.id,
            song_id: song!.id,
            target_scene_duration: planOpts.duration,
            llm_model: models?.llm?.[planOpts.plan_llm]?.model_id || "google/gemini-3-flash-preview",
            story_seed: planOpts.story_seed.trim() || undefined,
            start_index: startIdx,
            batch_size: callBatchSize,
          });
          setGenRetryAttempt(0);  // success — clear the retry indicator
          return r;
        } catch (e) {
          lastErr = e;
          if (!isTransientError(e) || attempt === TRANSIENT_RETRY_COUNT) {
            throw e;
          }
          setGenRetryAttempt(attempt + 1);
          // Sleep + retry. The polling effect refreshes the project query
          // each 2s anyway, so the UI keeps showing progress.
          await new Promise((r) => setTimeout(r, transientRetryDelay(attempt)));
        }
      }
      throw lastErr;
    };

    try {
      while (!genCancelRef.current) {
        const data = await callBatchWithRetry(start);
        setGenTotal(data.total_planned);
        setGenSoFar(data.scenes_so_far);
        // Pull fresh scenes immediately so the UI updates between batches.
        await qc.invalidateQueries({ queryKey: ["project", project.id] });
        await qc.refetchQueries({ queryKey: ["project", project.id] });
        // Single-batch mode stops after one call regardless of has_more.
        // Used by "Just scene 1" and "Add scene N+1".
        if (oneBatch) break;
        if (!data.has_more || data.next_start_index == null) break;
        start = data.next_start_index;
      }
    } catch (e: any) {
      setGenError(e?.message || "Generation failed");
    } finally {
      setGenRunning(false);
    }
  };

  // Poll the project every 2s while the batch loop is running so per-scene
  // progress flips live in the UI.
  useEffect(() => {
    if (!genRunning) return;
    const iv = setInterval(refresh, 2000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genRunning]);

  const addScene = useMutation({
    mutationFn: (data: any) => api.scenes.create(data),
    onSuccess: refresh,
  });

  const clearAll = useMutation({
    mutationFn: () => api.scenes.deleteAll(project.id),
    onSettled: refresh,
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
          Scenes are generated in batches of {BATCH_SIZE} — each batch is a short LLM call that produces
          fully-expanded image + video prompts. Every new batch sees the scenes already planned
          (descriptions, image prompts, video prompts) so the visual vocabulary stays consistent
          across the song. Scenes are joined by hard cuts at assembly — no per-scene frame anchoring.
          Short batches mean you see scenes appear progressively, and a network blip only loses the
          in-flight batch.
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
            className="w-full bg-surface-3 border border-white/10 rounded-md px-2.5 py-2 text-xs text-white focus:outline-none focus:border-accent placeholder:text-zinc-600 resize-y"
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

        {/* Single LLM picker — used for batch generation AND per-scene re-expand. */}
        <div className="bg-surface-3 rounded-lg px-2.5 py-1.5 mb-3">
          <div className="flex items-center gap-1.5 mb-1">
            <Cpu className="w-3 h-3 text-zinc-500" />
            <span className="text-[10px] text-zinc-500">LLM (used for both batch generation and per-scene re-expand)</span>
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
        {models && models.llm[planOpts.plan_llm]?.note && (
          <p className="text-[10px] text-zinc-600 -mt-2 mb-3">
            <span className="text-zinc-500">Plan: </span>{models.llm[planOpts.plan_llm].note}
          </p>
        )}

        {scenes.length > 0 && (
          <p className="text-[11px] text-warning mb-3">
            ⚠ Generate replaces all {scenes.length} existing scenes
          </p>
        )}

        {/* BATCH GENERATOR — replaces (Re-plan + AI Expand all). One LLM
            call per batch (default 3 scenes), runs sequentially, each batch
            sees previous scenes for continuity. Short calls → no timeouts →
            user sees scenes appear as they're created.

            The full-song button on the LEFT generates everything. The "Just
            scene 1" button on the RIGHT is for iterating cheaply on the seed
            BEFORE committing to the full plan — it generates a single scene
            so you can review and tweak. After scene 1 is approved and its
            video rendered, use the chain icon (Link2) on the scene's row in
            the Generate step to iteratively add scene 2, scene 3, etc. —
            each chained from the previous one. No need to "add scene N+1"
            from here. */}
        <div className="flex gap-2">
          <button
            onClick={() => runGenerateLoop({ oneBatch: false, startFrom: 0 })}
            disabled={genRunning}
            className="flex-1 flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
          >
            {genRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
            {(() => {
              if (!genRunning) {
                return scenes.length ? "Re-generate Scenes" : "Generate Scenes";
              }
              if (genRetryAttempt > 0) {
                return `Retrying after backend blip… (attempt ${genRetryAttempt + 1}/${TRANSIENT_RETRY_COUNT + 1})`;
              }
              if (genTotal == null) return "Starting…";
              return `Generating ${genSoFar}/${genTotal} scenes…`;
            })()}
          </button>
          <button
            onClick={() => runGenerateLoop({ oneBatch: true, startFrom: 0 })}
            disabled={genRunning}
            className="shrink-0 flex items-center justify-center gap-1.5 bg-surface-3 hover:bg-surface-2 disabled:opacity-50 text-zinc-200 text-xs font-medium px-3 py-2.5 rounded-lg border border-white/10 transition-colors"
            title={
              "Generate ONLY scene #1 (one short LLM call) so you can iterate " +
              "on the story seed and style before committing to the full plan. " +
              "Wipes any existing scenes. After scene 1 is approved, use the " +
              "chain icon on its row in the Generate step to add scenes 2, 3, …"
            }
          >
            <Wand2 className="w-3.5 h-3.5" />
            Just scene 1
          </button>
        </div>
        {genRunning && (
          <div className="mt-2">
            <div className="h-1.5 bg-surface-3 rounded-full overflow-hidden">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${genTotal ? (genSoFar / genTotal) * 100 : 5}%` }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-500">
              <span>Batches of {BATCH_SIZE} · each batch carries previous scenes as continuity context</span>
              <button
                onClick={() => { genCancelRef.current = true; }}
                className="text-zinc-400 hover:text-red-400"
              >Stop after current batch</button>
            </div>
          </div>
        )}
        {genError && (
          <div className="mt-2 bg-red-900/20 border border-red-800/40 rounded-md px-2.5 py-2 text-[11px] text-red-300 flex items-start justify-between gap-2">
            <div>
              <span className="font-medium">Generation failed at batch starting #{genSoFar + 1}: </span>
              {genError.length > 400 ? genError.slice(0, 400) + "…" : genError}
              <div className="text-[10px] text-red-300/70 mt-1">
                {genSoFar > 0
                  ? `${genSoFar} scenes were planned before the failure — click Re-generate to start fresh, or fix the issue and re-run to retry the failed batch.`
                  : "No scenes were planned. Try again."}
              </div>
            </div>
            <button
              onClick={() => setGenError(null)}
              className="text-red-400/60 hover:text-red-200 shrink-0"
              title="Dismiss"
            >✕</button>
          </div>
        )}

        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          <ModelTag
            label="LLM"
            model={models?.llm?.[planOpts.plan_llm]?.name || planOpts.plan_llm}
            hint="Used for batch scene generation and per-scene re-expansion"
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
              {/* AI Expand all is no longer a separate step — the batch
                  generator produces fully-expanded scenes inline. Keeping
                  this stub commented out as breadcrumb in case we need
                  per-scene re-expansion later (the /expand-prompts endpoint
                  still works for individual scenes via the cog menu). */}
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
              <button
                onClick={async () => {
                  if (await confirm({
                    title: `Delete all ${scenes.length} scenes?`,
                    message:
                      `This removes every scene from this project (and their image / video assets in the DB). The plan starts from scratch. ` +
                      `Files on disk under storage/${project.id}/ are not touched — they're orphaned but cheap.`,
                    confirmLabel: "Clear all scenes",
                    destructive: true,
                  })) {
                    clearAll.mutate();
                  }
                }}
                disabled={clearAll.isPending}
                className="text-[11px] text-red-400 hover:text-red-300 flex items-center gap-1 disabled:opacity-50"
                title="Delete every scene in this project — useful before a fresh Re-plan"
              >
                {clearAll.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                {clearAll.isPending ? "Clearing…" : "Clear all"}
              </button>
            </div>
          </div>
          <div className="space-y-1.5">
            {scenes.map((s) => (
              <ScenePlanRow
                key={s.id}
                scene={s}
                expanded={expandedScene === s.id}
                onToggle={() => setExpandedScene(expandedScene === s.id ? null : s.id)}
                onUpdate={refresh}
                expandLlm={llmId}
                isExpandingBatch={genRunning}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ScenePlanRow({
  scene, expanded, onToggle, onUpdate, expandLlm, isExpandingBatch,
}: {
  scene: Scene;
  expanded: boolean;
  onToggle: () => void;
  onUpdate: () => void;
  expandLlm: string;
  // True while AI Expand All is running for the project. Scenes that haven't
  // yet flipped prompts_expanded=true are either queued or in flight.
  isExpandingBatch: boolean;
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

  // Confirmation for delete — reads the cached project to see if the next
  // scene is chained from this one, so we can warn about the unlink.
  const confirm = useConfirm();
  const cachedProject = qc.getQueryData<any>(["project", scene.project_id]);
  const nextScene: Scene | undefined = (cachedProject?.scenes || [])
    .find((s: Scene) => s.order === scene.order + 1);
  const nextChainedFromHere = !!nextScene?.chain_from_prev;
  const onDeleteClick = async () => {
    const msg = [
      `Permanently remove scene #${scene.order} from this project.`,
      "All its assets (images, videos) and prompt history will be deleted.",
      nextChainedFromHere
        ? `Scene #${nextScene?.order} is chained from here — its chain will be cleared (scene stays in place; you can re-chain or use its own first frame).`
        : null,
    ].filter(Boolean).join("\n");
    if (await confirm({
      title: `Delete scene #${scene.order}?`,
      message: msg,
      confirmLabel: "Delete scene",
      destructive: true,
    })) {
      remove.mutate();
    }
  };

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
        {/* AI / plan-only / expanding badge removed — the new batch generator
            always produces fully-expanded scenes, so the distinction is moot.
            Only surface the "expanding" indicator while a batch is mid-flight
            for the not-yet-generated rows. */}
        {!scene.prompts_expanded && isExpandingBatch && (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/30 shrink-0 flex items-center gap-0.5"
            title="Generation in progress — this scene is queued"
          >
            <Loader2 className="w-2.5 h-2.5 animate-spin" /> generating
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
            className={inputCls + " resize-y"} />

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
                className={inputCls + " resize-y"} />
              <span className="text-[10px] text-zinc-500">Image prompt</span>
              <textarea rows={2} value={form.image_prompt}
                onChange={(e) => setForm({ ...form, image_prompt: e.target.value })}
                className={inputCls + " resize-y"} />
            </div>
          </details>
          <div className="flex gap-2 pt-1">
            <button
              onClick={onDeleteClick}
              disabled={remove.isPending}
              className="text-[11px] text-zinc-600 hover:text-error transition-colors flex items-center gap-1 px-2 disabled:opacity-50"
              title={
                nextChainedFromHere
                  ? `Delete scene #${scene.order}. Also unlinks scene #${nextScene?.order} (its chain will clear).`
                  : `Delete scene #${scene.order} (removes from the plan + all its assets + prompt history).`
              }
            >
              <Trash2 className="w-3 h-3" /> {remove.isPending ? "Deleting..." : "Delete"}
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
