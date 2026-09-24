import type { Scene, VideoModel } from "./types";

/** Keep every selector and readiness hint aligned with the route we will submit. */
export function usesSongAudio(model: VideoModel | undefined, enabled = false): boolean {
  return !!model?.supports_audio_input && (!!model.requires_audio_input || enabled);
}

export function videoDurations(model: VideoModel, audioSync = false): number[] {
  return usesSongAudio(model, audioSync) ? model.audio_durations || model.durations : model.durations;
}

export function videoResolutions(model: VideoModel, audioSync = false): string[] {
  return usesSongAudio(model, audioSync) ? model.audio_resolutions || model.resolutions : model.resolutions;
}

export function videoAspects(model: VideoModel, audioSync = false): string[] {
  return usesSongAudio(model, audioSync) ? model.audio_aspects || model.aspects : model.aspects;
}

export function videoRate(model: VideoModel, resolution: string, audioSync = false): number | undefined {
  return usesSongAudio(model, audioSync) ? model.audio_pricing?.[resolution] : model.pricing[resolution]?.without_audio;
}

export function audioUsesFirstFrame(model: VideoModel | undefined): boolean {
  return model?.audio_input_mode === "wan_i2v" || model?.audio_input_mode === "ltx_a2v";
}

/** The provider receiving the next generation, including mandatory audio routes. */
export function videoProvider(model: VideoModel | undefined, audioSync = false): "fal" | "openrouter" {
  return usesSongAudio(model, audioSync) ? "fal" : model?.provider || "openrouter";
}

export function videoProviderLabel(provider: "fal" | "openrouter"): string {
  return provider === "fal" ? "fal" : "OpenRouter";
}

/** Every connected route, even when the comparison is showing only one price. */
export function videoProviderRoutes(model: VideoModel) {
  return [
    ...(!model.requires_audio_input ? [{ provider: model.provider || "openrouter", label: `${videoProviderLabel(model.provider || "openrouter")} · standard` }] : []),
    ...(model.supports_audio_input ? [{ provider: "fal", label: "fal · audio reference" }] : []),
  ];
}

/** Switching to a model may require its audio route to keep the scene's fixed length. */
export function videoSettingsForScene(model: VideoModel, scene: Scene) {
  const duration = Math.round(scene.audio_end - scene.audio_start);
  const needsAudioForLength = !model.durations.includes(duration) && !!model.audio_durations?.includes(duration);
  const audio_sync_enabled = usesSongAudio(model, !!scene.audio_sync_enabled || needsAudioForLength);
  const resolutions = videoResolutions(model, audio_sync_enabled);
  return {
    audio_sync_enabled,
    resolution: resolutions.includes(scene.resolution) ? scene.resolution : resolutions[0],
  };
}
