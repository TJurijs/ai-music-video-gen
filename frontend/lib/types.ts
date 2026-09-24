export interface Project {
  id: number;
  name: string;
  description?: string;
  style?: string;
  aspect_ratio: string;
  // Persistent narrative seed — set by auto-plan, reused by AI Expand to
  // keep per-scene prompts anchored to the same story direction.
  story_seed?: string;
  target_scene_duration?: number | null;
  created_at: string;
  updated_at: string;
  song_count?: number;
  scene_count?: number;
  scenes_done?: number;
  songs?: Song[];
  scenes?: Scene[];
  characters?: Character[];
}

export interface Song {
  id: number;
  project_id: number;
  title: string;
  artist?: string;
  source: "suno" | "upload";
  file_path?: string;
  file_url?: string;
  duration?: number;
  bpm?: number;
  key?: string;
  lyrics?: string;
  transcription_json?: string;
  beats_json?: string;
  sections_json?: string;
  theme_analysis?: string;
  status: "pending" | "generating" | "analyzing" | "ready" | "error";
  error_message?: string | null;
  created_at: string;
}

export interface ThemeAnalysis {
  theme?: string;
  narrative?: string;
  mood?: string;
  characters_in_lyrics?: string[];
  visual_world?: string;
  suggested_visual_style?: string;
}

export interface TranscriptionWord {
  word: string;
  start: number;
  end: number;
  confidence: number;
}

export interface Section {
  start: number;
  end: number;
  label: string;
}

export interface CharacterPortrait {
  id: number;
  character_id: number;
  model_used?: string;
  cost_usd: number;
  is_active: boolean;
  created_at: string;
  url: string;
  // Snapshot of the character description that was current when this
  // portrait variant was created. Activating the variant restores this
  // onto the parent Character.description so AI Expand stays in sync.
  description?: string | null;
}

export interface Character {
  id: number;
  project_id: number;
  name: string;
  description: string;
  reference_image_path?: string;
  reference_image_url?: string;
  trigger_word?: string;
  portrait_status?: "idle" | "generating" | "done" | "error";
  portrait_error?: string | null;
  portrait_model?: string | null;
  portraits?: CharacterPortrait[];
  created_at: string;
}

export type SceneStatus =
  | "pending"
  | "generating_image"
  | "image_ready"
  | "generating_video"
  | "done"
  | "error"
  | "cancelled";

export type GenerationPhase = "image" | "video" | "all";

export interface SceneAsset {
  id: number;
  scene_id: number;
  asset_type: "image" | "video";
  model_used?: string;
  cost_usd: number;
  cost_detail?: string;
  metadata_json?: string;
  is_active: boolean;
  created_at: string;
  url: string;
}

export interface ScenePromptVersion {
  id: number;
  scene_id: number;
  prompt_type: "image" | "video";
  text: string;
  source: "plan" | "expand" | "soften" | "manual";
  cost_usd: number;
  is_active: boolean;
  created_at: string;
}

export interface Scene {
  video_timing_stale?: boolean;
  id: number;
  project_id: number;
  order: number;
  audio_start: number;
  audio_end: number;
  duration: number;
  lyrics_segment?: string;
  description?: string;
  video_prompt?: string;
  image_prompt?: string;
  reference_image_path?: string;
  reference_image_url?: string;
  video_path?: string;
  video_url?: string;
  video_model: string;
  image_model: string;
  resolution: string;
  align_to_beats: boolean;
  prompts_expanded: boolean;
  // Send this scene's song segment through the model's fal audio route.
  // Depending on audio_input_mode, it uses a first-frame anchor or image
  // references. Audio input is guidance, not a guarantee of lip sync.
  audio_sync_enabled?: boolean;
  // Exact first/last frame conditioning and separate character references
  // are mutually exclusive OpenRouter modes.
  video_reference_mode?: "frame" | "character";
  // Scene chaining: when on, video gen uses the PREVIOUS scene's extracted
  // last frame as this scene's first_frame for pixel-perfect seams.
  chain_from_prev?: boolean;
  // Populated after every video gen — the URL of the JPG we extracted from
  // the rendered video's final frame, for the NEXT scene to chain from.
  extracted_last_frame_path?: string;
  extracted_last_frame_url?: string;
  prompt_versions?: ScenePromptVersion[];
  status: SceneStatus;
  error_message?: string;
  cancel_requested?: boolean;
  generation_run_id?: string | null;
  generation_phase?: GenerationPhase | null;
  generation_requested_at?: string | null;
  assets?: SceneAsset[];
  created_at: string;
}

export type JobType = "image" | "video" | "music" | "transcription" | "assembly";
export type JobStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface GenerationJob {
  id: number;
  project_id: number;
  scene_id?: number;
  job_type: JobType | "llm_plan" | "llm_expand";
  provider: string;
  external_id?: string;
  status: JobStatus;
  result_url?: string;
  result_path?: string;
  error?: string;
  cost_usd: number;
  cost_detail?: string;
  request_json?: string;
  created_at: string;
  completed_at?: string;
}

export interface SceneGenerationPreflight {
  resuming?: boolean;
  resumable_job_id?: number | null;
  scene_id: number;
  scene_order: number;
  ready: boolean;
  errors: string[];
  warnings: string[];
  provider?: "openrouter" | "fal" | null;
  route?: string | null;
  will_generate_image: boolean;
  estimated_image_cost: number;
  estimated_video_cost: number;
  estimated_cost: number;
}

export interface GenerationPreflight {
  phase: GenerationPhase;
  scene_count: number;
  ready_count: number;
  estimated_cost: number;
  scenes: SceneGenerationPreflight[];
}

export interface ProjectCosts {
  total_usd: number;
  by_type: Record<string, number>;
  by_provider: Record<string, number>;
  by_scene: Record<number, number>;
  job_count: number;
}

export type VideoTier = "debug" | "cheap" | "mid" | "premium";

export interface VideoModel {
  history?: {
    attempts: number; submitted: number; provider_attempts: number; completed: number;
    failed: number; pending: number; policy_rejections: number; possible_policy_rejections: number;
    active_scenes: number; local_edits: number; video_assets: number; unique_scenes: number;
    projects: { id: number; name: string }[]; evidence_note: string;
    guardrail_status: "rejections_observed" | "possible_rejections" | "none_observed" | "no_history";
    routes?: { provider: string; route: string; attempts: number; completed: number; policy_rejections: number }[];
  };
  available?: boolean;
  unavailable_reason?: string;
  name: string;
  model_id: string;
  // Seedance and Wan 3 accept song plus image/character references via R2V.
  fal_r2v_model_id?: string;
  // Wan and LTX use first-frame input plus driving audio through fal.
  fal_audio_model_id?: string;
  provider?: "openrouter" | "fal";
  requires_audio_input?: boolean;
  audio_input_mode?: "seedance_r2v" | "wan_r2v" | "wan_i2v" | "ltx_a2v";
  audio_durations?: number[];
  audio_aspects?: string[];
  audio_resolutions?: string[];
  // fal reference-audio route pricing in USD per output second. This is a
  // separate SKU from the normal OpenRouter pricing matrix below.
  audio_pricing?: Record<string, number>;
  tier: VideoTier;
  tagline: string;
  durations: number[];
  resolutions: string[];
  aspects: string[];
  supports_first_frame: boolean;
  supports_last_frame: boolean;
  supports_reference_images: boolean;
  // True if `audio_sync_enabled` on a scene with this model takes effect.
  // Mirrors backend config's supports_audio_input — drives the mic toggle
  // visibility on the scene row.
  supports_audio_input?: boolean;
  // Reference audio is different from model-generated sound. "app" means
  // the Generate Scenes flow can send the song segment today; "provider"
  // means the upstream model supports it but this app has not wired it yet.
  audio_reference_support?: "app" | "provider" | "none";
  audio_note?: string;
  face_guardrail?: "low" | "medium" | "high";
  face_guardrail_note?: string;
  reference_note?: string;
  // Pricing matrix kept as { with_audio, without_audio } for backward compat
  // with existing OpenRouter pricing_skus snapshots. We always pay the
  // without_audio rate on the OpenRouter route (audio is muxed at
  // assembly). The fal R2V route has its own pricing — see
  // backend/app/services/pricing.py video_cost_fal_seedance_r2v.
  pricing: Record<string, { with_audio: number; without_audio: number }>;
  max_duration: number;
  note?: string;
}

export interface ImageModel {
  available?: boolean;
  unavailable_reason?: string;
  name: string;
  model_id: string;
  price_per_image: number;
  supports_reference_images?: boolean;
  note?: string;
}

export interface LLMModel {
  available?: boolean;
  unavailable_reason?: string;
  name: string;
  model_id: string;
  tier?: "cheap" | "mid" | "premium";
  note?: string;
}

export interface ModelsConfig {
  verified_at?: string;
  video: Record<string, VideoModel>;
  image: Record<string, ImageModel>;
  llm: Record<string, LLMModel>;
}
