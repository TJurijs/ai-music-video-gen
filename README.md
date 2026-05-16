# Music Video Studio

AI-generated music videos end-to-end: song → lyrics + beat analysis → scene
plan → character cast → per-scene image + video → final assembly with audio.

**Stack:** FastAPI + SQLModel + SQLite (backend), Next.js 14 + React Query +
TypeScript + Tailwind (frontend). All video / image / LLM calls route
through OpenRouter. fal.ai is optional, used only for word-level lyric
timestamps when `FAL_API_KEY` is set; otherwise OpenRouter transcription
is the fallback (no per-word timing).

---

## Quick start

### Prerequisites

| Tool | Version | Install (macOS) | Install (Windows) |
|---|---|---|---|
| Python | **3.11+** | `brew install python@3.11` | python.org |
| Node | **18+** | `brew install node` | nodejs.org |
| ffmpeg | recent | `brew install ffmpeg` | `winget install ffmpeg` |
| Git | any | preinstalled | git-scm.com |

### First time on a new machine (full walkthrough)

```bash
# 1. Install system prerequisites — macOS
brew install python@3.11 node ffmpeg

# 2. Clone
git clone <your-fork-url> musicvideo
cd musicvideo

# 3. Configure your API key
cp backend/.env.example backend/.env
# Edit backend/.env: paste your OpenRouter key from https://openrouter.ai/keys
# (FAL_API_KEY is optional — only needed for word-level lyric timestamps)

# 4. Bootstrap — creates backend/.venv, installs Python + npm deps,
#    opens both servers in two Terminal windows
chmod +x start.sh
./start.sh

# OR if you'd rather drive from Claude Code Preview:
# Run start.sh ONCE first (to create the venv + install deps), then open
# Claude Code and start the `backend` / `frontend` configs from .claude/launch.json
```

After step 4, visit http://localhost:3000 — the app is live.

### Why step 1 matters on macOS

macOS preinstalled Python is 3.9, but this backend uses `dict | None` union
syntax (PEP 604, **requires Python 3.10+**). `start.sh` checks your Python
version and refuses to proceed if it's too old. `brew install python@3.11`
gives you `python3` pointing at 3.11+.

If `python3 --version` still shows 3.9 after the brew install, add to your
`~/.zshrc`:
```bash
export PATH="/opt/homebrew/opt/python@3.11/bin:$PATH"
```

### Other ways to run (after first-time setup)

**Option 1 — macOS / Linux shell script:**
```bash
chmod +x start.sh
./start.sh
```
Opens two Terminal windows (backend + frontend), installs deps + creates
a venv at `backend/.venv/` on first run.

**Option 2 — Windows:**
```bat
start.bat
```
Same idea, two `cmd` windows.

**Option 3 — Claude Code Preview** (works on any OS):

If you're working inside Claude Code, the `.claude/launch.json` file
defines `backend` and `frontend` as preview targets. Open the Preview
panel and pick "Start" — both servers boot inside Claude Code with logs
visible per-target.

Before first preview run you still need to install deps once:
```bash
./start.sh                # macOS / Linux  (or `start.bat` on Windows)
# OR manually:
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd ../frontend && npm install
```

After that, Claude Code Preview picks up the configured launch entries
and the dev servers come up inside the preview pane.

**The launch.json default uses `python3`** (macOS convention). On Windows,
change it to `python` — see comment in `.claude/launch.json`.

---

| | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8010 |
| API docs | http://localhost:8010/docs |

The Next.js dev server proxies `/api/*` and `/storage/*` to `localhost:8010`.
First-run installs take 2–3 minutes; subsequent starts are instant.

---

## What it does

1. **Create a project** with a name, visual style ("cyberpunk noir"), and aspect ratio.
2. **Upload or generate a song.** Backend analyzes it: BPM, key, beat
   timestamps, section boundaries, word-level lyric transcription (via
   fal-whisper if `FAL_API_KEY` is set, OpenRouter otherwise), song
   theme / narrative / mood.
3. **Define characters** — manually or via "AI Suggest" (cast proposed
   from the song's theme). Each character has a name, description, and
   optionally a portrait used as a visual reference at video gen time.
4. **Plan scenes** in two flows:
   - **Generate Scenes** — full-song batch plan. LLM divides the song
     into N self-contained shots aligned to beat / section windows, with
     fully-expanded image + video prompts per scene.
   - **Just scene 1** — single-scene plan. Useful for iterating on the
     story seed and visual style cheaply before committing to a full plan.
5. **Per-scene generation** — image first (cheap preview), then video
   (img-to-video). Character portraits attach automatically when a
   character's name appears in the prompt AND the chosen video model
   supports reference images (Seedance variants only; Kling / Veo drop
   them on the OpenRouter route).
6. **Chain scenes iteratively** — click the chain icon on scene N to add
   scene N+1 with `chain_from_prev=true`. The new scene starts with empty
   prompts; the wand icon then asks a vision-LLM to write its
   `video_prompt` + description using scene N's actual rendered last
   frame as visual context. Result: pixel-accurate handoff at the seam,
   motion that picks up from exactly where the previous clip ended.
7. **Assembly** — ffmpeg concatenates all scene clips in order, muxes
   the song verbatim, and writes a streaming-friendly MP4
   (`-movflags +faststart`).

---

## Iterative-build flow (recommended)

This is the usable workflow once a story seed is set:

1. **Plan step:** "Just scene 1" → backend plans one scene from the seed.
2. **Generate step:** click "Img" on scene 1 to render the still, then
   "Vid" to render the video. The backend extracts the genuine last
   frame of the rendered clip automatically.
3. **Same row, scene 1:** click the chain icon `🔗` → creates an empty
   scene 2 chained from scene 1.
4. **Scene 2 row:** click the wand icon `✨` → vision-LLM reads scene 1's
   actual last frame and writes scene 2's `video_prompt` + description.
   The prompt is anchored to the song's mood + the arc position
   (opening / rising / climax / resolution) so each clip evolves rather
   than feeling like "more of the same."
5. **Scene 2 row:** click "Vid" → renders the video starting on scene 1's
   last frame. No need to generate a still for chained scenes — the
   chain frame replaces the planned still at submit time.
6. Repeat from step 3 for scenes 3, 4, … until the song ends.

Alternative: skip the iterative chain workflow and use "Generate Scenes"
(full song, batches of 3 in one go).

---

## Project layout

```
.
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI app, startup hooks, /storage handler
│   │   ├── config.py             # Settings + VIDEO_MODELS / IMAGE_MODELS / LLM_MODELS
│   │   ├── database.py           # SQLModel engine + create_all
│   │   ├── models.py             # All DB tables
│   │   ├── routers/              # FastAPI route modules
│   │   │   ├── projects.py
│   │   │   ├── songs.py
│   │   │   ├── scenes.py         # generate-batch, expand, scene CRUD, chain-next,
│   │   │   │                     # continuation-prompt, version mgmt
│   │   │   └── generation.py     # per-scene render dispatcher + assembly
│   │   └── services/
│   │       ├── openrouter.py     # Provider client (video, image, LLM, transcription)
│   │       ├── fal_client.py     # Thin wrapper for fal-ai/whisper (optional)
│   │       ├── audio_analysis.py # librosa + transcription
│   │       ├── scene_planner.py  # LLM prompts: plan_scene_batch, expand,
│   │       │                     # generate_continuation_prompts, soften, characters
│   │       ├── generation_service.py  # Per-scene pipeline (image → video)
│   │       ├── assembly.py       # ffmpeg concat + audio mux
│   │       ├── pricing.py        # Cost calc per model / phase
│   │       ├── llm_json.py       # Tolerant JSON parsing for LLM outputs
│   │       ├── urls.py           # On-disk paths → public URLs (with cache-bust opt)
│   │       └── versioning.py     # Shared make_active / delete_and_promote
│   ├── storage/                  # Generated content (gitignored, 100s of MB)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/                      # Next.js App Router pages
│   │   ├── layout.tsx
│   │   ├── providers.tsx         # QueryClient + ConfirmProvider
│   │   └── projects/             # Project list + project detail pages
│   ├── components/
│   │   ├── ConfirmDialog.tsx     # In-app overlay replacing window.confirm()
│   │   └── studio/
│   │       ├── FlowStudio.tsx    # Main per-project workspace
│   │       ├── SongPanel.tsx
│   │       ├── Lightbox.tsx
│   │       └── cells/            # Workflow "steps" — one cell per stage
│   │           ├── StepSongCell.tsx
│   │           ├── StepCharactersCell.tsx
│   │           ├── StepPlanCell.tsx        # Generate Scenes / Just scene 1
│   │           ├── StepGenerateCell.tsx    # Per-scene image / video rendering
│   │           ├── StepAssembleCell.tsx
│   │           └── generate/     # SceneGenRow, FrameSlot, tooltip, badges, ...
│   ├── lib/
│   │   ├── api.ts                # Typed fetch wrapper
│   │   └── types.ts              # Shared Scene / Character / Project types
│   ├── next.config.mjs           # Proxies /api → backend
│   └── package.json
├── .gitignore
├── CLAUDE.md                     # ⭐ Agent instructions — READ THIS if you're an AI working here
├── README.md                     # this file
├── start.sh                      # macOS/Linux
├── start.bat                     # Windows
└── openrouter-openapi.yaml       # Reference OpenAPI spec for OpenRouter
```

---

## Configuration

All settings live in `backend/app/config.py`. Env vars override defaults
(read from `.env` at repo root or `backend/.env`).

**Required:** `OPENROUTER_API_KEY`
**Optional:** `FAL_API_KEY` (word-level lyric timestamps via fal-ai/whisper;
falls back to OpenRouter transcription without per-word timing if unset),
`SUNO_API_KEY` (Suno music generation; Lyria 3 Pro via OpenRouter is the
default music source).

Default model selections live in `config.py`'s `VIDEO_MODELS`, `IMAGE_MODELS`,
`LLM_MODELS` dicts. Each entry has a key (used in the per-scene model
picker), the OpenRouter `model_id`, pricing, supported durations /
resolutions, a `supports_reference_images` flag (Seedance: yes; Kling /
Veo: no on the OpenRouter route), and a `note` describing tradeoffs.

---

## Cross-platform notes

- **Storage paths in the database are absolute.** A SQLite database created
  on Windows (with paths like `C:\...`) won't resolve on macOS. The `.db`
  file is gitignored — you'll start fresh on a new machine.
- **`ffmpeg` and `ffprobe`** must be on `$PATH` (or `%PATH%`) — the assembly
  + last-frame-extraction pipelines shell out to them. They're standard
  `brew install ffmpeg` on macOS.
- **Storage directory.** The default is `backend/storage/` (created on
  startup). Override with `STORAGE_DIR=` env var.
- **Backend port.** Defaults to `8010` to avoid colliding with anything on
  `8000`. If you change it, update the Next.js rewrites in
  `frontend/next.config.mjs` AND `PUBLIC_BASE_URL` in your `.env`.

---

## What's NOT in the pipeline (deliberately removed)

- **Lipsync / audio-sync.** Tried fal Seedance reference-to-video with
  audio and fal OmniHuman; neither produced acceptable results on music
  vocals. Removed. The pipeline produces purely visual scenes; the song
  is muxed in verbatim at assembly time.
- **Frame chaining via the video model's `last_frame_path`.** Used to
  pass scene N+1's planned still to the video model as a soft target;
  the model rarely landed on it pixel-perfect and the seam showed.
  Replaced with chain_from_prev: scene N+1's first_frame is scene N's
  actual extracted last frame (pixel-accurate).
- **Per-clip motion-offset trim in assembly.** A heuristic that
  shortened clips to mask old chaining's slow-ramp artifact. Removed
  along with the artifact.

---

## For AI agents working on this codebase

Read **[`CLAUDE.md`](./CLAUDE.md)** before making changes. It documents:
- Code conventions established by recent refactors (URL helpers, versioning
  service, LLM JSON parser, etc.)
- Schema migration pattern for new model fields
- Where things live + how the pieces talk to each other
- Patterns to avoid (frame chaining done wrong, hardcoded URLs, manual
  `is_active` flag flipping, etc.)
