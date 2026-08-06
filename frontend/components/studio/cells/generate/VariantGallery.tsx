"use client";
import { Check, Loader2, X } from "lucide-react";
import { useState } from "react";
import { useConfirm } from "@/components/ConfirmDialog";
import type { SceneAsset } from "@/lib/types";

export default function VariantGallery({
  assetType, assets, modelLookup, onActivate, onDelete, onClose,
}: {
  assetType: "image" | "video";
  assets: SceneAsset[];
  modelLookup?: Record<string, any>;
  onActivate: (id: number) => Promise<void>;
  onDelete: (id: number) => void;
  onClose: () => void;
}) {
  const confirm = useConfirm();
  const [activatingId, setActivatingId] = useState<number | null>(null);
  const [activationError, setActivationError] = useState<string | null>(null);

  const activate = async (asset: SceneAsset) => {
    if (asset.is_active || activatingId !== null) return;
    setActivationError(null);
    setActivatingId(asset.id);
    try {
      await onActivate(asset.id);
      onClose();
    } catch (error) {
      setActivationError(
        error instanceof Error ? error.message : "Could not activate this variant.",
      );
    } finally {
      setActivatingId(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="bg-surface-2 border border-white/10 rounded-xl p-4 max-w-4xl w-full max-h-[85vh] overflow-y-auto"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold capitalize">{assetType} variants ({assets.length})</h3>
          <button onClick={onClose} className="text-zinc-400 hover:text-white"><X className="w-4 h-4" /></button>
        </div>
        <p className="text-[10px] text-zinc-500 mb-3">
          Click a variant to make it the active one used for downstream generation. X to delete.
        </p>
        {activationError && (
          <p role="alert" className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded-md px-3 py-2 mb-3">
            Activation failed: {activationError}
          </p>
        )}
        <div className="grid grid-cols-3 gap-3">
          {assets.map((a) => {
            const cleanLabel = modelLookup?.[a.model_used || ""]?.name?.replace(/\s*\(.*\)/, "")
              || a.model_used
              || "—";
            return (
              <div
                key={a.id}
                className={`relative rounded-md overflow-hidden border-2 transition-colors group ${
                  a.is_active ? "border-accent" : "border-white/10 hover:border-white/30"
                }`}
                title={`${a.model_used} · $${a.cost_usd?.toFixed(3) || 0} · ${new Date(a.created_at).toLocaleString()}`}
              >
                <button
                  type="button"
                  onClick={() => activate(a)}
                  disabled={a.is_active || activatingId !== null}
                  className="block w-full text-left disabled:cursor-default"
                  aria-label={a.is_active ? "Active variant" : "Make this variant active"}
                >
                  {assetType === "image" ? (
                    <img src={a.url} className="w-full aspect-video object-cover" alt="" />
                  ) : (
                    <video src={a.url} className="w-full aspect-video object-cover" muted
                      onMouseEnter={(e) => e.currentTarget.play()}
                      onMouseLeave={(e) => e.currentTarget.pause()} />
                  )}
                </button>
                <span
                  className={`pointer-events-none absolute top-1 left-1 text-[9px] px-1.5 py-0.5 rounded font-medium ${
                    assetType === "image" ? "bg-blue-500/80 text-white" : "bg-accent/80 text-white"
                  }`}
                >
                  {cleanLabel}
                </span>
                {a.is_active && (
                  <span className="pointer-events-none absolute top-1 right-1 text-[9px] bg-emerald-500 text-white px-1.5 py-0.5 rounded font-medium flex items-center gap-0.5">
                    <Check className="w-2.5 h-2.5" /> ACTIVE
                  </span>
                )}
                {activatingId === a.id && (
                  <span className="pointer-events-none absolute inset-0 bg-black/60 text-white flex items-center justify-center gap-1 text-[11px] font-medium">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Activating…
                  </span>
                )}
                <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-0.5">
                  <div className="text-[10px] text-zinc-300 font-mono">
                    ${a.cost_usd?.toFixed(3) || "—"}
                  </div>
                </div>
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (await confirm({
                      title: `Delete ${assetType} version`,
                      message: `Delete this ${assetType} version permanently?`,
                      confirmLabel: "Delete",
                      destructive: true,
                    })) {
                      onDelete(a.id);
                    }
                  }}
                  className="absolute bottom-1 right-1 text-zinc-300 hover:text-red-400 bg-black/60 rounded p-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
                  title="Delete this version"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
