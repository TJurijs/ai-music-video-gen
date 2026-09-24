"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { Project, Scene } from "@/lib/types";
import VideoModelCheatSheet from "./cells/generate/VideoModelCheatSheet";

/** Read-only comparison; actual choices belong to each scene. */
export default function ModelsButton({ project, scenes = [] }: { project?: Project; scenes?: Scene[] }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const { data: models, error, isFetching, refetch } = useQuery({ queryKey: ["models"], queryFn: api.models.list, enabled: open, staleTime: 0 });
  useEffect(() => {
    if (!open || !dialog.current) return;
    const element = dialog.current;
    element.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { element.close(); document.body.style.overflow = previousOverflow; button.current?.focus(); };
  }, [open]);
  return <>
    <button ref={button} type="button" onClick={() => setOpen(true)} aria-haspopup="dialog" className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs font-medium text-zinc-200 hover:border-violet-400/40 hover:bg-violet-500/10">
      <BookOpen className="h-3.5 w-3.5 text-violet-300" />Models
    </button>
    {open && <dialog ref={dialog} aria-labelledby="models-title" aria-describedby="models-description" onCancel={(event) => { event.preventDefault(); setOpen(false); }} onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }} className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-6xl overflow-hidden rounded-2xl border border-white/10 bg-surface-2 p-0 text-white shadow-2xl backdrop:bg-black/70 backdrop:backdrop-blur-sm">
      <div className="flex max-h-[90dvh] flex-col">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-white/10 px-5 py-4 sm:px-6">
          <div><h2 id="models-title" className="text-base font-semibold">Your video models</h2><p id="models-description" className="mt-1 text-xs text-zinc-400">Used and manually added models. Compare here; choose model, length and quality on each scene.</p></div>
          <button type="button" aria-label="Close model comparison" onClick={() => setOpen(false)} className="rounded-lg p-1.5 text-zinc-400 hover:bg-white/5 hover:text-white"><X className="h-5 w-5" /></button>
        </div>
        <div className="overflow-y-auto overscroll-contain p-5 sm:p-6">
          {error && <div role="alert" className="mb-4 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-xs text-amber-200"><p>{error instanceof Error ? error.message : "Could not load models."}</p><button type="button" onClick={() => refetch()} disabled={isFetching} className="mt-2 underline disabled:opacity-50">Try again</button></div>}
          {models ? <VideoModelCheatSheet models={models} project={project} scenes={scenes} /> : !error && <p role="status" className="flex items-center gap-2 py-8 text-sm text-zinc-400"><Loader2 className="h-4 w-4 animate-spin" />Loading model history…</p>}
        </div>
      </div>
    </dialog>}
  </>;
}
