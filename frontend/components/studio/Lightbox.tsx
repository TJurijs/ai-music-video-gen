"use client";
import { useEffect } from "react";
import { X } from "lucide-react";

interface Props {
  src: string;
  alt?: string;
  caption?: string;
  onClose: () => void;
}

export default function Lightbox({ src, alt, caption, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/85 backdrop-blur-sm flex items-center justify-center p-4 cursor-zoom-out"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <button
        onClick={onClose}
        className="absolute top-4 right-4 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
        aria-label="Close"
      >
        <X className="w-5 h-5" />
      </button>
      <div className="max-w-[90vw] max-h-[90vh] flex flex-col items-center gap-3" onMouseDown={(e) => e.stopPropagation()}>
        <img
          src={src}
          alt={alt || ""}
          className="max-w-full max-h-[85vh] object-contain rounded-lg shadow-2xl"
        />
        {caption && <p className="text-sm text-zinc-300 text-center">{caption}</p>}
      </div>
    </div>
  );
}
