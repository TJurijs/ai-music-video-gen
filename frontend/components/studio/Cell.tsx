"use client";
import { ReactNode } from "react";
import { Check, Loader2, AlertCircle, Circle, ChevronDown } from "lucide-react";

export type CellStatus = "locked" | "ready" | "running" | "complete" | "error" | "skipped";

interface Props {
  step: number;
  title: string;
  subtitle?: string;
  status: CellStatus;
  expanded: boolean;
  onToggle: () => void;
  children: ReactNode;
  badge?: ReactNode;
  optional?: boolean;
}

const STATUS_RING: Record<CellStatus, string> = {
  locked: "bg-surface-3 text-zinc-600 border-white/5",
  ready: "bg-surface-2 text-zinc-300 border-accent/40",
  running: "bg-accent/20 text-accent border-accent",
  complete: "bg-green-900/30 text-green-400 border-green-700/50",
  error: "bg-red-900/30 text-red-400 border-red-700/50",
  skipped: "bg-surface-3 text-zinc-500 border-white/5",
};

const STATUS_ICON: Record<CellStatus, ReactNode> = {
  locked: <Circle className="w-3.5 h-3.5" />,
  ready: <Circle className="w-3.5 h-3.5" />,
  running: <Loader2 className="w-3.5 h-3.5 animate-spin" />,
  complete: <Check className="w-3.5 h-3.5" strokeWidth={3} />,
  error: <AlertCircle className="w-3.5 h-3.5" />,
  skipped: <Circle className="w-3.5 h-3.5" />,
};

export default function Cell({
  step, title, subtitle, status, expanded, onToggle, children, badge, optional,
}: Props) {
  const locked = status === "locked";

  return (
    <section
      id={`cell-${step}`}
      className={`relative rounded-2xl border transition-all ${
        locked ? "border-white/5 opacity-50" : "border-white/10 bg-surface-1"
      } ${expanded ? "shadow-2xl shadow-black/40" : ""}`}
    >
      {/* Step connector line */}
      {step > 1 && (
        <div className="absolute -top-6 left-7 w-px h-6 bg-white/10" aria-hidden />
      )}

      <button
        onClick={onToggle}
        disabled={locked}
        className={`w-full flex items-center gap-4 px-5 py-4 text-left transition-colors ${
          locked ? "cursor-not-allowed" : "hover:bg-white/[0.02]"
        }`}
      >
        {/* Step indicator circle */}
        <div
          className={`shrink-0 w-9 h-9 rounded-full border flex items-center justify-center font-semibold text-xs ${STATUS_RING[status]}`}
        >
          {status === "ready" || status === "locked" ? step : STATUS_ICON[status]}
        </div>

        {/* Title block */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className={`text-sm font-semibold ${locked ? "text-zinc-600" : "text-white"}`}>
              {title}
            </h2>
            {optional && (
              <span className="text-[10px] uppercase tracking-wide text-zinc-600 font-medium">
                Optional
              </span>
            )}
            {badge}
          </div>
          {subtitle && <p className="text-xs text-zinc-500 mt-0.5 truncate">{subtitle}</p>}
        </div>

        {/* Chevron */}
        {!locked && (
          <ChevronDown
            className={`w-4 h-4 text-zinc-500 shrink-0 transition-transform ${
              expanded ? "rotate-180" : ""
            }`}
          />
        )}
      </button>

      {expanded && !locked && (
        <div className="px-5 pb-5 pt-1 border-t border-white/5">{children}</div>
      )}
    </section>
  );
}
