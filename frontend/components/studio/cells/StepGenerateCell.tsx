"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Image as ImageIcon, Video, RefreshCw, SlidersHorizontal, DollarSign } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Scene, GenerationJob, ProjectCosts } from "@/lib/types";
import SceneGenRow from "./generate/SceneGenRow";
import GlobalModelPicker from "./generate/GlobalModelPicker";
import VideoModelCheatSheet from "./generate/VideoModelCheatSheet";
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
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [checkingPreflight, setCheckingPreflight] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["project", project.id] });

  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });

  const generateBatch = useMutation({
    mutationFn: ({
      phase, sceneIds, force,
    }: {
      phase: "image" | "video" | "all";
      sceneIds: number[];
      force: boolean;
    }) => api.generation.generateBatch(project.id, sceneIds, force, phase),
    onSuccess: refresh,
    onError: (error) => setBulkError((error as Error).message),
  });

  const runBulk = async ({
    phase,
    sceneIds,
    force = false,
    title,
    confirmLabel,
    destructive = false,
  }: {
    phase: "image" | "video" | "all";
    sceneIds: number[];
    force?: boolean;
    title: string;
    confirmLabel: string;
    destructive?: boolean;
  }) => {
    setBulkError(null);
    setCheckingPreflight(true);
    try {
      const report = await api.generation.preflight(
        project.id,
        phase,
        sceneIds,
        force,
      );
      const blocked = report.scenes.filter((scene) => !scene.ready);
      if (blocked.length) {
        setBulkError(
          blocked
            .map((scene) => `Scene #${scene.scene_order}: ${scene.errors.join("; ")}`)
            .join("\n")
        );
        return;
      }
      if (!report.scene_count) {
        setBulkError("No scenes currently need this operation.");
        return;
      }
      const providers = Array.from(new Set(
        report.scenes.map((scene) => scene.provider).filter(Boolean)
      ));
      const warnings = Array.from(new Set(
        report.scenes.flatMap((scene) => scene.warnings)
      ));
      const ok = await confirm({
        title,
        message: [
          `${report.scene_count} scene${report.scene_count === 1 ? "" : "s"} passed preflight.`,
          `Estimated new provider cost: ${fmtCost(report.estimated_cost)}.`,
          providers.length ? `Provider route${providers.length === 1 ? "" : "s"}: ${providers.join(" + ")}.` : null,
          warnings.length ? `Notes:\n• ${warnings.slice(0, 4).join("\n• ")}` : null,
          "Existing successful variants stay available unless you delete them separately.",
        ].filter(Boolean).join("\n\n"),
        confirmLabel,
        destructive,
      });
      if (ok) {
        generateBatch.mutate({ phase, sceneIds, force });
      }
    } catch (error) {
      setBulkError((error as Error).message || "Preflight failed");
    } finally {
      setCheckingPreflight(false);
    }
  };

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
  const errored = scenes.filter((s) => s.status === "error").length;
  const inProgress = scenes.filter((s) =>
    ["generating_image", "generating_video"].includes(s.status)
  ).length;
  const noImage = scenes.filter((s) => !s.reference_image_url).length;
  const noImageIds = scenes.filter((s) => !s.reference_image_url).map((s) => s.id);
  const videoCandidateIds = scenes
    .filter((s) => ["image_ready", "error", "cancelled"].includes(s.status))
    .map((s) => s.id);
  const sceneDurations = scenes.map((scene) => Math.round(scene.audio_end - scene.audio_start));
  const bulkBusy = generateBatch.isPending || checkingPreflight;

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
            <SlidersHorizontal className="w-2.5 h-2.5" /> Default models for all {scenes.length} scenes
            <span className="text-zinc-700 normal-case ml-1">(video model can be overridden from each scene's Vid button)</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
              options={Object.entries(models.video).map(([k, m]) => {
                const unsupported = Array.from(new Set(
                  sceneDurations.filter((duration) => !m.durations.includes(duration))
                )).sort((a, b) => a - b);
                return {
                  key: k,
                  label: m.name,
                  disabled: unsupported.length > 0,
                  reason: unsupported.length
                    ? `does not support ${unsupported.map((value) => `${value}s`).join(", ")} scenes`
                    : undefined,
                };
              })}
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
                  icon={<SlidersHorizontal className="w-2.5 h-2.5" />}
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

      {models && (
        <VideoModelCheatSheet
          models={models}
          selectedModel={mostCommon(scenes.map((s) => s.video_model)) || ""}
          sceneDurations={sceneDurations}
          disabled={setGlobalModel.isPending}
          onSelect={(videoModel, resolution) =>
            setGlobalModel.mutate({ video_model: videoModel, resolution })
          }
        />
      )}

      {/* Bulk actions — split into stages so you preview cheap stills before paying for video */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <button
          onClick={() => runBulk({
            phase: "image",
            sceneIds: noImageIds,
            title: "Generate reference stills",
            confirmLabel: "Generate stills",
          })}
          disabled={bulkBusy || noImage === 0}
          className="flex items-center justify-center gap-2 bg-blue-500/15 hover:bg-blue-500/30 border border-blue-500/30 text-blue-300 disabled:opacity-50 text-sm font-medium py-2.5 rounded-lg transition-colors"
          title="Generate reference still images for scenes that don't have one yet; exact provider cost is recorded from the response"
        >
          <ImageIcon className="w-4 h-4" />
          Generate {noImage} Still{noImage === 1 ? "" : "s"}
        </button>
        <button
          onClick={() => runBulk({
            phase: "video",
            sceneIds: videoCandidateIds,
            title: "Generate scene videos",
            confirmLabel: "Generate videos",
          })}
          disabled={bulkBusy || videoCandidateIds.length === 0}
          className="flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
          title="Generate video clips from existing reference stills"
        >
          <Video className="w-4 h-4" />
          Generate {videoCandidateIds.length} Video{videoCandidateIds.length === 1 ? "" : "s"}
        </button>
      </div>
      <div className="flex gap-2">
        {scenes.some((s) => !!s.reference_image_url) && (
          <button
            onClick={() => runBulk({
              phase: "image",
              sceneIds: scenes.map((scene) => scene.id),
              force: true,
              title: "Regenerate all stills",
              confirmLabel: "Regenerate all",
            })}
            disabled={bulkBusy}
            className="flex-1 text-xs px-3 py-2 bg-blue-500/10 hover:bg-blue-500/20 text-blue-300 border border-blue-500/30 rounded-lg transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
            title="Re-render all stills using current image model + project aspect ratio + current style. Useful after changing global settings. Old stills are kept as variants."
          >
            {bulkBusy
              ? <><Loader2 className="w-3 h-3 animate-spin" /> Re-rendering stills…</>
              : <><RefreshCw className="w-3 h-3" /> Regenerate all stills</>}
          </button>
        )}
        {done > 0 && (
          <button
            onClick={() => runBulk({
              phase: "all",
              sceneIds: scenes.map((scene) => scene.id),
              force: true,
              title: "Regenerate every scene",
              confirmLabel: "Regenerate everything",
              destructive: true,
            })}
            disabled={bulkBusy}
            className="flex-1 text-xs px-3 py-2 bg-surface-2 hover:bg-surface-3 text-zinc-400 hover:text-white border border-white/10 rounded-lg transition-colors flex items-center justify-center gap-1.5"
          >
            <RefreshCw className="w-3 h-3" /> Regenerate everything from scratch
          </button>
        )}
      </div>

      {bulkError && (
        <div className="whitespace-pre-line rounded-lg border border-red-800/40 bg-red-900/20 px-3 py-2 text-xs text-red-300">
          <div className="flex items-start justify-between gap-3">
            <span>{bulkError}</span>
            <button
              type="button"
              onClick={() => setBulkError(null)}
              className="shrink-0 text-red-400/70 hover:text-red-200"
              aria-label="Dismiss generation error"
            >
              ×
            </button>
          </div>
        </div>
      )}

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
