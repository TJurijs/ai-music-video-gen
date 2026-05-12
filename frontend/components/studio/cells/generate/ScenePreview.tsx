"use client";
import { useState, useEffect, useRef } from "react";
import type { Scene, Song } from "@/lib/types";
import { fmt } from "./shared";

export default function ScenePreview({ scene, song, onClose }: { scene: Scene; song?: Song; onClose: () => void }) {
  const [songOn, setSongOn] = useState(!!song?.file_url);
  const audioRef = useRef<HTMLAudioElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const songUrl = song?.file_url;

  // Sync the song audio segment (audio_start–audio_end) to the video playback.
  // The video has no native audio (or just sfx); the song audio overlays it
  // so the user can hear how the clip lands against their music.
  useEffect(() => {
    const v = videoRef.current;
    const a = audioRef.current;
    if (!v) return;
    const seekToStart = () => {
      if (a && songOn && songUrl) {
        a.currentTime = scene.audio_start;
        a.play().catch(() => {});
      }
    };
    const stopAtEnd = () => {
      if (a && a.currentTime >= scene.audio_end) {
        a.currentTime = scene.audio_start;
      }
    };
    v.play().catch(() => {});
    seekToStart();
    v.addEventListener("play", seekToStart);
    a?.addEventListener("timeupdate", stopAtEnd);
    return () => {
      v.removeEventListener("play", seekToStart);
      a?.removeEventListener("timeupdate", stopAtEnd);
      a?.pause();
    };
  }, [songOn, songUrl, scene.audio_start, scene.audio_end]);

  return (
    <div
      className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-6"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-surface-2 border border-white/10 rounded-xl overflow-hidden max-w-4xl w-full" onMouseDown={(e) => e.stopPropagation()}>
        <div className="p-3 border-b border-white/10 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium">Scene #{scene.order}</span>
            <span className="text-[10px] font-mono text-zinc-500">
              {fmt(scene.audio_start)}–{fmt(scene.audio_end)} · {scene.resolution}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {songUrl && (
              <button
                onClick={() => setSongOn((v) => !v)}
                className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
                  songOn
                    ? "bg-accent/30 border-accent/50 text-accent"
                    : "bg-surface-3 border-white/10 text-zinc-400"
                }`}
                title="Toggle song audio overlay"
              >
                {songOn ? "🔊 Song: ON" : "🔇 Song: OFF"}
              </button>
            )}
            <button onClick={onClose} className="text-zinc-400 hover:text-white px-2">×</button>
          </div>
        </div>
        <video
          ref={videoRef}
          src={scene.video_url}
          className="w-full max-h-[70vh] bg-black"
          controls
          autoPlay
          loop
          muted={!scene.generate_audio}
        />
        {/* Hidden audio element overlays the song segment when toggled on */}
        {songUrl && songOn && <audio ref={audioRef} src={songUrl} preload="auto" />}
        <p className="text-[10px] text-zinc-500 px-3 py-2 border-t border-white/5">
          Video has {scene.generate_audio ? "model-generated audio (sfx)" : "no embedded audio"}.
          {scene.lipsync_enabled && " Lipsync to song was applied."}
          {" Toggle song to A/B with your music."}
        </p>
      </div>
    </div>
  );
}
