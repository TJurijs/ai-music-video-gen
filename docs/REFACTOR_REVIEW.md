# Reliability and usability review — 2026-09-17

## Findings and changes

| Finding | Change |
| --- | --- |
| A transient poll, download, or media-validation failure could mark an already-paid video request failed, allowing a new paid submission. | Keep the saved provider handle resumable until the provider confirms a terminal failure. Resume before inspecting current inputs or model selections. Use a saved output URL first and refresh expired URLs without submitting a new render. |
| Local cancellation could be mistaken for confirmed fal cancellation. | Distinguish provider-confirmed cancellation from local interruption; preserve the provider handle on the latter. |
| One failed callback aborted the rest of a scene batch. | Run up to two independent scenes concurrently per batch, isolate failures, honor queued cancellation, and wait for required chain predecessors. Already-submitted child jobs resume independently. |
| Windows diagnostic output could interrupt provider work with an invalid console handle. | Use a nonthrowing provider logger. |
| Partial downloads could leave incomplete files behind. | Retry safe GET requests with bounded backoff and publish the completed download atomically. Paid submissions are never automatically replayed. |
| The launcher enabled automatic backend reload and could inherit a stale frontend API destination. | Disable reload by default; allow explicit development opt-in. Pin the launched frontend to the backend's loopback port. Support legacy PowerShell hashing and prefer PowerShell 7 in the batch wrapper. |
| Project refreshes issued per-scene history queries. | Bulk-load histories and aggregate dashboard counts. A 20-scene project with characters requires seven SELECTs, independent of scene count; a scene-list read requires three. |
| A background read failure removed the editor, and generic errors falsely promised no changes occurred. | Preserve the last project view with a reconnect notice. Surface request uncertainty and limit automatic retries to reads. |
| Bulk generation and model settings could include busy scenes or hide partial failures. | Use shared readiness rules, stage still/video actions, show progress and searchable status filters, disable conflicting actions, and bound settings writes. Keep Resume separate from submitting another render. |
| Scene planning retried paid requests after ambiguous network failures. | Stop automatic replay, refresh saved scenes, and explain the recovery state. |
| The configured Next.js and several dependency versions had published advisories. | Update Next.js to 16.3.5, Vitest to 4.1.11, PostCSS to 8.5.28, Sharp to 0.35.4, and affected transitive dependencies. |

The [model catalog review](MODEL_CATALOG.md) records eight added choices, corrected
capabilities/pricing, retired incompatible routes, official sources and the
offline provider-contract fixture. Existing project model keys remain intact.

## Verification

- Backend: CI Ruff selection (`F,E9`) and 129 tests passed, including paid-job recovery, queued cancellation, provider result retries, atomic downloads, query counts, catalog contracts, 16 new-model workflow cases, fixed-length planning and real ffmpeg export timing.
- Frontend: TypeScript, 15 unit tests and optimized production build passed.
- Dependency checks: `pip-audit -r requirements.txt` and npm audit reported no known vulnerabilities after patching.
- Windows launcher: PowerShell syntax check and actual app startup, using the lockfile dependency install path.
- Runtime/browser: health, models, existing projects and video preflight passed; inspected workflow navigation, status filters, model comparison, current portrait options and saved-video readiness. No browser console errors observed.
- Provider verification used public catalogs and mocked jobs. No paid generation was submitted.

## Remaining limits

Background work still runs inside the local API process. This is not a durable
worker queue, and concurrency is bounded per batch, not across every independent
request. A process interruption before a provider returns its task ID remains
ambiguous; it cannot be made exactly resumable without provider idempotency or a
reconciliation API. Synchronous image/planning calls have the same limitation.
Provider moderation, availability and account restrictions can still reject work.
The new model routes are catalog-verified; live rendering quality and latency have
not been benchmarked. The macOS/Linux launcher changes were not run on this Windows host.

The five new video models have first-frame/chaining integration and shared
estimated pricing. Wan 3.0 and the two new Seedance variants also have separate
named character-reference mode. None of the five has song-driven audio input
connected in this app, and end-frame targeting is not connected for any model.
The UI now distinguishes these missing app features from provider capabilities.
Video job costs remain estimates, not reconciled invoices.

## Fixed scene lengths — 2026-09-18

Planning now uses the selected whole-second length exactly instead of evenly
redistributing the song duration and rounding boundaries. A 151.96-second song
at 15 seconds produces 11 full 15-second clips, with the final clip trimmed at
the song end during export. The full closing clip is included in generation
cost estimates. The selected length is saved with the project. Continuation
batches reject changed lengths or inconsistent saved timing before provider
submission. The UI explains fixed lengths and the closing trim.

Audio and video now encode together during assembly. A real ffmpeg regression
test found the previous stream-copy mux could lose closing frames at `-shortest`;
the single-pass export preserves the ending within one video frame. Tests cover
song endings within a clip, exact clip boundaries and partial-project exports.

Project 5 was backed up and corrected without provider calls: its original ten
scene IDs and prompts remain, timing is now contiguous at 15 seconds per scene,
lyrics were resliced, and a chained closing hold covers the song remainder.

For a future larger deployment, the durable worker and migration plan in
[AUDIT.md](../AUDIT.md) remains applicable.
