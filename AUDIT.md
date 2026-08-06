# Music Video Studio audit

Audit date: 2026-07-27

## Verdict

The app did not need a wholesale rewrite. Its five-step product flow and core
domain model are coherent, and replacing them would create more risk than value.
The correct first move was to harden state transitions, provider recovery,
storage, schema integrity, cost visibility, and error handling in place.

After this pass, the app is suitable as a local, single-user production tool.
It is not yet suitable as an internet-exposed or horizontally scaled service.
The next architectural investment should be a durable worker boundary plus
formal database migrations, while preserving the current UI and API workflow.

## What was fixed in this pass

### Stability and paid-work safety

- Scene, portrait, song, and assembly starts now use database-backed claims or
  uniqueness rules to reject duplicate starts.
- OpenRouter, fal, and Suno external task identifiers are persisted. Retrying
  after a backend restart resumes a recoverable provider job instead of
  submitting and charging again.
- fal cancellation keeps the remote handle unless provider cancellation is
  confirmed. A local cancel can no longer silently orphan a paid job.
- Startup reconciles interrupted local state and tells the user whether Retry
  will resume remote work.
- Asset replacement is transactional at the file/application level: output is
  downloaded, probed, and activated before an older usable asset is retired.
- Failed scene re-planning preserves the existing plan.
- A project is constrained to one song at both API and database levels.

### Storage and media correctness

- Uploads have explicit byte limits, allow-listed extensions, controlled
  destinations, and ffprobe validation.
- Storage deletion is limited to the configured storage root.
- HTTP media streaming supports valid single byte ranges, including suffix
  ranges, and rejects malformed or multiple ranges.
- ffmpeg/ffprobe calls are bounded and moved off the async event loop where
  appropriate.
- Assembly rejects gaps and only accepts a contiguous completed scene prefix,
  preventing an apparently successful but structurally wrong final video.

### Data integrity and recovery

- SQLite enables foreign keys, WAL, a 30-second busy timeout, and uniqueness
  indexes for active assets, active prompt versions, scene order, song slots,
  and active assemblies.
- Schema changes create a consistent SQLite backup before compatibility
  mutations.
- Startup recovery clears stale ownership tokens and reconciles jobs, scenes,
  songs, and portraits without deleting valid media.

### Product usability

- Paid video generation has a preflight with route, snapped settings, warnings,
  and estimated cost, followed by explicit confirmation.
- The model selector includes the newly requested models and a collapsed visual
  inventory showing OpenRouter/fal route, per-minute cost, first/last frame,
  character references, reference audio, mutual exclusions, and face-policy
  notes.
- Reference mode is visible per scene; frame controls are replaced with an
  explanatory state when character references are selected.
- Song failures are visible and retryable, with safe removal and no hardcoded
  backend URL.
- Project/create/delete and scene-edit failures are surfaced instead of being
  swallowed.
- Dialogs, project cards, and responsive layouts received basic keyboard,
  focus, and ARIA improvements.
- The Windows launcher runs both services in one terminal and forces UTF-8,
  eliminating the observed legacy `charmap` logging crash.

### Maintainability and supply chain

- Frontend upgraded to Next.js 16.2 and React 19.2; vulnerable transitive
  packages are pinned through audited overrides.
- Backend framework and provider-client dependencies were upgraded and pinned.
- Backend pytest coverage now targets workflow guards, remote resume,
  generation ownership, media validation, and range streaming.
- Frontend Vitest coverage targets model/reference behavior and scene-sequence
  assembly rules.
- CI runs lint/import checks, tests, production build, and dependency audits.
- Startup installation is lock/hash based instead of opening unmanaged terminal
  windows and reinstalling everything on every run.

## Remaining risks

| Priority | Risk | Why it matters | Recommendation |
|---|---|---|---|
| P1 | Background work still executes inside the API process | A reload or crash interrupts local CPU/file work; only providers with durable handles can resume exactly | Introduce a worker interface and durable queue before multi-user use |
| P1 if exposed | No authentication or authorization | Any process that can reach the API can mutate projects and trigger paid work | Keep loopback-only; add auth, CSRF/origin policy, rate limits, and per-user ownership before network exposure |
| P1 for scaling | SQLite plus ad-hoc compatibility migrations | Correct for one local user, but not for multiple writers or repeatable deployments | Add Alembic; use PostgreSQL for hosted/multi-user mode |
| P2 | Live provider contracts are only smoke-tested manually | Providers can change model IDs, fields, pricing, and constraints independently | Add key-gated nightly contract tests that submit the cheapest safe fixture and alert on schema drift |
| P2 | `generation_service.py` and `SceneGenRow.tsx` remain large | Changes to one provider or UI concern can affect unrelated paths | Split by provider/route and by scene-row concern behind stable interfaces |
| P2 | Limited structured observability | Plain logs make it hard to correlate a user click, local job, provider task, and output | Add request/run IDs to structured logs and a diagnostics view/export |
| P2 | Backup retention is unbounded | Migration backups are safe but can accumulate | Add documented retention or a user-controlled cleanup command |
| P3 | No end-to-end browser test in CI | Unit tests will not catch every workflow or CSS regression | Add Playwright CI tests with mocked providers for create -> song -> plan -> preflight -> assemble guards |

## Recommended architecture, without rewriting the product

```text
Next.js UI
    |
FastAPI command/query API
    |
Job service ---- provider adapters (OpenRouter / fal / Suno)
    |                            |
durable queue                saved remote handles
    |
worker process ---- media validator / ffmpeg
    |
repository layer ---- SQLite local mode / PostgreSQL hosted mode
```

The important boundary is `Job service`: one idempotent command creates a local
job, one worker owns it, and provider adapters expose `submit`, `poll`, `cancel`,
and `recover`. The UI does not need to change substantially.

## Proposed next phases

### Phase 1 - completed in this audit

- Close data-loss, duplicate-charge, storage, restart, dependency, and major UX
  failure modes.
- Establish tests, CI, preflight, and operational documentation.

### Phase 2 - durable execution

1. Define a provider-neutral job state machine and idempotency key.
2. Move background task bodies behind a worker interface.
3. Start with an in-process/local worker implementation to preserve one-command
   startup; add Redis + ARQ/Dramatiq only for hosted mode.
4. Add a recovery sweeper and explicit terminal states.
5. Move compatibility SQL into Alembic revisions and test upgrades from a copy
   of the current database.

Completion criteria: killing either web process during a provider or assembly
job never creates duplicate paid work, and every job reaches a visible terminal
or resumable state after restart.

### Phase 3 - modularity and diagnostics

1. Split `generation_service.py` into orchestration, OpenRouter video, fal audio
   video, reference preparation, and asset activation modules.
2. Split `SceneGenRow.tsx` into state/controller hooks and focused presentational
   components.
3. Centralize character-name matching so backend and UI cannot drift.
4. Add structured event logs and a per-scene diagnostics drawer showing request
   snapshot, provider task ID, snapped settings, durations, and errors.

### Phase 4 - hosted/multi-user readiness, only if needed

1. PostgreSQL, authenticated users, project ownership, quotas, and rate limits.
2. Object storage with signed URLs and retention rules.
3. Separate API and worker deployment, health/readiness checks, and monitoring.
4. Provider contract tests and mocked browser E2E tests in CI.

## Release gate

Before treating a change as releasable:

```text
backend: ruff + pytest + pip-audit
frontend: typecheck + Vitest + production build + npm audit
runtime: health/models/project API smoke test
browser: project page renders, model sheet opens, no console/page errors
paid routes: preflight only unless a deliberate low-cost provider smoke test is approved
```

Paid provider generation was intentionally not triggered by the audit test run.
The integration logic is covered with mocked provider handles and should receive
an explicit, budgeted live smoke test when desired.
