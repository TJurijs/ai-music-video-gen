"use client";
import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import type { Scene, Song, TranscriptionWord, Section } from "@/lib/types";

interface Props {
  song?: Song;
  scenes: Scene[];
  currentTime: number;
  selectedSceneId: number | null;
  onSelectScene: (id: number) => void;
  onTimeChange: (t: number) => void;
}

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-zinc-700 border-zinc-600",
  generating_image: "bg-blue-900 border-blue-700",
  generating_video: "bg-purple-900 border-purple-700",
  lipsync: "bg-indigo-900 border-indigo-700",
  done: "bg-green-900 border-green-700",
  error: "bg-red-900/60 border-red-700",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  generating_image: "Image...",
  generating_video: "Video...",
  lipsync: "Lipsync...",
  done: "✓",
  error: "Error",
};

export default function SceneTimeline({
  song, scenes, currentTime, selectedSceneId, onSelectScene, onTimeChange,
}: Props) {
  const duration = song?.duration ?? 0;
  const words: TranscriptionWord[] = song?.transcription_json ? JSON.parse(song.transcription_json) : [];
  const beats: number[] = song?.beats_json ? JSON.parse(song.beats_json) : [];
  const sections: Section[] = song?.sections_json ? JSON.parse(song.sections_json) : [];

  const timelineRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);

  const timeToPercent = (t: number) => duration ? (t / duration) * 100 : 0;

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!timelineRef.current || !duration) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = (x / rect.width) * duration;
    onTimeChange(Math.max(0, Math.min(t, duration)));
  };

  const sortedScenes = [...scenes].sort((a, b) => a.order - b.order);

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5 bg-surface-1 shrink-0">
        <span className="text-xs text-zinc-400 font-medium">
          {scenes.length} scenes
          {song?.status === "ready" && ` · ${formatTime(duration)}`}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500">Zoom</span>
          <input
            type="range" min={1} max={5} step={0.5}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            className="w-20 accent-accent"
          />
        </div>
      </div>

      {/* Scrollable timeline area */}
      <div className="flex-1 overflow-x-auto overflow-y-hidden relative">
        <div style={{ width: `${100 * zoom}%`, minWidth: "100%", height: "100%", position: "relative" }}>

          {/* Section labels */}
          {duration > 0 && sections.length > 0 && (
            <div className="absolute top-0 left-0 right-0 h-5 flex" style={{ zIndex: 1 }}>
              {sections.map((sec, i) => (
                <div
                  key={i}
                  className="absolute flex items-center px-1"
                  style={{
                    left: `${timeToPercent(sec.start)}%`,
                    width: `${timeToPercent(sec.end - sec.start)}%`,
                  }}
                >
                  <span className="text-[10px] text-zinc-600 truncate capitalize">{sec.label}</span>
                </div>
              ))}
            </div>
          )}

          {/* Beat markers */}
          {duration > 0 && beats.length > 0 && (
            <div className="absolute top-5 left-0 right-0 h-3" style={{ zIndex: 1 }}>
              {beats.map((beat, i) => (
                <div
                  key={i}
                  className="absolute top-0 bottom-0 w-px bg-white/5"
                  style={{ left: `${timeToPercent(beat)}%` }}
                />
              ))}
            </div>
          )}

          {/* Lyrics ticker */}
          {duration > 0 && words.length > 0 && (
            <div className="absolute left-0 right-0 h-5 overflow-hidden" style={{ top: 32, zIndex: 2 }}>
              {words.map((w, i) => (
                <span
                  key={i}
                  className="absolute text-[9px] text-zinc-500 whitespace-nowrap"
                  style={{ left: `${timeToPercent(w.start)}%` }}
                >
                  {w.word}
                </span>
              ))}
            </div>
          )}

          {/* Clickable timeline bar */}
          <div
            ref={timelineRef}
            className="absolute left-0 right-0 cursor-pointer"
            style={{ top: 60, height: 8 }}
            onClick={handleTimelineClick}
          >
            <div className="h-full bg-surface-3 rounded-full relative">
              {/* Playhead */}
              {duration > 0 && (
                <div
                  className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-accent rounded-full -translate-x-1/2 shadow-lg z-10"
                  style={{ left: `${timeToPercent(currentTime)}%` }}
                />
              )}
            </div>
          </div>

          {/* Scene blocks */}
          <div className="absolute left-0 right-0" style={{ top: 76, bottom: 0 }}>
            {duration === 0 ? (
              <div className="flex items-center justify-center h-full text-xs text-zinc-600">
                {song ? "Analyzing song..." : "Add a song to see the timeline"}
              </div>
            ) : sortedScenes.length === 0 ? (
              <div className="flex items-center justify-center h-full text-xs text-zinc-600">
                Use Auto-Plan or add scenes manually
              </div>
            ) : (
              sortedScenes.map((scene) => (
                <SceneBlock
                  key={scene.id}
                  scene={scene}
                  duration={duration}
                  timeToPercent={timeToPercent}
                  selected={scene.id === selectedSceneId}
                  onClick={() => onSelectScene(scene.id)}
                />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function SceneBlock({
  scene, duration, timeToPercent, selected, onClick,
}: {
  scene: Scene;
  duration: number;
  timeToPercent: (t: number) => number;
  selected: boolean;
  onClick: () => void;
}) {
  const left = timeToPercent(scene.audio_start);
  const width = timeToPercent(scene.audio_end - scene.audio_start);
  const colorClass = STATUS_COLORS[scene.status] ?? STATUS_COLORS.pending;

  return (
    <div
      onClick={onClick}
      className={`absolute top-2 bottom-2 rounded-lg border cursor-pointer transition-all select-none ${colorClass} ${selected ? "ring-2 ring-accent ring-offset-1 ring-offset-surface" : "hover:brightness-125"}`}
      style={{ left: `${left}%`, width: `${Math.max(width, 0.5)}%` }}
      title={scene.description ?? `Scene ${scene.order}`}
    >
      <div className="px-1.5 py-1 h-full flex flex-col justify-between overflow-hidden">
        <div className="flex items-center justify-between gap-1">
          <span className="text-[10px] font-semibold text-white/70">#{scene.order}</span>
          <span className="text-[9px] text-white/50">{STATUS_LABEL[scene.status]}</span>
        </div>
        {scene.description && (
          <p className="text-[9px] text-white/60 truncate leading-tight">{scene.description}</p>
        )}
        <span className="text-[9px] text-white/40">{formatTime(scene.audio_end - scene.audio_start)}</span>
      </div>
    </div>
  );
}

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}
