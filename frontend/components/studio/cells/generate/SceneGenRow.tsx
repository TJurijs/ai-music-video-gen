"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Image as ImageIcon, Video, Mic2, Settings, Square, Trash2, Link2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Scene, Song, GenerationJob, ModelsConfig } from "@/lib/types";
import Lightbox from "../../Lightbox";
import ScenePreview from "./ScenePreview";
import VideoModelCard from "./VideoModelCard";
import DescriptionWithPromptTooltip from "./DescriptionWithPromptTooltip";
import CharacterRefsBadge from "./CharacterRefsBadge";
import { StatusPill, ExpandedBadge, SceneErrorBanner } from "./SceneStatus";
import FrameSlot from "./FrameSlot";
import SplitGenerateButton from "./SplitGenerateButton";
import { fmt, fmtCost } from "./shared";

export default function SceneGenRow({
  scene, models, song, sceneCost, sceneJobs, onRefresh,
}: {
  scene: Scene;
  models?: ModelsConfig;
  song?: Song;
  sceneCost: number;
  sceneJobs: GenerationJob[];
  onRefresh: () => void;
}) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [showModels, setShowModels] = useState(false);

  const generate = useMutation({
    mutationFn: ({ phase, force }: { phase: "image" | "video" | "lipsync" | "all"; force: boolean }) =>
      api.generation.generateScene(scene.id, force, phase as any),
    onSuccess: onRefresh,
  });
  const cancel = useMutation({
    mutationFn: () => api.generation.cancelScene(scene.id),
    onSuccess: onRefresh,
  });
  const activate = useMutation({
    mutationFn: (assetId: number) => api.scenes.activateAsset(scene.id, assetId),
    onSuccess: onRefresh,
  });
  const deleteAsset = useMutation({
    mutationFn: (assetId: number) => api.scenes.deleteAsset(scene.id, assetId),
    onSuccess: onRefresh,
  });
  const clearScene = useMutation({
    mutationFn: () => api.scenes.clear(scene.id),
    onSuccess: onRefresh,
  });
  const activatePrompt = useMutation({
    mutationFn: (versionId: number) => api.scenes.activatePrompt(scene.id, versionId),
    onSuccess: onRefresh,
  });
  const deletePrompt = useMutation({
    mutationFn: (versionId: number) => api.scenes.deletePrompt(scene.id, versionId),
    onSuccess: onRefresh,
  });
  const [showPreview, setShowPreview] = useState(false);
  const [showImage, setShowImage] = useState(false);

  const updateModel = useMutation({
    mutationFn: (data: { video_model?: string; image_model?: string; lipsync_model?: string; resolution?: string; generate_audio?: boolean; lipsync_enabled?: boolean; chain_from_prev?: boolean }) =>
      api.scenes.update(scene.id, data),
    onSuccess: onRefresh,
  });

  const isRunning = ["generating_image", "generating_video", "lipsync"].includes(scene.status);
  const hasImage = !!scene.reference_image_url;
  const hasVideo = !!scene.video_url;
  const imageAssets = scene.assets?.filter((a) => a.asset_type === "image") || [];
  const videoAssets = scene.assets?.filter((a) => a.asset_type === "video") || [];
  const lipsyncAssets = scene.assets?.filter((a) => a.asset_type === "lipsync") || [];

  // Pull the previous scene from the cached project query so the chained
  // first-frame can be displayed (we need its `extracted_last_frame_url`).
  // Falls back gracefully if the project isn't in cache yet.
  const project = qc.getQueryData<any>(["project", scene.project_id]);
  const allScenes: Scene[] = project?.scenes || [];
  const prevScene = allScenes.find((s) => s.order === scene.order - 1);
  const canChain = scene.order >= 1;
  const chainActive = !!scene.chain_from_prev && canChain;

  // Detect whether the active video is lipsynced (for the badge on the video frame)
  const activeVideoAsset = videoAssets.find((a) => a.is_active);
  const activeVideoIsLipsynced = (() => {
    if (!activeVideoAsset?.metadata_json) return false;
    try { return !!JSON.parse(activeVideoAsset.metadata_json).lipsynced; } catch { return false; }
  })();

  return (
    <div className={`bg-surface-2 rounded-lg overflow-hidden border ${
      scene.status === "done" ? "border-green-700/30" :
      scene.status === "error" ? "border-red-700/40" :
      isRunning ? "border-accent/40" : "border-white/5"
    }`}>
      {/* Top row: scene #, time, status, description, action buttons */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5">
        <span className="text-[10px] font-mono text-zinc-500 shrink-0 w-6 text-center">
          #{scene.order}
        </span>
        <span className="text-[10px] font-mono text-zinc-500 shrink-0 w-20">
          {fmt(scene.audio_start)}–{fmt(scene.audio_end)}
        </span>
        <StatusPill status={scene.status} />
        <ExpandedBadge expanded={!!scene.prompts_expanded} />
        {sceneCost > 0 && (
          <span
            className="text-[9px] font-mono text-green-400/80 shrink-0"
            title={sceneJobs.map((j) => `${j.job_type}: ${fmtCost(j.cost_usd)} — ${j.cost_detail || ""}`).join("\n")}
          >
            {fmtCost(sceneCost)}
          </span>
        )}
        <DescriptionWithPromptTooltip scene={scene} />
        {canChain && (
          <button
            onClick={() => updateModel.mutate({ chain_from_prev: !chainActive })}
            disabled={updateModel.isPending}
            className={`shrink-0 p-1 rounded transition-colors ${
              chainActive
                ? "bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 ring-1 ring-emerald-500/40"
                : "text-zinc-500 hover:text-emerald-300"
            } disabled:opacity-50`}
            title={
              chainActive
                ? `Chained from scene #${scene.order} — click to disconnect. Video will start on this scene's planned still instead.`
                : `Chain from scene #${scene.order}: this clip's first frame becomes the previous scene's actual last rendered frame (seamless seam). Click to enable.`
            }
          >
            <Link2 className="w-3.5 h-3.5" />
          </button>
        )}
        <button
          onClick={() => setShowModels(!showModels)}
          className="text-zinc-500 hover:text-white p-1 rounded transition-colors shrink-0"
          title="Model settings"
        >
          <Settings className="w-3.5 h-3.5" />
        </button>
        {isRunning && (
          <button
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending || scene.cancel_requested}
            className="text-xs px-2 py-1 bg-red-500/15 hover:bg-red-500/30 text-red-300 border border-red-500/30 rounded transition-colors disabled:opacity-50 flex items-center gap-1 shrink-0"
            title="Stop generation"
          >
            <Square className="w-3 h-3" />
            {scene.cancel_requested ? "Stopping..." : "Stop"}
          </button>
        )}
        {!isRunning && (hasImage || hasVideo || (scene.assets?.length || 0) > 0) && (
          <button
            onClick={async () => {
              if (await confirm({
                title: `Clear scene #${scene.order}`,
                message: "Clear all generated content for this scene?\nDescription and prompts stay; images, videos, and lipsync clips will be deleted.",
                confirmLabel: "Clear scene",
                destructive: true,
              })) {
                clearScene.mutate();
              }
            }}
            disabled={clearScene.isPending}
            className="text-zinc-600 hover:text-red-400 p-1 rounded transition-colors disabled:opacity-50 shrink-0"
            title="Clear all generated assets for this scene (keeps description and prompts)"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {scene.error_message && (
        <SceneErrorBanner
          scene={scene}
          onSoftened={onRefresh}
        />
      )}

      {/* Frame slots — reference still | video clip. Scenes are independent
          shots joined by hard cuts at assembly, so there's no last-frame anchor. */}
      <div className="grid grid-cols-2 gap-2 p-2">
        <FrameSlot
          title="First frame"
          assetType="image"
          assets={imageAssets}
          activeUrl={scene.reference_image_url}
          modelLabel={models?.image?.[scene.image_model]?.name || scene.image_model}
          onOpenLightbox={() => scene.reference_image_url && setShowImage(true)}
          onActivate={(id) => activate.mutate(id)}
          onDelete={(id) => deleteAsset.mutate(id)}
          modelLookup={models?.image}
          scene={scene}
          onPromptActivate={(id) => activatePrompt.mutate(id)}
          onPromptDelete={(id) => deletePrompt.mutate(id)}
          chainedFromUrl={chainActive ? (prevScene?.extracted_last_frame_url ?? null) : null}
          chainedFromOrder={chainActive ? prevScene?.order ?? null : null}
          actionButton={
            <SplitGenerateButton
              label={imageAssets.length > 0 ? "+ Img" : "Img"}
              icon={<ImageIcon className="w-3 h-3" />}
              running={scene.status === "generating_image"}
              disabled={generate.isPending || isRunning}
              currentModel={scene.image_model}
              options={models?.image ? Object.entries(models.image).map(([k, m]) => ({ key: k, label: m.name })) : []}
              onClickMain={() => generate.mutate({ phase: "image", force: hasImage })}
              onPickModel={(key) => {
                updateModel.mutate({ image_model: key });
                setTimeout(() => generate.mutate({ phase: "image", force: hasImage }), 200);
              }}
              colorClasses="bg-blue-500/15 hover:bg-blue-500/30 text-blue-300 border-blue-500/30"
              title={imageAssets.length > 0
                ? "Generate another image variant — keeps prior versions"
                : "Generate reference still"}
            />
          }
        />

        <FrameSlot
          title="Video"
          assetType="video"
          assets={videoAssets}
          activeUrl={scene.video_url}
          modelLabel={
            activeVideoAsset
              ? (activeVideoAsset.model_used?.includes(" + ")
                  ? activeVideoAsset.model_used
                  : (models?.video?.[activeVideoAsset.model_used || ""]?.name || activeVideoAsset.model_used || "—"))
              : (models?.video?.[scene.video_model]?.name || scene.video_model)
          }
          isLipsynced={activeVideoIsLipsynced}
          onOpenLightbox={() => scene.video_url && setShowPreview(true)}
          onActivate={(id) => activate.mutate(id)}
          onDelete={(id) => deleteAsset.mutate(id)}
          modelLookup={models?.video}
          scene={scene}
          onPromptActivate={(id) => activatePrompt.mutate(id)}
          onPromptDelete={(id) => deletePrompt.mutate(id)}
          actionButton={
            <div className="flex gap-1 flex-wrap justify-center">
              <SplitGenerateButton
                label={videoAssets.length > 0 ? "+ Vid" : "Vid"}
                icon={<Video className="w-3 h-3" />}
                running={scene.status === "generating_video"}
                disabled={generate.isPending || isRunning || !hasImage}
                currentModel={scene.video_model}
                options={models?.video ? Object.entries(models.video).map(([k, m]) => ({ key: k, label: m.name })) : []}
                onClickMain={() => generate.mutate({ phase: "video", force: scene.status === "done" })}
                onPickModel={(key) => {
                  updateModel.mutate({ video_model: key });
                  setTimeout(() => generate.mutate({ phase: "video", force: scene.status === "done" }), 200);
                }}
                colorClasses="bg-accent/20 hover:bg-accent/40 text-accent border-accent/30"
                title={!hasImage ? "Generate image first" : videoAssets.length > 0
                  ? "Generate another video variant — keeps prior versions"
                  : "Generate video from reference image"}
              />
              <SplitGenerateButton
                label={lipsyncAssets.length > 0 ? "+ Sync" : "Sync"}
                icon={<Mic2 className="w-3 h-3" />}
                running={scene.status === "lipsync"}
                disabled={generate.isPending || isRunning || !hasVideo}
                currentModel={scene.lipsync_model}
                options={models?.lipsync ? Object.entries(models.lipsync).map(([k, m]) => ({ key: k, label: m.name })) : []}
                onClickMain={() => generate.mutate({ phase: "lipsync", force: false })}
                onPickModel={(key) => {
                  updateModel.mutate({ lipsync_model: key });
                  setTimeout(() => generate.mutate({ phase: "lipsync", force: false }), 200);
                }}
                colorClasses="bg-indigo-500/15 hover:bg-indigo-500/30 text-indigo-300 border-indigo-500/30"
                title={!hasVideo ? "Generate video first" : lipsyncAssets.length > 0
                  ? "Run lipsync again — saved as another video variant"
                  : "Run lipsync on existing video"}
              />
            </div>
          }
        />
      </div>

      <CharacterRefsBadge scene={scene} />

      {showModels && models && (
        <div className="border-t border-white/5 bg-surface-3 px-3 py-3 space-y-3">
          {/* Image model picker */}
          <div>
            <label className="text-[10px] text-zinc-500 mb-1.5 flex items-center gap-1 uppercase tracking-wide">
              <ImageIcon className="w-2.5 h-2.5" /> Image Model
            </label>
            <select
              value={scene.image_model}
              onChange={(e) => updateModel.mutate({ image_model: e.target.value })}
              className="w-full bg-surface-2 border border-white/10 rounded-md px-2 py-1.5 text-xs text-white focus:outline-none focus:border-accent"
            >
              {Object.entries(models.image).map(([key, m]) => (
                <option key={key} value={key}>
                  {m.name} — ${m.price_per_image}/img
                </option>
              ))}
            </select>
            {models.image[scene.image_model]?.note && (
              <p className="text-[10px] text-zinc-600 mt-1">{models.image[scene.image_model].note}</p>
            )}
          </div>

          <div>
            <label className="text-[10px] text-zinc-500 mb-1.5 flex items-center gap-1 uppercase tracking-wide">
              <Video className="w-2.5 h-2.5" /> Video Model
            </label>
            <div className="grid grid-cols-1 gap-1.5">
              {Object.entries(models.video).map(([key, m]) => (
                <VideoModelCard
                  key={key}
                  modelKey={key}
                  model={m}
                  selected={scene.video_model === key}
                  duration={Math.round(scene.audio_end - scene.audio_start)}
                  resolution={scene.resolution}
                  withAudio={scene.generate_audio}
                  onSelect={() => updateModel.mutate({ video_model: key })}
                />
              ))}
            </div>
          </div>

          {/* Lipsync model picker */}
          {models.lipsync && (
            <div>
              <label className="text-[10px] text-zinc-500 mb-1.5 flex items-center gap-1 uppercase tracking-wide">
                <Mic2 className="w-2.5 h-2.5" /> Lipsync Model
              </label>
              <select
                value={scene.lipsync_model}
                onChange={(e) => updateModel.mutate({ lipsync_model: e.target.value })}
                className="w-full bg-surface-2 border border-white/10 rounded-md px-2 py-1.5 text-xs text-white focus:outline-none focus:border-accent"
              >
                {Object.entries(models.lipsync).map(([key, m]) => (
                  <option key={key} value={key}>
                    {m.name} — ${m.price_per_clip}/clip
                  </option>
                ))}
              </select>
              {models.lipsync[scene.lipsync_model]?.note && (
                <p className="text-[10px] text-zinc-600 mt-1">{models.lipsync[scene.lipsync_model].note}</p>
              )}
            </div>
          )}

          {/* Resolution / quality selector — constrained to current model's options */}
          {(() => {
            const cur = models.video[scene.video_model];
            if (!cur) return null;
            return (
              <div>
                <label className="text-[10px] text-zinc-500 mb-1 flex items-center gap-1 uppercase tracking-wide">
                  Resolution
                </label>
                <div className="flex gap-1">
                  {cur.resolutions.map((res) => (
                    <button
                      key={res}
                      onClick={() => updateModel.mutate({ resolution: res })}
                      className={`text-[10px] px-2.5 py-1 rounded-md border transition-colors ${
                        scene.resolution === res
                          ? "bg-accent/30 border-accent/50 text-accent font-medium"
                          : "bg-surface-2 border-white/10 text-zinc-400 hover:text-white"
                      }`}
                    >
                      {res}
                    </button>
                  ))}
                </div>
              </div>
            );
          })()}


        </div>
      )}
      {showPreview && hasVideo && (
        <ScenePreview scene={scene} song={song} onClose={() => setShowPreview(false)} />
      )}
      {showImage && scene.reference_image_url && (
        <Lightbox
          src={scene.reference_image_url}
          caption={`Scene #${scene.order} reference still — ${scene.description?.slice(0, 80) || ""}`}
          onClose={() => setShowImage(false)}
        />
      )}
    </div>
  );
}
