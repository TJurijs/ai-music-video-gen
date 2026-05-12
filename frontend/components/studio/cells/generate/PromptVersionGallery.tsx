"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Check, X, FileText, Pencil } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";

export default function PromptVersionGallery({
  sceneId, promptType, versions, onActivate, onDelete, onClose,
}: {
  sceneId: number;
  promptType: "image" | "video";
  versions: import("@/lib/types").ScenePromptVersion[];
  onActivate: (id: number) => void;
  onDelete: (id: number) => void;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [editingFromId, setEditingFromId] = useState<number | "new" | null>(null);
  const [draftText, setDraftText] = useState("");

  const sourceColor = (s: string) => ({
    plan:   "bg-zinc-700/50 text-zinc-300 border-zinc-500/30",
    expand: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    soften: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    manual: "bg-blue-500/15 text-blue-300 border-blue-500/40",
  }[s] || "bg-zinc-700/50 text-zinc-300 border-zinc-500/30");

  const saveDraft = useMutation({
    mutationFn: () => {
      const field = (promptType === "video" ? "video_prompt" : "image_prompt") as "video_prompt" | "image_prompt";
      return api.scenes.update(sceneId, { [field]: draftText.trim() });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project"] });
      // Find any project-keyed query and refetch
      qc.invalidateQueries({ predicate: (q) => Array.isArray(q.queryKey) && q.queryKey[0] === "project" });
      setEditingFromId(null);
      setDraftText("");
    },
  });
  const saveError = saveDraft.error instanceof Error ? saveDraft.error.message : null;

  const startEdit = (fromId: number | "new", text: string) => {
    setEditingFromId(fromId);
    setDraftText(text);
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="bg-surface-2 border border-white/10 rounded-xl p-4 max-w-3xl w-full max-h-[85vh] overflow-y-auto"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold capitalize">
            {promptType} prompt history ({versions.length})
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => startEdit("new", "")}
              className="text-[11px] px-2 py-1 bg-blue-500/15 hover:bg-blue-500/30 text-blue-300 border border-blue-500/40 rounded flex items-center gap-1"
              title="Write a fresh prompt version from scratch"
            >
              <FileText className="w-3 h-3" /> New blank version
            </button>
            <button onClick={onClose} className="text-zinc-400 hover:text-white"><X className="w-4 h-4" /></button>
          </div>
        </div>
        <p className="text-[10px] text-zinc-500 mb-3">
          Every plan / expand / soften / manual edit creates a new version. The active one is what gets sent to the {promptType} model. Click a version to make it active. ✏ to clone and edit. X deletes.
        </p>

        {editingFromId !== null && (
          <div className="mb-3 bg-blue-500/5 border-2 border-blue-500/40 rounded-md p-3 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-[9px] px-1.5 py-0.5 rounded border bg-blue-500/15 text-blue-300 border-blue-500/40 uppercase tracking-wide font-medium">
                editing
              </span>
              <span className="text-[10px] text-zinc-500">
                {editingFromId === "new" ? "new blank version" : `cloned from version ${editingFromId}`} — saving will create a new active version (source: manual)
              </span>
              <span className="text-[10px] text-zinc-600 ml-auto">{draftText.length} chars</span>
            </div>
            <textarea
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              autoFocus
              rows={Math.min(20, Math.max(6, Math.floor(draftText.length / 60)))}
              placeholder={`Write or edit the ${promptType}_prompt…`}
              className="w-full bg-surface-3 border border-white/10 rounded-md p-2 text-[11px] text-zinc-200 leading-relaxed focus:outline-none focus:border-accent resize-y font-mono"
            />
            {saveError && (
              <p className="text-[10px] text-red-400">Save failed: {saveError.slice(0, 200)}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => saveDraft.mutate()}
                disabled={saveDraft.isPending || !draftText.trim()}
                className="text-[11px] px-3 py-1 bg-accent hover:bg-accent-hover text-white rounded flex items-center gap-1 disabled:opacity-50"
              >
                {saveDraft.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                Save as new version + activate
              </button>
              <button
                onClick={() => { setEditingFromId(null); setDraftText(""); }}
                className="text-[11px] px-3 py-1 bg-surface-3 hover:bg-surface text-zinc-400 hover:text-white rounded"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {versions.length === 0 && editingFromId === null && (
          <p className="text-xs text-zinc-500 italic">No prompt versions yet — click "New blank version" above to write one.</p>
        )}
        <div className="space-y-2">
          {versions.map((v) => (
            <div
              key={v.id}
              className={`relative rounded-md border-2 p-3 cursor-pointer transition-colors ${
                v.is_active ? "border-accent bg-accent/5" : "border-white/10 bg-surface-3 hover:border-white/30"
              }`}
              onClick={() => onActivate(v.id)}
              title={`Click to make active · ${new Date(v.created_at).toLocaleString()}`}
            >
              <div className="flex items-center gap-2 mb-1.5">
                <span className={`text-[9px] px-1.5 py-0.5 rounded border uppercase tracking-wide font-medium ${sourceColor(v.source)}`}>
                  {v.source}
                </span>
                {v.is_active && (
                  <span className="text-[9px] bg-emerald-500 text-white px-1.5 py-0.5 rounded font-medium">
                    ACTIVE
                  </span>
                )}
                <span className="text-[10px] text-zinc-500">
                  {new Date(v.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </span>
                <span className="text-[10px] text-zinc-600 ml-auto">{v.text.length} chars</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    startEdit(v.id, v.text);
                  }}
                  className="text-zinc-500 hover:text-blue-300 p-0.5"
                  title="Clone this version into an editable draft"
                >
                  <Pencil className="w-3 h-3" />
                </button>
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (await confirm({
                      title: "Delete prompt version",
                      message: "Delete this prompt version permanently?",
                      confirmLabel: "Delete",
                      destructive: true,
                    })) {
                      onDelete(v.id);
                    }
                  }}
                  className="text-zinc-500 hover:text-red-400 p-0.5"
                  title="Delete this version"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
              <p className="text-[11px] text-zinc-300 leading-relaxed whitespace-pre-line">
                {v.text}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
