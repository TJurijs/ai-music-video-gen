"use client";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Film, Loader2, Sparkles, DollarSign, AlertCircle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { Project, Scene, Song, ProjectCosts } from "@/lib/types";

export default function StepAssembleCell({
  project, scenes, song, costs,
}: {
  project: Project;
  scenes: Scene[];
  song?: Song;
  costs?: ProjectCosts;
}) {
  // Status query — polls every 3s while running, stops when terminal
  const status = useQuery({
    queryKey: ["assembly", project.id],
    queryFn: () => api.generation.assembleStatus(project.id),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "running" ? 3000 : false;
    },
  });

  const assemble = useMutation({
    mutationFn: () => api.generation.assemble(project.id),
    onSuccess: () => status.refetch(),
  });

  const done = scenes.filter((s) => s.status === "done").length;
  const total = scenes.length;
  const totalDuration = scenes.reduce((acc, s) => acc + (s.audio_end - s.audio_start), 0);

  if (total === 0 || done < total) {
    return (
      <div className="pt-4 text-sm text-zinc-500">
        Complete all scene generations to enable assembly. ({done}/{total} ready)
      </div>
    );
  }

  const s = status.data;
  const isRunning = s?.status === "running" || assemble.isPending;
  const isCompleted = s?.status === "completed" && !!s.url;
  const isFailed = s?.status === "failed";

  return (
    <div className="space-y-4 pt-4">
      <div className="bg-surface-2 rounded-xl p-4 grid grid-cols-4 gap-3 text-center">
        <div>
          <div className="text-2xl font-bold text-accent">{total}</div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide mt-0.5">Scenes</div>
        </div>
        <div>
          <div className="text-2xl font-bold">{fmt(totalDuration)}</div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide mt-0.5">Length</div>
        </div>
        <div>
          <div className="text-2xl font-bold">{project.aspect_ratio}</div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide mt-0.5">Aspect</div>
        </div>
        <div>
          <div className="text-2xl font-bold text-green-400">{fmtCost(costs?.total_usd ?? 0)}</div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide mt-0.5">Total Cost</div>
        </div>
      </div>

      {/* Detailed cost breakdown */}
      {costs && costs.total_usd > 0 && (
        <div className="bg-surface-2 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <DollarSign className="w-3.5 h-3.5 text-green-400" />
            <h3 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider">Cost Breakdown</h3>
          </div>
          <div className="space-y-3">
            <CostGroup title="By stage" entries={[
              ["Music generation", costs.by_type.music],
              ["Audio transcription", costs.by_type.transcription],
              ["Scene planning (LLM)", (costs.by_type.llm_plan || 0) + (costs.by_type.llm_expand || 0)],
              ["Reference images", costs.by_type.image],
              ["Video generation", costs.by_type.video],
              ["Lipsync", costs.by_type.lipsync],
            ]} />
            <CostGroup title="By provider" entries={[
              ["OpenRouter", costs.by_provider.openrouter],
              ["fal.ai", costs.by_provider.fal],
              ["Suno", costs.by_provider.suno],
            ]} />
            <div className="pt-2 mt-2 border-t border-white/5 flex justify-between text-sm font-medium">
              <span className="text-zinc-300">Total ({costs.job_count} jobs)</span>
              <span className="text-green-400 font-mono">{fmtCost(costs.total_usd)}</span>
            </div>
          </div>
        </div>
      )}

      {/* Inline player + download (the main result panel) */}
      {isCompleted && s?.url ? (
        <AssembledVideoPanel
          videoUrl={s.url}
          projectName={project.name}
          completedAt={s.completed_at}
          scenes={scenes}
          onReassemble={() => assemble.mutate()}
          reassembling={assemble.isPending}
        />
      ) : isRunning ? (
        <AssemblyRunningPanel startedAt={s?.started_at} />
      ) : (
        <button
          onClick={() => assemble.mutate()}
          disabled={assemble.isPending}
          className="w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-medium py-3 rounded-lg transition-colors"
        >
          <Sparkles className="w-4 h-4" />
          Assemble Final Video
        </button>
      )}

      {isFailed && (
        <div className="bg-red-900/20 border border-red-800/40 rounded-lg p-3 space-y-2">
          <div className="flex items-start gap-2 text-red-300">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <div className="flex-1 text-xs leading-snug">
              <span className="font-medium">Assembly failed: </span>
              {s?.error || "unknown error"}
            </div>
          </div>
          <button
            onClick={() => assemble.mutate()}
            disabled={assemble.isPending}
            className="text-xs px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 text-red-200 border border-red-500/40 rounded flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw className="w-3 h-3" /> Retry
          </button>
        </div>
      )}

      {assemble.isError && !isFailed && (
        <div className="bg-red-900/20 border border-red-800/40 rounded-lg p-3 text-sm text-red-300">
          {(assemble.error as Error).message}
        </div>
      )}
    </div>
  );
}

function AssemblyRunningPanel({ startedAt }: { startedAt?: string | null }) {
  const elapsed = startedAt ? Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)) : 0;
  return (
    <div className="bg-accent/10 border border-accent/30 rounded-xl p-4 space-y-2">
      <div className="flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin text-accent" />
        <span className="text-sm font-medium text-accent">Assembling final video…</span>
        {elapsed > 0 && <span className="text-[10px] font-mono text-zinc-500">{elapsed}s</span>}
      </div>
      <p className="text-[11px] text-zinc-400 leading-relaxed">
        ffmpeg is concatenating all scene clips and muxing the song audio. Typically takes 30–90s for a 3-minute song. This panel will switch to a video player when it's done.
      </p>
      <div className="h-1 bg-surface-3 rounded-full overflow-hidden">
        <div className="h-full bg-accent animate-pulse" style={{ width: "70%" }} />
      </div>
    </div>
  );
}

function AssembledVideoPanel({
  videoUrl, projectName, completedAt, scenes, onReassemble, reassembling,
}: {
  videoUrl: string;
  projectName: string;
  completedAt?: string | null;
  scenes: Scene[];
  onReassemble: () => void;
  reassembling: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  // Track playback time so the scene strip can show progress + highlight the
  // current scene. Re-runs on `completedAt` change because the <video> element
  // is keyed off cacheBuster (which derives from completedAt) and remounts on
  // re-assembly — without this dep, listeners would stay attached to the
  // destroyed element and currentTime would never update.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => setCurrentTime(v.currentTime);
    const onSeek = () => setCurrentTime(v.currentTime);
    const onMeta = () => setDuration(v.duration || 0);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onSeek);
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("durationchange", onMeta);
    // Capture initial values in case metadata already loaded before this
    // effect attached (race between img/video preload and React commit).
    setCurrentTime(v.currentTime);
    setDuration(v.duration || 0);
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("seeked", onSeek);
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("durationchange", onMeta);
    };
  }, [videoUrl, completedAt]);

  // Remember whether the video was playing when scrub started, so we can
  // resume on release. Pausing during scrub is the standard pattern: it
  // avoids the browser rejecting rapid seeks while the playback engine is
  // still trying to render audio from the previous position.
  const wasPlayingBeforeScrubRef = useRef(false);

  const seekTo = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    const clamped = Math.max(0, Math.min(t, (v.duration || 0) - 0.05));
    v.currentTime = clamped;
    // Update state immediately so the playhead reflects the drag position
    // without waiting for the video's seeked/timeupdate events — those can
    // be delayed or even skipped when seeks are issued in rapid succession.
    setCurrentTime(clamped);
  };

  const onScrubStart = () => {
    const v = videoRef.current;
    if (!v) return;
    wasPlayingBeforeScrubRef.current = !v.paused;
    v.pause();
  };

  const onScrubEnd = () => {
    const v = videoRef.current;
    if (!v) return;
    if (wasPlayingBeforeScrubRef.current) {
      v.play().catch(() => {});
    }
  };

  // Browser-native "Save as…" dialog: fetch the video as a blob, then
  // trigger a download with the project name. This avoids navigating away
  // (which `<a href download>` does on some Windows + browser combos).
  const handleDownload = async () => {
    try {
      const res = await fetch(videoUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `${projectName.replace(/[^a-zA-Z0-9 \-_]/g, "_")}.mp4`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (e) {
      alert(`Download failed: ${(e as Error).message}`);
    }
  };

  // Cache-bust the video URL on each completion so the player loads the
  // newly-assembled file rather than a previously-cached version.
  const cacheBuster = completedAt ? `?t=${encodeURIComponent(completedAt)}` : "";

  return (
    <div className="bg-surface-2 border border-white/10 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
        <div className="flex items-center gap-2">
          <Film className="w-3.5 h-3.5 text-green-400" />
          <span className="text-xs font-medium text-green-300">Final video assembled</span>
          {completedAt && (
            <span className="text-[10px] text-zinc-500 font-mono">
              {new Date(completedAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleDownload}
            className="text-xs px-2 py-1 bg-green-500/15 hover:bg-green-500/30 text-green-300 border border-green-500/30 rounded flex items-center gap-1"
            title="Save the final .mp4 to disk (opens your browser's Save As dialog)"
          >
            <Download className="w-3 h-3" /> Download
          </button>
          <button
            onClick={onReassemble}
            disabled={reassembling}
            className="text-xs px-2 py-1 bg-surface-3 hover:bg-surface text-zinc-400 hover:text-white border border-white/10 rounded flex items-center gap-1 disabled:opacity-50"
            title="Re-assemble using current scene videos + song"
          >
            {reassembling ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            Re-assemble
          </button>
        </div>
      </div>
      <video
        ref={videoRef}
        key={videoUrl + cacheBuster}
        src={videoUrl + cacheBuster}
        controls
        className="w-full max-h-[70vh] bg-black"
      />
      <SceneStrip
        scenes={scenes}
        currentTime={currentTime}
        duration={duration}
        onSeek={seekTo}
        onScrubStart={onScrubStart}
        onScrubEnd={onScrubEnd}
      />
    </div>
  );
}

function SceneStrip({ scenes, currentTime, duration, onSeek, onScrubStart, onScrubEnd }: {
  scenes: Scene[];
  currentTime: number;
  duration: number;
  onSeek: (time: number) => void;
  onScrubStart?: () => void;
  onScrubEnd?: () => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);
  const [pressed, setPressed] = useState(false);
  const [hoverX, setHoverX] = useState<number | null>(null);

  // Sum of planned audio durations (what scenes were authored against).
  // Assembly is a straight concat — no per-clip trimming — so video duration
  // ≈ sum of clip durations and the scale factor (was duration/audioTotal)
  // collapses to ~1. We still use the loaded video.duration when available
  // because individual clips can be ±a few hundred ms off from their planned
  // audio_end - audio_start, and the player gives us the real total.
  const audioTotal = scenes.reduce((acc, s) => acc + (s.audio_end - s.audio_start), 0);
  const playbackTotal = duration > 0 ? duration : audioTotal;
  const scale = audioTotal > 0 ? playbackTotal / audioTotal : 1;
  const baseAudioStart = scenes[0]?.audio_start ?? 0;

  const playheadPct = playbackTotal > 0
    ? Math.min(100, Math.max(0, (currentTime / playbackTotal) * 100))
    : 0;

  // Convert a viewport-X coord into a video timestamp.
  const timeAtClientX = (clientX: number): number | null => {
    const rect = stripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return null;
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return pct * playbackTotal;
  };

  // Pointer Events with setPointerCapture — the element receives ALL pointer
  // events for the rest of the gesture even when the cursor leaves the
  // strip, so we don't need global window listeners.
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // older browsers / edge cases — fall back to plain drag
    }
    setPressed(true);
    onScrubStart?.();
    const t = timeAtClientX(e.clientX);
    if (t !== null) onSeek(t);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = stripRef.current?.getBoundingClientRect();
    if (rect) setHoverX(e.clientX - rect.left);
    // e.buttons is a live bitmask of mouse buttons — 1 = primary down.
    // This is more reliable than tracking state because it reflects the
    // hardware state, not React's possibly-stale closure.
    if ((e.buttons & 1) === 1) {
      const t = timeAtClientX(e.clientX);
      if (t !== null) onSeek(t);
    }
  };

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {}
    setPressed(false);
    onScrubEnd?.();
  };

  const onPointerLeave = () => {
    if (!pressed) setHoverX(null);
  };

  // Mouse-wheel scrub: spin the wheel to nudge time forward/backward. Useful
  // for fine positioning when reviewing a specific moment.
  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    // Many users have horizontal trackpads (deltaX) and many have vertical
    // mice (deltaY) — accept whichever is dominant.
    const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (delta === 0) return;
    e.preventDefault();
    // Sensitivity: 1 wheel "tick" (~100 deltaY units on most mice) ≈ 1 second
    onSeek(Math.max(0, Math.min(playbackTotal, currentTime + delta * 0.01)));
  };

  if (!scenes.length || audioTotal <= 0) return null;

  // Hover decoration (time + scene name at hover position).
  const stripWidth = stripRef.current?.getBoundingClientRect().width ?? 0;
  const hoverPct = hoverX !== null && stripWidth > 0
    ? Math.max(0, Math.min(1, hoverX / stripWidth))
    : null;
  const hoverTime = hoverPct !== null ? hoverPct * playbackTotal : null;
  const hoverScene = hoverTime !== null
    ? scenes.find((s) => {
        const start = (s.audio_start - baseAudioStart) * scale;
        const end = start + (s.audio_end - s.audio_start) * scale;
        return hoverTime >= start && hoverTime < end;
      })
    : null;

  return (
    <div className="bg-surface-2 border-t border-white/5 px-3 py-2">
      <div className="flex items-center justify-between text-[10px] text-zinc-500 mb-1.5">
        <span className="uppercase tracking-wider">Scenes • click, drag, or scroll-wheel to scrub</span>
        <span className="font-mono">
          {fmt(currentTime)} / {fmt(playbackTotal)}
        </span>
      </div>
      <div className="relative">
        <div
          ref={stripRef}
          className={`relative h-7 rounded overflow-hidden border border-white/10 bg-black/40 select-none touch-none ${
            pressed ? "cursor-grabbing" : "cursor-pointer"
          }`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
          onWheel={onWheel}
        >
          {/* Scene blocks — visual / orientation only; the whole strip is
              the scrubber, so blocks don't intercept clicks. */}
          <div className="absolute inset-0 flex pointer-events-none">
            {scenes.map((scene, i) => {
              const sceneAudioDur = scene.audio_end - scene.audio_start;
              const widthPct = (sceneAudioDur / audioTotal) * 100;
              const sceneVideoStart = (scene.audio_start - baseAudioStart) * scale;
              const sceneVideoEnd = sceneVideoStart + sceneAudioDur * scale;
              const isActive = currentTime >= sceneVideoStart && currentTime < sceneVideoEnd;
              return (
                <div
                  key={scene.id}
                  className={`relative flex-shrink-0 text-[10px] font-mono flex items-center justify-center ${
                    isActive
                      ? "bg-accent/30 ring-1 ring-inset ring-accent text-white font-semibold z-[2]"
                      : "bg-surface-3 text-zinc-300"
                  } ${i > 0 ? "border-l border-black/40" : ""}`}
                  style={{ width: `${widthPct}%` }}
                >
                  <span className="relative z-10">
                    {widthPct > 3.5 ? i + 1 : ""}
                  </span>
                </div>
              );
            })}
          </div>
          {/* Played-region tint. During drag we kill the CSS transition so
              the fill snaps to the mouse instead of easing. */}
          <div
            className={`absolute top-0 bottom-0 left-0 bg-accent/25 pointer-events-none ${
              pressed ? "" : "transition-[width] duration-100 ease-linear"
            }`}
            style={{ width: `${playheadPct}%` }}
          />
          {/* Faint hover indicator — separate from the playhead, only when
              not dragging (playhead and hover are at the same X during drag). */}
          {hoverX !== null && !pressed && (
            <div
              className="absolute top-0 bottom-0 w-px bg-white/40 pointer-events-none"
              style={{ left: `${hoverX}px` }}
            />
          )}
          {/* Playhead — snaps instantly during drag, eases during playback. */}
          <div
            className={`absolute top-0 bottom-0 w-0.5 bg-white pointer-events-none ${
              pressed ? "" : "transition-[left] duration-100 ease-linear"
            }`}
            style={{
              left: `${playheadPct}%`,
              boxShadow: "0 0 6px rgba(255,255,255,0.85)",
            }}
          />
        </div>
        {/* Hover tooltip — time + scene at the hover position, floats just
            above the strip. */}
        {hoverX !== null && hoverTime !== null && (
          <div
            className="absolute bottom-full mb-1.5 bg-black/85 border border-white/10 text-[10px] px-2 py-1 rounded whitespace-nowrap pointer-events-none shadow-xl z-10"
            style={{
              left: `${hoverX}px`,
              transform: "translateX(-50%)",
            }}
          >
            <span className="font-mono text-white">{fmt(hoverTime)}</span>
            {hoverScene && (
              <span className="text-zinc-400 ml-2">scene {hoverScene.order + 1}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function fmt(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function fmtCost(usd: number): string {
  if (!usd) return "$0";
  if (usd < 0.01) return `<$0.01`;
  if (usd < 1) return `$${usd.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}`;
  return `$${usd.toFixed(2)}`;
}

function CostGroup({ title, entries }: {
  title: string;
  entries: Array<[string, number | undefined]>;
}) {
  const filtered = entries.filter(([, v]) => v && v > 0) as Array<[string, number]>;
  if (!filtered.length) return null;
  return (
    <div>
      <p className="text-[10px] text-zinc-500 uppercase tracking-wide mb-1.5">{title}</p>
      <div className="space-y-1">
        {filtered.map(([label, value]) => (
          <div key={label} className="flex justify-between text-xs">
            <span className="text-zinc-400">{label}</span>
            <span className="font-mono text-zinc-300">{fmtCost(value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
