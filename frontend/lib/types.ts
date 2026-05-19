export interface Project {
  id: number;
  name: string;
  description?: string;
  style?: string;
  aspect_ratio: string;
  // Persistent narrative seed — set by auto-plan, reused by AI Expand to
  // keep per-scene prompts anchored to the same story direction.
  story_seed?: string;
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
  source: "lyria" | "suno" | "upload";
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
  status: "pending" | "analyzing" | "ready" | "error";
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
  // Audio-sync (fal Seedance reference-to-video). When true AND the
  // chosen video_model has supports_audio_input=true, the backend
  // routes through fal R2V instead of OpenRouter I2V: scene's audio
  // window is sliced from the song and sent with character refs; no
  // first_frame is used in this mode. No-op on other models.
  audio_sync_enabled?: boolean;
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
  assets?: SceneAsset[];
  created_at: string;
}

export type JobType = "image" | "video" | "music" | "transcription";
export type JobStatus = "pending" | "running" | "completed" | "failed";

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
  created_at: string;
  completed_at?: string;
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
  name: string;
  model_id: string;
  // When set, the audio-sync toggle on a scene with this model becomes
  // available: backend routes video gen through fal's R2V endpoint (the
  // path that accepts an audio reference + character refs and rejects
  // first_frame). Only set on Seedance variants today.
  fal_r2v_model_id?: string;
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
  name: string;
  model_id: string;
  price_per_image: number;
  supports_reference_images?: boolean;
  note?: string;
}

export interface LLMModel {
  name: string;
  model_id: string;
  tier?: "cheap" | "mid" | "premium";
  note?: string;
}

export interface ModelsConfig {
  video: Record<string, VideoModel>;
  image: Record<string, ImageModel>;
  llm: Record<string, LLMModel>;
}
