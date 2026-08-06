import type { Scene } from "./types";

export interface ContiguousSceneSequence {
  orderedScenes: Scene[];
  doneScenes: Scene[];
  hasCompletedAfterGap: boolean;
}

/** Return the finished prefix that can be assembled from scene 1 onward. */
export function contiguousDoneSequence(scenes: Scene[]): ContiguousSceneSequence {
  const orderedScenes = [...scenes].sort((a, b) => a.order - b.order);
  let doneCount = 0;
  while (
    doneCount < orderedScenes.length
    && orderedScenes[doneCount].status === "done"
  ) {
    doneCount += 1;
  }
  return {
    orderedScenes,
    doneScenes: orderedScenes.slice(0, doneCount),
    hasCompletedAfterGap: orderedScenes
      .slice(doneCount)
      .some((scene) => scene.status === "done"),
  };
}
