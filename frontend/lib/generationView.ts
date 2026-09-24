import type { GenerationJob, ModelsConfig, Project, Scene } from "./types";
import { textMentionsCharacter } from "../components/studio/cells/generate/shared";
import { audioUsesFirstFrame, usesSongAudio, videoAspects, videoDurations, videoResolutions } from "./videoModels";

export type SceneFilter = "all" | "attention" | "running" | "stills" | "videos" | "complete";

export function isSceneBusy(scene: Scene): boolean {
  return !!scene.generation_run_id || ["generating_image", "generating_video"].includes(scene.status);
}

export function hasResumableVideoJob(jobs: GenerationJob[]): boolean {
  return jobs.some((job) => job.job_type === "video" && !!job.external_id
    && job.status === "running" && ["fal", "openrouter"].includes(job.provider));
}

/** Client hints only; the server preflight remains authoritative before every submission. */
export function videoReadinessReason(scene: Scene, project: Project, models?: ModelsConfig): string | null {
  if (!models) return "Loading available models…";
  const model = models.video[scene.video_model];
  if (!model) return "Choose an available model in this scene’s video settings.";
  if (model.available === false) return model.unavailable_reason || "This video model is unavailable. Choose another model.";
  const duration = Math.round(scene.audio_end - scene.audio_start);
  const audioSync = usesSongAudio(model, scene.audio_sync_enabled);
  if (!videoDurations(model, audioSync).includes(duration)) return `${model.name} cannot render ${duration}s in this mode. Choose a compatible video model or enable song sync.`;
  if (model.aspects && !videoAspects(model, audioSync).includes(project.aspect_ratio)) return `${model.name} does not support this project's ${project.aspect_ratio} aspect ratio.`;
  if (model.resolutions && !videoResolutions(model, audioSync).includes(scene.resolution)) return `Choose a supported resolution for ${model.name}.`;
  const previous = project.scenes?.find((candidate) => candidate.order === scene.order - 1);
  if (scene.chain_from_prev && previous?.video_timing_stale) return `Regenerate scene #${previous.order} for its new timing before using its final frame.`;
  const hasFrame = scene.chain_from_prev ? !!previous?.extracted_last_frame_url : !!scene.reference_image_url;
  const text = `${scene.description || ""} ${scene.image_prompt || ""} ${scene.video_prompt || ""}`;
  const hasCharacter = project.characters?.some((character) => !!character.reference_image_url && textMentionsCharacter(text, character.name));
  if (audioSync) {
    if (!project.songs?.some((song) => song.file_url || song.file_path)) return "Add a song file before using audio sync.";
    if (!audioUsesFirstFrame(model) && hasCharacter) return null;
  } else if (scene.video_reference_mode === "character") {
    if (!model.supports_reference_images) return "Choose a model with character references, or switch to First frame mode.";
    if (scene.chain_from_prev) return "Disconnect this scene from the previous scene to use character references.";
    return hasCharacter ? null : "Name a character with a saved portrait in this scene’s prompt.";
  }
  if (!hasFrame) return scene.chain_from_prev
    ? `Generate scene #${scene.order - 1} first to use its final frame.`
    : "Generate and review the first frame, then generate the video.";
  return null;
}

export function generationView(project: Project, models: ModelsConfig | undefined, jobs: GenerationJob[]) {
  const scenes = [...(project.scenes || [])].sort((a, b) => a.order - b.order);
  const jobsByScene = new Map<number, GenerationJob[]>();
  for (const job of jobs) {
    if (job.scene_id != null) jobsByScene.set(job.scene_id, [...(jobsByScene.get(job.scene_id) || []), job]);
  }
  const running = scenes.filter(isSceneBusy);
  const idle = scenes.filter((scene) => !isSceneBusy(scene));
  const attention = idle.filter((scene) => !!scene.video_timing_stale || !!scene.error_message || ["error", "cancelled"].includes(scene.status)
    || hasResumableVideoJob(jobsByScene.get(scene.id) || []));
  const complete = idle.filter((scene) => scene.status === "done" && !scene.video_timing_stale);
  // Preserve successful videos and never enqueue a second task for a claimed scene.
  const unfinished = idle.filter((scene) => scene.video_timing_stale || (!scene.video_url && scene.status !== "done"));
  const stills = unfinished.filter((scene) => {
    const model = models?.video[scene.video_model];
    const audioSync = usesSongAudio(model, scene.audio_sync_enabled);
    const characterOnly = !audioSync && model?.supports_reference_images && scene.video_reference_mode === "character";
    return !scene.reference_image_url && !scene.chain_from_prev && !characterOnly
      && !hasResumableVideoJob(jobsByScene.get(scene.id) || []);
  });
  const videos = idle.filter((scene) => hasResumableVideoJob(jobsByScene.get(scene.id) || [])
    || ((scene.video_timing_stale || (!scene.video_url && scene.status !== "done")) && videoReadinessReason(scene, project, models) === null));
  return { scenes, running, attention, complete, stills, videos, jobsByScene };
}
