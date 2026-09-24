import { describe, expect, it } from "vitest";
import { generationView, videoReadinessReason } from "./generationView";
import type { GenerationJob, ModelsConfig, Project, Scene } from "./types";

const scene = (id: number, changes: Partial<Scene> = {}): Scene => ({
  id, project_id: 1, order: id, audio_start: 0, audio_end: 5, duration: 5,
  video_model: "test", image_model: "test", resolution: "720p", status: "pending",
  align_to_beats: false, prompts_expanded: true, created_at: "", ...changes,
});
const models = { video: { test: { name: "Test", durations: [5], supports_reference_images: true } }, image: {}, llm: {} } as unknown as ModelsConfig;
const project = (scenes: Scene[]): Project => ({ id: 1, name: "Test", aspect_ratio: "16:9", created_at: "", updated_at: "", scenes });

describe("generation work selection", () => {
  it("excludes queued work and successful videos from bulk actions", () => {
    const view = generationView(project([
      scene(1, { generation_run_id: "claimed" }),
      scene(2, { status: "error", video_url: "/old.mp4" }),
      scene(3),
      scene(4, { reference_image_url: "/still.jpg", status: "image_ready" }),
    ]), models, []);
    expect(view.running.map((s) => s.id)).toEqual([1]);
    expect(view.stills.map((s) => s.id)).toEqual([3]);
    expect(view.videos.map((s) => s.id)).toEqual([4]);
    expect(view.attention.map((s) => s.id)).toEqual([2]);
  });

  it("requires the previous final frame even if a chained scene has its own still", () => {
    const p = project([scene(1), scene(2, { chain_from_prev: true, reference_image_url: "/still.jpg" })]);
    expect(videoReadinessReason(p.scenes![1], p, models)).toContain("Generate scene #1 first");
    expect(generationView(p, models, []).videos).toHaveLength(0);
  });

  it("accepts character mode without a still when the named portrait is ready", () => {
    const p = project([scene(1, { video_reference_mode: "character", description: "Al walks" })]);
    p.characters = [{ id: 1, project_id: 1, name: "Al", description: "", reference_image_url: "/al.jpg", created_at: "" }];
    expect(generationView(p, models, []).stills).toHaveLength(0);
    expect(generationView(p, models, []).videos).toHaveLength(1);
  });

  it("offers provider resume even if the old inputs or selected model no longer work", () => {
    const p = project([scene(1, { video_model: "removed", status: "error" })]);
    const jobs = [{ id: 1, scene_id: 1, provider: "openrouter", job_type: "video", status: "running", external_id: "paid-job" }] as GenerationJob[];
    const view = generationView(p, models, jobs);
    expect(view.stills).toHaveLength(0);
    expect(view.videos.map((s) => s.id)).toEqual([1]);
  });

  it("keeps failed replacement renders discoverable alongside their previous successful clip", () => {
    const p = project([scene(1, { status: "done", video_url: "/previous.mp4", error_message: "Connection interrupted" })]);
    const jobs = [{ id: 1, scene_id: 1, provider: "fal", job_type: "video", status: "running", external_id: "paid-job" }] as GenerationJob[];
    const view = generationView(p, models, jobs);
    expect(view.complete).toHaveLength(1);
    expect(view.attention).toHaveLength(1);
    expect(view.videos).toHaveLength(1);
  });

  it("offers stills when frame-based audio overrides a saved character-reference preference", () => {
    const audioModels = { ...models, video: { test: { ...models.video.test, supports_audio_input: true, audio_input_mode: "wan_i2v" as const } } };
    const p = project([scene(1, { video_reference_mode: "character", audio_sync_enabled: true })]);
    expect(generationView(p, audioModels, []).stills).toHaveLength(1);
  });

  it("offers a replacement for a stale video while keeping the original preview", () => {
    const previous = scene(1, { status: "done", video_url: "/previous.mp4", reference_image_url: "/still.jpg", video_timing_stale: true });
    const view = generationView(project([previous]), models, []);
    expect(view.complete).toHaveLength(0);
    expect(view.attention.map((item) => item.id)).toEqual([1]);
    expect(view.videos.map((item) => item.id)).toEqual([1]);
    expect(view.videos[0].video_url).toBe("/previous.mp4");
    expect(view.stills).toHaveLength(0);
  });

  it("does not enqueue a stale video until its required still exists", () => {
    const previous = scene(1, { video_url: "/previous.mp4", video_timing_stale: true });
    const view = generationView(project([previous]), models, []);
    expect(view.stills.map((item) => item.id)).toEqual([1]);
    expect(view.videos).toHaveLength(0);
  });

  it("waits for the previous scene's new render before chaining from a stale clip", () => {
    const p = project([
      scene(1, { video_url: "/old.mp4", extracted_last_frame_url: "/old-last.jpg", video_timing_stale: true }),
      scene(2, { chain_from_prev: true }),
    ]);
    expect(videoReadinessReason(p.scenes![1], p, models)).toContain("Regenerate scene #1");
    expect(generationView(p, models, []).videos).toHaveLength(0);
  });
});

describe("audio-driven scene readiness", () => {
  const audioModels = {
    ...models,
    video: {
      wan: {
        ...models.video.test, name: "Wan 2.7", durations: [5, 10], audio_durations: [5, 10, 15],
        resolutions: ["720p", "1080p"], audio_resolutions: ["720p", "1080p"],
        aspects: ["16:9", "9:16"], supports_audio_input: true, audio_input_mode: "wan_i2v" as const,
      },
      ltx: {
        ...models.video.test, name: "LTX 2.5 Fast", durations: [5, 10, 15, 20],
        resolutions: ["1080p"], aspects: ["16:9", "9:16"], requires_audio_input: true,
        supports_audio_input: true, supports_reference_images: false, audio_input_mode: "ltx_a2v" as const,
      },
      seedance: {
        ...models.video.test, supports_audio_input: true, audio_input_mode: "seedance_r2v" as const,
      },
      wan3: {
        ...models.video.test, name: "Wan 3.0", durations: [5, 15, 30], audio_durations: [5, 15],
        resolutions: ["480p", "720p", "1080p"], aspects: ["16:9", "9:16"],
        supports_audio_input: true, audio_input_mode: "wan_r2v" as const,
      },
    },
  };
  const withSong = (scenes: Scene[]): Project => ({
    ...project(scenes),
    songs: [{ id: 1, project_id: 1, title: "Track", source: "upload", status: "ready", file_url: "/song.mp3", created_at: "" }],
    characters: [{ id: 1, project_id: 1, name: "Al", description: "", reference_image_url: "/al.jpg", created_at: "" }],
  });

  it("accepts a 15-second Wan scene only when its audio route is active", () => {
    const s = scene(1, { video_model: "wan", audio_end: 15, reference_image_url: "/first.jpg" });
    const p = withSong([s]);
    expect(videoReadinessReason(s, p, audioModels)).toContain("cannot render 15s");
    expect(videoReadinessReason({ ...s, audio_sync_enabled: true }, p, audioModels)).toBeNull();
  });

  it("allows Wan 3 song input with portraits while enforcing its shorter audio limit", () => {
    const s = scene(1, { video_model: "wan3", audio_end: 15, description: "Al sings", audio_sync_enabled: true });
    expect(videoReadinessReason(s, withSong([s]), audioModels)).toBeNull();
    expect(videoReadinessReason(s, project([s]), audioModels)).toContain("Add a song file");
    const longer = { ...s, audio_end: 30, reference_image_url: "/scene.jpg" };
    expect(videoReadinessReason(longer, withSong([longer]), audioModels)).toContain("cannot render 30s");
    expect(videoReadinessReason({ ...longer, audio_sync_enabled: false }, withSong([longer]), audioModels)).toBeNull();
  });

  it("requires the song for LTX even if a saved scene has its audio flag off", () => {
    const s = scene(1, { video_model: "ltx", audio_end: 15, resolution: "1080p", reference_image_url: "/first.jpg", audio_sync_enabled: false });
    expect(videoReadinessReason(s, project([s]), audioModels)).toContain("Add a song file");
    expect(videoReadinessReason(s, withSong([s]), audioModels)).toBeNull();
  });

  it("requires a scene first frame for LTX even when a named portrait is ready", () => {
    const s = scene(1, { video_model: "ltx", resolution: "1080p", description: "Al sings", video_reference_mode: "character" });
    const p = withSong([s]);
    expect(videoReadinessReason(s, p, audioModels)).toContain("Generate and review the first frame");
    expect(generationView(p, audioModels, []).stills.map((item) => item.id)).toEqual([1]);
    expect(generationView(p, audioModels, []).videos).toHaveLength(0);
    const seedanceScene = { ...s, video_model: "seedance", audio_sync_enabled: true };
    expect(videoReadinessReason(seedanceScene, withSong([seedanceScene]), audioModels)).toBeNull();
  });

  it("requires the previous rendered final frame for chained LTX scenes", () => {
    const s = scene(2, { video_model: "ltx", resolution: "1080p", description: "Al sings", chain_from_prev: true, reference_image_url: "/own-still.jpg" });
    const p = withSong([scene(1), s]);
    expect(videoReadinessReason(s, p, audioModels)).toContain("Generate scene #1 first");
    p.scenes![0].extracted_last_frame_url = "/previous-last.jpg";
    expect(videoReadinessReason(s, p, audioModels)).toBeNull();
  });

  it("blocks unsupported LTX resolution and aspect before offering generation", () => {
    const s = scene(1, { video_model: "ltx", reference_image_url: "/first.jpg" });
    expect(videoReadinessReason(s, withSong([s]), audioModels)).toContain("supported resolution");
    const square = { ...withSong([s]), aspect_ratio: "1:1" };
    expect(videoReadinessReason({ ...s, resolution: "1080p" }, square, audioModels)).toContain("1:1 aspect ratio");
  });
});
