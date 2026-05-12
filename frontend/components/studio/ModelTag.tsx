"use client";
import { Cpu } from "lucide-react";

interface Props {
  label: string;
  model: string;
  hint?: string;
  className?: string;
}

/** Small chip showing "Label · Model name" — used to surface what model handled a step. */
export default function ModelTag({ label, model, hint, className = "" }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] text-zinc-500 ${className}`}
      title={hint}
    >
      <Cpu className="w-2.5 h-2.5 opacity-60" />
      <span className="text-zinc-600">{label}:</span>
      <span className="text-zinc-400 font-medium">{model}</span>
    </span>
  );
}
