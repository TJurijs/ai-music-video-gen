import { describe, expect, it } from "vitest";
import { fmt, fmtCost, mostCommon, textMentionsCharacter } from "./shared";

describe("generation helpers", () => {
  it("matches character names on word boundaries, not substrings", () => {
    expect(textMentionsCharacter("Al walks into frame", "Al")).toBe(true);
    expect(textMentionsCharacter("a wall fills the frame", "Al")).toBe(false);
    expect(textMentionsCharacter("Elias enters", "Elias Thorne")).toBe(true);
    expect(textMentionsCharacter("Céleste turns", "Céleste")).toBe(true);
  });

  it("formats durations and costs predictably", () => {
    expect(fmt(65.9)).toBe("1:05");
    expect(fmtCost(0)).toBe("$0");
    expect(fmtCost(0.005)).toBe("<$0.01");
    expect(fmtCost(1.2)).toBe("$1.20");
  });

  it("returns the most common value", () => {
    expect(mostCommon(["a", "b", "a"])).toBe("a");
    expect(mostCommon([])).toBeUndefined();
  });
});
