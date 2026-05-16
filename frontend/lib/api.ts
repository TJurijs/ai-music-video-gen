import type {
  Project, Song, Scene, SceneAsset, Character, GenerationJob, ModelsConfig, ProjectCosts,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...init?.headers },
      ...init,
    });
  } catch (networkErr) {
    // Browser fetch threw — usually means network is gone or the page was
    // closed mid-request. Rare; surface as-is for the toast.
    throw new Error(
      `Network error: ${(networkErr as Error).message || "request did not complete"}. ` +
      `The backend may be restarting — retry in a moment.`
    );
  }
  if (!res.ok) {
    // FastAPI returns errors as `{"detail": "..."}` — extract that so toasts
    // show the actionable message instead of the raw JSON wrapper. Falls
    // back to the body as-is for non-JSON / non-standard error shapes.
    const raw = await res.text();
    let msg = raw;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.detail === "string") {
        msg = parsed.detail;
      } else if (parsed && Array.isArray(parsed.detail)) {
        // FastAPI validation errors come as an array of issue objects.
        msg = parsed.detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
      }
    } catch {
      // raw wasn't JSON — leave msg as the raw text
    }
    // The Next.js dev proxy returns a generic "Internal Server Error" body
    // when upstream (FastAPI on :8010) is unreachable — typically during a
    // backend restart. The opaque "500: Internal Server Error" toast that
    // surfaces from that case is the most-confused-about UX in the studio,
    // so translate it into something actionable.
    if (res.status === 500 && /^internal server error$/i.test(msg.trim())) {
      throw new Error(
        "Backend unreachable (likely restarting). Your request didn't reach the server, so nothing changed. Try again in a moment."
      );
    }
    throw new Error(`${res.status}: ${msg}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------
export const api = {
  projects: {
    list: () => request<Project[]>("/projects"),
    get: (id: number) => request<Project>(`/projects/${id}`),
    create: (data: { name: string; description?: string; style?: string; aspect_ratio?: string }) =>
      request<Project>("/projects", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Project>) =>
      request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    delete: (id: number) => request<void>(`/projects/${id}`, { method: "DELETE" }),
    expandStyle: (style: string, llm_model?: string) =>
      request<{ expanded: string }>("/projects/expand-style", {
        method: "POST",
        body: JSON.stringify({ style, llm_model }),
      }),
    addCharacter: (projectId: number, data: { name: string; description: string; trigger_word?: string }) =>
      request<Character>(`/projects/${projectId}/characters`, { method: "POST", body: JSON.stringify(data) }),
    updateCharacter: (projectId: number, charId: number, data: { name?: string; description?: string; trigger_word?: string }) =>
      request<Character>(`/projects/${projectId}/characters/${charId}`, { method: "PATCH", body: JSON.stringify(data) }),
    deleteCharacter: (projectId: number, charId: number) =>
      request<void>(`/projects/${projectId}/characters/${charId}`, { method: "DELETE" }),
    uploadCharacterImage: async (projectId: number, charId: number, file: File): Promise<Character> => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${BASE}/projects/${projectId}/characters/${charId}/image`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    generateCharacterPortrait: (projectId: number, charId: number, image_model = "gemini-flash-image") =>
      request<{ message: string; character_id: number }>(`/projects/${projectId}/characters/${charId}/portrait`, {
        method: "POST",
        body: JSON.stringify({ image_model }),
      }),
    suggestCharacters: (projectId: number, data: { visual_style?: string; count?: number }) =>
      request<{ characters: Character[]; visual_style_used: string }>(
        `/projects/${projectId}/characters/suggest`,
        { method: "POST", body: JSON.stringify(data) },
      ),
    listPortraits: (projectId: number, charId: number) =>
      request<import("./types").CharacterPortrait[]>(
        `/projects/${projectId}/characters/${charId}/portraits`,
      ),
    activatePortrait: (projectId: number, charId: number, assetId: number) =>
      request<Character>(
        `/projects/${projectId}/characters/${charId}/portraits/${assetId}/activate`,
        { method: "POST" },
      ),
    deletePortrait: (projectId: number, charId: number, assetId: number) =>
      request<void>(
        `/projects/${projectId}/characters/${charId}/portraits/${assetId}`,
        { method: "DELETE" },
      ),
    updatePortraitDescription: (projectId: number, charId: number, assetId: number, description: string) =>
      request<import("./types").CharacterPortrait>(
        `/projects/${projectId}/characters/${charId}/portraits/${assetId}`,
        { method: "PATCH", body: JSON.stringify({ description }) },
      ),
    expandCharacter: (projectId: number, charId: number, llm_model?: string) =>
      request<Character>(
        `/projects/${projectId}/characters/${charId}/expand`,
        { method: "POST", body: JSON.stringify({ llm_model }) },
      ),
    regenerateCharacter: (projectId: number, charId: number, llm_model?: string) =>
      request<Character>(
        `/projects/${projectId}/characters/${charId}/regenerate`,
        { method: "POST", body: JSON.stringify({ llm_model }) },
      ),
  },

  // ---------------------------------------------------------------------------
  // Songs
  // ---------------------------------------------------------------------------
  songs: {
    get: (id: number) => request<Song>(`/songs/${id}`),
    delete: (id: number) => request<void>(`/songs/${id}`, { method: "DELETE" }),
    upload: async (projectId: number, title: string, artist: string, file: File): Promise<Song> => {
      const form = new FormData();
      form.append("file", file);
      const params = new URLSearchParams({ project_id: String(projectId), title, artist });
      const res = await fetch(`${BASE}/songs/upload?${params}`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    generate: (data: {
      project_id: number;
      title?: string;
      artist?: string;
      description: string;
      style_tags?: string;
      lyrics?: string;
      instrumental?: boolean;
      source: "lyria" | "suno";
    }) => request<Song>("/songs/generate", { method: "POST", body: JSON.stringify(data) }),
  },

  // ---------------------------------------------------------------------------
  // Scenes
  // ---------------------------------------------------------------------------
  scenes: {
    list: (projectId: number) => request<Scene[]>(`/scenes?project_id=${projectId}`),
    get: (id: number) => request<Scene>(`/scenes/${id}`),
    create: (data: Partial<Scene> & { project_id: number; order: number; audio_start: number; audio_end: number }) =>
      request<Scene>("/scenes", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Scene>) =>
      request<Scene>(`/scenes/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    delete: (id: number) => request<void>(`/scenes/${id}`, { method: "DELETE" }),
    deleteAll: (projectId: number) =>
      request<{ deleted: number }>(`/scenes?project_id=${projectId}`, { method: "DELETE" }),
    expandPrompts: (id: number, llm_model?: string) =>
      request<Scene>(`/scenes/${id}/expand-prompts`, {
        method: "POST",
        body: JSON.stringify({ llm_model }),
      }),
    // Vision-grounded continuation prompt for a chained scene. Uses the
    // PREV scene's actual rendered last frame as visual context, so the
    // generated video_prompt describes motion that picks up from exactly
    // where the previous clip ended. Requires: chain_from_prev=true on this
    // scene, AND the prev scene's video has been rendered (so the extracted
    // last frame exists on disk). Otherwise returns 400 with an actionable
    // message — surface it to the user verbatim.
    generateContinuationPrompt: (id: number, llm_model?: string) =>
      request<Scene>(`/scenes/${id}/continuation-prompt`, {
        method: "POST",
        body: JSON.stringify({ llm_model }),
      }),
    // Ensure scene N+1 exists AND is chained from scene N. Creates the next
    // scene if missing (with empty prompts, inherited model settings), or
    // flips `chain_from_prev` on if it already exists. Returns the next
    // scene's full data so callers can navigate to it / focus its row.
    chainToNext: (sceneId: number) =>
      request<Scene>(`/scenes/${sceneId}/chain-next`, { method: "POST" }),
    generateBatch: (data: {
      project_id: number;
      song_id: number;
      target_scene_duration?: number;
      llm_model?: string;
      story_seed?: string;
      start_index?: number;
      batch_size?: number;
    }) =>
      request<{
        batch_scenes: Scene[];
        scenes_so_far: number;
        total_planned: number;
        has_more: boolean;
        next_start_index: number | null;
      }>("/scenes/generate-batch", { method: "POST", body: JSON.stringify(data) }),
    clear: (id: number) =>
      request<{ scene_id: number; assets_removed: number; files_deleted: number }>(
        `/scenes/${id}/clear`,
        { method: "POST" },
      ),
    softenPrompt: (id: number, field: "video_prompt" | "image_prompt", llm_model?: string) =>
      request<Scene>(`/scenes/${id}/soften-prompt`, {
        method: "POST",
        body: JSON.stringify({ field, llm_model }),
      }),
    listPrompts: (id: number, prompt_type?: "image" | "video") =>
      request<import("./types").ScenePromptVersion[]>(
        `/scenes/${id}/prompts${prompt_type ? `?prompt_type=${prompt_type}` : ""}`,
      ),
    activatePrompt: (id: number, version_id: number) =>
      request<Scene>(`/scenes/${id}/prompts/${version_id}/activate`, { method: "POST" }),
    deletePrompt: (id: number, version_id: number) =>
      request<void>(`/scenes/${id}/prompts/${version_id}`, { method: "DELETE" }),
    listAssets: (id: number) => request<SceneAsset[]>(`/scenes/${id}/assets`),
    activateAsset: (sceneId: number, assetId: number) =>
      request<Scene>(`/scenes/${sceneId}/assets/${assetId}/activate`, { method: "POST" }),
    deleteAsset: (sceneId: number, assetId: number) =>
      request<void>(`/scenes/${sceneId}/assets/${assetId}`, { method: "DELETE" }),
    // Download URLs — straight static endpoints, set window.location.href to trigger
    // the browser's save dialog. Returning the URL keeps the call site simple.
    firstFrameUrl: (sceneId: number) => `/api${`/scenes/${sceneId}/first-frame`}`,
    audioChunkUrl: (sceneId: number) => `/api${`/scenes/${sceneId}/audio-chunk`}`,
    uploadVideo: async (sceneId: number, file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`/api/scenes/${sceneId}/upload-video`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const raw = await res.text();
        let msg = raw;
        try {
          const parsed = JSON.parse(raw);
          if (parsed?.detail) msg = parsed.detail;
        } catch {}
        throw new Error(`${res.status}: ${msg}`);
      }
      return res.json() as Promise<{ scene_id: number; asset_id: number; file_path: string }>;
    },
  },

  // ---------------------------------------------------------------------------
  // Generation
  // ---------------------------------------------------------------------------
  generation: {
    generateScene: (sceneId: number, force = false, phase: "image" | "video" | "all" = "all") =>
      request<{ message: string; scene_id: number; phase: string }>("/generation/scene", {
        method: "POST",
        body: JSON.stringify({ scene_id: sceneId, force, phase }),
      }),
    generateBatch: (projectId: number, sceneIds?: number[], force = false, phase: "image" | "video" | "all" = "all") =>
      request<{ message: string; scene_ids: number[]; phase: string }>("/generation/batch", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, scene_ids: sceneIds, force, phase }),
      }),
    cancelScene: (sceneId: number) =>
      request<{ message: string; scene_id: number }>(`/generation/scene/${sceneId}/cancel`, { method: "POST" }),
    assemble: (projectId: number) =>
      request<{ message: string; job_id: number }>(`/generation/assemble/${projectId}`, { method: "POST" }),
    assembleStatus: (projectId: number) =>
      request<{
        status: "none" | "running" | "completed" | "failed";
        url: string | null;
        error?: string | null;
        started_at?: string | null;
        completed_at?: string | null;
        job_id?: number;
      }>(`/generation/assemble/${projectId}/status`),
    getJobs: (projectId: number) => request<GenerationJob[]>(`/generation/jobs/${projectId}`),
    getStatus: (projectId: number) =>
      request<{ total: number; by_status: Record<string, number>; complete_pct: number }>(
        `/generation/status/${projectId}`
      ),
    getCosts: (projectId: number) => request<ProjectCosts>(`/generation/costs/${projectId}`),
  },

  // ---------------------------------------------------------------------------
  // Models config
  // ---------------------------------------------------------------------------
  models: {
    list: () => request<ModelsConfig>("/models"),
  },
};
