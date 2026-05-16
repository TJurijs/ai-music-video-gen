# CLAUDE.md — Agent Instructions

This file is read automatically by Claude Code at the start of every
session. If you're an AI agent (Claude / Cursor / Aider / Cline) working
on this codebase, **read this end-to-end before making changes**.

---

## What this project is

An AI-driven music-video studio. User uploads a song → backend extracts
beats / sections / lyrics → LLM plans scenes → image model renders stills
→ video model animates them → ffmpeg assembles the final MP4 with the
song muxed in. **Purely visual pipeline** — lipsync was removed in v1.

**Stack:**
- **Backend:** FastAPI + SQLModel + SQLite (one .db file in repo root)
- **Frontend:** Next.js 14 App Router + React Query + TypeScript + Tailwind
- **AI provider:** OpenRouter for everything (video / image / LLM). fal.ai
  is optional and used only for word-level lyric transcription via
  fal-ai/whisper when `FAL_API_KEY` is set; otherwise OpenRouter
  transcription is used (no per-word timing).
- **Media tools:** ffmpeg + ffprobe (system binaries, must be on PATH)

---

## First-time-on-this-machine checklist

If this is the first time you (the agent) are working in this repo on
this machine, verify these are in place BEFORE making changes:

```bash
# 1. Python version — codebase needs 3.10+
python3 --version            # expect 3.10 or higher
# If too old on macOS:  brew install python@3.11

# 2. ffmpeg + ffprobe on PATH
ffmpeg -version | head -1
ffprobe -version | head -1
# If missing on macOS:  brew install ffmpeg

# 3. Node 18+
node --version

# 4. Backend venv exists with deps installed
ls backend/.venv/bin/python  # macOS / Linux
# If missing:  ./start.sh    (or manually: cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)

# 5. .env file with at least OPENROUTER_API_KEY
ls backend/.env || ls .env
# If missing:  cp backend/.env.example backend/.env  (then paste your key)
```

If ANY of those fails, fix it before touching code. The error messages
you'll get from running the wrong-version Python or missing-ffmpeg
environment are cryptic and waste time.

**Run any of these to confirm everything works end-to-end:**

```bash
# Backend imports without error
cd backend && backend/.venv/bin/python -c "from app.main import app; print('ok')"

# Frontend type-check
cd frontend && npx tsc --noEmit

# Full system: ./start.sh then visit localhost:3000 and create a project
```

## Run + verify changes

**Three ways to start the dev servers — all interchangeable:**

| Option | Command | Notes |
|---|---|---|
| Shell script (macOS/Linux) | `./start.sh` | Opens two Terminal windows. Creates venv on first run. |
| Batch file (Windows) | `start.bat` | Same idea, two cmd windows. |
| **Claude Code Preview** | open Preview panel, pick `backend` + `frontend` from `.claude/launch.json` | Cross-platform; logs render in the preview pane. |

If you're running Claude Code on macOS and using Preview mode, the
launch.json defaults to `python3` (Mac convention). On Windows, swap
to `python` — see the `_comment` field in `.claude/launch.json`.

**Manual** (if you want to avoid the helper scripts):
```bash
# Backend (port 8010)
cd backend
python3 -m uvicorn app.main:app --reload --port 8010 --timeout-graceful-shutdown 300

# Frontend (port 3000, proxies /api/* → :8010)
cd frontend
npm run dev
```

Either way: the first launch installs Python + npm deps (~2–3 min);
subsequent starts are instant.

**Always** run these checks before declaring work done:
```bash
# Backend can start
cd backend && .venv/bin/python -c "from app.main import app; print('ok')"

# Frontend type-check
cd frontend && npx tsc --noEmit
```

The user expects clean output from both. Don't ship code that fails either.

When testing in Claude Code Preview, you can:
- Watch backend logs via the `backend` preview tab
- Hit the running frontend via the `frontend` preview tab
- Use the `mcp__Claude_Preview__preview_*` tools to interact with the UI
  (click, fill, screenshot, console_logs, network) — these are pre-
  authorized in `.claude/settings.json`

---

## Code conventions — DO follow

### 1. Storage URLs go through `to_storage_url()`

Every response that returns a public URL for a file in `storage/` must use
the helper in `backend/app/services/urls.py`. Never hardcode `http://localhost:8010/...`.

```python
# ✅ Right
from app.services.urls import to_storage_url
return {"url": to_storage_url(asset.file_path)}

# ❌ Wrong
return {"url": f"http://localhost:8010/storage/{...}"}
```

The base URL is `settings.public_base_url` (env-configurable).

For files **mutated in place** (same filename, new bytes per regen — e.g.
`extracted_last_frame_path` which is always `scene_N_last.jpg`), pass
`cache_bust=True` to append an mtime query string. Otherwise browsers
hold the stale cached version after a regen.

### 2. LLM JSON parsing goes through `parse_llm_json()`

LLM responses are messy — markdown fences, prose wrappers, occasional
singleton-arrays-instead-of-objects. The helper in
`backend/app/services/llm_json.py` handles all of that.

```python
# ✅ Right
from app.services.llm_json import parse_llm_json
parsed = parse_llm_json(raw_response, context="My Feature")

# ❌ Wrong — re-inventing the tolerance logic per call site
parsed = json.loads(raw_response.strip("`json\n"))
if isinstance(parsed, list): parsed = parsed[0]
```

Use `expect="dict"` (default) or `expect="list"`. Errors include the first
300 chars of the raw response in the message — keep that pattern.

### 3. Versioned rows use `make_active()` / `delete_and_promote()`

Several tables share an "exactly one active row per scope" lifecycle:
`SceneAsset` (image/video per scene), `ScenePromptVersion` (image / video
prompt history), `CharacterAsset` (portrait versions). The helpers
in `backend/app/services/versioning.py` enforce the invariant.

```python
# ✅ Right
from app.services.versioning import make_active
make_active(
    db,
    target=new_asset,
    siblings_filter=[SceneAsset.scene_id == scene.id,
                     SceneAsset.asset_type == "image"],
    on_active_change=lambda a: setattr(scene, "reference_image_path", a.file_path),
)
db.commit()

# ❌ Wrong — manual deactivation loops
priors = db.exec(select(SceneAsset).where(...is_active == True)).all()
for p in priors: p.is_active = False
new_asset.is_active = True
db.add(new_asset)
```

### 4. Endpoints have a top-level try/except with traceback logging

`generate_scene_batch`, `generate_continuation_prompt`, and similar
endpoints wrap their entire body. Any unhandled exception logs
`traceback.print_exc()` and surfaces a 500 with a specific message:

```python
try:
    # all the work
    return result
except HTTPException:
    raise  # let intentional ones through
except Exception as e:
    print(f"[my-endpoint] uncaught:")
    traceback.print_exc()
    raise HTTPException(500, f"My endpoint crashed ({type(e).__name__}: {str(e)[:300]}).")
```

Generic "500: Internal Server Error" toasts without detail are the past.

### 5. Schema migrations go through `_apply_schema_migrations()`

SQLModel's `create_all()` only creates new TABLES, it does NOT alter
existing ones to add new fields. Adding a column to an existing model:

1. Add the field to the model in `backend/app/models.py`
2. Add it to the `expected` dict in `_apply_schema_migrations()` in
   `backend/app/main.py`:

```python
expected = {
    "scene": {
        "your_new_column": "VARCHAR",  # or BOOLEAN NOT NULL DEFAULT 0, etc.
    },
}
```

The startup hook runs `ALTER TABLE ADD COLUMN` for any missing fields.

### 6. Frontend confirms use `useConfirm()`, not `window.confirm()`

The codebase replaced all native confirms with an in-app overlay:

```tsx
// ✅ Right
const confirm = useConfirm();
if (await confirm({ title: "Delete X", message: "...", destructive: true })) { ... }

// ❌ Wrong
if (window.confirm("Delete X?")) { ... }
```

The provider is mounted in `app/providers.tsx`. Esc cancels, Enter confirms.

### 7. API errors flow through `request()` which extracts `detail`

`frontend/lib/api.ts`'s `request()` already extracts FastAPI's `{"detail": "..."}`
shape. So when surfacing mutation errors:

```tsx
const errMsg = mutation.error instanceof Error ? mutation.error.message : null;
// errMsg will be like "500: My endpoint crashed (KeyError: 'foo')." — already clean
```

Don't re-parse the message.

### 8. Prompts are written through `_save_prompt_version`

`Scene.image_prompt` and `Scene.video_prompt` are convenience MIRRORS of
the currently-active `ScenePromptVersion`. Writes go through
`_save_prompt_version()` in `routers/scenes.py` which keeps the version
history and updates the mirror via `make_active`'s `on_active_change`
callback. Never write directly to `Scene.image_prompt` from a router or
service — that bypasses the audit trail and the mirror falls out of sync.

---

## Patterns to avoid — DO NOT add back

### Lipsync / audio-sync paths

Tried fal Seedance reference-to-video with audio + fal OmniHuman; neither
produced acceptable results on music vocals. **Removed in v1.** Do not
reintroduce: the song's audio is muxed in verbatim at assembly time, and
no video model on the OpenRouter route accepts audio reference anyway.

If a future Seedance variant on OpenRouter starts exposing audio-input
support, that's worth revisiting — but the `audio_sync_enabled` field
and the `_generate_video_fal_*` functions are gone for a reason.

### Frame chaining via the video model's `last_frame`

Old chaining passed scene N+1's *planned still* to the video model as a
soft target. The model rarely landed on it pixel-perfect → visible seam
discontinuity. **Removed.**

The current chaining uses `Scene.chain_from_prev` (off by default). When
true, video gen at scene N+1 uses scene N's **extracted last frame**
(real rendered pixels from the .mp4) as `first_frame_path`. See
`generation_service.py` and `_extract_last_frame()`.

### Per-clip motion-offset trim in assembly

`_detect_motion_offset()` used to trim leading frames per clip to mask
old chaining's slow-ramp artifact. With old chaining gone, the trim
silently shortened videos. **Removed.** Assembly is now a straight
ffmpeg concat + audio mux, plus `-movflags +faststart`.

### Hardcoded `http://localhost:8010/storage/...` strings

One helper: `to_storage_url()` in `services/urls.py`. Use it.

### Manual `is_active = True/False` loops

One set of helpers: `services/versioning.py`. Use them.

### `window.confirm()` / `window.alert()` popups

`useConfirm()` overlay.

### Re-implementing LLM JSON parsing

`parse_llm_json()`.

### Adding a field to a model without a migration entry

The .db file is gitignored. Developers get a fresh DB on first run, BUT
existing developers already have a DB. New fields must be registered in
`_apply_schema_migrations()` or they'll silently 500 on read.

### `/scenes/auto-plan` and `/scenes/expand-all`

Removed in v1. The single batch endpoint `/scenes/generate-batch`
produces fully-expanded scenes (image_prompt + video_prompt +
description) inline, in batches. Per-scene re-expansion still happens
via `/scenes/{id}/expand-prompts`. Adding a new "plan everything at
once" endpoint is reverting to the v0 architecture — don't.

---

## How the pipeline flows (v1)

```
User uploads / generates song
  └─→ songs.upload (POST /songs/upload)
      └─→ audio_analysis.analyze_song()
          ├─→ librosa: BPM, key, beats, sections
          ├─→ fal whisper (if FAL_API_KEY) OR openrouter: lyric transcription
          └─→ openrouter LLM: theme / narrative / mood / visual_world

User clicks "Suggest characters"
  └─→ projects.suggest_characters
      └─→ scene_planner.suggest_characters() → 3 character cards

User clicks "Generate Scenes" or "Just scene 1"
  └─→ POST /scenes/generate-batch (one call per batch of N scenes)
      └─→ scene_planner.plan_scene_batch()
          └─→ openrouter LLM (with song theme + prior scenes as continuity)
              → fully-expanded scene dicts inserted into DB

User clicks chain icon `🔗` on a rendered scene
  └─→ POST /scenes/{id}/chain-next
      └─→ ensures scene N+1 exists with chain_from_prev=true, empty prompts

User clicks wand icon `✨` on a chained scene
  └─→ POST /scenes/{id}/continuation-prompt
      └─→ scene_planner.generate_continuation_prompts()
          └─→ Multimodal LLM call: text (story seed + theme + arc position +
              prior scenes + lyrics) + image (prev scene's extracted last frame)
              → video_prompt + 1-line description (NOT image_prompt — chained
              scenes don't use one)

User clicks "Img" on a scene
  └─→ POST /generation/scene (phase="image")
      └─→ BackgroundTask: generation_service.generate_scene(phase="image")
          └─→ openrouter image gen + character refs

User clicks "Vid" on a scene
  └─→ POST /generation/scene (phase="video")
      └─→ BackgroundTask: generation_service.generate_scene(phase="video")
          ├─→ Resolve first_frame_path:
          │     - chain_from_prev: prev_scene.extracted_last_frame_path
          │     - else: scene.reference_image_path
          ├─→ openrouter video submit + poll + download .mp4
          ├─→ _extract_last_frame() → scene.extracted_last_frame_path
          └─→ _save_asset() via make_active()

User clicks "Assemble final video"
  └─→ POST /generation/assemble/{project_id}
      └─→ BackgroundTask: assembly.assemble_project()
          ├─→ ffmpeg concat (re-encode video)
          ├─→ ffmpeg mux with song audio (-shortest, -movflags +faststart)
          └─→ GenerationJob status="completed", result_path=storage/N/final.mp4
```

---

## Frontend architecture

The studio is a **multi-step workflow** rendered as a stack of "cells":

```
FlowStudio
├── StepSongCell        — upload/generate the song + audio analysis
├── StepCharactersCell  — define cast (manual or AI Suggest)
├── StepPlanCell        — Generate Scenes (full song) / Just scene 1
├── StepGenerateCell    — per-scene image / video rendering
└── StepAssembleCell    — assemble final video, scrub strip, download
```

Per-scene UI lives in `cells/generate/`:
- `SceneGenRow.tsx` — the scene row (status pill, description, chain `🔗`,
  wand `✨`, settings cog, delete `X`, clear-assets trash, frame slots)
- `FrameSlot.tsx` — left/right slot for image / video
- `DescriptionWithPromptTooltip.tsx` — hover preview of the prompts the
  model will see
- `CharacterRefsBadge.tsx` — which characters' portraits will be sent as
  `input_references` (model-aware: only Seedance variants use refs on
  the OpenRouter route; Kling / Veo drop them silently)
- `VideoModelCard.tsx`, `SplitGenerateButton.tsx`, `SceneStatus.tsx`,
  `VariantGallery.tsx`, `PromptVersionGallery.tsx`, `ScenePreview.tsx`

**State management:** React Query. Every query key is structured (e.g.
`["project", projectId]`). After a mutation, call `qc.invalidateQueries(...)`
or pass `onSuccess: refresh` where `refresh` invalidates the right key.

**Auto-polling** is wired ad-hoc per cell:
- `StepPlanCell` polls every 2s while the batch loop is running
- `StepAssembleCell` polls `/api/generation/assemble/{id}/status` every 3s
- `StepCharactersCell` polls the project every 2.5s while a portrait is `generating`

---

## File map (where to look for things)

| Concern | File |
|---|---|
| Add a new video model | `backend/app/config.py` (`VIDEO_MODELS` dict) |
| Add a new image model | `backend/app/config.py` (`IMAGE_MODELS` dict) |
| New LLM-driven feature (prompt) | `backend/app/services/scene_planner.py` |
| Change how characters are described | `expand_character_description` in `scene_planner.py` |
| Change how scenes are planned in batch | `plan_scene_batch` in `scene_planner.py` |
| Change the wand (continuation prompt) | `generate_continuation_prompts` in `scene_planner.py` |
| Per-scene generation pipeline | `backend/app/services/generation_service.py` |
| Final ffmpeg assembly | `backend/app/services/assembly.py` |
| Pricing | `backend/app/services/pricing.py` |
| HTTP range request handler (video scrub) | `backend/app/main.py` (`@app.get("/storage/{path:path}")`) |
| Frontend mutation/query helpers | `frontend/lib/api.ts` |
| Frontend shared types | `frontend/lib/types.ts` |
| Confirm dialog system | `frontend/components/ConfirmDialog.tsx` |
| Per-scene UI | `frontend/components/studio/cells/generate/SceneGenRow.tsx` |
| Plan-step UI | `frontend/components/studio/cells/StepPlanCell.tsx` |
| Assembly UI | `frontend/components/studio/cells/StepAssembleCell.tsx` |

---

## Known gotchas

### OpenRouter contract drifts per provider

- **Seedance** has a server-side image-content filter that **refuses
  photoreal portraits as `input_references`** with `InputImageSensitiveContentDetected`.
  The backend used to auto-degrade (drop refs, then first_frame) but
  that silently broke chaining. v1 surfaces the error explicitly so the
  user picks recovery (switch to Kling/Veo, or activate a less
  recognizable portrait variant).
- **Veo** requires `personGeneration="allow_adult"` to render real people.
  We set it on every Veo submission. Without it, Veo refuses.
- **Veo + refs incompatibility**: Veo on OpenRouter ignores
  `input_references` when a `first_frame` is also present. So
  `supports_reference_images=False` for Veo variants in `VIDEO_MODELS`.
- **Kling on OpenRouter** ignores `input_references` entirely (the
  feature is exposed in Kling's native UI but OpenRouter's passthrough
  drops it). So `supports_reference_images=False`.
- **Gemini Image** occasionally returns *text describing the image*
  instead of an image. Auto-retry-on-text in `generate_image` handles
  this; the most common cause is a celebrity name in the prompt
  tripping the likeness filter.

### Backend reload kills in-flight requests

`uvicorn --reload` watches files; saving any Python file kills the worker.
Mitigations:
- Startup uses `--timeout-graceful-shutdown 300` so in-flight requests
  get 5 minutes to finish before reload.
- The batch endpoint (`generate-batch`) makes SHORT LLM calls per batch
  (~5-10s each), so a single bad reload loses at most one batch.
- The frontend retries transient errors up to 5× with exponential
  backoff. When mid-batch transient errors happen, you see "Retrying
  after backend blip…" without losing in-flight work.

### Scene chaining requires sequential generation

`scene.chain_from_prev` reads `prev_scene.extracted_last_frame_path`,
which only exists after prev's video has been fully rendered. If you
enable chain on scene 5 but scene 4 hasn't been generated yet, video
gen errors out with: *"Scene 5 is chained from scene 4 but that
scene's video hasn't been rendered yet. Generate scene 4 first."*

The continuation-prompt endpoint enforces the same precondition: it
needs the prev's last frame on disk to feed the vision LLM. Don't
parallelize chained scenes.

### Scene durations get snapped by the video model

`generation_service._closest_supported(raw_duration, model.durations)`
fits the scene duration to what the model supports. A 10s scene routed
to Veo Lite (`[4, 6, 8]`) will render as 8s. Assembled videos can come
out shorter than the song. To preserve full duration use Kling (3-15s)
or Seedance 2.0 (4-15s) for any scene needing >8s.

### `extracted_last_frame_path` is mutated in place

It's always `storage/{project}/extracted/scene_{N}_last.jpg` regardless
of how many times scene N's video has been regenerated. Browsers cache
the URL → stale chain preview. Fix: `to_storage_url(path, cache_bust=True)`
appends an mtime query string. This is the ONLY file the codebase
mutates in place; everything else uses unique-timestamped filenames.

### `Scene.image_prompt` / `Scene.video_prompt` mirror active prompt version

`Scene.image_prompt` and `Scene.video_prompt` are convenience fields
mirroring the **currently-active** `ScenePromptVersion`. Reads go
through the convenience fields (faster); writes go through
`_save_prompt_version` which keeps the version history and updates
the mirror via `make_active`'s `on_active_change` callback.

Don't write directly to `Scene.image_prompt` — bypasses the audit
trail and the mirror falls out of sync.

---

## Test workflow before declaring done

1. **Backend can start:** `cd backend && .venv/bin/python -c "from app.main import app; print('ok')"`
2. **Frontend type-check:** `cd frontend && npx tsc --noEmit` returns nothing
3. **One smoke test in the area you touched.** Examples:
   - Touched the LLM JSON parser? Run a real generate-batch, verify scenes appear.
   - Touched the video gen flow? Generate one scene's image (cheap), verify it
     lands on disk and the API surfaces the URL.
   - Touched the chain icon? Render scene 1, click `🔗` on its row, verify
     scene 2 appears with chain_from_prev=true.
   - Touched the wand? Click it on a chained scene, verify the description
     fills in and video_prompt updates.
   - Touched the URL helper? Hit `GET /api/projects/{id}` and verify URLs
     point at `settings.public_base_url`.

The user gets unhappy when checks pass but actual behavior is broken.
Always smoke-test.

---

## Style + tone (when surfacing to the user)

The user prefers:
- Direct, technical writing
- Specific data over generalities ("the LLM returned 16 words" not "the LLM was concise")
- "What I did + why + what to check" structure for change summaries
- Tables for comparison
- Code blocks for exact changes
- No emoji-heavy filler, no "I'd be happy to..."
- Honest admissions when you don't know — never invent

When making changes:
- Use TodoWrite for any work spanning >3 distinct steps
- Don't ask "should I X?" if X is the obvious next step — just do it
- Trust-but-verify: after refactoring, smoke-test the actual behavior
