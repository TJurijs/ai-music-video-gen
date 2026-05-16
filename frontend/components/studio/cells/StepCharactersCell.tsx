"use client";
import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, X, User, Upload, Sparkles, Loader2, ImageIcon, Wand2, ZoomIn, Pencil, Check, Dices } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Character, Song, ThemeAnalysis } from "@/lib/types";
import Lightbox from "../Lightbox";

export default function StepCharactersCell({
  project, characters, song,
}: { project: Project; characters: Character[]; song?: Song }) {
  const qc = useQueryClient();

  // Auto-poll the project while any character portrait is mid-generation.
  // Without this, the spinner just sits there for 20–60s because nothing
  // refetches the project query — even though the background task has
  // already finished. We stop polling the moment everyone's done.
  const anyGenerating = characters.some((c) => c.portrait_status === "generating");
  useEffect(() => {
    if (!anyGenerating) return;
    const id = setInterval(() => {
      qc.invalidateQueries({ queryKey: ["project", project.id] });
    }, 2500);
    return () => clearInterval(id);
  }, [anyGenerating, project.id, qc]);
  const [showForm, setShowForm] = useState(false);
  const [showSuggest, setShowSuggest] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", trigger_word: "" });

  // Default suggest visual style from project or song's theme analysis
  const themeStyle: string = (() => {
    if (project.style) return project.style;
    if (song?.theme_analysis) {
      try { return (JSON.parse(song.theme_analysis) as ThemeAnalysis).suggested_visual_style || ""; }
      catch { return ""; }
    }
    return "";
  })();
  const [suggestForm, setSuggestForm] = useState({ visual_style: themeStyle, count: 3 });
  const [preview, setPreview] = useState<{ src: string; caption: string } | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["project", project.id] });

  const add = useMutation({
    mutationFn: () => api.projects.addCharacter(project.id, form),
    onSuccess: () => {
      setForm({ name: "", description: "", trigger_word: "" });
      setShowForm(false);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.projects.deleteCharacter(project.id, id),
    onSuccess: refresh,
  });

  const suggest = useMutation({
    mutationFn: () => api.projects.suggestCharacters(project.id, suggestForm),
    onSuccess: () => { setShowSuggest(false); refresh(); },
  });

  const songReady = song?.status === "ready";

  return (
    <div className="space-y-3 pt-4">
      <p className="text-xs text-zinc-500">
        Add a character with a reference image and we'll keep it consistent across all scenes.
        Same image model is used for scene generation so identity is preserved.
      </p>


      {characters.length > 0 && (
        <div className="space-y-2">
          {characters.map((c) => (
            <CharacterRow
              key={c.id}
              character={c}
              projectId={project.id}
              onDelete={() => remove.mutate(c.id)}
              onRefresh={refresh}
              onPreview={(src) => setPreview({ src, caption: `${c.name} — ${c.description.slice(0, 100)}…` })}
            />
          ))}
        </div>
      )}

      {preview && (
        <Lightbox src={preview.src} caption={preview.caption} onClose={() => setPreview(null)} />
      )}

      {/* AI suggest panel */}
      {showSuggest ? (
        <div className="bg-accent/5 border border-accent/30 rounded-lg p-3 space-y-2.5">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-accent">
            <Wand2 className="w-3 h-3" /> AI Character Suggestions
          </div>
          <p className="text-[10px] text-zinc-500">
            Claude reads the song's theme + your visual style and pre-fills detailed
            descriptions. You then generate portraits one by one.
          </p>
          <div>
            <label className="text-[10px] text-zinc-500 block mb-1">Visual Style</label>
            <input
              placeholder="e.g. neon noir, cinematic, anime, gritty realism..."
              value={suggestForm.visual_style}
              onChange={(e) => setSuggestForm({ ...suggestForm, visual_style: e.target.value })}
              className={inputCls}
              autoFocus
            />
          </div>
          <div>
            <label className="text-[10px] text-zinc-500 block mb-1">
              How many characters? <span className="text-zinc-300 font-medium">{suggestForm.count}</span>
            </label>
            <input
              type="range" min={1} max={6} step={1}
              value={suggestForm.count}
              onChange={(e) => setSuggestForm({ ...suggestForm, count: Number(e.target.value) })}
              className="w-full accent-accent"
            />
          </div>
          {!songReady && (
            <p className="text-[10px] text-warning">
              ⚠ No song analyzed yet — suggestions will be based on visual style only.
            </p>
          )}
          <div className="flex gap-2 pt-1">
            <button
              onClick={() => setShowSuggest(false)}
              className="flex-1 text-xs py-2 bg-surface-3 hover:bg-surface text-zinc-400 rounded-lg transition-colors"
            >Cancel</button>
            <button
              onClick={() => suggest.mutate()}
              disabled={suggest.isPending}
              className="flex-1 text-xs py-2 bg-accent hover:bg-accent-hover text-white font-medium rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-1"
            >
              {suggest.isPending ? <><Loader2 className="w-3 h-3 animate-spin" /> Suggesting...</> : <>
                <Wand2 className="w-3 h-3" /> Suggest {suggestForm.count}
              </>}
            </button>
          </div>
        </div>
      ) : (
        !showForm && (
          <button
            onClick={() => setShowSuggest(true)}
            className="w-full flex items-center justify-center gap-2 bg-accent/10 hover:bg-accent/20 border border-accent/30 text-accent text-sm py-2.5 rounded-lg transition-colors"
          >
            <Wand2 className="w-3.5 h-3.5" /> Suggest characters with AI
          </button>
        )
      )}

      {showForm ? (
        <div className="bg-surface-2 rounded-lg p-3 space-y-2 border border-white/10">
          <input
            placeholder="Character name (e.g. 'Lena')"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className={inputCls}
            autoFocus
          />
          <textarea
            placeholder="Detailed description: appearance, clothing, signature features..."
            rows={3}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            className={inputCls + " resize-y"}
          />
          <div className="flex gap-2 pt-1">
            <button onClick={() => setShowForm(false)} className="flex-1 text-xs py-2 bg-surface-3 hover:bg-surface text-zinc-400 rounded-lg transition-colors">
              Cancel
            </button>
            <button
              onClick={() => add.mutate()}
              disabled={!form.name || !form.description || add.isPending}
              className="flex-1 text-xs py-2 bg-accent hover:bg-accent-hover text-white font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              {add.isPending ? "Adding..." : "Add Character"}
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setShowForm(true)}
          className="w-full flex items-center justify-center gap-2 border border-dashed border-white/10 hover:border-accent/40 hover:text-white text-zinc-500 text-sm py-3 rounded-lg transition-colors"
        >
          <Plus className="w-4 h-4" /> Add Character
        </button>
      )}
    </div>
  );
}

const PORTRAIT_MODELS = [
  { key: "gemini-3.1-flash-image", short: "Flash", full: "Gemini 3.1 Flash Image", price: "$0.04" },
  { key: "gemini-3-pro-image",     short: "Pro",   full: "Gemini 3 Pro Image",   price: "$0.06" },
] as const;

function CharacterRow({
  character, projectId, onDelete, onRefresh, onPreview,
}: {
  character: Character;
  projectId: number;
  onDelete: () => void;
  onRefresh: () => void;
  onPreview: (src: string) => void;
}) {
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [imageModel, setImageModel] = useState<string>("gemini-3.1-flash-image");
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState({
    name: character.name,
    description: character.description,
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.projects.uploadCharacterImage(projectId, character.id, file),
    onSuccess: onRefresh,
  });

  const generate = useMutation({
    mutationFn: () => api.projects.generateCharacterPortrait(projectId, character.id, imageModel),
    onSuccess: onRefresh,
  });

  const saveEdit = useMutation({
    mutationFn: () => api.projects.updateCharacter(projectId, character.id, {
      name: editForm.name.trim(),
      description: editForm.description.trim(),
    }),
    onSuccess: () => { setIsEditing(false); onRefresh(); },
  });

  const regenerateDescription = useMutation({
    mutationFn: () => api.projects.regenerateCharacter(projectId, character.id),
    onSuccess: onRefresh,
  });
  const activatePortrait = useMutation({
    mutationFn: (assetId: number) => api.projects.activatePortrait(projectId, character.id, assetId),
    onSuccess: onRefresh,
  });
  const deletePortrait = useMutation({
    mutationFn: (assetId: number) => api.projects.deletePortrait(projectId, character.id, assetId),
    onSuccess: onRefresh,
  });
  const updatePortraitDesc = useMutation({
    mutationFn: ({ assetId, description }: { assetId: number; description: string }) =>
      api.projects.updatePortraitDescription(projectId, character.id, assetId, description),
    onSuccess: onRefresh,
  });
  const [showPortraits, setShowPortraits] = useState(false);

  // Esc closes the portrait gallery — small but standard affordance that
  // was missing. Especially helpful when there's only 1 variant and the
  // "Pick portrait" toggle button feels weird to re-click.
  useEffect(() => {
    if (!showPortraits) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowPortraits(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showPortraits]);
  // When non-null: id of the portrait variant whose description is being edited.
  // The textarea takes over the variant's tile so the rest of the gallery stays
  // navigable.
  const [editingDescId, setEditingDescId] = useState<number | null>(null);
  const [draftDesc, setDraftDesc] = useState("");
  const portraits = character.portraits || [];

  const hasImage = !!character.reference_image_url;
  const isGenerating = character.portrait_status === "generating";
  const isError = character.portrait_status === "error";
  const isWorking = generate.isPending || upload.isPending || isGenerating;

  return (
    <div className="bg-surface-2 rounded-lg p-3 flex items-start gap-3">
      {/* Portrait area */}
      <div
        className={`shrink-0 w-16 h-16 rounded-full overflow-hidden bg-surface-3 border border-white/10 relative group ${hasImage ? "cursor-zoom-in" : ""}`}
        onClick={() => { if (hasImage && character.reference_image_url) onPreview(character.reference_image_url); }}
        title={hasImage ? "Click to view full size" : ""}
      >
        {hasImage ? (
          <>
            <img
              src={character.reference_image_url}
              alt={character.name}
              className="w-full h-full object-cover transition-transform group-hover:scale-105"
            />
            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
              <ZoomIn className="w-5 h-5 text-white" />
            </div>
          </>
        ) : (
          <div className="w-full h-full flex items-center justify-center text-zinc-600">
            <User className="w-7 h-7" />
          </div>
        )}
        {isWorking && (
          <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
            <Loader2 className="w-5 h-5 animate-spin text-accent" />
          </div>
        )}
      </div>

      {/* Info + actions */}
      <div className="flex-1 min-w-0">
        {isEditing ? (
          <div className="space-y-1.5 mb-2">
            <input
              value={editForm.name}
              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              placeholder="Character name"
              autoFocus
              className="w-full bg-surface-3 border border-white/10 rounded px-2 py-1 text-xs font-medium text-white focus:outline-none focus:border-accent"
            />
            <textarea
              value={editForm.description}
              onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
              placeholder="Detailed description..."
              rows={2}
              className="w-full bg-surface-3 border border-white/10 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-accent resize-y"
            />
            <div className="flex items-center gap-2">
              <button
                onClick={() => saveEdit.mutate()}
                disabled={saveEdit.isPending || !editForm.name.trim() || !editForm.description.trim()}
                className="text-[10px] flex items-center gap-1 px-2 py-0.5 bg-accent/30 hover:bg-accent/50 text-accent rounded disabled:opacity-40"
              >
                {saveEdit.isPending ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Check className="w-2.5 h-2.5" />}
                Save
              </button>
              <button
                onClick={() => {
                  setEditForm({
                    name: character.name,
                    description: character.description,
                  });
                  setIsEditing(false);
                }}
                className="text-[10px] text-zinc-500 hover:text-white"
              >
                Cancel
              </button>
              <span className="ml-auto text-[9px] text-zinc-600">
                Existing portrait stays. Re-generate if you want it to match the edited description.
              </span>
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm">{character.name}</span>
              <button
                onClick={async () => {
                  if (await confirm({
                    title: `Reroll "${character.name}"`,
                    message: "Replace the current description with a fresh take from the name + project style + song theme?\nThe portrait stays — you can re-generate it after.",
                    confirmLabel: "Reroll",
                  })) {
                    regenerateDescription.mutate();
                  }
                }}
                disabled={regenerateDescription.isPending}
                className="ml-auto text-[10px] text-fuchsia-300 hover:text-fuchsia-200 flex items-center gap-1 disabled:opacity-40"
                title="Reroll — generate a fresh description from just the name + project style. Existing portrait stays."
              >
                {regenerateDescription.isPending ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Dices className="w-2.5 h-2.5" />}
                {regenerateDescription.isPending ? "Rolling..." : "Reroll"}
              </button>
              <button
                onClick={() => setIsEditing(true)}
                className="text-zinc-600 hover:text-white p-0.5 rounded transition-colors"
                title="Edit name and description"
              >
                <Pencil className="w-3 h-3" />
              </button>
            </div>
            <p className="text-xs text-zinc-400 mt-0.5 line-clamp-2 mb-2">{character.description}</p>
          </>
        )}

        <div className="flex items-center gap-1.5 flex-wrap">
          {/* Per-character model toggle */}
          <div className="flex bg-surface-3 rounded-md p-0.5 border border-white/5">
            {PORTRAIT_MODELS.map((m) => (
              <button
                key={m.key}
                onClick={() => setImageModel(m.key)}
                disabled={isWorking}
                title={`${m.full} (${m.price}/img)`}
                className={`text-[10px] px-2 py-0.5 rounded transition-colors disabled:opacity-40 ${
                  imageModel === m.key
                    ? "bg-accent/30 text-accent font-medium"
                    : "text-zinc-500 hover:text-white"
                }`}
              >
                {m.short}
              </button>
            ))}
          </div>

          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); }}
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={isWorking}
            className="text-[10px] flex items-center gap-1 px-2 py-1 bg-surface-3 hover:bg-surface text-zinc-300 rounded-md transition-colors disabled:opacity-40"
          >
            <Upload className="w-2.5 h-2.5" />
            {hasImage ? "Replace" : "Upload"}
          </button>
          {portraits.length > 1 && (
            <button
              onClick={() => setShowPortraits(!showPortraits)}
              className={`text-[10px] flex items-center gap-1 px-2 py-1 rounded-md transition-colors font-medium ${
                showPortraits
                  ? "bg-accent/40 text-accent border border-accent/60"
                  : "bg-accent/15 text-accent border border-accent/40 hover:bg-accent/25"
              }`}
              title={`${portraits.length} portrait versions saved — click to pick which one is active`}
            >
              <ImageIcon className="w-2.5 h-2.5" />
              Pick portrait ({portraits.length})
            </button>
          )}
          <button
            onClick={() => generate.mutate()}
            disabled={isWorking}
            className="text-[10px] flex items-center gap-1 px-2 py-1 bg-accent/20 hover:bg-accent/30 text-accent border border-accent/30 rounded-md transition-colors disabled:opacity-40"
            title={hasImage
              ? `Add another variant via ${PORTRAIT_MODELS.find(m => m.key === imageModel)?.full} — keeps prior portraits, you pick which is active`
              : `Generate first portrait via ${PORTRAIT_MODELS.find(m => m.key === imageModel)?.full}`}
          >
            <Sparkles className="w-2.5 h-2.5" />
            {hasImage ? "+ Variant" : "Generate"}
          </button>
        </div>

        {/* Progress / status banner */}
        {isGenerating && (
          <div className="mt-2 bg-accent/10 border border-accent/30 rounded-md px-2.5 py-1.5 flex items-center gap-2">
            <Loader2 className="w-3 h-3 animate-spin text-accent shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-[10px] text-accent font-medium">
                Generating portrait{character.portrait_model ? ` with ${
                  PORTRAIT_MODELS.find(m => m.key === character.portrait_model)?.full
                  || character.portrait_model
                }` : "..."}
              </p>
              <div className="h-0.5 bg-surface-3 rounded-full mt-1 overflow-hidden">
                <div className="h-full bg-accent animate-pulse" style={{ width: "60%" }} />
              </div>
            </div>
          </div>
        )}
        {isError && (
          <div className="mt-2 bg-red-900/20 border border-red-800/40 rounded-md px-2.5 py-1.5 text-[10px] text-red-300">
            ❌ Portrait failed: {character.portrait_error || "unknown error"}
          </div>
        )}

        {/* Portrait version history — pick the active one or delete unwanted ones */}
        {showPortraits && portraits.length > 0 && (
          <div className="mt-2 bg-surface-3 rounded-md p-2 border border-white/5">
            <div className="flex items-center justify-between mb-1.5 gap-2">
              <span className="text-[10px] text-zinc-500 uppercase tracking-wide flex-1 truncate">
                {portraits.length} portrait variant{portraits.length === 1 ? "" : "s"} — click image to activate
              </span>
              <button
                onClick={() => setShowPortraits(false)}
                className="shrink-0 inline-flex items-center gap-1 text-[10px] text-zinc-400 hover:text-white px-2 py-0.5 rounded border border-white/10 hover:border-white/30 transition-colors"
                title="Close gallery (Esc)"
              >
                <X className="w-3 h-3" /> Close
              </button>
            </div>
            <p className="text-[10px] text-zinc-500 mb-2 leading-snug">
              Each variant carries its own description. Activating swaps both the
              portrait and the description — so a "blindfolded" variant and a
              "clean" variant can co-exist without dragging the blindfold into
              every scene.
            </p>
            <div className="grid grid-cols-2 gap-2">
              {portraits.map((p) => (
                <div
                  key={p.id}
                  className={`relative rounded-md overflow-hidden border-2 ${
                    p.is_active ? "border-accent" : "border-transparent hover:border-white/20"
                  }`}
                  title={`${p.model_used || "—"} · $${p.cost_usd?.toFixed(3) || "0"} · ${new Date(p.created_at).toLocaleString()}`}
                >
                  <div
                    className="relative aspect-square cursor-pointer"
                    onClick={() => activatePortrait.mutate(p.id)}
                  >
                    <img src={p.url} className="w-full h-full object-cover" alt="" />
                    {p.is_active && (
                      <span className="absolute top-0.5 right-0.5 text-[8px] bg-accent text-white px-1 rounded font-medium">
                        ACTIVE
                      </span>
                    )}
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (await confirm({
                          title: "Delete portrait version",
                          message: "Delete this portrait variant permanently? Its bundled description goes with it.",
                          confirmLabel: "Delete",
                          destructive: true,
                        })) {
                          deletePortrait.mutate(p.id);
                        }
                      }}
                      className="absolute top-0.5 left-0.5 text-zinc-300 hover:text-red-400 bg-black/60 rounded p-0.5"
                      title="Delete this variant (and its description)"
                    >
                      <X className="w-2 h-2" />
                    </button>
                    <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-1 py-0.5">
                      <div className="text-[8px] text-white truncate">{p.model_used || "—"}</div>
                    </div>
                  </div>
                  {/* Bundled description — viewable / editable per variant.
                      Activating the variant also restores this onto the
                      character so scenes pick it up via AI Expand. */}
                  <div className="bg-surface-3 border-t border-white/5 p-1.5">
                    {editingDescId === p.id ? (
                      <div className="space-y-1">
                        <textarea
                          value={draftDesc}
                          onChange={(e) => setDraftDesc(e.target.value)}
                          className="w-full bg-surface-2 border border-white/10 rounded px-1.5 py-1 text-[10px] text-zinc-200 leading-snug resize-y min-h-[64px] focus:outline-none focus:border-accent"
                          placeholder="Description bundled with this portrait variant…"
                        />
                        <div className="flex gap-1 justify-end">
                          <button
                            onClick={() => setEditingDescId(null)}
                            className="text-[10px] text-zinc-500 hover:text-zinc-200 px-1.5 py-0.5 rounded"
                          >Cancel</button>
                          <button
                            onClick={() => {
                              updatePortraitDesc.mutate(
                                { assetId: p.id, description: draftDesc },
                                { onSuccess: () => setEditingDescId(null) },
                              );
                            }}
                            disabled={updatePortraitDesc.isPending}
                            className="text-[10px] text-accent hover:text-accent-hover px-1.5 py-0.5 rounded flex items-center gap-0.5 disabled:opacity-50"
                          >
                            <Check className="w-2.5 h-2.5" /> Save
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start gap-1.5">
                        <div
                          className="text-[10px] text-zinc-400 leading-snug line-clamp-4 flex-1"
                          title={p.description || "No description set on this variant"}
                        >
                          {p.description
                            ? p.description
                            : <span className="italic text-zinc-600">no bundled description</span>}
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setDraftDesc(p.description || "");
                            setEditingDescId(p.id);
                          }}
                          className="text-zinc-500 hover:text-accent shrink-0 mt-0.5"
                          title="Edit this variant's bundled description"
                        >
                          <Pencil className="w-2.5 h-2.5" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <button
        onClick={onDelete}
        className="text-zinc-600 hover:text-error transition-colors p-1"
        title="Delete character"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

const inputCls = "w-full bg-surface-3 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent text-white placeholder:text-zinc-600";
