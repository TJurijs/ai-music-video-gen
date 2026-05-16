"use client";
import { X } from "lucide-react";
import { useConfirm } from "@/components/ConfirmDialog";
import type { SceneAsset } from "@/lib/types";

export default function VariantGallery({
  assetType, assets, modelLookup, onActivate, onDelete, onClose,
}: {
  assetType: "image" | "video";
  assets: SceneAsset[];
  modelLookup?: Record<string, any>;
  onActivate: (id: number) => void;
  onDelete: (id: number) => void;
  onClose: () => void;
}) {
  const confirm = useConfirm();
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
        <div className="grid grid-cols-3 gap-3">
          {assets.map((a) => {
            const cleanLabel = modelLookup?.[a.model_used || ""]?.name?.replace(/\s*\(.*\)/, "")
              || a.model_used
              || "—";
            return (
              <div
                key={a.id}
                className={`relative rounded-md overflow-hidden border-2 cursor-pointer transition-colors group ${
                  a.is_active ? "border-accent" : "border-white/10 hover:border-white/30"
                }`}
                onClick={() => onActivate(a.id)}
                title={`${a.model_used} · $${a.cost_usd?.toFixed(3) || 0} · ${new Date(a.created_at).toLocaleString()}`}
              >
                {assetType === "image" ? (
                  <img src={a.url} className="w-full aspect-video object-cover" alt="" />
                ) : (
                  <video src={a.url} className="w-full aspect-video object-cover" muted
                    onMouseEnter={(e) => e.currentTarget.play()}
                    onMouseLeave={(e) => e.currentTarget.pause()} />
                )}
                <span
                  className={`absolute top-1 left-1 text-[9px] px-1.5 py-0.5 rounded font-medium ${
                    assetType === "image" ? "bg-blue-500/80 text-white" : "bg-accent/80 text-white"
                  }`}
                >
                  {cleanLabel}
                </span>
                {a.is_active && (
                  <span className="absolute top-1 right-1 text-[9px] bg-emerald-500 text-white px-1.5 py-0.5 rounded font-medium">
                    ACTIVE
                  </span>
                )}
                <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-0.5">
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
