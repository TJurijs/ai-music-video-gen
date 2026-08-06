# Music Video Studio

Music Video Studio is a local-first workflow for turning a song into a planned,
generated, and assembled music video:

`song -> analysis -> characters -> scene plan -> image/video clips -> final MP4`

The backend is FastAPI + SQLModel + SQLite. The frontend is Next.js 16,
React 19, TypeScript, React Query, and Tailwind. ffmpeg/ffprobe validate and
assemble media.

## Quick start

Prerequisites:

- Python 3.11+
- Node.js 20.9+
- ffmpeg and ffprobe on `PATH`
- API keys for the providers you use

On Windows:

```powershell
Copy-Item backend/.env.example backend/.env
# Fill in backend/.env, then:
./start.ps1
```

`start.bat` is a wrapper around the same PowerShell launcher. Both services run
in one terminal and stop together with Ctrl+C.

On macOS/Linux:

```bash
cp backend/.env.example backend/.env
# Fill in backend/.env, then:
./start.sh
```

The launchers create `backend/.venv`, install dependencies only when lock files
change, and start both services in the current terminal.

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend | http://localhost:8010 |
| API docs | http://localhost:8010/docs |

The servers bind to loopback by default. The app has no user authentication, so
do not expose either port to a network without adding an authenticated reverse
proxy and reviewing the API threat model.

## Configuration

Copy [`backend/.env.example`](backend/.env.example) to `backend/.env`.

| Variable | Required for |
|---|---|
| `OPENROUTER_API_KEY` | LLM planning, image generation, and OpenRouter video routes |
| `FAL_API_KEY` | fal video/audio-reference routes and word-level transcription |
| `SUNO_API_KEY` | Song generation |
| `STORAGE_DIR` | Optional generated-media location |
| `PUBLIC_BASE_URL` | Optional backend URL used in asset links |

Model defaults and the model capability inventory live in
[`backend/app/config.py`](backend/app/config.py). The Generate step also exposes
the inventory as a collapsed model cheat sheet, including provider, price,
frame/character/audio reference support, mutual exclusions, and face
guardrail notes.

## Workflow

1. Create a project and choose an aspect ratio and visual direction.
2. Upload one song or generate one with Suno. Audio is validated before it is
   accepted and then analyzed for duration, beats, sections, lyrics, and theme.
3. Add or suggest characters and optionally generate portrait references.
4. Plan the full song or start with one scene. A failed re-plan leaves the
   current plan untouched.
5. Generate a scene still and video. The preflight shows snapped duration,
   resolution, route, warnings, and estimated cost before paid submission.
6. For continuity, chain a new scene from the real extracted last frame of the
   previous clip. Character-reference mode is an alternative when a model
   cannot combine frame anchors and separate character images.
7. Assemble the contiguous completed scene prefix with the original song.
   Missing middle scenes are reported and block accidental partial assembly.

## Reliability model

- Uploads are size-bounded, media-probed, and written through controlled paths.
- Scene generation uses an atomic ownership token, preventing duplicate runs.
- OpenRouter, fal, and Suno task handles are persisted when the provider offers
  them. After a backend restart, Retry resumes the saved task instead of paying
  for a second submission.
- New output files are activated only after validation; failed replacements do
  not destroy the previous usable asset.
- SQLite enables foreign keys, WAL, a busy timeout, uniqueness invariants, and
  startup recovery for interrupted local work.
- A consistent database backup is created before compatibility schema changes.
- The Windows launcher forces UTF-8 so Unicode prompts and logs cannot trigger
  legacy `charmap` failures.

Provider calls that do not return a durable remote task handle cannot be resumed
exactly after a process crash. See [`AUDIT.md`](AUDIT.md) for the remaining
production limitations and recommended next architecture.

## Development checks

Backend:

```powershell
cd backend
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe -m ruff check app tests --select F,E9
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip_audit -r requirements.txt
```

Frontend:

```powershell
cd frontend
npm ci
npm run check
npm audit --audit-level=high
```

The same checks run in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Data and backups

- SQLite database: `backend/musicvideo.db`
- Generated media: `backend/storage/` unless `STORAGE_DIR` overrides it
- Automatic migration backups: `backend/backups/`

Database paths are local-machine paths. Copying only the database to another
machine does not copy its media; migrate the database and storage directory
together.

## Code map

```text
backend/app/
  main.py                    application lifecycle and storage streaming
  database.py                SQLite configuration, migrations, invariants
  routers/                   projects, songs, scenes, generation APIs
  services/
    generation_service.py    image/video orchestration and remote resume
    media_files.py           bounded uploads and ffprobe validation
    assembly.py              normalized ffmpeg assembly
    pricing.py               preflight and recorded cost calculations
    openrouter.py             OpenRouter API client
    fal_client.py             fal queue submit/poll/cancel client
    suno.py                   Suno submit/poll client

frontend/
  app/                       Next.js routes
  components/studio/         five-step project workflow
  lib/api.ts                 typed fetch client
  lib/types.ts               shared frontend domain types
```

## Current scope

This is a strong local single-user application, not yet a multi-user production
service. The next meaningful architectural step is a durable worker queue and
proper schema migrations, not a wholesale UI rewrite. Details and priorities are
in [`AUDIT.md`](AUDIT.md).
