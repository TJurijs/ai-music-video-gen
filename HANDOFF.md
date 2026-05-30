# Agent handoff — pick up this project here

This is the knowledge-transfer doc for whoever takes over next.

**Read order:**
1. This file (gives you the current state in <10 min)
2. [`CLAUDE.md`](./CLAUDE.md) — the long-form conventions, file map, and "patterns to avoid" rules
3. [`README.md`](./README.md) — user-facing setup + what the app does
4. `git log --oneline` — last ~15 commits tell the story of the most recent changes

If anything in this file conflicts with `CLAUDE.md`, **`CLAUDE.md` wins** — it's the canonical reference. This file is just a faster on-ramp.

---

## The 60-second mental model

User uploads a song → backend analyses it (beats, sections, lyrics, theme via LLM) → user sees a multi-step "cells" UI in Next.js → LLM plans scenes → user clicks per-scene buttons to render a still then a video clip → ffmpeg assembles everything into one MP4 with the song muxed.

**Two video routes, per scene:**

| Route | When | Endpoint | Cost (8s @ 720p) |
|---|---|---|---|
| **OpenRouter image-to-video** (default) | every non-audio-sync scene | `/api/v1/videos` (OpenRouter) | ~$0.40 (Seedance), ~$1.34 (Kling), ~$1.60 (Veo) |
| **fal Seedance reference-to-video** (opt-in via mic icon) | scene uses Seedance + user wants audio sync | `queue.fal.run/bytedance/seedance-2.0/reference-to-video` (or `/fast/`) | ~$2.40 (standard) / ~$1.20 (fast) |

The fal R2V path does NOT accept a `first_frame`. Identity comes entirely from character portrait `image_urls`. We also pass the scene's generated still as another `image_url` ref. Audio is sliced from the song and trimmed ~150ms under video duration (fal rejects audio >= video duration).

---

## What the latest session shipped (most recent → oldest)

Latest commit is at the top. `git show <hash>` for full detail; this is just orientation.

| Hash | Headline | What it does |
|---|---|---|
| `7660081` | Dismiss-error button | `POST /scenes/{id}/dismiss-error` + X button on `SceneErrorBanner`. Clears `error_message` + resets `status` based on what's actually on disk. Doesn't touch assets. |
| `637ea26` | Character-ref matcher checks image_prompt too | `_find_character_references` (backend) and `DescriptionWithPromptTooltip` / `CharacterRefsBadge` (frontend) all now search `video_prompt + image_prompt + description` and match full name OR any single token. Surfaced when user wrote "the trio" in video_prompt — names only existed in image_prompt → ZERO refs were passed. |
| `01fb1cb` | Assembly freeze fix | Switched from ffmpeg concat **demuxer** (fragile, freezes on spec mismatch) to concat **filter** with per-clip `scale+pad+setsar+fps+format` normalization. Picks lowest common dims via ffprobe. |
| `1a7abc2` | Image/video prompt alignment | Added explicit "image_prompt and video_prompt MUST AGREE" sections to `SCENE_PLAN_SYSTEM` and `generate_scene_prompts` — 7-point consistency checklist (setting, time, weather, cast, wardrobe, pose, framing, props). |
| `db6e42e` | Audio-sync duration instrumentation | `_probe_duration` helper + audio-slice warnings + saves `rendered_duration` / `audio_duration` into asset metadata so "asked 15, got 9" cases are diagnosable. |
| `18665d9` | Audio-sync passes still as ref too | `_generate_video_fal_seedance_audio` adds scene's generated still (or chained prev frame) into `image_urls` alongside character portraits. fal R2V REQUIRES at least one image_url. |
| `7c29945` | Fix fal R2V endpoint slugs + field names | Path uses `/fast/` not `-fast`. Fields are `image_urls` (plural) + `audio_urls` (plural list), not `reference_image_urls` / `audio_url`. Also dropped supports_audio_input from Seedance 1.5 (fal doesn't publish 1.5). |
| `826c0cc` | Audio-sync re-introduced via Seedance R2V | Brought back `audio_sync_enabled` field + fal R2V route. Mic toggle on each Seedance scene row. The OmniHuman / post-process lipsync paths stay retired. |
| `0f1ce29` | Migration drops retired columns | Startup `_apply_schema_migrations` now ALSO drops retired columns (lipsync_model etc.) — was needed because SQLite enforces NOT NULL on INSERT even for orphan columns. |
| `2b0c06a` | v1 finalization | Stripped ALL dead code from v0 — 5 frontend components deleted, 2 backend endpoints (auto-plan, expand-all) removed, fal_client trimmed to whisper-only at the time. |

---

## Current state — what works, what's flaky

### Works solidly

- Single-scene generation flow (Img → Vid) on OpenRouter for Seedance / Kling / Veo
- Chain icon → wand icon → Vid for iterative scene-by-scene building
- Vision-grounded continuation prompts (the wand) — feeds prev scene's actual rendered last frame to a multimodal LLM
- Story seed + arc-aware prompt continuation
- Assembly via the concat **filter** path (normalizes spec mismatches)
- Per-scene cost tracking via `GenerationJob` rows
- Lyric-driven scene planning (concrete imagery from lyrics_segment shows up in prompts)

### Works but flaky

- **Seedance audio-sync (fal R2V)** — works end-to-end but:
  - Hits fal's content filter on photoreal character portraits with `InputImageSensitiveContentDetected`. Recovery is to regenerate stylized portrait variants.
  - Audio-length-caps-video-duration is non-obvious. We log + save it in metadata now, but the user has to know to check.
  - Seedance 2.0 Fast caps at 10s natively — user picking it for a 15s scene silently gets a 10s clip. We have `_closest_supported` snapping that's visible in the cost detail string, but not super prominent in the UI.
- **Frame chaining** requires sequential generation — `chain_from_prev=true` reads prev's `extracted_last_frame_path`, which only exists after prev's video has rendered. Parallelizing chained scenes fails on each downstream one.

### Known limitations the user hasn't asked to fix yet

- **Audio overlay is master-from-0:00** during assembly (not per-scene slices). If the user skips scenes mid-song or some clips render shorter than requested, visuals drift out of sync with the song. Covered in detail in [the assembly-audio question](#user-questions-asked-but-not-yet-acted-on). User said they'd think about it.
- **Errored scenes block downstream chained scenes** structurally (chain needs prev's extracted last frame). Dismissing the error visually does NOT fix this — the dismiss tooltip is explicit about it.
- **Audio-sync needs FAL_API_KEY**. If not set, the route surfaces a clear error pointing to .env.

---

## File map quick-ref (for things you'll touch)

| If the user asks for… | Look in… |
|---|---|
| New video model | `backend/app/config.py` `VIDEO_MODELS` |
| Change how scenes are planned | `backend/app/services/scene_planner.py` (`SCENE_PLAN_SYSTEM`, `plan_scene_batch`) |
| Change the wand (continuation prompt) | `backend/app/services/scene_planner.py` `generate_continuation_prompts` |
| Per-scene render pipeline | `backend/app/services/generation_service.py` |
| Two video routes' branching | `_run_pipeline` in `generation_service.py` (audio-sync test + dispatch) |
| Assembly (ffmpeg) | `backend/app/services/assembly.py` |
| Pricing | `backend/app/services/pricing.py` |
| Storage URL helper (use it everywhere) | `backend/app/services/urls.py` |
| Tolerant LLM JSON parsing | `backend/app/services/llm_json.py` |
| Per-scene UI | `frontend/components/studio/cells/generate/SceneGenRow.tsx` |
| Scene description tooltip (what's sent to model) | `frontend/components/studio/cells/generate/DescriptionWithPromptTooltip.tsx` |
| Character refs badge | `frontend/components/studio/cells/generate/CharacterRefsBadge.tsx` |
| Error banner | `frontend/components/studio/cells/generate/SceneStatus.tsx` |
| Frontend API client | `frontend/lib/api.ts` |
| Shared types | `frontend/lib/types.ts` |
| Confirm dialog system | `frontend/components/ConfirmDialog.tsx` |

---

## User-questions asked but not yet acted on

These came up in the latest session, were answered with explanation, but the user didn't ask for implementation:

### "Audio in assembly is master overlay, not per-scene"

User confirmed they understood. Open question whether to implement per-scene audio slicing (option A in the answer: slice `song[audio_start..audio_end]` per scene → concat in order → mux). Would matter if:
- User skips scenes mid-song
- User's audio-sync clips render shorter than their scene window (Seedance Fast 10s cap, etc.)

If they ask about it, the answer is in `assemble_project` in `assembly.py`. Sketch: use the concat filter's `[a]` audio outputs too, build per-scene audio with `aselect`/`atrim` from the song input.

### "Duration drift warning could be a UI chip"

I instrumented backend logging + metadata, but didn't surface "scene rendered shorter than requested" in the UI. If user asks for this: read `metadata_json.rendered_duration` and `metadata_json.duration` from the active video asset on each scene row and show a small amber chip if they differ.

### "Surface model max-duration cap in UI before generation"

`VideoModelCard.tsx` already shows a `snaps {duration}→{snapped}` chip in the conflict case, but only inside the dropdown picker. User might want a more visible warning on the scene row itself when scene_window > model_max.

---

## How the user works (style + tone)

This is captured in `CLAUDE.md` but worth repeating:

- **Direct, technical writing**. No filler, no "I'd be happy to…".
- **Specific data over generalities**. "The LLM returned 16 words" beats "the LLM was concise."
- **Tables for comparisons.** Code blocks for exact changes.
- **"What I did + why + what to check"** is the structure they like for change summaries.
- **They want to know when you don't know**. Honest admissions of uncertainty beat invented confidence.
- **They use the multi-step UI a lot** — every change should integrate with the existing cells flow (`StepSongCell` → `StepCharactersCell` → `StepPlanCell` → `StepGenerateCell` → `StepAssembleCell`).
- **TodoWrite-style task tracking** is helpful when work spans >3 steps. The reminder system will nudge you; it's not nagging, just helpful.

---

## Verification ritual (run before declaring work done)

```bash
# Backend imports without error (catches most stupid things)
cd backend && .venv/bin/python -c "from app.main import app; print('ok')"

# Frontend type-check (must be silent)
cd frontend && npx tsc --noEmit

# One smoke test in the area you touched. Examples:
# - Touched the LLM JSON parser? Real generate-batch run, verify scenes appear.
# - Touched video gen flow? Generate one scene's image, verify URL is correct.
# - Touched the wand? Click it on a chained scene; verify prompt updates.
# - Touched the URL helper? GET /api/projects/{id}, verify URLs are absolute.
```

The user is fast to notice when "checks pass but it doesn't actually work." Always smoke-test the path you changed.

---

## Gotchas the next agent will hit (not in CLAUDE.md)

These are recent enough they're not in CLAUDE.md yet:

### 1. fal R2V slug format

`bytedance/seedance-2.0/reference-to-video` and `bytedance/seedance-2.0/fast/reference-to-video`. NOT `seedance-2.0-fast/...` (404s). NOT `fal-ai/bytedance/...` (404s for these specific paths).

Field names in the request body:
- `image_urls` (list, NOT `reference_image_urls`)
- `audio_urls` (list, NOT `audio_url`)
- `generate_audio: false` (we supply audio, don't let model add more)
- `duration: "10"` (string, not int — fal's enum is `"auto" | "4" .. "15"`)

### 2. Character-ref matcher exists in THREE places

Backend `_find_character_references` in `generation_service.py`, plus frontend `CharacterRefsBadge.tsx` and `DescriptionWithPromptTooltip.tsx` both reimplement the same heuristic. They all need to agree. Currently they all check:

```python
haystack = video_prompt + image_prompt + description
# Match if (full_name in haystack) OR (any single token in haystack)
```

If you change ONE matcher, change all three or the badge will lie to the user.

### 3. Storage URL cache-busting

`extracted_last_frame_path` is the ONLY file in the codebase that's mutated in place (always `scene_N_last.jpg`). Pass `cache_bust=True` to `to_storage_url()` when serving it — otherwise the browser shows the stale JPG forever. All other files use unique-timestamped filenames so they don't need this.

### 4. SceneAsset.metadata_json now carries duration drift info

The audio-sync path saves:
- `metadata.duration` — what we asked for
- `metadata.audio_duration` — actual ffprobe'd audio file
- `metadata.rendered_duration` — actual ffprobe'd output video

If you build any "this didn't render right" UI, those are the fields to read.

### 5. Migration adds AND drops columns

`_apply_schema_migrations` in `backend/app/main.py` has both `expected` (columns to add via ALTER TABLE ADD COLUMN) and `retired` (columns to drop via ALTER TABLE DROP COLUMN, SQLite 3.35+). Whenever you remove a field from the Scene model, move it to `retired` — otherwise existing user DBs get NOT NULL violations on INSERT.

---

## Patterns to NOT reintroduce

These were tried and removed. Don't bring them back without a strong reason:

- **OmniHuman** — no separate character ref slot, useless for multi-character scenes.
- **Post-process lipsync (LatentSync / MuseTalk / Wav2Lip)** — produces visible mouth artifacts on music vocals.
- **Frame chaining via the video model's `last_frame_path`** — model rarely lands pixel-perfect, visible seam discontinuity. Replaced with chain_from_prev using extracted last frame.
- **`/scenes/auto-plan` and `/scenes/expand-all`** — replaced by `/scenes/generate-batch` which produces fully-expanded scenes inline.
- **Per-clip motion-offset trim in assembly** — was solving a problem from the old chaining approach.
- **Hardcoded `http://localhost:8010/storage/...` strings** — use `to_storage_url()`.
- **Manual `is_active = True/False` loops** — use `services/versioning.py`.
- **`window.confirm()`** — use `useConfirm()`.

---

## Suggested first move

1. Get the dev servers running: `./start.sh` (creates the venv on first run, opens two terminals).
2. Open http://localhost:3000, create a test project, upload any short audio clip.
3. Walk through Song → Characters (AI Suggest is fine) → Plan ("Just scene 1") → Generate (Img then Vid on scene 1) → click chain on scene 1 to add scene 2, click wand on scene 2, click Vid on scene 2.
4. Try Assemble. Verify the final MP4 plays.
5. Now you've touched every part of the pipeline once. Whatever the user asks next, you'll have context for it.

---

## Open questions you might get asked

Based on the trajectory of this session, plausible next requests:

- "Surface duration drift / model cap warnings on the scene row" — implementation sketch in [User-questions asked but not acted on](#user-questions-asked-but-not-yet-acted-on).
- "Switch assembly audio to per-scene slicing" — same section.
- "Audio-sync still failing with InputImageSensitiveContentDetected" — answer is to restyle character portraits (less photoreal, more painterly). The portrait gen prompt is in `scene_planner.expand_character_description`.
- "I want a NEW model X" — add to `VIDEO_MODELS` or `IMAGE_MODELS` in `config.py`. Set the right capability flags. The UI picks it up automatically from `/api/models`.
- "Why is scene N+1 not rendering?" — almost always because it's chained from scene N and scene N has either errored or never been rendered. Check `prev_scene.extracted_last_frame_path`.

Good luck. The codebase is in a clean state. Don't be afraid to push back on the user if they ask for something that contradicts a "patterns to avoid" entry — they appreciate honest disagreement.
