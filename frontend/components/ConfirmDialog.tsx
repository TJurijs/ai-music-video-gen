"use client";
import {
  createContext, useContext, useState, useCallback, useEffect, type ReactNode,
} from "react";

// In-app overlay replacement for window.confirm(). Returns a Promise<boolean>
// so callsites can `if (await confirm(...)) doIt()` exactly like the native API.
//
// The provider keeps at most one dialog open at a time; resolving it
// (Confirm, Cancel, click-outside, or Esc) closes it.

export type ConfirmOpts = {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
};

type Resolver = (v: boolean) => void;
type ConfirmFn = (opts: ConfirmOpts | string) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: Resolver }) | null>(null);

  const confirm: ConfirmFn = useCallback((opts) => {
    const normalized: ConfirmOpts = typeof opts === "string" ? { message: opts } : opts;
    return new Promise<boolean>((resolve) => {
      setState({ ...normalized, resolve });
    });
  }, []);

  const handle = useCallback((ok: boolean) => {
    if (!state) return;
    state.resolve(ok);
    setState(null);
  }, [state]);

  // Esc cancels, Enter confirms. Esc is the universal "dismiss".
  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); handle(false); }
      else if (e.key === "Enter") { e.preventDefault(); handle(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, handle]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <div
          className="fixed inset-0 z-[100] bg-black/70 backdrop-blur-sm flex items-center justify-center p-6 animate-in fade-in duration-100"
          onMouseDown={(e) => { if (e.target === e.currentTarget) handle(false); }}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="bg-surface-2 border border-white/10 rounded-xl shadow-2xl max-w-md w-full p-5"
            onMouseDown={(e) => e.stopPropagation()}
          >
            {state.title && (
              <h3 className="text-sm font-semibold mb-2 text-white">{state.title}</h3>
            )}
            <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-line">
              {state.message}
            </p>
            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => handle(false)}
                className="text-xs px-3 py-1.5 bg-surface-3 hover:bg-surface text-zinc-300 hover:text-white border border-white/10 rounded-md transition-colors"
              >
                {state.cancelLabel ?? "Cancel"}
              </button>
              <button
                onClick={() => handle(true)}
                autoFocus
                className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${
                  state.destructive
                    ? "bg-red-500/30 hover:bg-red-500/50 text-red-100 border border-red-500/40"
                    : "bg-accent/30 hover:bg-accent/50 text-accent border border-accent/40"
                }`}
              >
                {state.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm must be used inside <ConfirmProvider>");
  }
  return ctx;
}
