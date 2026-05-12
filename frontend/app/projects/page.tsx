"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Film, Plus, Music, Layers, Trash2, Wand2, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import type { Project } from "@/lib/types";

export default function ProjectsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", style: "", aspect_ratio: "16:9" });

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: api.projects.list,
  });

  const createMutation = useMutation({
    mutationFn: api.projects.create,
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/projects/${p.id}`);
    },
  });

  const expandStyle = useMutation({
    mutationFn: () => api.projects.expandStyle(form.style),
    onSuccess: (r) => setForm((f) => ({ ...f, style: r.expanded })),
  });

  const deleteMutation = useMutation({
    mutationFn: api.projects.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    createMutation.mutate(form);
  };

  return (
    <div className="min-h-screen bg-surface text-white">
      {/* Header */}
      <header className="border-b border-white/5 px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Film className="w-6 h-6 text-accent" />
          <span className="font-semibold text-lg tracking-tight">Music Video Studio</span>
        </div>
        <button
          onClick={() => setShowNew(true)}
          className="flex items-center gap-2 bg-accent hover:bg-accent-hover text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4" /> New Project
        </button>
      </header>

      <main className="max-w-5xl mx-auto px-8 py-10">
        <h1 className="text-2xl font-bold mb-6">Projects</h1>

        {isLoading ? (
          <div className="text-zinc-500 text-sm">Loading...</div>
        ) : projects.length === 0 ? (
          <div className="border border-dashed border-white/10 rounded-xl p-16 text-center">
            <Film className="w-12 h-12 text-zinc-600 mx-auto mb-4" />
            <p className="text-zinc-400 mb-4">No projects yet</p>
            <button
              onClick={() => setShowNew(true)}
              className="bg-accent hover:bg-accent-hover text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              Create your first project
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {projects.map((p) => (
              <ProjectCard
                key={p.id}
                project={p}
                onOpen={() => router.push(`/projects/${p.id}`)}
                onDelete={async () => {
                  if (await confirm({
                    title: `Delete "${p.name}"`,
                    message: "Delete this project and all its scenes, characters, songs, and generated assets? This cannot be undone.",
                    confirmLabel: "Delete project",
                    destructive: true,
                  })) {
                    deleteMutation.mutate(p.id);
                  }
                }}
              />
            ))}
          </div>
        )}
      </main>

      {/* New project modal — only close on backdrop press (not click-drag from inside) */}
      {showNew && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
          onMouseDown={(e) => { if (e.target === e.currentTarget) setShowNew(false); }}
        >
          <div className="bg-surface-2 border border-white/10 rounded-xl p-6 w-full max-w-md shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
            <h2 className="font-semibold text-lg mb-5">New Project</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-xs text-zinc-400 mb-1">Project Name *</label>
                <input
                  className="w-full bg-surface-3 border border-white/10 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                  placeholder="My Music Video"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
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
                  rows={form.style.length > 80 ? 5 : 2}
                />
                <p className="text-[10px] text-zinc-600 mt-0.5">
                  Appended to every image and video render to keep look consistent. Click AI Expand to turn a short hint into a detailed style guide.
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
                <button type="button" onClick={() => setShowNew(false)} className="flex-1 bg-surface-3 hover:bg-surface border border-white/10 text-sm py-2 rounded-lg transition-colors">
                  Cancel
                </button>
                <button type="submit" disabled={createMutation.isPending} className="flex-1 bg-accent hover:bg-accent-hover text-sm py-2 rounded-lg font-medium transition-colors disabled:opacity-50">
                  {createMutation.isPending ? "Creating..." : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function ProjectCard({ project, onOpen, onDelete }: {
  project: Project;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const pct = project.scene_count
    ? Math.round(((project.scenes_done ?? 0) / project.scene_count) * 100)
    : 0;

  return (
    <div
      className="bg-surface-1 border border-white/5 hover:border-accent/40 rounded-xl p-5 cursor-pointer group transition-all"
      onClick={onOpen}
    >
      <div className="flex items-start justify-between mb-3">
        <h3 className="font-semibold text-sm truncate flex-1">{project.name}</h3>
        <button
          className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-error transition-all ml-2"
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
      {project.style && <p className="text-xs text-zinc-500 mb-3 truncate">{project.style}</p>}
      <div className="flex items-center gap-4 text-xs text-zinc-500 mb-3">
        <span className="flex items-center gap-1"><Music className="w-3 h-3" /> {project.song_count ?? 0} song</span>
        <span className="flex items-center gap-1"><Layers className="w-3 h-3" /> {project.scene_count ?? 0} scenes</span>
      </div>
      {(project.scene_count ?? 0) > 0 && (
        <div>
          <div className="flex justify-between text-xs text-zinc-500 mb-1">
            <span>Progress</span><span>{pct}%</span>
          </div>
          <div className="h-1 bg-surface-3 rounded-full">
            <div className="h-1 bg-accent rounded-full transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      <p className="text-xs text-zinc-600 mt-3">
        {new Date(project.created_at).toLocaleDateString()}
        {" · "}{project.aspect_ratio}
      </p>
    </div>
  );
}
