# Video model usage audit

Checked **2026-09-25** against the active `backend/musicvideo.db`, opened read-only. No provider requests, generation, asset activation, or database updates were performed for this audit. The separate root-level `musicvideo.db` contains two projects but no scenes or generation jobs; it is not the active studio database.

## Scope and counting rules

- Six retained projects; five have video generation history. Project job totals: #1 = 32, #2 = 0, #3 = 55, #4 = 26, #5 = 25, #6 = 32.
- **170 video job records**, dated 2026-05-11 through 2026-09-24: **141 completed, 29 failed, zero pending/running/cancelled**.
- **151 accepted provider jobs** have an external handle, saved result, or completed status. Another **15 attempts received a provider rejection at submission**. Four failed locally without evidence that they reached a provider. Thus **166 provider attempts have outcome evidence**.
- Model attribution uses the immutable request snapshot first: 111 jobs have snapshots. The other 59 are attributed from their saved billing labels, matching the longest exact model name to distinguish Fast, Mini, Lite, and base variants. All 170 jobs could be attributed. Current scene selections are not historical usage.
- **123 retained video assets**, including **12 locally retimed variants**, are separate from provider-job counts. There are 64 active video assets across the projects. A completed job can have no retained asset; several local variants can also come from one paid render.
- These counts describe retained records, not all lifetime activity. Deleted projects or jobs cannot be reconstructed here, and retries count as separate attempts. `completed` includes locally recovered outputs; it does not mean the provider originally met the requested duration or visual quality.

## Models to retain

Keep models with attempted generation, including those whose attempts all failed. Removing failure-only models would hide the most useful guardrail evidence.

| Model key | Jobs | Accepted | Completed | Failed | Confirmed policy | Provider attempts | Project IDs | Active scenes | Saved assets | Local retimings |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| `wan-2.7` | 58 | 58 | 58 | 0 | 0 | 58 | 3, 6 | 23 | 58 | 0 |
| `veo-3.1-lite` | 29 | 29 | 27 | 2 | 0 | 29 | 1, 3 | 1 | 2 | 0 |
| `kling-v3.0-std` | 27 | 27 | 27 | 0 | 0 | 27 | 1, 3, 4 | 18 | 23 | 0 |
| `ltx-2.5-fast` | 17 | 17 | 14 | 3 | 3 | 17 | 6 | 10 | 26 | 12 |
| `grok-imagine-video-1.5` | 16 | 13 | 13 | 3 | 0 | 15 | 3, 5 | 11 | 13 | 0 |
| `seedance-2.0-mini` | 11 | 0 | 0 | 11 | 11 | 11 | 5 | 0 | 0 | 0 |
| `seedance-2.0` | 5 | 3 | 0 | 5 | 5 | 5 | 1, 3, 6 | 0 | 0 | 0 |
| `veo-3.1` | 4 | 1 | 1 | 3 | 0 | 1 | 4 | 0 | 0 | 0 |
| `seedance-2.0-fast` | 2 | 2 | 0 | 2 | 2 | 2 | 6 | 0 | 0 | 0 |
| `kling-v3.0-pro` | 1 | 1 | 1 | 0 | 0 | 1 | 3 | 1 | 1 | 0 |
| **Total** | **170** | **151** | **141** | **29** | **21** | **166** | **5 projects** | **64** | **123** | **12** |

## Guardrails: what the history actually shows

The interface should present observed counts with denominators, rather than universal labels such as “permissive” or “strict.” The sample is selected, repeated prompts and inputs are not independent, and a successful render does not imply all content will be accepted.

| Model and route | Confirmed policy failures / provider attempts | Interpretation supported by retained errors |
|---|---:|---|
| Seedance 2.0 Mini / OpenRouter | 11 / 11 | All failed at submission with an explicit sensitive-input-image / real-person privacy code. No accepted provider handles. |
| Seedance 2.0 / OpenRouter | 2 / 2 | Explicit image privacy refusals at submission. |
| Seedance 2.0 / fal | 3 / 3 | Result responses explicitly classify supplied-image likeness/privacy refusals as content-policy violations. One older record lacks the exact adapter snapshot. |
| Seedance 2.0 Fast / fal | 2 / 2 | Result responses explicitly classify supplied-image likeness/privacy refusals as content-policy violations. |
| LTX 2.5 Fast / fal | 3 / 17 | Explicit content-checker refusals assigned to the prompt field. These are repeated attempts, not three independent scenes or a reliable general rejection rate. |
| Veo 3.1 Lite / OpenRouter | 0 / 29 confirmed | Two empty-output failures mention that content *may* have been filtered. Keep them as **possible filtering**, not confirmed policy violations. |
| Wan 2.7 / OpenRouter | 0 / 25 | No policy failures recorded. |
| Wan 2.7 / fal | 0 / 33 | No policy failures recorded. Nine older records lack the exact adapter snapshot; 24 identify `wan-i2v-audio`. |
| Kling 3.0 Standard / OpenRouter | 0 / 27 | No policy failures recorded. |
| Grok Imagine Video 1.5 / OpenRouter | 0 / 15 | Two failures were insufficient credits. A separate local restart failure is excluded from this denominator. |
| Veo 3.1 / OpenRouter | 0 / 1 | Only one evidenced provider outcome. Three local invalid-argument failures provide no moderation evidence. |
| Kling 3.0 Pro / OpenRouter | 0 / 1 | Only one evidenced provider outcome; insufficient evidence for a broad moderation claim. |

There are **21 confirmed policy failures**, **two possible filtering failures**, **two credit failures**, and **four local failures**. This exactly accounts for the 29 failed video job records. Historical errors should never be surfaced wholesale: provider responses may embed prompts, private media locations, or request identifiers.

## Models with no retained video usage

The following seven catalog entries have **zero video jobs and zero saved video assets** and can be removed from normal model pickers without deleting historical media or changing already generated clips:

- `wan-3.0`
- `seedance-2.5`
- `hailuo-3-max`
- `runway-gen-4.5`
- `seedance-1.5-pro`
- `veo-3.1-fast`
- `hailuo-2.3`

The catalog may retain their definitions internally for decoding saved settings or migration compatibility. Selecting a model in a scene without ever generating does not make it a used model.

**User override, September 25:** Wan 3.0 was explicitly restored to the selectable catalog despite zero recorded attempts. Its fal reference-to-video route is now connected for song audio reference, capped at 15 seconds in the app so the complete scene segment can be supplied. Standard OpenRouter generation still supports up to 30 seconds. Contract and offline workflow checks do not establish singing/lip-sync quality; no live Wan 3 render was submitted during integration. The other six unused models remain hidden.

## Capability alignment cautions

- Usage history establishes what was attempted and what failed; it cannot establish every supported duration, resolution, first-frame, last-frame, reference, or audio combination. Those must remain tied to the specific provider contract and the implemented adapter.
- OpenRouter and fal routes for the same model have different inputs, lengths, prices, and observed outcomes. Keep their history distinguishable.
- A rendered person in a first-frame image is not proof that separate character references were accepted. Old billing labels can mention reference counts even when an adapter did not use that input.
- The app's use of the preceding clip's extracted final image as the next clip's **first frame** is different from sending a target **last frame** to a provider. Target-last-frame control remains unconnected.
- LTX assets include local retiming/recovery. Their final 15-second or song-tail lengths do not prove that LTX originally produced those exact lengths.

`backend/app/services/model_inventory.py` computes the live, sanitized inventory for the app. Its tests cover snapshot precedence, legacy model-name matching, failed submission accounting, possible versus confirmed moderation, local retiming, route separation, and exclusion of untried current scene selections.
