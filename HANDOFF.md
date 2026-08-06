# Maintainer handoff

Start with [`README.md`](README.md), then read [`AUDIT.md`](AUDIT.md). The audit
contains the current risk register and next architecture proposal.

## Current operating model

- Local single-user app on frontend `127.0.0.1:3000` and backend
  `127.0.0.1:8010`.
- SQLite and generated media are local under `backend/` by default.
- OpenRouter handles planning/images and normal video routes; selected
  audio-reference video routes use fal; music generation uses Suno.
- A generation retry may resume a persisted remote provider task. Never replace
  retry logic with an unconditional resubmit.
- Character-reference mode and exact frame mode are intentionally exclusive for
  routes that cannot carry both. The backend preflight is authoritative.

## Important invariants

- One song per project.
- One scene per `(project_id, order)`.
- One active asset per scene/asset type and one active prompt per prompt type.
- At most one active assembly per project.
- A scene generation run must own its `generation_run_id` before mutating state.
- Files must be validated before activation; deletion must go through the
  storage-root-safe helpers in `services/media_files.py`.
- Paid provider task IDs and request snapshots must be committed before polling.
- Assembly input must be a contiguous completed prefix.

## High-change files

- `backend/app/services/generation_service.py`: generation orchestration and
  resume paths; first candidate for modular extraction.
- `backend/app/config.py`: model capability inventory used by API, pricing,
  preflight, and the frontend cheat sheet.
- `backend/app/services/pricing.py`: cost estimates; keep route-specific fal and
  OpenRouter pricing separate.
- `frontend/components/studio/cells/generate/SceneGenRow.tsx`: scene controls;
  second candidate for modular extraction.
- `backend/app/main.py`: startup migration/recovery. Treat existing user data as
  valuable and preserve the pre-mutation backup behavior.

## Verification

Run the commands in the README. Do not use a real paid generation as an
unannounced smoke test. For a runtime check, start with `start.ps1`/`start.sh`,
exercise health/models/project reads, then inspect the project and model cheat
sheet in a browser.

## Known next work

The highest-value next change is a durable job-service/worker boundary followed
by Alembic migrations. Do not rewrite the five-step UI or domain model first;
they are not the source of the remaining operational risk.
