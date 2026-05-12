"use client";
import { Image as ImageIcon, Video, Mic2, Layers, FileText, Link2 } from "lucide-react";
import { useState } from "react";
import type { Scene, SceneAsset } from "@/lib/types";
import VariantGallery from "./VariantGallery";
import PromptVersionGallery from "./PromptVersionGallery";

export default function FrameSlot({
  title, assetType, assets, activeUrl, modelLabel, isLipsynced, onOpenLightbox, onActivate, onDelete, modelLookup, actionButton,
  scene, onPromptActivate, onPromptDelete,
  chainedFromUrl, chainedFromOrder,
}: {
  title: string;
  assetType: "image" | "video";
  assets: SceneAsset[];
  activeUrl?: string | null;
  modelLabel: string;
  isLipsynced?: boolean;
  onOpenLightbox: () => void;
  onActivate: (id: number) => void;
  onDelete: (id: number) => void;
  modelLookup?: Record<string, any>;
  actionButton: React.ReactNode;
  scene: Scene;
  onPromptActivate: (id: number) => void;
  onPromptDelete: (id: number) => void;
  // When the scene is chained from the previous one, this is the prev
  // scene's actual last rendered frame — what the video model will see as
  // its first_frame. Replaces the displayed thumbnail in the image slot
  // so users can preview the real handoff. Null when not chained.
  chainedFromUrl?: string | null;
  chainedFromOrder?: number | null;
}) {
  const [showGallery, setShowGallery] = useState(false);
  const [showPrompts, setShowPrompts] = useState(false);
  const variantCount = assets.length;
  // Filter prompt versions for this slot's type ("image" or "video")
  const promptVersions = (scene.prompt_versions || []).filter((p) => p.prompt_type === assetType);
  const activePrompt = promptVersions.find((p) => p.is_active);
  // When chaining is on, the IMAGE slot's planned still is overridden at
  // video-gen time by the previous scene's extracted last frame. We badge
  // both the image (info — your still is bypassed) and the video slot
  // (info — this clip will start on a different frame than the still
  // suggests).
  const isChained = !!scene.chain_from_prev && scene.order >= 1;
  // For the IMAGE slot under chain: replace what we display with the
  // previous scene's actual last rendered frame (or a placeholder if
  // that scene hasn't been generated yet). The planned still still
  // lives in `assets` and can be reached via the "variants" button.
  const isImageSlotChainOverride = isChained && assetType === "image";
  const displayUrl = isImageSlotChainOverride
    ? (chainedFromUrl ?? null)  // null → render the "generate prev first" placeholder
    : activeUrl;
  const chainPlaceholder = isImageSlotChainOverride && !chainedFromUrl;
  return (
    <div className="bg-surface-3 rounded-md border border-white/5 p-1.5 flex flex-col gap-1.5">
      <div className="text-[9px] text-zinc-500 uppercase tracking-wide flex items-center justify-between gap-1">
        <span>{title}</span>
        <div className="flex items-center gap-1.5">
          {promptVersions.length > 0 && (
            <button
              onClick={() => setShowPrompts(true)}
              className="flex items-center gap-0.5 text-zinc-400 hover:text-zinc-200"
              title={
                promptVersions.length === 1
                  ? `Active prompt (${activePrompt?.source ?? "?"}) — click to view`
                  : `${promptVersions.length} prompt versions — click to view history & pick active`
              }
            >
              <FileText className="w-2.5 h-2.5" />
              {promptVersions.length > 1 && <span>×{promptVersions.length}</span>}
            </button>
          )}
          {variantCount > 1 && (
            <button
              onClick={() => setShowGallery(true)}
              className="flex items-center gap-0.5 text-accent hover:text-accent-hover"
              title={`${variantCount} ${assetType} variants — click to pick which is active`}
            >
              <Layers className="w-2.5 h-2.5" />
              ×{variantCount}
            </button>
          )}
        </div>
      </div>
      <div
        className={`relative aspect-video bg-surface-2 rounded overflow-hidden ${
          displayUrl ? "cursor-zoom-in" : ""
        }`}
        onClick={displayUrl ? onOpenLightbox : undefined}
        title={displayUrl ? (assetType === "video" ? "Open video preview" : "View full image") : ""}
      >
        {displayUrl ? (
          assetType === "image" ? (
            <img src={displayUrl} className="w-full h-full object-cover" alt="" />
          ) : (
            <video src={displayUrl} className="w-full h-full object-cover" muted loop
              onMouseEnter={(e) => e.currentTarget.play()}
              onMouseLeave={(e) => e.currentTarget.pause()} />
          )
        ) : chainPlaceholder ? (
          // Chained from prev but prev has no rendered video yet — show
          // an actionable placeholder instead of the empty image icon.
          <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-emerald-300/80 px-2 text-center">
            <Link2 className="w-4 h-4" />
            <span className="text-[9px] leading-tight">
              Waiting for scene #{chainedFromOrder ?? scene.order}'s video.
              Generate it first.
            </span>
          </div>
        ) : (
          <div className="w-full h-full flex items-center justify-center text-zinc-700">
            {assetType === "image" ? <ImageIcon className="w-5 h-5" /> : <Video className="w-5 h-5" />}
          </div>
        )}
        {isLipsynced && displayUrl && (
          <span className="absolute top-1 left-1 text-[8px] bg-indigo-500/80 text-white px-1 py-0.5 rounded font-medium flex items-center gap-0.5">
            <Mic2 className="w-2 h-2" /> sync
          </span>
        )}
        {isChained && (
          <span
            className="absolute top-1 right-1 text-[8px] bg-emerald-500/80 text-white px-1 py-0.5 rounded font-medium flex items-center gap-0.5"
            title={
              assetType === "image"
                ? `Chained: this image is scene #${chainedFromOrder ?? scene.order}'s actual last frame — what video gen will use as first_frame. Planned still saved as a variant.`
                : `Chained: this clip will open on scene #${chainedFromOrder ?? scene.order}'s actual last frame.`
            }
          >
            <Link2 className="w-2 h-2" /> chain #{chainedFromOrder ?? scene.order}
          </span>
        )}
      </div>
      <div className="text-[9px] text-zinc-500 truncate" title={modelLabel}>
        {isImageSlotChainOverride
          ? <span className="text-emerald-400/80 italic">last frame of scene #{chainedFromOrder ?? scene.order}</span>
          : displayUrl ? modelLabel : <span className="italic">not generated</span>}
      </div>
      <div>
        {actionButton}
      </div>
      {showGallery && (
        <VariantGallery
          assetType={assetType}
          assets={assets}
          modelLookup={modelLookup}
          onActivate={(id) => { onActivate(id); setShowGallery(false); }}
          onDelete={onDelete}
          onClose={() => setShowGallery(false)}
        />
      )}
      {showPrompts && (
        <PromptVersionGallery
          sceneId={scene.id}
          promptType={assetType}
          versions={promptVersions}
          onActivate={(id) => { onPromptActivate(id); setShowPrompts(false); }}
          onDelete={onPromptDelete}
          onClose={() => setShowPrompts(false)}
        />
      )}
    </div>
  );
}
