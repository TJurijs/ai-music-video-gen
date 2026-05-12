"use client";

export default function GlobalModelPicker({
  icon, label, value, options, onChange, disabled,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | undefined;
  options: { key: string; label: string }[];
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <div className="text-[9px] text-zinc-500 mb-0.5 flex items-center gap-1">{icon} {label}</div>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled || options.length === 0}
        className="w-full bg-surface-3 border border-white/10 rounded px-1.5 py-1 text-[10px] text-white focus:outline-none focus:border-accent disabled:opacity-50"
        title={`Set this ${label.toLowerCase()} model for every scene at once`}
      >
        {options.length === 0 && <option value="">—</option>}
        {options.map((o) => (
          <option key={o.key} value={o.key}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}
