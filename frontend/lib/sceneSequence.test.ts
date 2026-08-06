import { describe, expect, it } from "vitest";
import { contiguousDoneSequence } from "./sceneSequence";
import type { Scene } from "./types";

function scene(id: number, order: number, status: Scene["status"]): Scene {
  return {
    id,
    project_id: 1,
    order,
    audio_start: order - 1,
    audio_end: order,
    duration: 1,
    status,
    video_model: "test",
    image_model: "test",
    resolution: "720p",
    align_to_beats: true,
    prompts_expanded: false,
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("contiguousDoneSequence", () => {
  it("sorts scenes and returns only the finished prefix", () => {
    const result = contiguousDoneSequence([
      scene(3, 3, "done"),
      scene(1, 1, "done"),
      scene(2, 2, "pending"),
    ]);
    expect(result.orderedScenes.map((item) => item.order)).toEqual([1, 2, 3]);
    expect(result.doneScenes.map((item) => item.order)).toEqual([1]);
    expect(result.hasCompletedAfterGap).toBe(true);
  });

  it("allows a normal partial prefix when nothing is complete after it", () => {
    const result = contiguousDoneSequence([
      scene(1, 1, "done"),
      scene(2, 2, "image_ready"),
    ]);
    expect(result.doneScenes).toHaveLength(1);
    expect(result.hasCompletedAfterGap).toBe(false);
  });
});
