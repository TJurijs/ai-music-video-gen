"use client";
import { Loader2, ChevronDown } from "lucide-react";
import { useState, useEffect, useRef, useLayoutEffect } from "react";
import { createPortal } from "react-dom";

export default function SplitGenerateButton({
  label, icon, running, disabled, currentModel, options,
  onClickMain, onPickModel, colorClasses, title,
}: {
  label: string;
  icon: React.ReactNode;
  running: boolean;
  disabled: boolean;
  currentModel: string;
  options: { key: string; label: string }[];
  onClickMain: () => void;
  onPickModel: (key: string) => void;
  colorClasses: string;
  title: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const chevronRef = useRef<HTMLButtonElement>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number; minWidth: number } | null>(null);

  // Position the portal-rendered menu relative to the chevron button.
  // useLayoutEffect avoids the visible jump after opening.
  useLayoutEffect(() => {
    if (!open || !chevronRef.current) {
      setMenuPos(null);
      return;
    }
    const rect = chevronRef.current.getBoundingClientRect();
    const menuMin = 220;
    // Prefer right-aligned to the chevron (so menu doesn't blow off the right edge)
    const right = rect.right;
    const left = Math.max(8, right - menuMin);
    // Below the button by default; if it would clip the bottom, flip above
    const viewportH = window.innerHeight;
    const estMenuH = Math.min(options.length * 30 + 16, 320);
    let top = rect.bottom + 4;
    if (top + estMenuH > viewportH - 8) {
      top = Math.max(8, rect.top - 4 - estMenuH);
    }
    setMenuPos({ top, left, minWidth: menuMin });
  }, [open, options.length]);

  // Close on outside click + on scroll
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      const inWrap = wrapRef.current?.contains(target);
      const inMenu = (target as HTMLElement)?.closest?.("[data-split-menu]");
      if (!inWrap && !inMenu) setOpen(false);
    };
    const onScroll = () => setOpen(false);
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative inline-flex">
      <button
        onClick={onClickMain}
        disabled={disabled}
        className={`text-xs pl-2.5 pr-1.5 py-1.5 border rounded-l-lg transition-colors disabled:opacity-50 flex items-center gap-1 ${colorClasses}`}
        title={title + ` — uses ${currentModel}. Click ▾ to pick a different model for this run.`}
      >
        {running ? <Loader2 className="w-3 h-3 animate-spin" /> : icon}
        {label}
      </button>
      <button
        ref={chevronRef}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        disabled={disabled || options.length === 0}
        className={`text-xs px-1 py-1.5 border-l-0 border rounded-r-lg transition-colors disabled:opacity-50 flex items-center ${colorClasses}`}
        title="Pick a model for this run (also sets this scene's default)"
      >
        <ChevronDown className={`w-2.5 h-2.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && menuPos && typeof document !== "undefined" && createPortal(
        <div
          data-split-menu
          className="fixed z-[100] bg-surface-2 border border-white/15 rounded-md shadow-2xl py-1 max-h-[60vh] overflow-y-auto"
          style={{ top: menuPos.top, left: menuPos.left, minWidth: menuPos.minWidth }}
        >
          {options.map((o) => (
            <button
              key={o.key}
              onClick={() => { setOpen(false); onPickModel(o.key); }}
              className={`w-full text-left text-xs px-3 py-1.5 hover:bg-accent/20 flex items-center justify-between gap-3 ${
                o.key === currentModel ? "text-accent font-medium" : "text-zinc-300"
              }`}
            >
              <span>{o.label}</span>
              {o.key === currentModel && <span className="text-[9px] text-zinc-500 shrink-0">current</span>}
            </button>
          ))}
        </div>,
        document.body
      )}
    </div>
  );
}
