# Music Video Studio

AI-generated music videos end-to-end: song → lyrics + beat analysis → scene
plan → character cast → per-scene image + video → final assembly with audio.

**Stack:** FastAPI + SQLModel + SQLite (backend), Next.js 14 + React Query +
TypeScript + Tailwind (frontend). All AI calls routed through OpenRouter
(video/image/LLM) plus optional fal.ai for lipsync.

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
# (FAL_API_KEY is optional, only needed for lipsync)

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

## What does it do

1. **Create a project** with a name, visual style ("cyberpunk noir"), and aspect ratio.
2. **Upload or generate a song.** Backend analyzes it: BPM, key, beat
   timestamps, section boundaries, word-level lyric transcription, song
   theme/narrative/mood.
3. **Define characters** — either manually or via "AI Suggest" which proposes
   a cast from the song's theme.
4. **Auto-plan scenes** — LLM divides the song into N self-contained shots
   aligned to beats / sections, with image + video prompts per scene,
   referencing concrete lyric imagery.
5. **Per-scene generation** — image first (cheap preview), then video
   (img-to-video), then optional lipsync. Character portraits attach
   automatically when a character's name appears in the prompt.
6. **Assembly** — ffmpeg concatenates all scene clips, muxes the song, writes
   a final MP4 with HTTP-range streaming support so the browser can scrub it.
7. **Scene chaining** (opt-in) — for seamless scene-to-scene handoffs, enable
   chain on a scene and its video starts on the previous scene's actual last
   rendered frame instead of its own planned still.

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
│   │   │   ├── scenes.py         # auto-plan, expand-all, scene CRUD + versions
│   │   │   └── generation.py     # generate / batch / assemble / costs
│   │   └── services/
│   │       ├── openrouter.py     # Provider client (video, image, LLM)
│   │       ├── fal_client.py     # fal.ai for lipsync
│   │       ├── audio_analysis.py # librosa + transcription
│   │       ├── scene_planner.py  # LLM prompts: auto-plan, expand, soften, characters
│   │       ├── generation_service.py  # The per-scene pipeline (image→video→lipsync)
│   │       ├── assembly.py       # ffmpeg concat + audio mux
│   │       ├── pricing.py        # Cost calc per model / phase
│   │       ├── llm_json.py       # Tolerant JSON parsing for LLM outputs
│   │       ├── urls.py           # Convert on-disk paths → public URLs
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
│   │       ├── SceneEditor.tsx
│   │       ├── SongPanel.tsx
│   │       ├── Lightbox.tsx
│   │       └── cells/            # Workflow "steps" — one cell per stage
│   │           ├── StepSongCell.tsx
│   │           ├── StepCharactersCell.tsx
│   │           ├── StepPlanCell.tsx
│   │           ├── StepGenerateCell.tsx
│   │           ├── StepAssembleCell.tsx
│   │           └── generate/     # Subcomponents split out of StepGenerateCell
│   ├── lib/
│   │   ├── api.ts                # Typed fetch wrapper
│   │   └── types.ts              # Shared Scene / Character / Project types
│   ├── next.config.mjs           # Proxies /api → backend
│   └── package.json
├── .gitignore
├── .env                          # YOUR keys (gitignored — do not commit)
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

**Required env var:** `OPENROUTER_API_KEY`
**Optional:** `FAL_API_KEY` (lipsync), `SUNO_API_KEY` (alternative music gen)

Default model selections live in `config.py`'s `VIDEO_MODELS`, `IMAGE_MODELS`,
`LLM_MODELS`, `LIPSYNC_MODELS` dicts. Each entry has a key (used in `.env`
defaults + the per-scene model picker), the OpenRouter `model_id`, pricing,
supported durations / resolutions, and a `note` describing tradeoffs.

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

## For AI agents working on this codebase

Read **[`CLAUDE.md`](./CLAUDE.md)** before making changes. It documents:
- Code conventions established by recent refactors (URL helpers, versioning
  service, LLM JSON parser, etc.)
- Schema migration pattern for new model fields
- Where things live + how the pieces talk to each other
- Patterns to avoid (frame chaining done wrong, hardcoded URLs, manual
  `is_active` flag flipping, etc.)
