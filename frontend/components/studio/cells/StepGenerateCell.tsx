"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Image as ImageIcon, Video, RefreshCw, Settings, DollarSign } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Scene, GenerationJob, ProjectCosts } from "@/lib/types";
import SceneGenRow from "./generate/SceneGenRow";
import GlobalModelPicker from "./generate/GlobalModelPicker";
import { fmtCost, mostCommon } from "./generate/shared";

export default function StepGenerateCell({
  project, scenes, jobs, costs,
}: {
  project: Project;
  scenes: Scene[];
  jobs: GenerationJob[];
  costs?: ProjectCosts;
}) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const refresh = () => qc.invalidateQueries({ queryKey: ["project", project.id] });

  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });

  const generateBatch = useMutation({
    mutationFn: (phase: "image" | "video" | "all") =>
      api.generation.generateBatch(project.id, undefined, false, phase),
    onSuccess: refresh,
  });

  const regenerateAll = useMutation({
    mutationFn: () => api.generation.generateBatch(project.id, undefined, true, "all"),
    onSuccess: refresh,
  });

  const regenAllStills = useMutation({
    mutationFn: () => api.generation.generateBatch(project.id, undefined, true, "image"),
    onSuccess: refresh,
  });

  // Global default model setter — patches every scene at once
  const setGlobalModel = useMutation({
    mutationFn: async (data: { image_model?: string; video_model?: string; resolution?: string }) => {
      await Promise.all(scenes.map((s) => api.scenes.update(s.id, data)));
    },
    onSuccess: refresh,
  });

  if (scenes.length === 0) {
    return <div className="pt-4 text-sm text-zinc-500">Plan some scenes first.</div>;
  }

  const done = scenes.filter((s) => s.status === "done").length;
  const pending = scenes.filter((s) => s.status === "pending").length;
  const imageReady = scenes.filter((s) => s.status === "image_ready").length;
  const errored = scenes.filter((s) => s.status === "error").length;
  const cancelled = scenes.filter((s) => s.status === "cancelled").length;
  const inProgress = scenes.filter((s) =>
    ["generating_image", "generating_video"].includes(s.status)
  ).length;
  const noImage = scenes.filter((s) => !s.reference_image_url).length;

  return (
    <div className="space-y-4 pt-4">
      {/* Progress bar */}
      <div>
        <div className="flex items-center justify-between text-xs mb-2">
          <span className="text-zinc-400">{done} of {scenes.length} complete</span>
          <span className="text-zinc-500">
            {inProgress > 0 && <span className="text-accent">{inProgress} running · </span>}
            {pending > 0 && <span>{pending} pending</span>}
            {errored > 0 && <span className="text-error"> · {errored} errors</span>}
          </span>
        </div>
        <div className="h-1.5 bg-surface-3 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent transition-all"
            style={{ width: `${(done / scenes.length) * 100}%` }}
          />
        </div>
      </div>

      {/* Global model defaults — applies to every scene at once */}
      {models && (
        <div className="bg-surface-2 rounded-lg border border-white/5 p-2.5 space-y-1.5">
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide flex items-center gap-1">
            <Settings className="w-2.5 h-2.5" /> Default models for all {scenes.length} scenes
            <span className="text-zinc-700 normal-case ml-1">(can be overridden per scene in Settings)</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <GlobalModelPicker
              icon={<ImageIcon className="w-2.5 h-2.5" />}
              label="Image"
              value={mostCommon(scenes.map((s) => s.image_model))}
              options={Object.entries(models.image).map(([k, m]) => ({ key: k, label: m.name }))}
              onChange={(v) => setGlobalModel.mutate({ image_model: v })}
              disabled={setGlobalModel.isPending}
            />
            <GlobalModelPicker
              icon={<Video className="w-2.5 h-2.5" />}
              label="Video"
              value={mostCommon(scenes.map((s) => s.video_model))}
              options={Object.entries(models.video).map(([k, m]) => ({ key: k, label: m.name }))}
              onChange={(v) => {
                // When changing the global video model, also reset everyone's
                // resolution to the new model's first supported option — the
                // old resolution might not be valid for the new model.
                const newModelCfg = models.video[v];
                const fallbackRes = newModelCfg?.resolutions?.[0];
                setGlobalModel.mutate(
                  fallbackRes ? { video_model: v, resolution: fallbackRes } : { video_model: v }
                );
              }}
              disabled={setGlobalModel.isPending}
            />
            {/* Resolution picker — options derive from the currently-selected
                video model. Mass-applies to every scene. */}
            {(() => {
              const currentVideoKey = mostCommon(scenes.map((s) => s.video_model)) || "";
              const cfg = currentVideoKey ? models.video[currentVideoKey] : undefined;
              if (!cfg?.resolutions?.length) return null;
              return (
                <GlobalModelPicker
                  icon={<Settings className="w-2.5 h-2.5" />}
                  label="Resolution"
                  value={mostCommon(scenes.map((s) => s.resolution)) || cfg.resolutions[0]}
                  options={cfg.resolutions.map((r: string) => ({ key: r, label: r }))}
                  onChange={(v) => setGlobalModel.mutate({ resolution: v })}
                  disabled={setGlobalModel.isPending}
                />
              );
            })()}
          </div>
        </div>
      )}

      {/* Bulk actions — split into stages so you preview cheap stills before paying for video */}
      <div className="grid grid-cols-2 gap-2">
        <button
          onClick={() => generateBatch.mutate("image")}
          disabled={generateBatch.isPending || noImage === 0}
          className="flex items-center justify-center gap-2 bg-blue-500/15 hover:bg-blue-500/30 border border-blue-500/30 text-blue-300 disabled:opacity-50 text-sm font-medium py-2.5 rounded-lg transition-colors"
          title="Generate reference still images for scenes that don't have one yet (~$0.04/scene)"
        >
          <ImageIcon className="w-4 h-4" />
          Generate {noImage} Still{noImage === 1 ? "" : "s"}
        </button>
        <button
          onClick={() => generateBatch.mutate("video")}
          disabled={generateBatch.isPending || imageReady + errored + cancelled === 0}
          className="flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
          title="Generate video clips from existing reference stills"
        >
          <Video className="w-4 h-4" />
          Generate {imageReady + errored + cancelled} Video{imageReady + errored + cancelled === 1 ? "" : "s"}
        </button>
      </div>
      <div className="flex gap-2">
        {scenes.some((s) => !!s.reference_image_url) && (
          <button
            onClick={async () => {
              const n = scenes.filter((s) => !!s.reference_image_url).length;
              if (await confirm({
                title: "Regenerate all stills",
                message: `Regenerate all ${n} stills as new variants? Estimated cost ~$${(0.04 * n).toFixed(2)}.\nThe old stills stay as variants — you can pick which one is active per scene.`,
                confirmLabel: "Regenerate all",
              })) {
                regenAllStills.mutate();
              }
            }}
            disabled={regenAllStills.isPending}
            className="flex-1 text-xs px-3 py-2 bg-blue-500/10 hover:bg-blue-500/20 text-blue-300 border border-blue-500/30 rounded-lg transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
            title="Re-render all stills using current image model + project aspect ratio + current style. Useful after changing global settings. Old stills are kept as variants."
          >
            {regenAllStills.isPending
              ? <><Loader2 className="w-3 h-3 animate-spin" /> Re-rendering stills…</>
              : <><RefreshCw className="w-3 h-3" /> Regenerate all stills</>}
          </button>
        )}
        {done > 0 && (
          <button
            onClick={async () => {
              if (await confirm({
                title: "Regenerate everything",
                message: `Regenerate all ${scenes.length} scenes (image + video) from scratch?`,
                confirmLabel: "Regenerate everything",
                destructive: true,
              })) {
                regenerateAll.mutate();
              }
            }}
            className="flex-1 text-xs px-3 py-2 bg-surface-2 hover:bg-surface-3 text-zinc-400 hover:text-white border border-white/10 rounded-lg transition-colors flex items-center justify-center gap-1.5"
          >
            <RefreshCw className="w-3 h-3" /> Regenerate everything from scratch
          </button>
        )}
      </div>

      {/* Per-scene grid */}
      <div className="grid gap-2">
        {scenes.map((s) => (
          <SceneGenRow
            key={s.id}
            scene={s}
            models={models}
            song={project.songs?.[0]}
            sceneCost={costs?.by_scene?.[s.id] ?? 0}
            sceneJobs={jobs.filter((j) => j.scene_id === s.id)}
            onRefresh={refresh}
          />
        ))}
      </div>

      {/* Cost breakdown summary */}
      {costs && costs.total_usd > 0 && (
        <div className="mt-2 bg-surface-2 rounded-lg p-3 border border-white/5">
          <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <DollarSign className="w-3 h-3" /> Spent so far
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
            {[
              ["Music", costs.by_type.music],
              ["Transcription", costs.by_type.transcription],
              ["Scene plan", (costs.by_type.llm_plan || 0) + (costs.by_type.llm_expand || 0)],
              ["Reference images", costs.by_type.image],
              ["Video clips", costs.by_type.video],
            ].filter(([, v]) => v && (v as number) > 0).map(([label, value]) => (
              <div key={label as string} className="flex justify-between text-zinc-400">
                <span>{label}</span>
                <span className="font-mono">{fmtCost(value as number)}</span>
              </div>
            ))}
            <div className="col-span-2 mt-1.5 pt-1.5 border-t border-white/5 flex justify-between font-medium text-green-400">
              <span>Total</span>
              <span className="font-mono">{fmtCost(costs.total_usd)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
