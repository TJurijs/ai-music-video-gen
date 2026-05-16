"use client";
import { useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Music, Upload, Sparkles, Clock, Activity, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Song, Scene } from "@/lib/types";

interface Props {
  project: Project;
  song?: Song;
  scenes: Scene[];
  onAutoPlan: (songId: number, duration: number, beats: boolean) => void;
  onRefresh: () => void;
}

export default function SongPanel({ project, song, scenes, onAutoPlan, onRefresh }: Props) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<"upload" | "generate">("upload");
  const [genForm, setGenForm] = useState({
    description: "", style_tags: "", lyrics: "",
    instrumental: false, source: "lyria" as "lyria" | "suno",
  });
  const [uploadMeta, setUploadMeta] = useState({ title: "", artist: "" });
  const [showPlan, setShowPlan] = useState(false);
  const [planOpts, setPlanOpts] = useState({ duration: 8, beats: true });

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      api.songs.upload(project.id, uploadMeta.title || file.name.replace(/\.[^.]+$/, ""), uploadMeta.artist, file),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["project", project.id] }); onRefresh(); },
  });

  const generateMutation = useMutation({
    mutationFn: () => api.songs.generate({
      project_id: project.id,
      description: genForm.description,
      style_tags: genForm.style_tags,
      lyrics: genForm.lyrics,
      instrumental: genForm.instrumental,
      source: genForm.source,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["project", project.id] }); onRefresh(); },
  });

  const deleteSongMutation = useMutation({
    mutationFn: api.songs.delete,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["project", project.id] }); onRefresh(); },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadMutation.mutate(file);
  };

  const statusColor = {
    pending: "text-zinc-400",
    analyzing: "text-blue-400",
    ready: "text-green-400",
    error: "text-red-400",
  };

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="p-4 border-b border-white/5">
        <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Song</h2>
      </div>

      {song ? (
        <div className="p-4 space-y-4">
          {/* Song info */}
          <div className="bg-surface-2 rounded-xl p-4 space-y-2">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <p className="font-medium text-sm truncate">{song.title}</p>
                {song.artist && <p className="text-xs text-zinc-500">{song.artist}</p>}
              </div>
              <span className={`text-xs font-medium ${statusColor[song.status as keyof typeof statusColor] ?? "text-zinc-400"}`}>
                {song.status}
              </span>
            </div>

            {song.status === "analyzing" && (
              <div className="flex items-center gap-2 text-xs text-blue-400">
                <RefreshCw className="w-3 h-3 animate-spin" /> Analyzing audio...
              </div>
            )}

            {song.status === "ready" && (
              <div className="grid grid-cols-3 gap-2 pt-1">
                <Stat label="Duration" value={song.duration ? formatDuration(song.duration) : "—"} />
                <Stat label="BPM" value={song.bpm ? Math.round(song.bpm).toString() : "—"} />
                <Stat label="Key" value={song.key ?? "—"} />
              </div>
            )}

            {song.status === "ready" && song.file_path && (
              <audio controls className="w-full mt-2 h-8" style={{ height: 32 }}>
                <source src={`/storage/${song.project_id}/audio/${song.file_path.split(/[/\\]/).pop()}`} />
              </audio>
            )}
          </div>

          {/* Lyrics preview */}
          {song.lyrics && (
            <div className="bg-surface-2 rounded-xl p-3">
              <p className="text-xs font-semibold text-zinc-400 mb-2">Lyrics</p>
              <p className="text-xs text-zinc-400 leading-relaxed line-clamp-6 whitespace-pre-line">
                {song.lyrics}
              </p>
            </div>
          )}

          {/* Auto Plan */}
          {song.status === "ready" && (
            <div className="bg-surface-2 rounded-xl overflow-hidden">
              <button
                className="w-full flex items-center justify-between p-3 text-sm font-medium hover:bg-surface-3 transition-colors"
                onClick={() => setShowPlan(!showPlan)}
              >
                <span className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-accent" /> Auto-Plan Scenes
                </span>
                {showPlan ? <ChevronDown className="w-4 h-4 text-zinc-500" /> : <ChevronRight className="w-4 h-4 text-zinc-500" />}
              </button>
              {showPlan && (
                <div className="p-3 pt-0 space-y-3 border-t border-white/5">
                  <div>
                    <label className="text-xs text-zinc-400 block mb-1">Scene Duration (sec)</label>
                    <input
                      type="number" min={3} max={20} step={1}
                      className="w-full bg-surface-3 border border-white/10 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
                      value={planOpts.duration}
                      onChange={(e) => setPlanOpts({ ...planOpts, duration: Number(e.target.value) })}
                    />
                  </div>
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      className="accent-accent"
                      checked={planOpts.beats}
                      onChange={(e) => setPlanOpts({ ...planOpts, beats: e.target.checked })}
                    />
                    <span className="text-zinc-300">Align to beats</span>
                  </label>
                  {scenes.length > 0 && (
                    <p className="text-xs text-warning">⚠ This will replace {scenes.length} existing scenes</p>
                  )}
                  <button
                    onClick={() => onAutoPlan(song.id, planOpts.duration, planOpts.beats)}
                    className="w-full bg-accent hover:bg-accent-hover text-white text-sm py-2 rounded-lg font-medium transition-colors"
                  >
                    Generate Scene Plan
                  </button>
                </div>
              )}
            </div>
          )}

          <button
            onClick={async () => {
              if (await confirm({ title: "Remove song", message: "Remove this song from the project?", confirmLabel: "Remove", destructive: true })) {
                deleteSongMutation.mutate(song.id);
              }
            }}
            className="w-full text-xs text-zinc-600 hover:text-error transition-colors py-1"
          >
            Remove song
          </button>
        </div>
      ) : (
        <div className="p-4 space-y-4">
          {/* Tab selector */}
          <div className="flex bg-surface-2 rounded-lg p-1">
            <button
              className={`flex-1 text-xs py-1.5 rounded-md transition-colors ${tab === "upload" ? "bg-accent text-white" : "text-zinc-400 hover:text-white"}`}
              onClick={() => setTab("upload")}
            >
              Upload MP3
            </button>
            <button
              className={`flex-1 text-xs py-1.5 rounded-md transition-colors ${tab === "generate" ? "bg-accent text-white" : "text-zinc-400 hover:text-white"}`}
              onClick={() => setTab("generate")}
            >
              Generate
            </button>
          </div>

          {tab === "upload" ? (
            <div className="space-y-3">
              <input
                className="w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                placeholder="Song title"
                value={uploadMeta.title}
                onChange={(e) => setUploadMeta({ ...uploadMeta, title: e.target.value })}
              />
              <input
                className="w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                placeholder="Artist (optional)"
                value={uploadMeta.artist}
                onChange={(e) => setUploadMeta({ ...uploadMeta, artist: e.target.value })}
              />
              <input ref={fileRef} type="file" accept=".mp3,.wav,.ogg,.m4a,.flac" className="hidden" onChange={handleFileChange} />
              <button
                onClick={() => fileRef.current?.click()}
                disabled={uploadMutation.isPending}
                className="w-full flex items-center justify-center gap-2 border border-dashed border-white/20 hover:border-accent/50 rounded-xl py-8 text-sm text-zinc-500 hover:text-white transition-colors disabled:opacity-50"
              >
                <Upload className="w-5 h-5" />
                {uploadMutation.isPending ? "Uploading..." : "Click to upload audio"}
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex bg-surface-2 rounded-lg p-1">
                {(["lyria", "suno"] as const).map((src) => (
                  <button
                    key={src}
                    className={`flex-1 text-xs py-1 rounded transition-colors capitalize ${genForm.source === src ? "bg-surface-3 text-white" : "text-zinc-500"}`}
                    onClick={() => setGenForm({ ...genForm, source: src })}
                  >
                    {src === "lyria" ? "Lyria (OpenRouter)" : "Suno"}
                  </button>
                ))}
              </div>
              <textarea
                className="w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent resize-y"
                rows={3}
                placeholder="Describe the song: mood, genre, vibe, energy..."
                value={genForm.description}
                onChange={(e) => setGenForm({ ...genForm, description: e.target.value })}
              />
              <input
                className="w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                placeholder="Style tags: e.g. electronic, dark pop, 120bpm"
                value={genForm.style_tags}
                onChange={(e) => setGenForm({ ...genForm, style_tags: e.target.value })}
              />
              <textarea
                className="w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent resize-y"
                rows={4}
                placeholder="Lyrics (optional — leave blank for auto-generated)"
                value={genForm.lyrics}
                onChange={(e) => setGenForm({ ...genForm, lyrics: e.target.value })}
              />
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="accent-accent" checked={genForm.instrumental}
                  onChange={(e) => setGenForm({ ...genForm, instrumental: e.target.checked })} />
                <span className="text-zinc-300">Instrumental only</span>
              </label>
              <button
                onClick={() => generateMutation.mutate()}
                disabled={!genForm.description || generateMutation.isPending}
                className="w-full bg-accent hover:bg-accent-hover text-white text-sm py-2 rounded-lg font-medium transition-colors disabled:opacity-50"
              >
                <Sparkles className="w-4 h-4 inline mr-1.5" />
                {generateMutation.isPending ? "Generating..." : "Generate Song"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="text-sm font-semibold">{value}</p>
    </div>
  );
}

function formatDuration(secs: number) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
