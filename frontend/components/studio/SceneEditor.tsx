"use client";
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Trash2, Zap, RefreshCw, Image as ImageIcon, Video, Mic2, ChevronDown,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Scene, Project } from "@/lib/types";

interface Props {
  scene: Scene;
  project: Project;
  onUpdate: (data: Partial<Scene>) => void;
  onGenerate: () => void;
  onDelete: () => void;
}

export default function SceneEditor({ scene, project, onUpdate, onGenerate, onDelete }: Props) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    description: scene.description ?? "",
    video_prompt: scene.video_prompt ?? "",
    image_prompt: scene.image_prompt ?? "",
    video_model: scene.video_model,
    image_model: scene.image_model,
    lipsync_enabled: scene.lipsync_enabled,
    align_to_beats: scene.align_to_beats,
    audio_start: scene.audio_start,
    audio_end: scene.audio_end,
  });
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ prompts: true });

  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });

  const expandPromptsMutation = useMutation({
    mutationFn: () => api.scenes.expandPrompts(scene.id),
    onSuccess: (updated) => {
      setForm((f) => ({
        ...f,
        video_prompt: updated.video_prompt ?? f.video_prompt,
        image_prompt: updated.image_prompt ?? f.image_prompt,
      }));
      qc.invalidateQueries({ queryKey: ["project", scene.project_id] });
    },
  });

  // Sync form when scene changes
  useEffect(() => {
    setForm({
      description: scene.description ?? "",
      video_prompt: scene.video_prompt ?? "",
      image_prompt: scene.image_prompt ?? "",
      video_model: scene.video_model,
      image_model: scene.image_model,
      lipsync_enabled: scene.lipsync_enabled,
      align_to_beats: scene.align_to_beats,
      audio_start: scene.audio_start,
      audio_end: scene.audio_end,
    });
  }, [scene.id]);

  const handleSave = () => onUpdate(form);
  const isGenerating = ["generating_image", "generating_video", "lipsync"].includes(scene.status);
  const duration = form.audio_end - form.audio_start;

  const videoModels = models?.video ?? {};
  const imageModels = models?.image ?? {};

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 shrink-0">
        <div>
          <h3 className="text-sm font-semibold">Scene {scene.order}</h3>
          <p className="text-xs text-zinc-500">
            {formatTime(scene.audio_start)} – {formatTime(scene.audio_end)}
            {" · "}{duration.toFixed(1)}s
          </p>
        </div>
        <div className="flex gap-1">
          <button onClick={onDelete} className="p-1.5 text-zinc-600 hover:text-error rounded transition-colors">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Status */}
        {scene.status !== "pending" && (
          <div className={`text-xs px-3 py-2 rounded-lg font-medium ${statusStyle(scene.status)}`}>
            {statusLabel(scene.status)}
            {scene.error_message && <p className="mt-1 opacity-70 font-normal">{scene.error_message}</p>}
          </div>
        )}

        {/* Reference image */}
        {scene.reference_image_url && (
          <div className="rounded-xl overflow-hidden aspect-video bg-surface-3">
            <img src={scene.reference_image_url} alt="" className="w-full h-full object-cover" />
          </div>
        )}

        {/* Video preview */}
        {scene.video_url && (
          <div className="rounded-xl overflow-hidden aspect-video bg-black">
            <video src={scene.video_url} controls className="w-full h-full object-cover" />
          </div>
        )}

        {/* Lyrics */}
        {scene.lyrics_segment && (
          <div className="bg-surface-2 rounded-xl px-3 py-2">
            <p className="text-[10px] text-zinc-500 mb-1 uppercase tracking-wide">Lyrics</p>
            <p className="text-xs italic text-zinc-300">"{scene.lyrics_segment}"</p>
          </div>
        )}

        {/* Timing */}
        <Section title="Timing" expanded={expanded.timing} onToggle={() => toggle("timing")}>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-zinc-500 block mb-1">Start (s)</label>
              <input type="number" step={0.1} className={inputCls}
                value={form.audio_start}
                onChange={(e) => setForm({ ...form, audio_start: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="text-xs text-zinc-500 block mb-1">End (s)</label>
              <input type="number" step={0.1} className={inputCls}
                value={form.audio_end}
                onChange={(e) => setForm({ ...form, audio_end: Number(e.target.value) })}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs mt-2 cursor-pointer">
            <input type="checkbox" className="accent-accent" checked={form.align_to_beats}
              onChange={(e) => setForm({ ...form, align_to_beats: e.target.checked })} />
            <span className="text-zinc-400">Align boundaries to beats</span>
          </label>
        </Section>

        {/* Description */}
        <div>
          <label className="text-xs text-zinc-500 block mb-1">Description</label>
          <textarea
            className={`${inputCls} resize-none`} rows={2}
            placeholder="Brief scene description..."
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </div>

        {/* Prompts */}
        <Section
          title="Generation Prompts"
          expanded={expanded.prompts ?? true}
          onToggle={() => toggle("prompts")}
          action={
            <button
              onClick={() => expandPromptsMutation.mutate()}
              disabled={expandPromptsMutation.isPending || !form.description}
              className="text-[10px] text-accent hover:text-accent-hover disabled:opacity-40 flex items-center gap-1 transition-colors"
              title="Auto-expand prompts with AI"
            >
              <RefreshCw className={`w-3 h-3 ${expandPromptsMutation.isPending ? "animate-spin" : ""}`} />
              AI Expand
            </button>
          }
        >
          <div>
            <label className="text-xs text-zinc-500 mb-1 flex items-center gap-1"><Video className="w-3 h-3" /> Video Prompt</label>
            <textarea className={`${inputCls} resize-none`} rows={4}
              placeholder="Detailed prompt for video generation..."
              value={form.video_prompt}
              onChange={(e) => setForm({ ...form, video_prompt: e.target.value })}
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1 flex items-center gap-1"><ImageIcon className="w-3 h-3" /> Image Prompt</label>
            <textarea className={`${inputCls} resize-none`} rows={3}
              placeholder="Reference still image prompt..."
              value={form.image_prompt}
              onChange={(e) => setForm({ ...form, image_prompt: e.target.value })}
            />
          </div>
        </Section>

        {/* Models */}
        <Section title="Models" expanded={expanded.models} onToggle={() => toggle("models")}>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Video Model</label>
            <select className={inputCls} value={form.video_model}
              onChange={(e) => setForm({ ...form, video_model: e.target.value })}>
              {Object.entries(videoModels).map(([key, m]) => {
                const r = m.resolutions[0];
                const rate = m.pricing[r]?.without_audio;
                return (
                  <option key={key} value={key}>
                    {m.name}{rate ? ` ($${rate}/s · max ${m.max_duration}s${m.generates_audio ? " · audio" : ""})` : ""}
                  </option>
                );
              })}
              {Object.keys(videoModels).length === 0 && (
                <option value={scene.video_model}>{scene.video_model}</option>
              )}
            </select>
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1 mt-2">Image Model</label>
            <select className={inputCls} value={form.image_model}
              onChange={(e) => setForm({ ...form, image_model: e.target.value })}>
              {Object.entries(imageModels).map(([key, m]) => (
                <option key={key} value={key}>{m.name} (${m.price_per_image}/img)</option>
              ))}
              {Object.keys(imageModels).length === 0 && (
                <option value={scene.image_model}>{scene.image_model}</option>
              )}
            </select>
          </div>
          <label className="flex items-center gap-2 text-xs mt-3 cursor-pointer">
            <input type="checkbox" className="accent-accent" checked={form.lipsync_enabled}
              onChange={(e) => setForm({ ...form, lipsync_enabled: e.target.checked })} />
            <Mic2 className="w-3 h-3 text-zinc-400" />
            <span className="text-zinc-400">Enable lipsync (fal.ai)</span>
          </label>
        </Section>
      </div>

      {/* Footer actions */}
      <div className="p-4 border-t border-white/5 flex gap-2 shrink-0">
        <button
          onClick={handleSave}
          className="flex-1 bg-surface-3 hover:bg-surface-2 border border-white/10 text-sm py-2 rounded-lg transition-colors"
        >
          Save
        </button>
        <button
          onClick={onGenerate}
          disabled={isGenerating}
          className="flex-1 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm py-2 rounded-lg font-medium transition-colors flex items-center justify-center gap-1.5"
        >
          {isGenerating ? (
            <><RefreshCw className="w-4 h-4 animate-spin" /> Working...</>
          ) : (
            <><Zap className="w-4 h-4" /> Generate</>
          )}
        </button>
      </div>
    </div>
  );

  function toggle(key: string) {
    setExpanded((e) => ({ ...e, [key]: !e[key] }));
  }
}

function Section({ title, expanded, onToggle, children, action }: {
  title: string;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="border border-white/5 rounded-xl overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-3 py-2 bg-surface-2 hover:bg-surface-3 transition-colors"
        onClick={onToggle}
      >
        <span className="text-xs font-medium text-zinc-300">{title}</span>
        <div className="flex items-center gap-2">
          {action && <span onClick={(e) => e.stopPropagation()}>{action}</span>}
          <ChevronDown className={`w-3.5 h-3.5 text-zinc-500 transition-transform ${expanded ? "rotate-180" : ""}`} />
        </div>
      </button>
      {expanded && <div className="p-3 space-y-2 bg-surface">{children}</div>}
    </div>
  );
}

const inputCls = "w-full bg-surface-2 border border-white/10 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-accent text-white";

function statusStyle(status: string) {
  const map: Record<string, string> = {
    generating_image: "bg-blue-900/40 text-blue-300",
    generating_video: "bg-purple-900/40 text-purple-300",
    lipsync: "bg-indigo-900/40 text-indigo-300",
    done: "bg-green-900/40 text-green-300",
    error: "bg-red-900/40 text-red-300",
  };
  return map[status] ?? "bg-zinc-800 text-zinc-300";
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    generating_image: "⏳ Generating reference image...",
    generating_video: "🎬 Generating video clip...",
    lipsync: "👄 Applying lipsync...",
    done: "✅ Generation complete",
    error: "❌ Generation failed",
  };
  return map[status] ?? status;
}

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}
