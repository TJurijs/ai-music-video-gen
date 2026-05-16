"use client";
import { Image as ImageIcon, Video, Layers, FileText, Link2, Trash2, Download, Upload } from "lucide-react";
import { useState } from "react";
import type { Scene, SceneAsset } from "@/lib/types";
import { useConfirm } from "@/components/ConfirmDialog";
import VariantGallery from "./VariantGallery";
import PromptVersionGallery from "./PromptVersionGallery";

export default function FrameSlot({
  title, assetType, assets, activeUrl, renderedWithLabel, renderedProvider, renderedResolution, nextModelLabel, nextProvider, nextResolution, onOpenLightbox, onActivate, onDelete, onDownloadUrl, onDownloadLabel, onUpload, uploadLabel, uploading, modelLookup, actionButton,
  scene, onPromptActivate, onPromptDelete,
  chainedFromUrl, chainedFromOrder,
}: {
  title: string;
  assetType: "image" | "video";
  assets: SceneAsset[];
  activeUrl?: string | null;
  // Model that produced the asset currently on display. null when nothing is
  // rendered yet — shown as "not generated yet".
  renderedWithLabel: string | null;
  // Provider that produced the displayed asset (always "openrouter" for
  // current renders). Pulled from the asset's metadata_json. null when
  // missing on older entries.
  renderedProvider?: string | null;
  // Resolution recorded on the displayed asset (e.g. "720p", "1080p").
  // Pulled from the asset's metadata_json. null when missing.
  renderedResolution?: string | null;
  // Model currently selected for the NEXT generation. Always set. Used to
  // tell the user what will run if they press the action button.
  nextModelLabel: string;
  // Provider the NEXT generation will route through. Always "openrouter"
  // in v1 — kept on the prop for forward compatibility.
  nextProvider?: string | null;
  // Resolution the NEXT generation will use (scene.resolution).
  nextResolution?: string | null;
  onOpenLightbox: () => void;
  onActivate: (id: number) => void;
  onDelete: (id: number) => void;
  // Optional download / upload hooks. When set, a small icon button appears
  // on the slot title row. Used for: first-frame download (image slot),
  // video upload to skip generation (video slot).
  onDownloadUrl?: string | null;
  onDownloadLabel?: string;
  onUpload?: (file: File) => void;
  uploadLabel?: string;
  uploading?: boolean;
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
  const confirm = useConfirm();
  const variantCount = assets.length;
  const activeAsset = assets.find((a) => a.is_active) ?? null;

  // Small resolution chip — sits next to provider so render quality is
  // visible at a glance. Highlighted accent when set, dimmed when unknown.
  const renderResolutionChip = (res: string | null | undefined) => {
    if (!res) return null;
    return (
      <span
        className="ml-1 align-baseline text-[8px] uppercase tracking-wide px-1 py-[1px] rounded border bg-accent/15 text-accent border-accent/30"
        title={`Resolution: ${res}`}
      >
        {res}
      </span>
    );
  };

  // Small provider chip. v1 always routes through OpenRouter; the chip
  // is kept so historical assets (with provider in their metadata) still
  // render a label, but visually distinct providers don't exist any more.
  const renderProviderChip = (provider: string | null | undefined) => {
    if (!provider) return null;
    return (
      <span
        className="ml-1 align-baseline text-[8px] uppercase tracking-wide px-1 py-[1px] rounded border bg-zinc-500/15 text-zinc-400 border-zinc-500/30"
        title="Routes through OpenRouter."
      >
        {provider}
      </span>
    );
  };
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
          {onDownloadUrl && (
            <a
              href={onDownloadUrl}
              download
              className="text-zinc-500 hover:text-zinc-200 transition-colors"
              title={onDownloadLabel || "Download"}
            >
              <Download className="w-2.5 h-2.5" />
            </a>
          )}
          {onUpload && (
            <label
              className={`text-zinc-500 hover:text-zinc-200 transition-colors cursor-pointer ${uploading ? "opacity-50 pointer-events-none" : ""}`}
              title={uploadLabel || "Upload a file as this variant"}
            >
              <input
                type="file"
                accept="video/mp4,video/quicktime,video/webm"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onUpload(f);
                  e.target.value = "";  // reset so re-selecting the same file fires onChange again
                }}
              />
              <Upload className="w-2.5 h-2.5" />
            </label>
          )}
          {activeAsset && (
            <button
              onClick={async () => {
                if (await confirm({
                  title: `Delete this ${assetType}?`,
                  message: variantCount > 1
                    ? `Delete the active ${assetType}. The most recent remaining variant will become active.`
                    : `Delete this ${assetType} — there are no other variants, so the slot will go back to "not generated".`,
                  confirmLabel: "Delete",
                  destructive: true,
                })) {
                  onDelete(activeAsset.id);
                }
              }}
              className="text-zinc-500 hover:text-red-400 transition-colors"
              title={`Delete the current ${assetType}`}
            >
              <Trash2 className="w-2.5 h-2.5" />
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
      {/* Line 1: what generated the asset currently on screen (or chain note,
          or empty-slot fallback). Reads as the PAST: "this is what you see". */}
      <div
        className="text-[9px] truncate"
        title={renderedWithLabel ? `Rendered with ${renderedWithLabel}` : "No asset rendered yet"}
      >
        {isImageSlotChainOverride ? (
          <span className="text-emerald-400/80 italic">
            last frame of scene #{chainedFromOrder ?? scene.order}
          </span>
        ) : renderedWithLabel ? (
          <>
            <span className="text-zinc-600">rendered with </span>
            <span className="text-zinc-300">{renderedWithLabel}</span>
            {renderProviderChip(renderedProvider)}
            {renderResolutionChip(renderedResolution)}
          </>
        ) : (
          <span className="italic text-zinc-600">not generated yet</span>
        )}
      </div>
      {/* Line 2: model staged for the NEXT generation. Reads as the FUTURE:
          "this is what will run if you press the button". Highlighted when it
          differs from what produced the on-screen asset so a staged switch
          is visually obvious. */}
      {(() => {
        const isChange =
          !!renderedWithLabel && renderedWithLabel !== nextModelLabel;
        return (
          <div
            className="text-[9px] truncate"
            title={`Pressing the button below will generate using ${nextModelLabel}. Use the ▾ menu to switch.`}
          >
            <span className="text-zinc-600">{renderedWithLabel ? "next " : "will use "}</span>
            <span
              className={
                isChange
                  ? "text-fuchsia-300 font-medium"
                  : "text-zinc-300"
              }
            >
              {nextModelLabel}
            </span>
            {renderProviderChip(nextProvider)}
            {renderResolutionChip(nextResolution)}
            {isChange && (
              <span className="ml-1 text-[8px] uppercase tracking-wide text-fuchsia-400/80">
                · staged change
              </span>
            )}
          </div>
        );
      })()}
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
