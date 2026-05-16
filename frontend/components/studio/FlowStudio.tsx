"use client";
import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import {
  ChevronLeft, Film, Settings as SettingsIcon, Download, Music, Users, Layers, Wand2, Sparkles, DollarSign, Pencil, Loader2, Check,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Project, Song, Scene, GenerationJob, Character, ProjectCosts } from "@/lib/types";
import Cell, { type CellStatus } from "./Cell";
import StepSongCell from "./cells/StepSongCell";
import StepCharactersCell from "./cells/StepCharactersCell";
import StepPlanCell from "./cells/StepPlanCell";
import StepGenerateCell from "./cells/StepGenerateCell";
import StepAssembleCell from "./cells/StepAssembleCell";

interface Props {
  project: Project;
  song?: Song;
  scenes: Scene[];
  characters: Character[];
  jobs: GenerationJob[];
  costs?: ProjectCosts;
}

export default function FlowStudio({ project, song, scenes, characters, jobs, costs }: Props) {
  const router = useRouter();
  const qc = useQueryClient();

  // Compute step statuses
  const songStatus: CellStatus = !song
    ? "ready"
    : song.status === "ready"
    ? "complete"
    : song.status === "error"
    ? "error"
    : "running";

  // Characters is independent — define them anytime
  const charactersStatus: CellStatus = characters.length > 0 ? "complete" : "ready";

  const planStatus: CellStatus = songStatus !== "complete"
    ? "locked"
    : scenes.length > 0
    ? "complete"
    : "ready";

  const generateStatus: CellStatus = scenes.length === 0
    ? "locked"
    : (() => {
        const done = scenes.filter((s) => s.status === "done").length;
        const running = scenes.some((s) =>
          ["generating_image", "generating_video"].includes(s.status)
        );
        const errored = scenes.some((s) => s.status === "error");
        if (running) return "running";
        if (done === scenes.length) return "complete";
        if (errored && done < scenes.length) return "error";
        return "ready";
      })();

  // Assembly unlocks as soon as at least one scene has a finished video —
  // partial assemblies are useful for previewing the cut so far. The
  // backend filters to status="done" scenes and -shortest-trims the
  // muxed audio to match.
  const anySceneDone = scenes.some((s) => s.status === "done");
  const assembleStatus: CellStatus = anySceneDone ? "ready" : "locked";

  // Auto-expand the first non-complete step on mount
  const initialExpand = useMemo(() => {
    if (songStatus !== "complete") return new Set([1]);
    if (planStatus !== "complete") return new Set([3]);
    if (generateStatus !== "complete") return new Set([4]);
    return new Set([5]);
  }, []); // intentionally only on mount

  const [expanded, setExpanded] = useState<Set<number>>(initialExpand);
  const [showEditProject, setShowEditProject] = useState(false);

  const toggle = (step: number) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(step)) next.delete(step);
      else next.add(step);
      return next;
    });

  // Auto-expand step when its status flips to "ready" (i.e. it just got unlocked)
  useEffect(() => {
    if (songStatus === "complete" && planStatus === "ready" && !expanded.has(3)) {
      setExpanded((s) => new Set(s).add(3));
    }
  }, [songStatus, planStatus]); // eslint-disable-line

  useEffect(() => {
    if (planStatus === "complete" && generateStatus === "ready" && !expanded.has(4)) {
      setExpanded((s) => new Set(s).add(4));
    }
  }, [planStatus, generateStatus]); // eslint-disable-line

  return (
    <div className="min-h-screen bg-surface text-white">
      {/* Sticky header */}
      <header className="sticky top-0 z-40 backdrop-blur bg-surface/85 border-b border-white/5">
        <div className="max-w-3xl mx-auto px-6 py-3 flex items-center gap-3">
          <button
            onClick={() => router.push("/projects")}
            className="text-zinc-500 hover:text-white p-1 rounded transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <Film className="w-4 h-4 text-accent" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <h1 className="font-semibold text-sm truncate">{project.name}</h1>
              <button
                onClick={() => setShowEditProject(true)}
                className="text-zinc-600 hover:text-white p-0.5 rounded transition-colors"
                title="Edit project name, style and aspect ratio"
              >
                <Pencil className="w-3 h-3" />
              </button>
            </div>
            <p
              className="text-[11px] text-zinc-500 truncate"
              title={project.style || undefined}
            >
              {project.style || "no style set"} · {project.aspect_ratio}
            </p>
          </div>
          {costs && costs.total_usd > 0 && (
            <div
              className="flex items-center gap-1.5 text-xs bg-green-900/20 border border-green-800/40 text-green-300 px-2.5 py-1 rounded-md font-mono"
              title={`${costs.job_count} jobs · click to see breakdown`}
            >
              <DollarSign className="w-3 h-3" />
              {fmtCost(costs.total_usd)}
            </div>
          )}
          <ProgressDots
            steps={[songStatus, charactersStatus, planStatus, generateStatus, assembleStatus]}
          />
        </div>
      </header>

      {/* Notebook content */}
      <main className="max-w-3xl mx-auto px-6 py-8 space-y-6">

        <Cell
          step={1}
          title="Song"
          subtitle={song ? `${song.title}${song.artist ? " · " + song.artist : ""}` : "Upload an MP3 or generate one"}
          status={songStatus}
          expanded={expanded.has(1)}
          onToggle={() => toggle(1)}
          badge={
            <span className="flex items-center gap-2">
              {song?.status === "ready" && (
                <span className="text-[10px] text-zinc-500">{Math.round(song.bpm ?? 0)} BPM · {song.key}</span>
              )}
              {costs && (costs.by_type.music || costs.by_type.transcription) ? (
                <span className="text-[10px] text-green-400/80 font-mono">
                  {fmtCost((costs.by_type.music || 0) + (costs.by_type.transcription || 0))}
                </span>
              ) : null}
            </span>
          }
        >
          <StepSongCell project={project} song={song} />
        </Cell>

        <Cell
          step={2}
          title="Characters"
          subtitle={characters.length ? `${characters.length} character${characters.length > 1 ? "s" : ""} defined` : "Define recurring characters for visual consistency"}
          status={charactersStatus}
          expanded={expanded.has(2)}
          onToggle={() => toggle(2)}
          optional
        >
          <StepCharactersCell project={project} characters={characters} song={song} />
        </Cell>

        <Cell
          step={3}
          title="Scene Plan"
          subtitle={scenes.length ? `${scenes.length} scenes mapped to your song` : "Auto-plan or build scenes manually"}
          status={planStatus}
          expanded={expanded.has(3)}
          onToggle={() => toggle(3)}
          badge={costs && (costs.by_type.llm_plan || costs.by_type.llm_expand) ? (
            <span className="text-[10px] text-green-400/80 font-mono">
              {fmtCost((costs.by_type.llm_plan || 0) + (costs.by_type.llm_expand || 0))}
            </span>
          ) : undefined}
        >
          <StepPlanCell project={project} song={song} scenes={scenes} />
        </Cell>

        <Cell
          step={4}
          title="Generate Scenes"
          subtitle={scenes.length
            ? `${scenes.filter(s => s.status === "done").length} of ${scenes.length} complete`
            : "Generate images and videos per scene"}
          status={generateStatus}
          expanded={expanded.has(4)}
          onToggle={() => toggle(4)}
          badge={costs && costs.by_type.video ? (
            <span className="text-[10px] text-green-400/80 font-mono">
              {fmtCost((costs.by_type.video || 0) + (costs.by_type.image || 0))}
            </span>
          ) : undefined}
        >
          <StepGenerateCell project={project} scenes={scenes} jobs={jobs} costs={costs} />
        </Cell>

        <Cell
          step={5}
          title="Final Assembly"
          subtitle="Stitch scenes with audio and export the finished video"
          status={assembleStatus}
          expanded={expanded.has(5)}
          onToggle={() => toggle(5)}
        >
          <StepAssembleCell project={project} scenes={scenes} song={song} costs={costs} />
        </Cell>

        <div className="text-center text-[11px] text-zinc-700 py-8">
          Steps unlock as you complete the previous one · Click any step header to expand
        </div>
      </main>

      {showEditProject && (
        <ProjectEditModal
          project={project}
          onClose={() => setShowEditProject(false)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["project", project.id] });
            qc.invalidateQueries({ queryKey: ["projects"] });
            setShowEditProject(false);
          }}
        />
      )}
    </div>
  );
}

function ProjectEditModal({
  project, onClose, onSaved,
}: {
  project: Project;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: project.name,
    style: project.style ?? "",
    aspect_ratio: project.aspect_ratio,
  });
  const save = useMutation({
    mutationFn: () => api.projects.update(project.id, form),
    onSuccess: onSaved,
  });
  const expandStyle = useMutation({
    mutationFn: () => api.projects.expandStyle(form.style),
    onSuccess: (r) => setForm((f) => ({ ...f, style: r.expanded })),
  });

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="bg-surface-2 border border-white/10 rounded-xl p-6 w-full max-w-md shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className="font-semibold text-lg mb-5">Edit Project</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-zinc-400 mb-1">Project Name</label>
            <input
              className="w-full bg-surface-3 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-400 mb-1 flex items-center justify-between">
              <span>Style / Mood</span>
              <button
                type="button"
                onClick={() => expandStyle.mutate()}
                disabled={expandStyle.isPending || !form.style.trim()}
                className="text-[10px] text-accent hover:text-accent-hover flex items-center gap-1 disabled:opacity-40"
                title="Turn a short style hint into a detailed style guide that gets appended to every render"
              >
                {expandStyle.isPending
                  ? <><Loader2 className="w-2.5 h-2.5 animate-spin" /> Expanding...</>
                  : <><Wand2 className="w-2.5 h-2.5" /> AI Expand</>
                }
              </button>
            </label>
            <textarea
              className="w-full bg-surface-3 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent resize-y leading-snug"
              placeholder="e.g. cyberpunk pc game, old VHS, neon noir, dreamlike..."
              value={form.style}
              onChange={(e) => setForm({ ...form, style: e.target.value })}
              rows={form.style.length > 80 ? 6 : 2}
            />
            <p className="text-[10px] text-zinc-600 mt-0.5">
              Appended to every image + video render prompt to keep look consistent.
            </p>
          </div>
          <div>
            <label className="block text-xs text-zinc-400 mb-1">Aspect Ratio</label>
            <select
              className="w-full bg-surface-3 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
              value={form.aspect_ratio}
              onChange={(e) => setForm({ ...form, aspect_ratio: e.target.value })}
            >
              <option value="16:9">16:9 (Widescreen)</option>
              <option value="9:16">9:16 (Vertical / Shorts)</option>
              <option value="1:1">1:1 (Square)</option>
            </select>
          </div>
          <div className="flex gap-3 pt-2">
            <button onClick={onClose} className="flex-1 bg-surface-3 hover:bg-surface border border-white/10 text-sm py-2 rounded-lg transition-colors">
              Cancel
            </button>
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending || !form.name.trim()}
              className="flex-1 bg-accent hover:bg-accent-hover text-sm py-2 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
            >
              {save.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function fmtCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.01) return `<$0.01`;
  if (usd < 1) return `$${usd.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}`;
  return `$${usd.toFixed(2)}`;
}

function ProgressDots({ steps }: { steps: CellStatus[] }) {
  return (
    <div className="flex items-center gap-1.5">
      {steps.map((s, i) => (
        <span
          key={i}
          className={`block w-2 h-2 rounded-full transition-colors ${
            s === "complete" ? "bg-green-500" :
            s === "running" ? "bg-accent animate-pulse" :
            s === "error" ? "bg-red-500" :
            s === "ready" ? "bg-zinc-500" :
            "bg-zinc-800"
          }`}
          title={`Step ${i + 1}: ${s}`}
        />
      ))}
    </div>
  );
}
