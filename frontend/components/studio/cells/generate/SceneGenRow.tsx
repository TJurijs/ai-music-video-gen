"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Image as ImageIcon, Video, Mic2, Square, Trash2, Link2, Download, Wand2, Loader2, X, Users, ImageOff, AlertCircle } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Scene, Song, GenerationJob, ModelsConfig } from "@/lib/types";
import Lightbox from "../../Lightbox";
import ScenePreview from "./ScenePreview";
import DescriptionWithPromptTooltip from "./DescriptionWithPromptTooltip";
import CharacterRefsBadge from "./CharacterRefsBadge";
import { StatusPill, ExpandedBadge, SceneErrorBanner } from "./SceneStatus";
import FrameSlot from "./FrameSlot";
import SplitGenerateButton from "./SplitGenerateButton";
import { fmt, fmtCost, textMentionsCharacter } from "./shared";

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
  const [actionError, setActionError] = useState<string | null>(null);
  const [checkingPreflight, setCheckingPreflight] = useState(false);

  const generate = useMutation({
    mutationFn: ({ phase, force }: { phase: "image" | "video" | "all"; force: boolean }) =>
      api.generation.generateScene(scene.id, force, phase),
    onSuccess: onRefresh,
    onError: (error) => setActionError((error as Error).message),
  });
  const cancel = useMutation({
    mutationFn: () => api.generation.cancelScene(scene.id),
    onSuccess: onRefresh,
    onError: (error) => setActionError((error as Error).message),
  });
  const activate = useMutation({
    mutationFn: (assetId: number) => api.scenes.activateAsset(scene.id, assetId),
    onSuccess: onRefresh,
    onError: (error) => setActionError((error as Error).message),
  });
  const deleteAsset = useMutation({
    mutationFn: (assetId: number) => api.scenes.deleteAsset(scene.id, assetId),
    onSuccess: onRefresh,
  });
  const clearScene = useMutation({
    mutationFn: () => api.scenes.clear(scene.id),
    onSuccess: onRefresh,
  });
  // Full scene delete (different from clear-assets above). Cascades to all
  // assets + prompt versions. Backend also auto-unlinks the NEXT scene's
  // chain_from_prev if it pointed here, so we don't leave a dangling chain.
  const deleteScene = useMutation({
    mutationFn: () => api.scenes.delete(scene.id),
    onSuccess: onRefresh,
  });
  const uploadVideo = useMutation({
    mutationFn: (file: File) => api.scenes.uploadVideo(scene.id, file),
    onSuccess: onRefresh,
    onError: (error) => setActionError((error as Error).message),
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
    mutationFn: (data: { video_model?: string; image_model?: string; resolution?: string; chain_from_prev?: boolean; audio_sync_enabled?: boolean; video_reference_mode?: "frame" | "character" }) =>
      api.scenes.update(scene.id, data),
    onSuccess: onRefresh,
    onError: (error) => setActionError((error as Error).message),
  });

  // Vision-grounded continuation prompt. Generates video + image prompts for
  // this chained scene by feeding the LLM the PREV scene's actual last frame
  // as visual context, so the motion flows naturally from where the previous
  // clip ended (no teleporting characters, no jump cuts).
  const continuationPrompt = useMutation({
    mutationFn: () => api.scenes.generateContinuationPrompt(scene.id),
    onSuccess: onRefresh,
  });
  const continuationErr = continuationPrompt.error instanceof Error
    ? continuationPrompt.error.message
    : null;

  // "Chain to next" — creates scene N+1 (or enables chain on existing N+1).
  // The icon on THIS row reflects the state of the NEXT scene's chain,
  // because that's the relationship the user is controlling. See backend
  // `/scenes/{id}/chain-next` for the three cases (create / enable / no-op).
  const chainNext = useMutation({
    mutationFn: () => api.scenes.chainToNext(scene.id),
    onSuccess: onRefresh,
  });
  // Disconnect path: flip chain_from_prev=false on the NEXT scene (not this
  // one). Doesn't delete the next scene — the user can still keep it as a
  // free-standing scene with its own first_frame.
  const unchainNext = useMutation({
    mutationFn: (nextSceneId: number) =>
      api.scenes.update(nextSceneId, { chain_from_prev: false } as any),
    onSuccess: onRefresh,
  });
  const chainNextErr =
    (chainNext.error instanceof Error ? chainNext.error.message : null) ||
    (unchainNext.error instanceof Error ? unchainNext.error.message : null);

  // A scene is already busy as soon as the backend claims it. The worker may
  // not have advanced the public status from "pending" yet, so relying only on
  // generating_* hides Stop and leaves the generation buttons incorrectly live.
  const hasGenerationClaim = !!scene.generation_run_id;
  const isRunning = hasGenerationClaim
    || ["generating_image", "generating_video"].includes(scene.status);
  const hasImage = !!scene.reference_image_url;
  const hasVideo = !!scene.video_url;
  const imageAssets = scene.assets?.filter((a) => a.asset_type === "image") || [];
  const videoAssets = scene.assets?.filter((a) => a.asset_type === "video") || [];

  // Pull adjacent scenes from the cached project query.
  //  - prevScene: the one before this one. Used to display the chained
  //    first-frame on THIS scene's left slot (when this scene is chained).
  //  - nextScene: the one after this one. Drives the NEW "chain to next"
  //    icon — it represents whether the NEXT clip picks up exactly where
  //    THIS one ends, not whether this one picks up from the prev.
  const project = qc.getQueryData<any>(["project", scene.project_id]);
  const allScenes: Scene[] = project?.scenes || [];
  const prevScene = allScenes.find((s) => s.order === scene.order - 1);
  const nextScene = allScenes.find((s) => s.order === scene.order + 1);
  // True if this scene's video acts as the visual anchor for scene N+1.
  // = next scene exists AND has chain_from_prev=true.
  const nextChainedFromHere = !!nextScene && !!nextScene.chain_from_prev;
  // chainActive (kept for the existing first-frame display logic): is THIS
  // scene chained from prev? That's still the field driving rendering — the
  // icon's semantics just point the other direction now.
  const canChain = scene.order >= 1;
  const chainActive = !!scene.chain_from_prev && canChain;
  const hasChainFrame = chainActive && !!prevScene?.extracted_last_frame_url;

  // Video routing — most scenes go through OpenRouter (I2V). When the
  // chosen model has supports_audio_input AND scene.audio_sync_enabled,
  // the backend routes through fal's R2V endpoint instead. R2V doesn't
  // use a first_frame, so the image slot / chain frame don't matter
  // for canGenerateVideo in audio-sync mode.
  const videoModelCfg = models?.video?.[scene.video_model];
  const sceneDuration = Math.round(scene.audio_end - scene.audio_start);
  const videoDurationCompatible = !!videoModelCfg?.durations.includes(sceneDuration);
  const modelSupportsAudio = !!videoModelCfg?.supports_audio_input;
  const audioSyncActive = !!scene.audio_sync_enabled && modelSupportsAudio;
  const audioUsesFrame = audioSyncActive && videoModelCfg?.audio_input_mode === "wan_i2v";
  const characterReferenceMode = (
    !audioSyncActive
    && !!videoModelCfg?.supports_reference_images
    && scene.video_reference_mode === "character"
  );
  const nextVideoProvider = audioSyncActive ? "fal" : "openrouter";
  // In audio-sync mode the model doesn't need a first_frame; the video
  // gen button should be enabled as long as the user has picked a model.
  // (At backend time we still require at least one named character ref;
  // we surface that as a render-time error rather than disabling the button.)
  const referenceHaystack = `${scene.video_prompt || ""} ${scene.image_prompt || ""} ${scene.description || ""}`.toLowerCase();
  const passedCharacterReferences = (project?.characters || []).filter((character: any) => {
    const name = (character.name || "").toLowerCase().trim();
    const named = textMentionsCharacter(referenceHaystack, name);
    return named && !!character.reference_image_url;
  });
  const hasUsableCharacterReference = passedCharacterReferences.length > 0;
  const canGenerateVideo = videoDurationCompatible && (
    audioSyncActive
      ? true
      : characterReferenceMode
        ? hasUsableCharacterReference
        : (hasImage || hasChainFrame)
  );

  const activeVideoAsset = videoAssets.find((a) => a.is_active);
  // Pull the recorded provider from the active video asset's metadata_json.
  // v1 always writes "openrouter"; the field stays for forward compatibility
  // and to keep historical assets rendering correctly.
  const renderedVideoProvider = (() => {
    if (!activeVideoAsset?.metadata_json) return null;
    try {
      const meta = JSON.parse(activeVideoAsset.metadata_json);
      return meta.provider || "openrouter";
    } catch {
      return null;
    }
  })();
  const renderedVideoResolution = (() => {
    if (!activeVideoAsset?.metadata_json) return null;
    try {
      return JSON.parse(activeVideoAsset.metadata_json).resolution || null;
    } catch {
      return null;
    }
  })();

  const runSingleGeneration = async (
    phase: "image" | "video" | "all",
    force: boolean,
  ) => {
    setActionError(null);
    setCheckingPreflight(true);
    try {
      const report = await api.generation.preflight(
        scene.project_id,
        phase,
        [scene.id],
        force,
      );
      const item = report.scenes[0];
      if (!item) {
        setActionError("This scene is not eligible for the requested operation.");
        return;
      }
      if (!item.ready) {
        setActionError(item.errors.join("\n"));
        return;
      }
      const ok = await confirm({
        title: `${force ? "Generate a new" : "Generate"} ${phase === "image" ? "still" : "video"} for scene #${scene.order}?`,
        message: [
          `Estimated new provider cost: ${fmtCost(item.estimated_cost)}.`,
          item.provider ? `Route: ${item.provider}${item.route ? ` · ${item.route}` : ""}.` : null,
          item.will_generate_image && phase !== "image"
            ? "A reference still will be generated first and is included in the estimate."
            : null,
          item.warnings.length ? `Notes:\n• ${item.warnings.join("\n• ")}` : null,
          force ? "The current successful asset remains in variant history." : null,
        ].filter(Boolean).join("\n\n"),
        confirmLabel: phase === "image" ? "Generate still" : "Generate video",
      });
      if (ok) generate.mutate({ phase, force });
    } catch (error) {
      setActionError((error as Error).message || "Preflight failed");
    } finally {
      setCheckingPreflight(false);
    }
  };

  return (
    <div className={`bg-surface-2 rounded-lg overflow-hidden border ${
      scene.status === "done" ? "border-green-700/30" :
      scene.status === "error" ? "border-red-700/40" :
      isRunning ? "border-accent/40" : "border-white/5"
    }`}>
      {/* Top row: scene #, time, status, description, action buttons */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-white/5">
        <span className="text-[10px] font-mono text-zinc-500 shrink-0 w-6 text-center">
          #{scene.order}
        </span>
        <span className="text-[10px] font-mono text-zinc-500 shrink-0 w-20">
          {fmt(scene.audio_start)}–{fmt(scene.audio_end)}
        </span>
        <StatusPill status={scene.status} />
        {hasGenerationClaim && scene.status === "pending" && (
          <span
            className="text-[9px] font-mono text-amber-300/90 shrink-0"
            title="Generation is queued and can be stopped before or during provider submission."
          >
            queued
          </span>
        )}
        <ExpandedBadge expanded={!!scene.prompts_expanded} />
        {sceneCost > 0 && (
          <span
            className="text-[9px] font-mono text-green-400/80 shrink-0"
            title={sceneJobs.map((j) => `${j.job_type}: ${fmtCost(j.cost_usd)} — ${j.cost_detail || ""}`).join("\n")}
          >
            {fmtCost(sceneCost)}
          </span>
        )}
        <DescriptionWithPromptTooltip
          scene={scene}
          characters={project?.characters}
          videoModelLabel={models?.video?.[scene.video_model]?.name || scene.video_model}
          videoModelUsesRefs={!!models?.video?.[scene.video_model]?.supports_reference_images}
          audioSyncActive={audioSyncActive}
          audioUsesFrame={audioUsesFrame}
        />
        {/* "Chain to next" icon. Click meanings:
              - No next scene exists → creates scene N+1 (empty prompts,
                chained, inheriting model settings). The user then fills its
                prompts via the wand button on that new row.
              - Next scene exists, NOT chained → flips chain on.
              - Next scene exists, chained → flips chain off (does NOT
                delete the next scene — to delete it, use its row's trash).
            Active styling indicates "next clip continues from this one".
            Always visible (including on scene 1 — that's how you build the
            chain in the first place). */}
        <button
          onClick={() => {
            if (nextChainedFromHere && nextScene) {
              unchainNext.mutate(nextScene.id);
            } else {
              chainNext.mutate();
            }
          }}
          disabled={chainNext.isPending || unchainNext.isPending}
          className={`shrink-0 p-1 rounded transition-colors ${
            nextChainedFromHere
              ? "bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 ring-1 ring-emerald-500/40"
              : "text-zinc-500 hover:text-emerald-300"
          } disabled:opacity-50`}
          title={
            nextChainedFromHere
              ? `Scene #${(nextScene?.order ?? scene.order + 1)} continues from this scene's last frame — click to disconnect (leaves scene #${nextScene?.order} in place but it stops using your last frame).`
              : nextScene
                ? `Connect this scene to scene #${nextScene.order}: its first frame will become this scene's actual last rendered frame (seamless handoff). Click to enable.`
                : `Add scene #${scene.order + 1} chained to this one. Creates an empty next scene that picks up exactly where this one ends. You'll fill its prompts with the wand button on that new row.`
          }
        >
          {chainNext.isPending
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Link2 className="w-3.5 h-3.5" />}
        </button>
        {/* Vision-grounded continuation prompt — populates THIS scene's
            prompts using the PREV scene's actual rendered last frame. Only
            meaningful when this scene is chained from prev AND prev's video
            has been rendered (so the extracted last frame is on disk).
            Hidden otherwise (the backend would 400 with "generate prev
            scene first" — better to just not show the button). */}
        {chainActive && hasChainFrame && (
          <button
            onClick={() => continuationPrompt.mutate()}
            disabled={continuationPrompt.isPending || isRunning}
            className="shrink-0 p-1 rounded transition-colors text-zinc-500 hover:text-violet-300 disabled:opacity-50"
            title={
              `Generate this scene's video & image prompts from the actual last frame of scene #${prevScene?.order ?? scene.order - 1}'s video. ` +
              `The LLM sees that frame and writes motion that flows from it — so the character won't teleport or change direction. ` +
              (scene.video_prompt
                ? "OVERWRITES the current prompts (saved to version history)."
                : "Fills in the empty prompts so you can generate video next.")
            }
          >
            {continuationPrompt.isPending
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : <Wand2 className="w-3.5 h-3.5" />}
          </button>
        )}
        {/* Audio-sync toggle — model-specific fal audio path. Visible
            only when the chosen video model has supports_audio_input
            (Seedance variants on the OpenRouter route).
            When ON:
              - Backend routes video gen through fal R2V (NOT OpenRouter).
              - first_frame is NOT used (Seedance R2V doesn't accept one),
                so the image slot becomes informational only.
              - At least one named cast character must have a portrait;
                the model uses those as image_urls + audio as audio_url.
              - Per-second cost is ~6× the OpenRouter rate. */}
        {modelSupportsAudio && (
          <button
            onClick={() => updateModel.mutate({ audio_sync_enabled: !audioSyncActive })}
            disabled={updateModel.isPending}
            className={`shrink-0 p-1 rounded transition-colors ${
              audioSyncActive
                ? "bg-fuchsia-500/20 text-fuchsia-300 hover:bg-fuchsia-500/30 ring-1 ring-fuchsia-500/40"
                : "text-zinc-500 hover:text-fuchsia-300"
            } disabled:opacity-50`}
            title={
              audioSyncActive
                ? audioUsesFrame
                  ? `Audio sync ON. Video generation routes through fal Wan 2.7 I2V with this scene's song slice plus its exact still/chained first frame. Separate character refs are not sent. Click to disable.`
                  : `Audio sync ON. Video generation routes through fal Seedance R2V with this scene's song slice plus image/character references; no exact first frame is used. Click to disable.`
                : videoModelCfg?.audio_input_mode === "wan_i2v"
                  ? `Audio sync OFF. Click to send this scene's song slice to fal Wan 2.7 with the exact scene/chained first frame as its visual anchor.`
                  : `Audio sync OFF. Click to send this scene's song slice to fal Seedance R2V with character and scene-image references.`
            }
          >
            <Mic2 className="w-3.5 h-3.5" />
          </button>
        )}
        {song && (
          <a
            href={api.scenes.audioChunkUrl(scene.id)}
            download
            className="shrink-0 p-1 rounded text-zinc-500 hover:text-fuchsia-300 transition-colors"
            title={`Download this scene's audio segment (${fmt(scene.audio_start)}–${fmt(scene.audio_end)}, sliced from the song). Use it to test models in their web UI with the exact audio our backend would send.`}
          >
            <Download className="w-3.5 h-3.5" />
          </a>
        )}
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
                message: "Clear all generated content for this scene?\nDescription and prompts stay; images and videos will be deleted.",
                confirmLabel: "Clear scene",
                destructive: true,
              })) {
                clearScene.mutate();
              }
            }}
            disabled={clearScene.isPending}
            className="text-zinc-600 hover:text-red-400 p-1 rounded transition-colors disabled:opacity-50 shrink-0"
            title="Clear all generated assets for this scene (keeps description and prompts so you can regenerate)"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
        {/* Full scene delete — removes the row itself, all its assets, all
            its prompt versions, and unlinks the next scene's chain_from_prev
            if it pointed here. Distinct from the clear-assets trash above:
            this is a "remove this scene from the plan" action. Always visible
            (you may want to delete a scene that hasn't been generated yet). */}
        {!isRunning && (
          <button
            onClick={async () => {
              const willUnlinkNext = !!nextChainedFromHere;
              const assetCount = scene.assets?.length || 0;
              const msg = [
                `Permanently remove scene #${scene.order} from this project.`,
                assetCount > 0
                  ? `${assetCount} asset${assetCount === 1 ? "" : "s"} (images / videos / prompt versions) will be deleted too.`
                  : "No assets to delete.",
                willUnlinkNext
                  ? `Scene #${nextScene?.order} was chained from here — its chain will be cleared (the scene itself stays in place; you can re-chain or generate it standalone).`
                  : null,
              ].filter(Boolean).join("\n");
              if (await confirm({
                title: `Delete scene #${scene.order}?`,
                message: msg,
                confirmLabel: "Delete scene",
                destructive: true,
              })) {
                deleteScene.mutate();
              }
            }}
            disabled={deleteScene.isPending}
            className="text-zinc-600 hover:text-red-400 p-1 rounded transition-colors disabled:opacity-50 shrink-0"
            title={
              nextChainedFromHere
                ? `Delete scene #${scene.order} entirely. Also unlinks scene #${nextScene?.order} (its chain to this scene will clear).`
                : `Delete scene #${scene.order} entirely (removes from the plan + all its assets + prompt history).`
            }
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {scene.error_message && (
        <SceneErrorBanner
          scene={scene}
          onSoftened={onRefresh}
          onDismissed={onRefresh}
        />
      )}
      {videoModelCfg && !videoDurationCompatible && (
        <div className="mx-3 mt-2 rounded-md border border-amber-700/40 bg-amber-900/20 px-2.5 py-2 text-[11px] text-amber-300">
          {videoModelCfg.name} cannot render this {sceneDuration}s scene. Choose a model supporting {sceneDuration}s before generating video.
        </div>
      )}
      {actionError && (
        <div className="mx-3 mt-2 flex items-start justify-between gap-2 whitespace-pre-line rounded-md border border-red-800/40 bg-red-900/20 px-2.5 py-2 text-[11px] text-red-300">
          <span>{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            className="shrink-0 text-red-400/70 hover:text-red-200"
            aria-label="Dismiss scene action error"
          >
            ×
          </button>
        </div>
      )}
      {continuationErr && (
        <div className="mx-3 mt-1 mb-1 bg-amber-900/20 border border-amber-800/40 rounded-md px-2.5 py-1.5 text-[11px] text-amber-300 flex items-start justify-between gap-2">
          <div>
            <span className="font-medium">Couldn't generate continuation prompt: </span>
            {continuationErr.length > 300 ? continuationErr.slice(0, 300) + "…" : continuationErr}
          </div>
          <button
            onClick={() => continuationPrompt.reset()}
            className="text-amber-400/60 hover:text-amber-200 shrink-0"
            title="Dismiss"
          >✕</button>
        </div>
      )}
      {chainNextErr && (
        <div className="mx-3 mt-1 mb-1 bg-amber-900/20 border border-amber-800/40 rounded-md px-2.5 py-1.5 text-[11px] text-amber-300 flex items-start justify-between gap-2">
          <div>
            <span className="font-medium">Couldn't chain to next: </span>
            {chainNextErr.length > 300 ? chainNextErr.slice(0, 300) + "…" : chainNextErr}
          </div>
          <button
            onClick={() => { chainNext.reset(); unchainNext.reset(); }}
            className="text-amber-400/60 hover:text-amber-200 shrink-0"
            title="Dismiss"
          >✕</button>
        </div>
      )}
      {/* Frame slots — reference still | video clip. Scenes are independent
          shots joined by hard cuts at assembly, so there's no last-frame anchor. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 p-2">
        {characterReferenceMode ? (
          <div className="bg-surface-3 rounded-md border border-violet-500/30 p-1.5 flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-medium text-text-secondary flex items-center gap-1">
                <Users className="w-3 h-3 text-violet-400" />
                Character references
              </span>
              <span className="text-[8px] font-semibold tracking-wide text-violet-300 bg-violet-500/10 border border-violet-500/25 rounded px-1 py-0.5">
                CHARACTER MODE
              </span>
            </div>

            <div className="aspect-video rounded border border-violet-500/20 bg-violet-500/[0.04] px-3 flex flex-col justify-center gap-2">
              <div>
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-violet-200">
                  <ImageOff className="w-3.5 h-3.5 shrink-0" />
                  No first frame is sent
                </div>
                <p className="text-[9px] leading-relaxed text-text-muted mt-1">
                  The opening composition comes from the video prompt. Identity comes from the portraits below.
                </p>
              </div>

              {passedCharacterReferences.length > 0 ? (
                <div className="flex items-center gap-1.5 flex-wrap">
                  {passedCharacterReferences.map((character: any) => (
                    <div
                      key={character.id}
                      className="flex items-center gap-1.5 rounded-full bg-surface-2 border border-violet-500/25 pr-2"
                      title={`${character.name} will be sent as a character reference`}
                    >
                      <img
                        src={character.reference_image_url}
                        alt=""
                        className="w-6 h-6 rounded-full object-cover border border-violet-400/30"
                      />
                      <span className="text-[9px] font-medium text-violet-200">{character.name}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex items-start gap-1.5 text-[9px] leading-relaxed text-amber-300">
                  <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
                  Name a character with a portrait in the scene prompt before generating.
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-2">
              <p className="text-[8px] leading-relaxed text-text-muted">
                {passedCharacterReferences.length > 0
                  ? `${passedCharacterReferences.length} portrait${passedCharacterReferences.length === 1 ? "" : "s"} will be sent; the saved still is bypassed.`
                  : "Video generation stays disabled until a matching portrait is found."}
              </p>
              {hasImage && (
                <button
                  type="button"
                  onClick={() => setShowImage(true)}
                  className="shrink-0 text-[8px] text-text-secondary hover:text-text-primary underline underline-offset-2"
                  title="View the saved still; it will not be sent in Character mode"
                >
                  View saved still
                </button>
              )}
            </div>
          </div>
        ) : (
        <FrameSlot
          title="First frame"
          assetType="image"
          assets={imageAssets}
          activeUrl={scene.reference_image_url}
          renderedWithLabel={(() => {
            const activeImg = imageAssets.find((a) => a.is_active);
            if (!activeImg) return null;
            return models?.image?.[activeImg.model_used || ""]?.name
              || activeImg.model_used
              || null;
          })()}
          renderedProvider="openrouter"
          nextModelLabel={models?.image?.[scene.image_model]?.name || scene.image_model}
          nextProvider="openrouter"
          renderedResolution={null}
          nextResolution={null}
          onOpenLightbox={() => {
            // The slot shows either the scene's own rendered still OR the
            // chained prev-scene's extracted last frame. Use whichever is
            // currently being displayed so the lightbox opens the same image
            // the user clicked, not a stale `reference_image_url` of null.
            const displayed = chainActive
              ? prevScene?.extracted_last_frame_url
              : scene.reference_image_url;
            if (displayed) setShowImage(true);
          }}
          onActivate={(id) => activate.mutateAsync(id).then(() => undefined)}
          onDelete={(id) => deleteAsset.mutate(id)}
          modelLookup={models?.image}
          scene={scene}
          onPromptActivate={(id) => activatePrompt.mutate(id)}
          onPromptDelete={(id) => deletePrompt.mutate(id)}
          chainedFromUrl={chainActive ? (prevScene?.extracted_last_frame_url ?? null) : null}
          chainedFromOrder={chainActive ? prevScene?.order ?? null : null}
          onDownloadUrl={
            (chainActive && prevScene?.extracted_last_frame_url) || hasImage
              ? api.scenes.firstFrameUrl(scene.id)
              : null
          }
          onDownloadLabel={
            chainActive
              ? `Download the chained first frame (scene #${prevScene?.order ?? scene.order - 1}'s extracted last frame)`
              : "Download this scene's reference still"
          }
          actionButton={
            <SplitGenerateButton
              label={imageAssets.length > 0 ? "+ Img" : "Img"}
              icon={<ImageIcon className="w-3 h-3" />}
              running={scene.status === "generating_image"}
              disabled={generate.isPending || checkingPreflight || isRunning}
              currentModel={scene.image_model}
              options={models?.image ? Object.entries(models.image).map(([k, m]) => ({ key: k, label: m.name })) : []}
              onClickMain={() => runSingleGeneration("image", hasImage)}
              onPickModel={(key) => updateModel.mutate({ image_model: key })}
              colorClasses="bg-blue-500/15 hover:bg-blue-500/30 text-blue-300 border-blue-500/30"
              title={audioSyncActive
                ? imageAssets.length > 0
                  ? "Generate another image variant — passed to fal Seedance R2V as one of the reference images (along with character portraits)."
                  : "Generate a reference still — in audio-sync mode it goes into Seedance R2V's image_urls as a compositional reference (not a strict first_frame)."
                : imageAssets.length > 0
                  ? "Generate another image variant — keeps prior versions"
                  : "Generate reference still"}
            />
          }
        />
        )}

        <FrameSlot
          title="Video"
          assetType="video"
          assets={videoAssets}
          activeUrl={scene.video_url}
          renderedWithLabel={
            activeVideoAsset
              ? (activeVideoAsset.model_used?.includes(" + ")
                  ? activeVideoAsset.model_used
                  : (models?.video?.[activeVideoAsset.model_used || ""]?.name || activeVideoAsset.model_used || null))
              : null
          }
          renderedProvider={renderedVideoProvider}
          renderedResolution={renderedVideoResolution}
          nextModelLabel={models?.video?.[scene.video_model]?.name || scene.video_model}
          nextProvider={nextVideoProvider}
          nextResolution={scene.resolution}
          onDownloadUrl={hasVideo ? scene.video_url : null}
          onDownloadLabel="Download the active video"
          onUpload={(f) => uploadVideo.mutate(f)}
          uploadLabel="Upload an MP4 as this scene's video — skips generation, saves as a new variant. Useful for plugging in a clip you rendered elsewhere."
          uploading={uploadVideo.isPending}
          onOpenLightbox={() => scene.video_url && setShowPreview(true)}
          onActivate={(id) => activate.mutateAsync(id).then(() => undefined)}
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
                disabled={generate.isPending || checkingPreflight || isRunning || !canGenerateVideo}
                currentModel={scene.video_model}
                options={models?.video ? Object.entries(models.video).map(([k, m]) => ({
                  key: k,
                  label: m.name,
                  disabled: !m.durations.includes(sceneDuration),
                  reason: !m.durations.includes(sceneDuration)
                    ? `${m.name} does not support ${sceneDuration}s scenes (supports ${m.durations.join(", ")}s)`
                    : undefined,
                })) : []}
                onClickMain={() => runSingleGeneration("video", scene.status === "done")}
                onPickModel={(key) => {
                  const fallbackResolution = models?.video?.[key]?.resolutions?.[0];
                  updateModel.mutate(fallbackResolution
                    ? { video_model: key, resolution: fallbackResolution }
                    : { video_model: key });
                }}
                colorClasses="bg-accent/20 hover:bg-accent/40 text-accent border-accent/30"
                title={!videoDurationCompatible
                  ? `${videoModelCfg?.name || scene.video_model} does not support ${sceneDuration}s scenes`
                  : !canGenerateVideo ? (characterReferenceMode
                  ? "Character-reference mode needs a named cast character with a portrait"
                  : chainActive
                    ? "Previous scene needs a video first (chain frame not extracted yet)"
                    : "Generate image first") : videoAssets.length > 0
                  ? "Generate another video variant — keeps prior versions"
                  : "Generate video from reference image"}
              />
            </div>
          }
        />
      </div>

      <CharacterRefsBadge
        scene={scene}
        disabled={updateModel.isPending || isRunning}
        onModeChange={(video_reference_mode) => updateModel.mutate({
          video_reference_mode,
          audio_sync_enabled: false,
        })}
      />

      {showPreview && hasVideo && (
        <ScenePreview scene={scene} song={song} onClose={() => setShowPreview(false)} />
      )}
      {showImage && (() => {
        // Open whichever image is currently shown in the slot. For chained
        // scenes that's the prev-scene's extracted last frame; otherwise
        // this scene's own reference still.
        const displayed = chainActive
          ? prevScene?.extracted_last_frame_url
          : scene.reference_image_url;
        if (!displayed) return null;
        const caption = chainActive
          ? `Scene #${scene.order} first frame — chained from scene #${prevScene?.order ?? scene.order - 1}'s extracted last frame`
          : `Scene #${scene.order} reference still — ${scene.description?.slice(0, 80) || ""}`;
        return (
          <Lightbox
            src={displayed}
            caption={caption}
            onClose={() => setShowImage(false)}
          />
        );
      })()}
    </div>
  );
}
