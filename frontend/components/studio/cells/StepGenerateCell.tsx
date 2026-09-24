"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Image as ImageIcon, Video, RefreshCw, Search, CheckCircle2, AlertCircle } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Scene, GenerationJob, ProjectCosts, GenerationPhase } from "@/lib/types";
import { generationView, type SceneFilter } from "@/lib/generationView";
import SceneGenRow from "./generate/SceneGenRow";
import { fmtCost } from "./generate/shared";

type BulkRequest = { phase: GenerationPhase; sceneIds: number[]; force?: boolean; title: string; confirmLabel: string };

export default function StepGenerateCell({ project, scenes, jobs, costs }: {
  project: Project; scenes: Scene[]; jobs: GenerationJob[]; costs?: ProjectCosts;
}) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [filter, setFilter] = useState<SceneFilter>("all");
  const [search, setSearch] = useState("");
  const [dirtySceneIds, setDirtySceneIds] = useState<Set<number>>(() => new Set());
  const trackSettingsDirty = useCallback((sceneId: number, dirty: boolean) => {
    setDirtySceneIds((previous) => {
      if (previous.has(sceneId) === dirty) return previous;
      const next = new Set(previous);
      if (dirty) next.add(sceneId); else next.delete(sceneId);
      return next;
    });
  }, []);
  const submissionLock = useRef(false);
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ["project", project.id] }),
    qc.invalidateQueries({ queryKey: ["jobs", project.id] }),
  ]);
  const { data: models, error: modelsError, refetch: refreshModels } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const view = useMemo(() => generationView({ ...project, scenes }, models, jobs), [project, scenes, models, jobs]);
  const generateBatch = useMutation({
    mutationFn: ({ phase, sceneIds, force }: BulkRequest) => api.generation.generateBatch(project.id, sceneIds, !!force, phase),
    onSettled: refresh,
  });

  const runBulk = async (request: BulkRequest) => {
    if (submissionLock.current || dirtySceneIds.size || !request.sceneIds.length) return;
    submissionLock.current = true;
    setError(null);
    setNotice(null);
    setChecking(true);
    try {
      const report = await api.generation.preflight(project.id, request.phase, request.sceneIds, !!request.force);
      const blocked = report.scenes.filter((scene) => !scene.ready);
      if (blocked.length) {
        setError(blocked.map((scene) => `Scene #${scene.scene_order}: ${scene.errors.join("; ")}`).join("\n"));
        return;
      }
      if (!report.scene_count) { setNotice("No scenes currently need this operation."); return; }
      const resumeCount = report.scenes.filter((scene) => scene.resuming).length;
      const warnings = Array.from(new Set(report.scenes.flatMap((scene) => scene.warnings)));
      const ok = await confirm({
        title: request.title,
        message: [
          `${report.scene_count} scene${report.scene_count === 1 ? "" : "s"} passed the readiness check.`,
          `Estimated new provider cost: ${fmtCost(report.estimated_cost)}.`,
          resumeCount ? `${resumeCount} existing provider job${resumeCount === 1 ? "" : "s"} will resume without submitting a new render.` : null,
          warnings.length ? `Notes:\n• ${warnings.slice(0, 4).join("\n• ")}` : null,
          "Existing successful variants stay in history.",
        ].filter(Boolean).join("\n\n"),
        confirmLabel: request.confirmLabel,
        destructive: !!request.force && request.phase === "all",
      });
      if (ok) {
        await generateBatch.mutateAsync(request);
        setNotice(`${report.scene_count} scene${report.scene_count === 1 ? "" : "s"} submitted. Progress updates automatically below.`);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not prepare generation.");
      await refresh();
    } finally {
      submissionLock.current = false;
      setChecking(false);
    }
  };

  if (!scenes.length) return <p className="pt-4 text-sm text-zinc-400">Create a scene plan to start generating.</p>;

  const done = view.complete.length;
  const running = view.running.length;
  const busy = checking || generateBatch.isPending || !models;
  const bulkBusy = busy || dirtySceneIds.size > 0;
  const filtered = filter === "all" ? view.scenes : view[filter];
  const shownScenes = filtered.filter((scene) => `${scene.order} ${scene.description || ""} ${scene.lyrics_segment || ""}`.toLowerCase().includes(search.toLowerCase()));
  const filters: { key: SceneFilter; label: string; count: number }[] = [
    { key: "all", label: "All scenes", count: scenes.length },
    { key: "attention", label: "Needs attention", count: view.attention.length },
    { key: "running", label: "In progress", count: running },
    { key: "stills", label: "Needs a still", count: view.stills.length },
    { key: "videos", label: "Ready for video", count: view.videos.length },
    { key: "complete", label: "Complete", count: done },
  ];

  return (
    <div className="space-y-4 pt-4">
      <div className="rounded-xl border border-white/10 bg-gradient-to-br from-accent/[0.08] to-surface-2 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-400" />{done} of {scenes.length} videos complete</h3>
            <p className="mt-1 text-xs leading-relaxed text-zinc-400">
              {running ? `${running} scene${running === 1 ? " is" : "s are"} queued or rendering. You can return here to check progress.`
                : view.attention.length ? "Review the scenes that need attention. Successful variants stay in history."
                : done === scenes.length ? "Your scenes are ready. Open Final Assembly to preview and export your video."
                : "Create stills, review the look, then generate video clips. Check the estimate before each action starts."}
            </p>
          </div>
          {view.attention.length > 0 && <button onClick={() => setFilter("attention")} className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300"><AlertCircle className="h-3.5 w-3.5" />Review {view.attention.length} scene{view.attention.length === 1 ? "" : "s"}</button>}
        </div>
        <div className="h-1.5 bg-surface-3 rounded-full overflow-hidden" role="progressbar" aria-label="Completed scene videos" aria-valuemin={0} aria-valuemax={scenes.length} aria-valuenow={done}>
          <div className="h-full bg-accent transition-all" style={{ width: `${done / scenes.length * 100}%` }} />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded-xl border border-blue-500/20 bg-blue-500/[0.04] p-3 space-y-2">
          <h4 className="text-xs font-medium text-blue-200">1 · Create the look</h4>
          <p className="text-xs text-zinc-400">Generate missing stills and review the first frames below.</p>
          <button onClick={() => runBulk({ phase: "image", sceneIds: view.stills.map((scene) => scene.id), title: "Generate missing stills", confirmLabel: "Generate stills" })} disabled={bulkBusy || !view.stills.length} className="w-full flex items-center justify-center gap-2 bg-blue-500/15 hover:bg-blue-500/30 border border-blue-500/30 text-blue-300 disabled:opacity-50 text-sm font-medium py-2.5 rounded-lg transition-colors">
            <ImageIcon className="h-4 w-4" />{view.stills.length ? `Generate ${view.stills.length} still${view.stills.length === 1 ? "" : "s"}` : "No missing stills"}
          </button>
        </div>
        <div className="rounded-xl border border-accent/20 bg-accent/[0.04] p-3 space-y-2">
          <h4 className="text-xs font-medium text-violet-200">2 · Bring scenes to life</h4>
          <p className="text-xs text-zinc-400">Chained scenes wait for the previous clip’s final frame.</p>
          <button onClick={() => runBulk({ phase: "video", sceneIds: view.videos.map((scene) => scene.id), title: "Generate ready videos", confirmLabel: "Start videos" })} disabled={bulkBusy || !view.videos.length} className="w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-2.5 rounded-lg transition-colors">
            <Video className="h-4 w-4" />{view.videos.length ? `Generate ${view.videos.length} video${view.videos.length === 1 ? "" : "s"}` : "No videos ready yet"}
          </button>
        </div>
      </div>
      {checking && <p role="status" className="flex items-center gap-2 text-xs text-zinc-400"><Loader2 className="h-3 w-3 animate-spin" />Checking scenes, estimating cost and preparing your request…</p>}
      {dirtySceneIds.size > 0 && <p role="status" className="text-xs text-amber-300">Save or cancel the settings on {dirtySceneIds.size} scene{dirtySceneIds.size === 1 ? "" : "s"} before starting batch generation.</p>}
      {notice && <p role="status" className="rounded-lg border border-emerald-800/40 bg-emerald-900/10 px-3 py-2 text-xs text-emerald-300">{notice}</p>}
      {modelsError && <div role="alert" className="flex items-center justify-between gap-2 rounded-lg border border-amber-800/40 p-3 text-xs text-amber-300"><span>Models could not be loaded. Generation is paused until they are available.</span><button onClick={() => refreshModels()} className="underline shrink-0">Try again</button></div>}

      {(scenes.some((scene) => scene.reference_image_url) || done > 0) && <details className="text-xs text-zinc-500">
        <summary className="cursor-pointer py-1">Regenerate existing work</summary>
        <p className="my-2">Creates new variants with a new provider cost. Review the estimate before confirming.</p>
        <div className="flex flex-wrap gap-2">
          <button disabled={bulkBusy || running > 0} onClick={() => runBulk({ phase: "image", sceneIds: scenes.map((scene) => scene.id), force: true, title: "Regenerate all stills", confirmLabel: "Regenerate stills" })} className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-surface-2 px-3 py-2 text-zinc-300 hover:bg-surface-3 disabled:opacity-40"><RefreshCw className="h-3 w-3" />Regenerate all stills</button>
          {done > 0 && <button disabled={bulkBusy || running > 0} onClick={() => runBulk({ phase: "all", sceneIds: scenes.map((scene) => scene.id), force: true, title: "Regenerate every scene", confirmLabel: "Regenerate everything" })} className="rounded-lg border border-white/10 bg-surface-2 px-3 py-2 text-zinc-300 hover:bg-surface-3 disabled:opacity-40">Regenerate all stills & videos</button>}
        </div>
      </details>}
      {error && <div role="alert" className="flex items-start justify-between gap-3 whitespace-pre-line rounded-lg border border-red-800/40 bg-red-900/20 px-3 py-2 text-xs text-red-300"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss generation error" className="shrink-0 px-1 text-red-300">×</button></div>}

      <div className="border-t border-white/10 pt-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h3 className="text-sm font-semibold">Scene workspace <span className="ml-1 text-xs font-normal text-zinc-500">{shownScenes.length} of {scenes.length}</span></h3><p className="mt-1 text-xs text-zinc-400">Choose each scene’s model, length and quality below. Compare capabilities in Models in the header.</p></div>
          <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-surface-2 px-2.5 py-2"><Search className="h-3.5 w-3.5 text-zinc-500" /><input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Find a scene" placeholder="Find a scene…" className="w-36 bg-transparent text-xs outline-none placeholder:text-zinc-600 sm:w-48" /></label>
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Filter scenes">
          {filters.map((item) => <button key={item.key} onClick={() => setFilter(item.key)} aria-pressed={filter === item.key} className={`rounded-full border px-2.5 py-1.5 text-xs transition-colors ${filter === item.key ? "border-accent/40 bg-accent/15 text-violet-200" : "border-white/10 text-zinc-400 hover:text-white hover:bg-white/5"}`}>{item.label}<span className="ml-1.5 opacity-60">{item.count}</span></button>)}
        </div>
      </div>
      <div className="grid gap-3">
        {shownScenes.map((scene) => <SceneGenRow key={scene.id} scene={scene} models={models} song={project.songs?.[0]} sceneCost={costs?.by_scene?.[scene.id] ?? 0} sceneJobs={view.jobsByScene.get(scene.id) || []} generationDisabled={busy} onRefresh={refresh} onSettingsDirtyChange={trackSettingsDirty} />)}
        {!shownScenes.length && <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-sm text-zinc-500">No scenes match this view. <button onClick={() => { setSearch(""); setFilter("all"); }} className="text-violet-300 underline underline-offset-4">Show all scenes</button></div>}
      </div>
      {costs && costs.total_usd > 0 && <details className="rounded-lg border border-white/5 bg-surface-2 p-3 text-xs text-zinc-400">
        <summary className="cursor-pointer">Spent so far <span className="float-right font-mono text-green-400">{fmtCost(costs.total_usd)}</span></summary>
        <dl className="mt-3 space-y-1.5">{Object.entries(costs.by_type).filter(([, value]) => value > 0).map(([key, value]) => <div key={key} className="flex justify-between"><dt>{({ music: "Music", transcription: "Transcription", llm_plan: "Scene planning", llm_expand: "Prompt expansion", image: "Reference images", video: "Video clips", assembly: "Assembly" } as Record<string, string>)[key] || key}</dt><dd className="font-mono">{fmtCost(value)}</dd></div>)}</dl>
      </details>}
    </div>
  );
}
