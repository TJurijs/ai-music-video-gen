import { describe, expect, it } from "vitest";
import type { Scene, VideoModel } from "./types";
import {
  audioUsesFirstFrame, usesSongAudio, videoAspects, videoDurations,
  videoProvider, videoProviderRoutes, videoRate, videoResolutions, videoSettingsForScene,
} from "./videoModels";

const wan: VideoModel = {
  name: "Wan 2.7", model_id: "alibaba/wan-2.7", tier: "mid", tagline: "",
  durations: Array.from({ length: 9 }, (_, i) => i + 2),
  audio_durations: Array.from({ length: 14 }, (_, i) => i + 2),
  resolutions: ["720p", "1080p"], audio_resolutions: ["720p", "1080p"],
  aspects: ["16:9", "9:16", "1:1"],
  pricing: { "720p": { with_audio: 0.1, without_audio: 0.1 }, "1080p": { with_audio: 0.1, without_audio: 0.1 } },
  audio_pricing: { "720p": 0.1, "1080p": 0.15 }, max_duration: 10,
  supports_audio_input: true, audio_input_mode: "wan_i2v",
  supports_first_frame: true, supports_last_frame: true, supports_reference_images: true,
};
const ltx: VideoModel = {
  ...wan, name: "LTX 2.5 Fast", model_id: "lightricks/ltx-2.5/audio-to-video/fast",
  provider: "fal", requires_audio_input: true, audio_input_mode: "ltx_a2v",
  durations: Array.from({ length: 19 }, (_, i) => i + 2),
  audio_durations: Array.from({ length: 19 }, (_, i) => i + 2),
  resolutions: ["1080p"], audio_resolutions: ["1080p"], aspects: ["16:9", "9:16"],
  audio_pricing: { "1080p": 0.13 },
  pricing: { "1080p": { with_audio: 0.13, without_audio: 0.13 } },
  supports_last_frame: false, supports_reference_images: false, max_duration: 20,
};
const wan3: VideoModel = {
  ...wan, name: "Wan 3.0", model_id: "alibaba/wan-3.0", audio_input_mode: "wan_r2v",
  durations: Array.from({ length: 29 }, (_, i) => i + 2), max_duration: 30,
  resolutions: ["480p", "720p", "1080p"], audio_resolutions: ["480p", "720p", "1080p"],
  audio_pricing: { "480p": 0.05, "720p": 0.1, "1080p": 0.2 },
};
const scene = (changes: Partial<Scene> = {}): Scene => ({
  id: 1, project_id: 1, order: 1, audio_start: 30, audio_end: 45, duration: 15,
  video_model: "old", image_model: "test", resolution: "720p", status: "pending",
  align_to_beats: false, prompts_expanded: true, created_at: "", ...changes,
});

describe("route-aware video model settings", () => {
  it("selects Wan's audio route to preserve a 15-second scene", () => {
    expect(videoDurations(wan, false)).not.toContain(15);
    expect(videoDurations(wan, true)).toContain(15);
    const settings = videoSettingsForScene(wan, scene());
    expect(settings).toEqual({ audio_sync_enabled: true, resolution: "720p" });
    expect(videoDurations(wan, settings.audio_sync_enabled)).toContain(15);
  });

  it("keeps short Wan scenes on the selected standard route", () => {
    expect(videoSettingsForScene(wan, scene({ audio_end: 40 })))
      .toEqual({ audio_sync_enabled: false, resolution: "720p" });
    expect(videoSettingsForScene(wan, scene({ audio_end: 40, audio_sync_enabled: true })))
      .toEqual({ audio_sync_enabled: true, resolution: "720p" });
  });

  it("requires audio and selects the supported resolution when switching to LTX", () => {
    expect(usesSongAudio(ltx, false)).toBe(true);
    expect(videoSettingsForScene(ltx, scene({ audio_sync_enabled: false })))
      .toEqual({ audio_sync_enabled: true, resolution: "1080p" });
    expect(videoResolutions(ltx, false)).toEqual(["1080p"]);
    expect(videoAspects(ltx, false)).toEqual(["16:9", "9:16"]);
  });

  it("uses the selected route's rate when pricing the same 15-second scene", () => {
    expect(videoRate(wan, "1080p", false)! * 15).toBeCloseTo(1.5);
    expect(videoRate(wan, "1080p", true)! * 15).toBeCloseTo(2.25);
    expect(videoRate(ltx, "1080p", false)! * 15).toBeCloseTo(1.95);
    expect(videoRate(ltx, "720p", true)).toBeUndefined();
  });

  it("clears stale audio settings for a model without an audio route", () => {
    const standard = { ...wan, supports_audio_input: false, audio_durations: undefined };
    expect(videoSettingsForScene(standard, scene({ audio_end: 40, audio_sync_enabled: true })))
      .toEqual({ audio_sync_enabled: false, resolution: "720p" });
    expect(usesSongAudio(undefined, true)).toBe(false);
  });

  it("distinguishes frame-driven audio from Seedance portrait references", () => {
    expect(audioUsesFirstFrame(wan)).toBe(true);
    expect(audioUsesFirstFrame(ltx)).toBe(true);
    expect(audioUsesFirstFrame({ ...wan, audio_input_mode: "seedance_r2v" })).toBe(false);
  });

  it("uses fal image references and the audio route limits for Wan 3", () => {
    expect(videoProvider(wan3, false)).toBe("openrouter");
    expect(videoProvider(wan3, true)).toBe("fal");
    expect(audioUsesFirstFrame(wan3)).toBe(false);
    expect(videoDurations(wan3, false)).toContain(30);
    expect(videoDurations(wan3, true)).toContain(15);
    expect(videoDurations(wan3, true)).not.toContain(16);
    expect(videoResolutions(wan3, true)).toEqual(["480p", "720p", "1080p"]);
    expect(videoRate(wan3, "1080p", true)! * 15).toBeCloseTo(3);
    expect(videoSettingsForScene(wan3, scene({ audio_sync_enabled: true })))
      .toEqual({ audio_sync_enabled: true, resolution: "720p" });
  });

  it("lists every connected provider without advertising a standard route for audio-only models", () => {
    expect(videoProviderRoutes(wan3)).toEqual([
      { provider: "openrouter", label: "OpenRouter · standard" },
      { provider: "fal", label: "fal · audio reference" },
    ]);
    expect(videoProviderRoutes(ltx)).toEqual([{ provider: "fal", label: "fal · audio reference" }]);
    expect(videoProvider(ltx, false)).toBe("fal");
    expect(videoProviderRoutes({ ...wan3, supports_audio_input: false })).toHaveLength(1);
  });
});
