# CLAUDE.md — Agent Instructions

This file is read automatically by Claude Code at the start of every
session. If you're an AI agent (Claude / Cursor / Aider / Cline) working
on this codebase, **read this end-to-end before making changes**.

---

## What this project is

An AI-driven music-video studio. User uploads a song → backend extracts
beats / sections / lyrics → LLM plans scenes → image model renders stills
→ video model animates them → ffmpeg assembles the final MP4 with the
song muxed in.

**Stack:**
- **Backend:** FastAPI + SQLModel + SQLite (one .db file in repo root)
- **Frontend:** Next.js 14 App Router + React Query + TypeScript + Tailwind
- **AI provider:** OpenRouter for everything (video / image / LLM), fal.ai
  optional for lipsync
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
# (or just `python` if venv is activated)

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
# Backend hygiene
cd backend && python -m pyflakes app/

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

These were established by recent refactors. Breaking them creates exactly
the kind of duplication and inconsistency the codebase was just cleaned of.

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

The base URL is `settings.public_base_url` (env-configurable). This is
what makes the app deployable behind a reverse proxy.

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
300 chars of the raw response in the message — keep that pattern when
adapting.

### 3. Versioned rows use `make_active()` / `delete_and_promote()`

Several tables share an "exactly one active row per scope" lifecycle:
`SceneAsset` (image/video/lipsync per scene), `ScenePromptVersion` (image
/ video prompt history), `CharacterAsset` (portrait versions). The helpers
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

Adding a new "versioned" model type? Use these helpers — same scope-filter +
on_active_change callback pattern works regardless of the table.

### 4. Endpoints have a top-level try/except with traceback logging

Both `auto_plan_scenes` and `expand_all_scenes` wrap their entire body. Any
unhandled exception logs `traceback.print_exc()` and surfaces a 500 with a
specific message:

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

Any new endpoint that does meaningful work (LLM calls, DB writes, file IO)
should follow this pattern. Generic "500: Internal Server Error" toasts
without detail are the past, not the future.

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

The startup hook runs `ALTER TABLE ADD COLUMN` for any missing fields. The
user gets a console message `[startup] schema migration added: ...`.

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

Don't re-parse the message. The status prefix + clean detail is what users see.

---

## Patterns to avoid — DO NOT add back

### Frame chaining via model `last_frame_path`

Old chaining passed scene N+1's *planned still* to the video model as
`last_frame`. The model treated it as a soft target and rarely landed on
it pixel-perfect → visible seam discontinuity. **Removed.**

The current chaining uses `Scene.chain_from_prev` (off by default). When
true, video gen at scene N+1 uses scene N's **extracted last frame** (real
rendered pixels from the .mp4) as `first_frame_path`. See
`generation_service.py` and `_extract_last_frame()`.

Don't reintroduce the old approach.

### Velocity-based seam trim

There used to be a `_detect_motion_offset()` function in `assembly.py` that
trimmed per-clip leading frames to fight the slow-ramp + duplicate-frame
issue introduced by old chaining. With old chaining gone, the trim was
solving a non-problem and silently shortening videos. **Removed.**

The current assembly is a straight ffmpeg concat with no per-clip trim,
plus `-movflags +faststart` for streaming-friendly MP4s.

### Hardcoded `http://localhost:8010/storage/...` strings

There used to be 4 separate hand-built URL constructions. Now there's
exactly one: `to_storage_url()` in `services/urls.py`. Use it.

### Manual `is_active = True/False` loops

There used to be ~280 lines of these scattered across routers. Now there's
exactly one set of helpers in `services/versioning.py`. Use them.

### `window.confirm()` / `window.alert()` popups

Replaced with `useConfirm()` overlay. Do not reintroduce native dialogs.

### Re-implementing LLM JSON parsing

Use `parse_llm_json()`. Don't inline `json.loads` + fence stripping + list
unwrapping per call site.

### Adding a field to a model without a migration entry

The .db file is gitignored. Developers get a fresh DB on first run, BUT
existing developers already have a DB. New fields must be registered in
`_apply_schema_migrations()` or they'll silently 500 on read.

---

## How the pipeline flows

```
User uploads song
  └─→ songs.upload (POST /songs/upload)
      └─→ audio_analysis.analyze_song()
          ├─→ librosa: BPM, key, beats, sections
          ├─→ openrouter / fal: word-level lyric transcription
          └─→ openrouter LLM: theme/narrative/mood/visual_world

User clicks "Suggest characters"
  └─→ projects.suggest_characters
      └─→ scene_planner.suggest_characters()
          └─→ openrouter LLM: 3 character cards

User clicks "Auto-plan scenes"
  └─→ scenes.auto_plan_scenes (POST /scenes/auto-plan)
      └─→ scene_planner.auto_plan_scenes()
          └─→ openrouter LLM: list of scene dicts
              (audio_start/end, image_prompt, video_prompt, lyrics_segment)

User clicks "Generate image" on a scene
  └─→ generation.generate_scene (POST /generation/scene, phase="image")
      └─→ BackgroundTask: generation_service.generate_scene(phase="image")
          └─→ openrouter image gen
              + char_refs (portraits of named characters)
              + style suffix via _append_style()

User clicks "Generate video" on a scene
  └─→ generation.generate_scene (POST /generation/scene, phase="video")
      └─→ BackgroundTask: generation_service.generate_scene(phase="video")
          ├─→ Resolve first_frame_path
          │     - Default: scene.reference_image_path
          │     - If scene.chain_from_prev: prev_scene.extracted_last_frame_path
          ├─→ openrouter video submission
          ├─→ Poll until completion
          ├─→ Download .mp4
          ├─→ _extract_last_frame() → scene.extracted_last_frame_path (for chaining downstream)
          └─→ _save_asset() via make_active()

User clicks "Assemble final video"
  └─→ generation.trigger_assembly (POST /generation/assemble/{project_id})
      └─→ BackgroundTask: assembly.assemble_project()
          ├─→ Write concat.txt listing all scene .mp4s in order
          ├─→ ffmpeg concat (re-encode video)
          ├─→ ffmpeg mux with song audio (-shortest, -movflags +faststart)
          └─→ Job row updated to status="completed"
```

---

## Frontend architecture

The studio is a **multi-step workflow** rendered as a stack of "cells":

```
FlowStudio
├── StepSongCell        — upload/generate the song + audio analysis
├── StepCharactersCell  — define cast (manual or AI Suggest)
├── StepPlanCell        — auto-plan scenes + AI Expand all
├── StepGenerateCell    — per-scene image/video/lipsync generation
└── StepAssembleCell    — assemble final video, scrub strip, download
```

`StepGenerateCell.tsx` was a 1591-line monolith; it's now a 228-line
orchestrator + 12 focused files in `cells/generate/`. Most per-scene
features go in `generate/SceneGenRow.tsx`.

**State management:** React Query. Every query key is structured (e.g.
`["project", projectId]`). After a mutation, call `qc.invalidateQueries(...)`
or pass `onSuccess: refresh` where `refresh` is a closure that invalidates
the right key.

**Auto-polling** is wired ad-hoc per cell. Example:
- `StepAssembleCell` polls `/api/generation/assemble/{id}/status` every 3s while running
- `StepCharactersCell` polls the project every 2.5s while any character is
  in `portrait_status: "generating"`

---

## File map (where to look for things)

| Concern | File |
|---|---|
| Add a new video model | `backend/app/config.py` (`VIDEO_MODELS` dict) |
| Add a new image model | `backend/app/config.py` (`IMAGE_MODELS` dict) |
| Add a new endpoint | `backend/app/routers/` (or new file under `routers/`) |
| New LLM-driven feature (prompt) | `backend/app/services/scene_planner.py` |
| Change how characters are described | `CHAR_SUGGEST_PROMPT` and `expand_character_description` in `scene_planner.py` |
| Change how scenes are planned | `SCENE_PLAN_SYSTEM` and `SCENE_PLAN_USER` in `scene_planner.py` |
| Per-scene generation pipeline | `backend/app/services/generation_service.py` |
| Final ffmpeg assembly | `backend/app/services/assembly.py` |
| Pricing | `backend/app/services/pricing.py` |
| HTTP range request handler (video scrub) | `backend/app/main.py` (`@app.get("/storage/{path:path}")`) |
| Frontend mutation/query helpers | `frontend/lib/api.ts` |
| Frontend shared types | `frontend/lib/types.ts` |
| Confirm dialog system | `frontend/components/ConfirmDialog.tsx` |
| Per-scene UI | `frontend/components/studio/cells/generate/SceneGenRow.tsx` |
| Assembly UI (scrubber, download, preview) | `frontend/components/studio/cells/StepAssembleCell.tsx` |

---

## Known gotchas

### The OpenRouter API contract drifts

OpenRouter is an aggregator; the per-provider quirks leak through. Specific
behaviors we know about:

- **Seedance** has a server-side image-content filter that **refuses photo-
  realistic portraits as `input_references`** with `InputImageSensitiveContentDetected`.
  Mitigation: auto-retry-with-degradation in `submit_video_job` drops refs,
  then drops first_frame, before erroring out. Users should pick Kling /
  Hailuo / Wan / Veo for character-heavy scenes.
- **Veo** has a `personGeneration` field; we set it to `"allow_adult"` on
  Veo submissions to allow photoreal people. Without this, Veo refuses.
- **Gemini Image** occasionally returns *text describing the image* instead
  of an image. Auto-retry-on-text in `generate_image` handles this; the
  most common cause is a celebrity name in the prompt triggering the
  likeness filter. Character prompts forbid celebrity references for this
  reason.

### Backend reload kills in-flight requests

`uvicorn --reload` watches files; saving any Python file kills the worker.
If you save during a long auto-plan (60s+), the request dies even though
the LLM call completed. Mitigations:

- Backend startup uses `--timeout-graceful-shutdown 300` so in-flight
  requests get 5 minutes to finish before the reload kills them.
- Both `auto_plan_scenes` and `expand_all_scenes` wrap their bodies in try
  /except with traceback logging so partial-success cases at least leave
  the DB consistent and a useful error message.

When debugging "auto-plan failed but actually succeeded": refresh and
check if scenes are in the DB. They usually are.

### Scene chaining requires sequential ordering

`scene.chain_from_prev` reads `prev_scene.extracted_last_frame_path` which
only exists after prev's video has been fully rendered. If a user enables
chain on scene 5 but scene 4 hasn't been generated yet, video gen errors
out with an actionable message: *"Scene 5 is chained from scene 4 but
that scene's video hasn't been rendered yet. Generate scene 4 first."*

For batch generation, this means chained scenes must be generated in order.
Parallel batch fires all scenes at once — the chained ones will fail with
that error and need a re-run after the upstream finishes.

### Scene durations get snapped by the video model

`generation_service` calls `_closest_supported(raw_duration, model.durations)`
to fit the scene duration to what the model supports. A 10s scene routed
to Veo Lite (supports `[4, 6, 8]`) will render as 8s. This is why assembled
videos are sometimes shorter than the song. To preserve full duration: use
Kling (3-15s) or Wan (5/10/15s) for any scene that needs >8s.

### Scene `image_prompt` / `video_prompt` have BOTH a "live" field and a versioned history

`Scene.image_prompt` and `Scene.video_prompt` are convenience fields
mirroring the **currently-active** `ScenePromptVersion`. Reads go through
the convenience fields (faster, simpler), writes go through
`_save_prompt_version` which keeps the version history *and* updates the
mirror via `make_active`'s `on_active_change` callback.

Don't write directly to `Scene.image_prompt` — bypasses the versioning
audit trail and the mirror falls out of sync with `is_active`.

---

## Test workflow before declaring done

1. **Backend:** `python -m pyflakes app/` returns nothing
2. **Frontend:** `npx tsc --noEmit` returns nothing
3. **Backend can start:** `python -c "from app.main import app; print('ok')"`
4. **One smoke test in the area you touched.** Examples:
   - Touched the LLM JSON parser? Run a real auto-plan, verify it returns scenes.
   - Touched the video gen flow? Generate one scene's image (cheap), verify
     it lands on disk and the API surfaces the URL.
   - Touched the URL helper? Hit `GET /api/projects/{id}` and verify URLs
     point at `settings.public_base_url`.
   - Touched the versioning service? Create + activate + delete a prompt
     version and verify exactly one row stays `is_active = True`.

The user gets unhappy when checks pass but actual behavior is broken —
specifically because most of the recent fixes were "the type-check passed
but the feature didn't work end-to-end." Always smoke-test.

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
