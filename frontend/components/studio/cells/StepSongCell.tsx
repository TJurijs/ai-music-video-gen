"use client";
import { useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, Sparkles, Loader2, Music, Trash2, Activity, BookOpen } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project, Song, TranscriptionWord, ThemeAnalysis } from "@/lib/types";
import ModelTag from "../ModelTag";

export default function StepSongCell({ project, song }: { project: Project; song?: Song }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<"upload" | "generate">("upload");
  const [meta, setMeta] = useState({ title: "", artist: "" });
  const [genForm, setGenForm] = useState({
    description: "", style_tags: "", lyrics: "",
    instrumental: false, source: "suno" as "lyria" | "suno",
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["project", project.id] });

  const upload = useMutation({
    mutationFn: (file: File) => api.songs.upload(project.id, meta.title || file.name.replace(/\.[^.]+$/, ""), meta.artist, file),
    onSuccess: refresh,
  });

  const generate = useMutation({
    mutationFn: () => api.songs.generate({ project_id: project.id, ...genForm }),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: api.songs.delete,
    onSuccess: refresh,
  });

  if (!song) {
    return (
      <div className="space-y-4 pt-4">
        <div className="flex bg-surface-3 rounded-lg p-1 max-w-xs">
          {(["upload", "generate"] as const).map((t) => (
            <button
              key={t}
              className={`flex-1 text-xs py-1.5 rounded-md transition-colors capitalize ${tab === t ? "bg-accent text-white" : "text-zinc-400 hover:text-white"}`}
              onClick={() => setTab(t)}
            >{t}</button>
          ))}
        </div>

        {tab === "upload" ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <input
                placeholder="Song title (optional)"
                value={meta.title}
                onChange={(e) => setMeta({ ...meta, title: e.target.value })}
                className={inputCls}
              />
              <input
                placeholder="Artist (optional)"
                value={meta.artist}
                onChange={(e) => setMeta({ ...meta, artist: e.target.value })}
                className={inputCls}
              />
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".mp3,.wav,.ogg,.m4a,.flac"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); }}
            />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={upload.isPending}
              className="w-full flex items-center justify-center gap-2 border-2 border-dashed border-white/10 hover:border-accent/50 rounded-xl py-12 text-sm text-zinc-500 hover:text-white transition-colors disabled:opacity-50"
            >
              {upload.isPending ? (
                <><Loader2 className="w-5 h-5 animate-spin" /> Uploading...</>
              ) : (
                <><Upload className="w-5 h-5" /> Click to upload audio file</>
              )}
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-[10px] text-zinc-500">
              Powered by Suno V5.5 via <span className="text-accent">sunoapi.org</span> · ~$0.12/song · 2 tracks per generation
            </p>
            <textarea
              rows={2}
              placeholder="Describe the song: mood, genre, vibe, energy..."
              value={genForm.description}
              onChange={(e) => setGenForm({ ...genForm, description: e.target.value })}
              className={inputCls + " resize-none"}
            />
            <input
              placeholder="Style tags: e.g. electronic, dark pop, 120bpm"
              value={genForm.style_tags}
              onChange={(e) => setGenForm({ ...genForm, style_tags: e.target.value })}
              className={inputCls}
            />
            <textarea
              rows={4}
              placeholder="Lyrics (optional — leave blank for AI to write them)"
              value={genForm.lyrics}
              onChange={(e) => setGenForm({ ...genForm, lyrics: e.target.value })}
              className={inputCls + " resize-none"}
            />
            <label className="flex items-center gap-2 text-sm cursor-pointer text-zinc-300">
              <input type="checkbox" className="accent-accent"
                checked={genForm.instrumental}
                onChange={(e) => setGenForm({ ...genForm, instrumental: e.target.checked })} />
              Instrumental only
            </label>
            <button
              onClick={() => generate.mutate()}
              disabled={!genForm.description || generate.isPending}
              className={primaryBtn}
            >
              {generate.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {generate.isPending ? "Generating song..." : "Generate Song"}
            </button>
          </div>
        )}
      </div>
    );
  }

  // Song exists — show analysis
  const isAnalyzing = song.status === "analyzing" || song.status === "pending";
  const isError = song.status === "error";
  const isReady = song.status === "ready";

  const words: TranscriptionWord[] = song.transcription_json ? JSON.parse(song.transcription_json) : [];
  const beats: number[] = song.beats_json ? JSON.parse(song.beats_json) : [];
  const audioFilename = song.file_path?.split(/[/\\]/).pop();

  return (
    <div className="space-y-4 pt-4">
      {isAnalyzing && (
        <div className="flex items-center gap-3 bg-blue-900/20 border border-blue-800/40 rounded-lg p-4">
          <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
          <div>
            <p className="text-sm text-blue-300 font-medium">Analyzing audio</p>
            <p className="text-xs text-blue-300/60">Detecting beats, sections, transcribing lyrics...</p>
          </div>
        </div>
      )}

      {isError && (
        <div className="bg-red-900/20 border border-red-800/40 rounded-lg p-4 text-sm text-red-300">
          Audio analysis failed. Check your OpenRouter key and try uploading again.
        </div>
      )}

      {isReady && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Duration" value={fmt(song.duration ?? 0)} icon={<Music className="w-3 h-3" />} />
            <Stat label="Tempo" value={`${Math.round(song.bpm ?? 0)} BPM`} icon={<Activity className="w-3 h-3" />} />
            <Stat label="Key" value={song.key ?? "—"} />
          </div>

          {/* Model attribution */}
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-zinc-500">
            {song.source === "suno" && <ModelTag label="Music" model="Suno V5.5 (sunoapi.org)" />}
            {song.source === "upload" && <ModelTag label="Source" model="Uploaded MP3" />}
            <ModelTag label="Beats / key" model="librosa (local)" hint="Beat detection, BPM, key, sectioning run locally — no API cost" />
            <ModelTag
              label="Lyrics"
              model={words.length > 0 ? "fal-ai/whisper (word timestamps)" : "Gemini 2.5 Flash (no timestamps)"}
              hint={words.length > 0
                ? "Word-level timestamps via fal-ai/whisper — drives scene-to-lyric alignment"
                : "Lyrics text only via OpenRouter chat completions — no per-word timing"}
            />
            {song.theme_analysis && <ModelTag label="Theme" model="Claude Sonnet 4.5" hint="Reads lyrics → theme, narrative, mood, visual world" />}
          </div>

          {audioFilename && (
            <audio
              controls
              className="w-full h-9 rounded-lg [&::-webkit-media-controls-panel]:bg-surface-2"
              src={`http://localhost:8010/storage/${song.project_id}/audio/${audioFilename}`}
            />
          )}

          {/* Theme analysis (when present) */}
          {song.theme_analysis && (() => {
            let theme: ThemeAnalysis = {};
            try { theme = JSON.parse(song.theme_analysis); } catch {}
            return (
              <div className="bg-accent/5 border border-accent/20 rounded-lg p-3 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-accent">
                  <BookOpen className="w-3 h-3" />
                  What this song is about
                </div>
                {theme.theme && (
                  <p className="text-xs"><span className="text-zinc-500">Theme:</span> <span className="text-zinc-200">{theme.theme}</span></p>
                )}
                {theme.narrative && (
                  <p className="text-xs"><span className="text-zinc-500">Story:</span> <span className="text-zinc-200">{theme.narrative}</span></p>
                )}
                {theme.mood && (
                  <p className="text-xs"><span className="text-zinc-500">Mood:</span> <span className="text-zinc-200">{theme.mood}</span></p>
                )}
                {theme.visual_world && (
                  <p className="text-xs"><span className="text-zinc-500">World:</span> <span className="text-zinc-200">{theme.visual_world}</span></p>
                )}
                {theme.characters_in_lyrics && theme.characters_in_lyrics.length > 0 && (
                  <p className="text-xs">
                    <span className="text-zinc-500">Characters in lyrics:</span>{" "}
                    <span className="text-zinc-200">{theme.characters_in_lyrics.join(", ")}</span>
                  </p>
                )}
                {theme.suggested_visual_style && (
                  <p className="text-[10px] italic text-zinc-500 pt-1 border-t border-white/5">
                    Suggested style → {theme.suggested_visual_style}
                  </p>
                )}
              </div>
            );
          })()}

          {song.lyrics && (
            <details className="bg-surface-2 rounded-lg overflow-hidden group">
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-zinc-400 hover:text-white flex items-center gap-2">
                <span>Transcription · {song.lyrics.length} chars</span>
                {words.length > 0 && (
                  <span className="text-[10px] text-accent font-mono">
                    {words.length} words timestamped ({words[0]?.start?.toFixed(1)}s–{words[words.length - 1]?.end?.toFixed(1)}s)
                  </span>
                )}
                {words.length === 0 && (
                  <span className="text-[10px] text-amber-400">no timestamps (re-upload to get word-level timing via fal-ai/whisper)</span>
                )}
              </summary>
              <div className="p-3 max-h-72 overflow-y-auto space-y-3">
                {words.length > 0 ? (
                  <>
                    <p className="text-[10px] text-zinc-500">
                      Click any word to seek the audio above. Timestamps drive scene-to-lyric alignment in planning.
                    </p>
                    <div className="flex flex-wrap gap-x-1 gap-y-1.5 leading-relaxed">
                      {words.map((w, i) => (
                        <button
                          key={i}
                          onClick={() => {
                            const audio = document.querySelector("audio") as HTMLAudioElement | null;
                            if (audio) {
                              audio.currentTime = w.start;
                              audio.play().catch(() => {});
                            }
                          }}
                          className="text-xs text-zinc-300 hover:text-accent hover:bg-accent/10 px-1 rounded transition-colors group/word"
                          title={`${w.start.toFixed(2)}s – ${w.end.toFixed(2)}s`}
                        >
                          {w.word}
                          <span className="text-[8px] text-zinc-600 ml-0.5 align-top group-hover/word:text-accent/70">
                            {w.start.toFixed(1)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-line">
                    {song.lyrics}
                  </p>
                )}
              </div>
            </details>
          )}

          <button
            onClick={async () => {
              if (await confirm({ title: "Remove song", message: "Remove this song from the project?", confirmLabel: "Remove", destructive: true })) {
                remove.mutate(song.id);
              }
            }}
            className="text-xs text-zinc-600 hover:text-error transition-colors flex items-center gap-1"
          >
            <Trash2 className="w-3 h-3" /> Remove song
          </button>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <div className="bg-surface-2 rounded-lg p-3">
      <div className="text-[10px] text-zinc-500 uppercase tracking-wider flex items-center gap-1">{icon}{label}</div>
      <div className="text-sm font-semibold mt-0.5">{value}</div>
    </div>
  );
}

function fmt(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

const inputCls = "w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent text-white placeholder:text-zinc-600";
const primaryBtn = "w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-2.5 rounded-lg transition-colors";
