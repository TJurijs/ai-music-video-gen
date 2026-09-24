# Model catalog review — 2026-09-25

## Wan 3 audio-reference connection — 25 September 2026

Wan 3 keeps two distinct routes: OpenRouter `alibaba/wan-3.0` for standard 2–30s video, and fal `alibaba/wan-3.0/reference-to-video` for the app's 2–15s song audio-reference mode. The fal provider permits longer output, but its reference audio is limited to 15 seconds total. The app limits this mode to complete scene-sized audio; it never quietly truncates a longer song segment.

The fal request uses `reference_image_urls` (up to 10), `reference_audio_urls` (one full PCM WAV song segment), integer `duration`, `resolution`, `aspect_ratio`, `prompt`, and the documented audio/safety/prompt-expansion settings. These are image references, not an exact first-frame anchor. The `audio` output flag is distinct from the audio-reference input; assembly continues to use the original song. A prompt can refer to the uploaded song as “Audio 1.”

Both routes offer 480p / 720p / 1080p; fal prices are $0.05 / $0.10 / $0.20 per output second ($0.75 / $1.50 / $3.00 for 15s). Supported fal aspect choices are 16:9, 4:3, 1:1, 3:4, 9:16. UI provider labels name the actual route and mark audio-reference use explicitly. Contract and offline tests validate integration; precise singing/lip-sync quality is untested live.

Sources: [fal Wan 3 request schema](https://fal.ai/models/alibaba/wan-3.0/reference-to-video/api), [fal pricing](https://fal.ai/models/alibaba/wan-3.0/reference-to-video), [Alibaba input constraints](https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference).

## Song-driven fal routes checked 24 September 2026

The public fal endpoint schemas and pricing pages were checked without authenticated provider calls. **No live video generation was submitted.** The integration is covered by offline HTTP contract tests, real local ffmpeg timing checks, and saved-job recovery tests; singing quality and provider availability have not been live-tested in this review.

| App model and route | Accepted app output | Audio / frame behavior | Estimated 15s cost |
| --- | --- | --- | --- |
| Wan 2.7 song sync — `fal-ai/wan/v2.7/image-to-video` | 2–15s, 720p / 1080p | WAV driving audio plus exact first frame; character appearance comes from that frame | $1.50 / $2.25 |
| LTX 2.5 Fast — `lightricks/ltx-2.5/audio-to-video/fast` | 2–20s, **1080p only**, 16:9 / 9:16 | Audio is required and determines length; scene still/chained frame supplied as `image_url` | $1.95 |

Wan's OpenRouter route remains 2–10s. Its fal song-sync route has its own 2–15s limit, so selecting a 15s Wan scene requires song sync. LTX is fal-only and always requires the song; it has no fabricated OpenRouter fallback. Unsupported resolutions or aspects are rejected before upload/submission.

The **top-level LTX audio-to-video schema** accepts `audio_url`, optional `image_url`, `prompt`, `guidance_scale`, and `aspect_ratio`. It does **not** accept `duration`, `resolution`, `fps`, `generate_audio`, separate character references or `end_image_url`. The same API page contains other I2V/T2V schemas with these fields; they are not the A2V contract. The app sends only the A2V fields and uses the page's fixed 1080p rate of $0.13 per input-audio second. Wan's rate is $0.10/s at 720p or $0.15/s at 1080p; its prompt limit is 5,000 characters and first-frame limit is 20 MB.

Both routes receive a PCM WAV sliced to the full chosen scene duration. A final scene extending beyond the song is padded with silence, preserving the 15s scene plan without changing the existing song samples. Seedance's 150ms audio safety margin does not apply to these routes. Small frame-grid differences in returned video are conformed to the planned length; materially mismatched lengths cannot replace the active clip. Saved fal jobs keep their endpoint, queue URLs, duration, route and estimate, so recovery retrieves the original request instead of billing another render. Scene chaining passes the prior rendered last frame as the next first frame. Target end frames and separate portrait slots are not sent by either app audio route.

Sources: [Wan API schema](https://fal.ai/models/fal-ai/wan/v2.7/image-to-video/api), [Wan pricing](https://fal.ai/models/fal-ai/wan/v2.7/image-to-video), [LTX Fast A2V schema](https://fal.ai/models/lightricks/ltx-2.5/audio-to-video/fast/api), [LTX Fast A2V pricing](https://fal.ai/models/lightricks/ltx-2.5/audio-to-video/fast).

## OpenRouter review — 17 September 2026

The app catalog was checked against the live public OpenRouter APIs on **17 September 2026**. Discovery used read-only requests; no generation was submitted or charged. The frozen subset in `backend/tests/fixtures/model_catalog_2026_09_17.json` records the provider capability and pricing fields used by the offline contract tests.

## Added routes

| App model | Provider model ID | Supported app output | Estimated video price per second |
| --- | --- | --- | --- |
| Wan 3.0 | `alibaba/wan-3.0` | 2–30s; 480p, 720p, 1080p; first frame or character references | $0.05 / $0.10 / $0.20 |
| Seedance 2.5 | `bytedance/seedance-2.5` | 4–30s; 480p, 720p; first frame or character references | ~$0.1028 / $0.2311 |
| Seedance 2.0 Mini | `bytedance/seedance-2.0-mini` | 4–15s; 480p, 720p; first frame or character references | ~$0.0336 / $0.0756 |
| MiniMax H3 Max | `minimax/hailuo-3-max` | 5–15s; 480p, **768p**; first frame | $0.05 / $0.08 |
| Runway Gen-4.5 | `runway/gen-4.5` | 2–10s; 720p; first frame | $0.12 |
| Gemini 3.1 Flash Lite Image | `google/gemini-3.1-flash-lite-image` | Image generation and reference-image input through chat completions | ~$0.04/image estimate |
| Gemini 3.8 Flash | `google/gemini-3.8-flash` | Planning, JSON responses, image input for continuation | Token billing |
| Gemini 3.5 Flash Lite | `google/gemini-3.5-flash-lite` | Planning, JSON responses, image input for continuation | Token billing |

The five video routes were listed by OpenRouter in July–September 2026. Flash Lite Image was already listed in June but was missing from the app. Gemini 3.8 Flash and 3.5 Flash Lite were listed in September and July respectively. Existing selected model keys and project defaults are preserved.

## Corrections

- Existing Gemini 3 Pro Image and Gemini 3.1 Flash Image keys now use the stable IDs `google/gemini-3-pro-image` and `google/gemini-3.1-flash-image`. The stable IDs accept the same chat-image route as the previews. Flash's estimate is now $0.08 rather than $0.04; actual billed usage replaces this estimate when the provider returns it.
- Both stable image variants and Flash Lite Image advertise image input/output in the chat catalog. The image capability catalog also advertises 16:9, 9:16 and 1:1, among other aspect ratios, and up to 14 reference images. Flash Image's model page documents `image_config` for aspect-ratio control on the chat route.
- Seedance 2.0 Fast now estimates $0.09072/s at 720p using the current $0.0000042/video-token SKU. Its description correctly allows 15 seconds.
- Seedance 2.0 now uses its resolution-specific SKUs: $0.0000077/video-token at 1080p and $0.000004/video-token at 4K, approximately $0.37422/s and $0.7776/s at 16:9.
- The old `gpt-image-1` label incorrectly said “GPT Image 1.5.” It now identifies GPT Image 1 correctly and is marked unavailable in the app. The legacy `seedream-4.5` key is also marked unavailable. These entries were sent to `/chat/completions` despite not being listed as compatible chat-image models. OpenRouter provides these model families through its separate `/images` API; Seedream also uses the `bytedance-seed` namespace there. Existing scene selections remain readable, and users receive instructions to select an available Gemini image model.
- Removed unconditional portrait-acceptance promises from Kling descriptions. Provider moderation can still reject a request.
- Planning cost descriptions no longer incorrectly identify all requests as Claude Sonnet.

## Adapter limits and pricing

Catalog availability is provider verification, **not a completed paid render test**. Provider latency, moderation, account restrictions, and upstream availability still affect generation.

New video entries use the existing OpenRouter `/videos` submit/poll/download adapter. At the September 17 review, none of these five entries accepted the song in the app; the September 25 Wan 3 integration above supersedes that limitation for Wan 3. New Seedance variants do not expose the app's audio-sync switch: the provider advertises audio reference capabilities, but the app has no connected audio route for those variants. Song-driven generation remains connected through the existing Seedance 2.0, Seedance 2.0 Fast and Wan 2.7 fal routes. Those routes and their estimates were retained; they were not newly reverified in this catalog review. All new OpenRouter submissions disable generated audio. Original song audio is added during assembly for every model; this does not imply motion or lip sync to that song.

OpenRouter documents first/last-frame images and character references as separate modes. If both are sent, frame inputs win. The app sends either a first frame or named character portraits, never both. Character mode requires matching names in scene text and saved portrait files, and cannot be combined with scene chaining. Wan 3.0, Seedance 2.5 and Seedance 2.0 Mini have this separate character mode; H3 Max and Runway carry appearance only through the first-frame still.

**End-frame targeting is not connected in the editor or scene pipeline.** Provider capability flags for last-frame support are retained as metadata, but are not usable app controls. Scene chaining is connected: it reuses the previous rendered clip's final frame as the next clip's first frame. Wan 3.0 and Runway Gen-4.5 also lack provider last-frame targeting. H3 Max does not accept 720p; use 480p or 768p. Video reference-count limits are not currently validated per model.

Seedance prices are estimates for 24 fps at 16:9: `width × height × duration × 24 / 1024 × token price`. Other aspect ratios, exact output dimensions, input charges, reference assets, and provider pricing changes can change the final charge. The displayed video rates, preflight estimate and saved video-job costs share the catalog rates; video-job costs are estimates, not reconciled provider invoices. Image prices are planning estimates, not flat provider quotes. The catalog does not imply a zero-cost retry. Paid generation was intentionally excluded from verification.

## Sources

- [OpenRouter video model catalog](https://openrouter.ai/api/v1/videos/models): exact route IDs, durations, resolutions, aspect ratios, frame capabilities and pricing SKUs.
- [OpenRouter chat model catalog](https://openrouter.ai/api/v1/models): chat-image output capabilities and JSON/image support for planning models.
- [OpenRouter image capability catalog](https://openrouter.ai/api/v1/images/models): aspect-ratio lists, reference limits and image resolutions.
- [Video generation API](https://openrouter.ai/docs/guides/overview/multimodal/video-generation): unified request contract, mode precedence, lifecycle and audio behavior.
- [Image generation API](https://openrouter.ai/docs/guides/overview/multimodal/image-generation): separate `/images` API and model discovery contract.
- [Seedance 2.5](https://openrouter.ai/bytedance/seedance-2.5), [Seedance 2.0 Mini](https://openrouter.ai/bytedance/seedance-2.0-mini), [Wan 3.0](https://openrouter.ai/alibaba/wan-3.0), [MiniMax H3 Max](https://openrouter.ai/minimax/hailuo-3-max), [Runway Gen-4.5](https://openrouter.ai/runway/gen-4.5): model descriptions and release dates.
- [Gemini 3.8 Flash](https://openrouter.ai/google/gemini-3.8-flash), [Gemini 3.5 Flash Lite](https://openrouter.ai/google/gemini-3.5-flash-lite): current planner routes.
- [Gemini 3.1 Flash Image](https://openrouter.ai/google/gemini-3.1-flash-image), [Gemini 3 Pro Image](https://openrouter.ai/google/gemini-3-pro-image): stable image routes and chat aspect-ratio configuration.

Some model-page pricing summaries derive hypothetical rates for resolutions the model does not accept. The app's allowed resolutions come from the dedicated capability API, not those summaries.
