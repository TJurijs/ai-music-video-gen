"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Film, ChevronLeft, Zap, Download, Plus, Settings,
} from "lucide-react";
import type { Project, Song, Scene, GenerationJob } from "@/lib/types";
import SongPanel from "./SongPanel";
import SceneTimeline from "./SceneTimeline";
import SceneEditor from "./SceneEditor";
import GenerationQueue from "./GenerationQueue";

interface Props {
  project: Project;
  song?: Song;
  scenes: Scene[];
  jobs: GenerationJob[];
  onUpdateProject: (data: Partial<Project>) => void;
  onUpdateScene: (id: number, data: Partial<Scene>) => void;
  onDeleteScene: (id: number) => void;
  onGenerateScene: (id: number) => void;
  onGenerateAll: () => void;
  onAssemble: () => void;
  onAutoPlan: (songId: number, duration: number, beats: boolean) => void;
  onRefresh: () => void;
}

export default function StudioLayout({
  project, song, scenes, jobs,
  onUpdateProject, onUpdateScene, onDeleteScene,
  onGenerateScene, onGenerateAll, onAssemble, onAutoPlan, onRefresh,
}: Props) {
  const router = useRouter();
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [showSettings, setShowSettings] = useState(false);

  const selectedScene = scenes.find((s) => s.id === selectedSceneId) ?? null;
  const activeJobs = jobs.filter((j) => j.status === "running" || j.status === "pending").length;
  const doneCount = scenes.filter((s) => s.status === "done").length;

  return (
    <div className="h-screen bg-surface text-white flex flex-col overflow-hidden">
      {/* Top Bar */}
      <header className="h-14 border-b border-white/5 flex items-center px-4 gap-3 shrink-0 bg-surface-1">
        <button onClick={() => router.push("/projects")} className="text-zinc-500 hover:text-white transition-colors">
          <ChevronLeft className="w-5 h-5" />
        </button>
        <Film className="w-4 h-4 text-accent" />
        <span className="font-semibold text-sm flex-1 truncate">{project.name}</span>
        {project.style && <span className="text-xs text-zinc-500 hidden md:block">{project.style}</span>}
        <div className="flex items-center gap-2 ml-auto">
          {scenes.length > 0 && (
            <button
              onClick={onGenerateAll}
              className="flex items-center gap-1.5 bg-accent/20 hover:bg-accent/30 text-accent border border-accent/30 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
            >
              <Zap className="w-3.5 h-3.5" /> Generate All
            </button>
          )}
          {doneCount > 0 && (
            <button
              onClick={onAssemble}
              className="flex items-center gap-1.5 bg-green-900/40 hover:bg-green-900/60 text-green-400 border border-green-800/50 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> Assemble ({doneCount})
            </button>
          )}
          <button onClick={() => setShowSettings(!showSettings)} className="text-zinc-500 hover:text-white p-2 rounded-lg hover:bg-surface-2 transition-colors">
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* Body — three columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Song Panel */}
        <aside className="w-72 border-r border-white/5 flex flex-col bg-surface-1 shrink-0">
          <SongPanel
            project={project}
            song={song}
            scenes={scenes}
            onAutoPlan={onAutoPlan}
            onRefresh={onRefresh}
          />
        </aside>

        {/* Center: Timeline + Scene Grid */}
        <main className="flex-1 flex flex-col overflow-hidden">
          <SceneTimeline
            song={song}
            scenes={scenes}
            currentTime={currentTime}
            selectedSceneId={selectedSceneId}
            onSelectScene={setSelectedSceneId}
            onTimeChange={setCurrentTime}
          />
        </main>

        {/* Right: Scene Editor */}
        <aside className="w-80 border-l border-white/5 flex flex-col bg-surface-1 shrink-0">
          {selectedScene ? (
            <SceneEditor
              scene={selectedScene}
              project={project}
              onUpdate={(data) => onUpdateScene(selectedScene.id, data)}
              onGenerate={() => onGenerateScene(selectedScene.id)}
              onDelete={() => { onDeleteScene(selectedScene.id); setSelectedSceneId(null); }}
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-zinc-600 text-sm gap-2 p-6 text-center">
              <Film className="w-8 h-8 opacity-30" />
              <p>Select a scene to edit</p>
              <p className="text-xs opacity-60">or use Auto-Plan to generate scenes from your song</p>
            </div>
          )}
        </aside>
      </div>

      {/* Bottom: Generation Queue */}
      <footer className="h-12 border-t border-white/5 bg-surface-1 shrink-0">
        <GenerationQueue jobs={jobs} scenes={scenes} />
      </footer>
    </div>
  );
}
