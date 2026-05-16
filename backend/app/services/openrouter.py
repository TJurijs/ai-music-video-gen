"""OpenRouter API client — covers video, image, audio, music, and LLM calls."""

import asyncio
import base64
import os
import httpx
from typing import Optional
from app.config import settings, OPENROUTER_BASE


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Music Video Studio",
    }


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

async def chat(messages: list, model: Optional[str] = None, json_mode: bool = False, timeout: int = 300) -> str:
    """Send a chat completion. Default timeout is 300s — large scene plans
    can produce 50KB+ JSON responses that take 60-120s on slower models."""
    model = model or settings.default_llm_model
    payload: dict = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter chat {r.status_code}: {r.text[:600]}")
        data = r.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content")
        if content is None:
            # Gemini returns null content on safety refusals; surface the reason.
            refusal = msg.get("refusal") or data.get("error", {}).get("message") or ""
            raise RuntimeError(f"OpenRouter chat returned null content. Refusal: {refusal[:300] or '(no reason given)'}")
        return content


# ---------------------------------------------------------------------------
# Image generation  (chat completions with image modality)
# ---------------------------------------------------------------------------

async def generate_image(
    prompt: str,
    model: Optional[str] = None,
    reference_image_paths: Optional[list[str]] = None,
    aspect_ratio: Optional[str] = None,
    _retry_count: int = 0,
) -> bytes:
    """Returns raw image bytes.

    If reference_image_paths is provided, they're sent as multi-image input
    so the model preserves character/style identity. Only useful with image
    models that support multi-image conditioning (e.g. Gemini 2.5 Flash Image).

    aspect_ratio (e.g. "16:9", "9:16", "1:1") is appended to the prompt as
    an explicit instruction. Image models on OpenRouter don't take aspect
    ratio as a structured parameter, but they reliably honor prompt-level
    cues when they're emphatic.

    Retry-on-text: Gemini 2.5 Flash Image sometimes returns a text description
    instead of an image (typical when the prompt mentions a real person's name
    and trips the likeness filter — the model "describes" the portrait but
    refuses to render). When that happens we retry once with a stricter
    "OUTPUT IMAGE ONLY" prefix to try to break the conversational fallback.
    """
    from app.config import IMAGE_MODELS
    model_key = model or settings.default_image_model
    model_id = IMAGE_MODELS.get(model_key, {}).get("model_id", "openai/gpt-image-1")

    # Inject aspect ratio cue if provided. Doubled emphasis because some
    # image models otherwise default to 1:1 regardless of prompt content.
    if aspect_ratio:
        prompt = (
            f"{prompt}\n\n"
            f"OUTPUT FORMAT: Render at {aspect_ratio} aspect ratio. "
            f"The frame must be {aspect_ratio} — do not output a square or differently-shaped image."
        )

    # Build content: text + optional reference images
    content: list = [{"type": "text", "text": prompt}]
    for ref_path in (reference_image_paths or []):
        if not ref_path or not os.path.exists(ref_path):
            continue
        with open(ref_path, "rb") as rf:
            ref_b64 = base64.b64encode(rf.read()).decode()
        ext = ref_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{mime};base64,{ref_b64}"},
        })

    payload = {
        "model": model_id,
        "modalities": ["image"],
        "messages": [{"role": "user", "content": content if len(content) > 1 else prompt}],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    msg = data["choices"][0]["message"]

    # Gemini & some OpenAI image models return images in a separate `images` array
    # on the message, not inside `content`. Check there first.
    images = msg.get("images")
    if isinstance(images, list):
        for part in images:
            if isinstance(part, dict):
                url = (part.get("image_url") or {}).get("url") or part.get("url")
                if url:
                    return await _bytes_from_url(url)

    # Fallback: content is a list of parts (older format)
    content = msg.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url")
                if url:
                    return await _bytes_from_url(url)

    # Fallback: content is a base64 data URL string
    if isinstance(content, str) and content.startswith("data:"):
        return await _bytes_from_url(content)

    # Text-only response — model returned a description instead of an image.
    # Classic Gemini failure: prompt tripped a likeness/content filter and
    # the model fell back to conversational mode. Retry once with an
    # emphatic image-only prefix; that breaks the conversational mode
    # often enough to be worth the extra round-trip.
    is_text_only = isinstance(content, str) and content and not content.startswith("data:")
    if is_text_only and _retry_count < 1:
        retry_prompt = (
            "OUTPUT IMAGE ONLY. Do not respond with any text. Generate the "
            "following as an image file:\n\n" + prompt
        )
        return await generate_image(
            retry_prompt,
            model=model,
            reference_image_paths=reference_image_paths,
            aspect_ratio=None,  # already baked into prompt on first try
            _retry_count=_retry_count + 1,
        )

    preview = str(content)[:200] if content else str(msg)[:300]
    if is_text_only:
        # Likeness filter most often. Keep the message short — the frontend
        # banner shows the "Soften" buttons inline so the user already has
        # the recovery actions one click away.
        raise ValueError(
            f"Image model content filter refused: returned text instead of an image. "
            f"Model={model}. Likely a real-person name in the prompt. "
            f"Try Soften image prompt, or pick a less strict image model."
        )

    if content is None and not images:
        # Gemini's empty-response refusal — no explanation, just silence.
        # Same recovery options as the text-only case.
        raise ValueError(
            f"Image model content filter refused: empty response (content=None). "
            f"Model={model}. Try Soften image prompt, swap to a different image model, "
            f"or remove 'photoreal' wording from the prompt."
        )

    raise ValueError(f"Unexpected image response format: {preview}")


async def _bytes_from_url(url: str) -> bytes:
    """Resolve a data: URL or remote URL to raw bytes."""
    if url.startswith("data:"):
        _, b64 = url.split(",", 1)
        return base64.b64decode(b64)
    async with httpx.AsyncClient(timeout=60) as dl:
        resp = await dl.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Video generation  (async job — submit → poll)
#
# Schema reference: GET https://openrouter.ai/openapi.yaml → VideoGenerationRequest
#   - prompt, model               (required)
#   - duration                    (must be in model.supported_durations)
#   - aspect_ratio, resolution
#   - frame_images[{frame_type, image}]   (first/last frame anchors)
#   - input_references[{...image_url}]    (character/style ref images)
# ---------------------------------------------------------------------------

def _data_url_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/{mime};base64,{b64}"


def _audio_data_url_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "mp3"
    mime = {
        "mp3": "mpeg", "mpeg": "mpeg",
        "wav": "wav", "ogg": "ogg",
        "m4a": "mp4", "aac": "aac", "flac": "flac",
    }.get(ext, "mpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:audio/{mime};base64,{b64}"


def _is_image_filter_error(error_text: str) -> bool:
    """Detect provider image-content / likeness rejections so we can degrade
    the payload and retry. Different models surface this with different
    error codes:
      - Seedance: 'InputImageSensitiveContentDetected.PrivacyInformation' /
        'input image may contain real person'
      - Kling: 'image contains sensitive content' (varies)
      - Veo: usually accepts photoreal portraits; if it rejects, message
        mentions 'safety' or 'personGeneration'
    Substring-matching against a few common signals keeps this resilient
    to error-message wording drift.
    """
    t = (error_text or "").lower()
    return any(
        sig in t for sig in (
            "inputimagesensitivecontentdetected",
            "privacyinformation",
            "real person",
            "real-person",
            "image may contain",
            "image contains sensitive",
            "persongeneration",
            "person generation",
            "celebrity",
            "likeness",
        )
    )


async def submit_video_job(
    prompt: str,
    model_id: str,
    duration: int = 8,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    first_frame_path: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    reference_image_paths: Optional[list[str]] = None,
) -> str:
    """Submit a video generation job. Returns the job ID for polling.

    On image-content-filter rejection, raises RuntimeError with actionable
    recovery instructions. We DELIBERATELY do not auto-degrade the payload —
    silently stripping the first_frame to get a render to succeed produces
    text-to-video output that breaks downstream chaining and confuses the
    user (the clip appears, but doesn't start on the expected pixel). The
    user picks the recovery path explicitly: switch to a permissive model
    (Kling / Veo) or activate a less-recognizable portrait variant.

    Note on audio: we never set `generate_audio` — the song's audio is
    muxed verbatim at assembly time, so model-generated audio would just
    get overwritten. The OpenRouter payload omits the field entirely (the
    model decides its default, which is "off" for all video models we use).
    """
    payload: dict = {
        "model": model_id,
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    # Veo accepts a personGeneration passthrough that controls its real-person
    # filter (default = dont_allow). Opt into allow_adult so photoreal
    # portraits / first-frames featuring people don't get rejected. Other
    # providers ignore unknown fields.
    if model_id.startswith("google/veo"):
        payload["personGeneration"] = "allow_adult"

    # FrameImage extends ContentPartImage: needs type="image_url" + image_url{url}
    # and adds frame_type alongside.
    frame_images: list = []
    if first_frame_path and os.path.exists(first_frame_path):
        frame_images.append({
            "type": "image_url",
            "image_url": {"url": _data_url_from_path(first_frame_path)},
            "frame_type": "first_frame",
        })
    if last_frame_path and os.path.exists(last_frame_path):
        frame_images.append({
            "type": "image_url",
            "image_url": {"url": _data_url_from_path(last_frame_path)},
            "frame_type": "last_frame",
        })
    if frame_images:
        payload["frame_images"] = frame_images

    refs: list = []
    for ref in (reference_image_paths or []):
        if ref and os.path.exists(ref):
            refs.append({
                "type": "image_url",
                "image_url": {"url": _data_url_from_path(ref)},
            })
    if refs:
        payload["input_references"] = refs

    # Log payload shape so we can confirm WHAT was actually sent. Doesn't
    # log the base64 image bytes — just the count + dimensions so it's
    # readable. Useful for "did the character ref actually go through?"
    print(
        f"[openrouter video] submit model={model_id} duration={duration}s "
        f"resolution={resolution} aspect={aspect_ratio} "
        f"first_frame={'yes' if first_frame_path else 'no'} "
        f"last_frame={'yes' if last_frame_path else 'no'} "
        f"input_references={len(refs)} ref(s) "
        f"prompt[:120]={prompt[:120]!r}"
    )

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/videos",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            error_text = r.text or ""

            # Image-content filter: previously this auto-degraded — dropping
            # character refs, then dropping first_frame, then falling back to
            # pure text-to-video. The user couldn't tell from the rendered
            # clip that the chain anchor had been dropped, so chained scenes
            # silently broke. Now we surface the rejection with actionable
            # text instead of silently degrading. The user picks the recovery
            # path: swap to a permissive model, activate a less-recognizable
            # portrait variant, etc.
            if _is_image_filter_error(error_text):
                what_was_rejected = []
                if reference_image_paths:
                    what_was_rejected.append(f"{len(refs)} character portrait(s)")
                if first_frame_path:
                    what_was_rejected.append("first_frame")
                if last_frame_path:
                    what_was_rejected.append("last_frame")
                payload_summary = " + ".join(what_was_rejected) or "image input(s)"

                suggestion = (
                    "Seedance has a stricter image-content filter than other models. "
                    "Options: (a) switch this scene to Kling 3.0 Pro/Std or Veo 3.1 — "
                    "both accept photoreal portraits where Seedance refuses; "
                    "(b) activate a less-recognizable portrait variant for this character "
                    "(blindfold / mask / heavy shadow / profile shot); "
                    "(c) regenerate the first_frame with the same obscuring treatment."
                )
                raise RuntimeError(
                    f"Image content filter refused: {payload_summary}. "
                    f"Model: {model_id}. {suggestion} "
                    f"Raw provider error: {error_text[:300]}"
                )
            # Other 4xx/5xx — surface as before.
            raise RuntimeError(
                f"OpenRouter video submit {r.status_code}: {error_text[:600]} "
                f"(model={model_id}, duration={duration}, resolution={resolution}, "
                f"aspect={aspect_ratio}, refs={len(refs)}, frames={len(frame_images)})"
            )
        # 2xx response — expect {"id": "..."} per OpenRouter docs. If the
        # shape changed (e.g., they renamed the field, or the body is empty,
        # or they return a list), the previous code raised bare KeyError('id')
        # which surfaced in the UI as just "'id'" — useless. Surface the
        # actual body so we can diagnose the schema drift.
        try:
            body = r.json()
        except Exception as e:
            raise RuntimeError(
                f"OpenRouter video submit returned 2xx but body wasn't JSON "
                f"({type(e).__name__}: {str(e)[:200]}). Raw: {r.text[:400]}"
            )
        # Try common shapes: {"id": ...}, {"data": {"id": ...}}, {"job_id": ...}
        if isinstance(body, dict):
            job_id = body.get("id") or body.get("job_id") or (
                body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None
            )
            if job_id:
                return job_id
        raise RuntimeError(
            f"OpenRouter video submit returned 2xx but no job id in the response. "
            f"Tried keys: 'id', 'job_id', 'data.id'. "
            f"Model: {model_id}. Response body: {str(body)[:600]}"
        )


async def get_video_status(job_id: str) -> dict:
    """One-shot poll: returns the raw video job status dict from OpenRouter."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{OPENROUTER_BASE}/videos/{job_id}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def poll_video_job(
    job_id: str,
    timeout: int = 600,
    interval: int = 15,
    is_cancelled = None,
) -> str:
    """Poll until video is ready; returns the download URL.

    is_cancelled is an optional sync callable returning True to abort polling
    (e.g. user pressed Stop). When triggered we raise asyncio.CancelledError so
    the surrounding pipeline can mark the scene cancelled.

    Transient network failures during polling (DNS blip, OpenRouter edge
    briefly unreachable, local Wi-Fi reconnect) used to kill the whole
    video gen — even though the job was still running on OpenRouter's side.
    We now absorb up to MAX_CONSECUTIVE_NETWORK_FAILS in a row before giving
    up. Real terminal errors (job failed, malformed response) still surface
    immediately.
    """
    MAX_CONSECUTIVE_NETWORK_FAILS = 4   # ~1 minute of failures at 15s interval
    consecutive_network_fails = 0
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if is_cancelled is not None and is_cancelled():
            raise asyncio.CancelledError(f"Video job {job_id} cancelled by user")
        try:
            data = await get_video_status(job_id)
            consecutive_network_fails = 0  # any success resets the counter
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError,
                httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout,
                httpx.PoolTimeout) as net_err:
            consecutive_network_fails += 1
            print(
                f"[poll {job_id}] transient network error "
                f"({type(net_err).__name__}: {str(net_err)[:120]}) — "
                f"attempt {consecutive_network_fails}/{MAX_CONSECUTIVE_NETWORK_FAILS}, "
                f"sleeping {interval}s and retrying"
            )
            if consecutive_network_fails >= MAX_CONSECUTIVE_NETWORK_FAILS:
                raise RuntimeError(
                    f"OpenRouter polling failed: {MAX_CONSECUTIVE_NETWORK_FAILS} consecutive "
                    f"network errors. Job {job_id} may still be running — refresh "
                    f"in a few minutes; the BackgroundTask's self-heal pass will "
                    f"mark it done if a video URL became available."
                ) from net_err
            await asyncio.sleep(interval)
            continue
        status = data.get("status")
        if status == "completed":
            urls = data.get("unsigned_urls") or data.get("urls") or []
            if urls:
                return urls[0]
            raise ValueError("Job completed but no URLs returned")
        if status in ("failed", "error"):
            raise RuntimeError(f"Video job failed: {data.get('error', 'unknown')}")
        await asyncio.sleep(interval)
    raise TimeoutError(f"Video job {job_id} timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Audio transcription  (word-level timestamps via Whisper)
# ---------------------------------------------------------------------------

async def transcribe_audio(audio_path: str, model: str = "google/gemini-2.5-flash") -> dict:
    """Transcribe audio via OpenRouter chat completions (audio input modality).

    OpenRouter does NOT host a Whisper-style transcription endpoint. Audio is
    sent as a base64 part in a chat message, and the response is plain text
    lyrics (no word-level timestamps). For timing we rely on librosa beats +
    section boundaries, not per-word timestamps.

    Returns {"text": "...", "segments": [], "words": []} for compatibility
    with the previous shape.
    """
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    filename = os.path.basename(audio_path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
    fmt = {"m4a": "m4a", "mp3": "mp3", "wav": "wav", "ogg": "ogg",
           "flac": "flac", "aac": "aac"}.get(ext, "mp3")

    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text":
                    "Transcribe this song's lyrics exactly as sung. "
                    "Output ONLY the lyrics, line by line, no commentary, no timestamps. "
                    "If the song is instrumental or you cannot make out lyrics, output the single word: instrumental"},
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": fmt}},
            ],
        }],
    }

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Transcription HTTP {r.status_code}: {r.text[:600]}")
        data = r.json()

    text = data["choices"][0]["message"]["content"]
    if isinstance(text, list):
        # Some providers return content as parts
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))

    text = (text or "").strip()
    if text.lower() in ("instrumental", "(instrumental)", "[instrumental]"):
        text = ""

    return {"text": text, "segments": [], "words": []}


# ---------------------------------------------------------------------------
# Music generation  (Google Lyria via OpenRouter)
# ---------------------------------------------------------------------------

async def generate_music_lyria(
    description: str,
    duration: str = "full",  # "full" = Lyria 3 Pro, "clip" = Lyria 3 Clip
) -> str:
    """Generate music with Lyria; returns a URL to the audio file."""
    model_id = "google/lyria-3-pro" if duration == "full" else "google/lyria-3-clip"

    # Lyria uses the audio generation endpoint (similar to TTS but for music)
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": description}],
        "modalities": ["audio"],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "audio_url":
                return part["audio_url"]["url"]
    if isinstance(content, str):
        return content

    raise ValueError(f"Unexpected Lyria response: {str(content)[:200]}")


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

async def download_file(url: str, dest_path: str) -> str:
    """Download a URL to a local path; returns the path.

    OpenRouter's video content endpoint (/api/v1/videos/{id}/content) requires
    auth even though the response calls them "unsigned_urls". Send the bearer
    token whenever the URL is on the OpenRouter API host.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    headers = _headers() if "openrouter.ai/api" in url else {}
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    f.write(chunk)
    return dest_path
