export function fmtCost(usd: number): string {
  if (!usd) return "$0";
  if (usd < 0.01) return `<$0.01`;
  if (usd < 1) return `$${usd.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}`;
  return `$${usd.toFixed(2)}`;
}

export function fmt(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function mostCommon<T>(arr: T[]): T | undefined {
  if (!arr.length) return undefined;
  const counts = new Map<T, number>();
  for (const v of arr) counts.set(v, (counts.get(v) ?? 0) + 1);
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0][0];
}

export function textMentionsCharacter(haystack: string, name: string): boolean {
  const normalizedName = name.toLocaleLowerCase().trim();
  if (!normalizedName) return false;
  const normalizedText = haystack.toLocaleLowerCase();
  const candidates = [normalizedName, ...normalizedName.split(/\s+/)];
  return candidates.some((candidate) => {
    if (!candidate) return false;
    const escaped = candidate.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(
      `(^|[^\\p{L}\\p{N}_])${escaped}(?=$|[^\\p{L}\\p{N}_])`,
      "u",
    ).test(normalizedText);
  });
}
