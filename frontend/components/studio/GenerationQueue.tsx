"use client";
import { RefreshCw, CheckCircle2, XCircle, Clock } from "lucide-react";
import type { GenerationJob, Scene } from "@/lib/types";

interface Props {
  jobs: GenerationJob[];
  scenes: Scene[];
}

export default function GenerationQueue({ jobs, scenes }: Props) {
  const active = jobs.filter((j) => j.status === "running" || j.status === "pending");
  const recent = jobs.filter((j) => j.status === "completed" || j.status === "failed").slice(0, 5);

  const scenesInProgress = scenes.filter((s) =>
    ["generating_image", "generating_video", "lipsync"].includes(s.status)
  );

  const total = scenes.length;
  const done = scenes.filter((s) => s.status === "done").length;
  const errors = scenes.filter((s) => s.status === "error").length;

  return (
    <div className="h-full flex items-center px-4 gap-6 text-xs overflow-x-auto">
      {/* Progress summary */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="w-24 h-1.5 bg-surface-3 rounded-full">
          <div
            className="h-1.5 bg-accent rounded-full transition-all"
            style={{ width: total ? `${(done / total) * 100}%` : "0%" }}
          />
        </div>
        <span className="text-zinc-500">{done}/{total} scenes</span>
        {errors > 0 && <span className="text-error">{errors} errors</span>}
      </div>

      <div className="w-px h-5 bg-white/5 shrink-0" />

      {/* Active jobs */}
      {scenesInProgress.length > 0 ? (
        <div className="flex items-center gap-3">
          <RefreshCw className="w-3 h-3 text-accent animate-spin shrink-0" />
          <div className="flex gap-2">
            {scenesInProgress.map((s) => (
              <JobChip key={s.id} label={`Scene ${s.order} · ${statusVerb(s.status)}`} status="running" />
            ))}
          </div>
        </div>
      ) : (
        <span className="text-zinc-600">No active generation</span>
      )}

      {/* Recent completed */}
      {recent.length > 0 && (
        <>
          <div className="w-px h-5 bg-white/5 shrink-0" />
          <div className="flex items-center gap-2">
            {recent.map((j) => (
              <JobChip
                key={j.id}
                label={`${j.job_type} · Scene ${j.scene_id ?? "?"}`}
                status={j.status as "completed" | "failed"}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function JobChip({ label, status }: { label: string; status: string }) {
  const styles: Record<string, string> = {
    running: "bg-purple-900/40 text-purple-300",
    pending: "bg-zinc-800 text-zinc-400",
    completed: "bg-green-900/30 text-green-400",
    failed: "bg-red-900/30 text-red-400",
  };
  const icons: Record<string, React.ReactNode> = {
    running: <RefreshCw className="w-2.5 h-2.5 animate-spin" />,
    pending: <Clock className="w-2.5 h-2.5" />,
    completed: <CheckCircle2 className="w-2.5 h-2.5" />,
    failed: <XCircle className="w-2.5 h-2.5" />,
  };
  return (
    <span className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] ${styles[status] ?? styles.pending}`}>
      {icons[status]}
      {label}
    </span>
  );
}

function statusVerb(status: string) {
  const map: Record<string, string> = {
    generating_image: "image",
    generating_video: "video",
    lipsync: "lipsync",
  };
  return map[status] ?? status;
}
