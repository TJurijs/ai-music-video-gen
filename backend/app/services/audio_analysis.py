"""Audio analysis: beat detection, section segmentation, transcription stitching.

librosa handles beat/section analysis locally.
Transcription tries fal-ai/whisper first (word-level timestamps), falls back
to OpenRouter chat-completions (lyrics text only, no timestamps).
"""

import numpy as np
import librosa
from typing import Optional
from app.config import settings
from app.services import openrouter, fal_client


async def analyze_song(audio_path: str, existing_lyrics: Optional[str] = None) -> dict:
    """
    Full analysis pipeline. Returns:
    {
        duration, bpm, key,
        beats: [float, ...],
        sections: [{start, end, label}, ...],
        transcription: [{word, start, end, confidence}, ...],
        lyrics: str
    }
    """
    beats, bpm, key, sections, duration = _analyze_audio(audio_path)
    transcription, lyrics = await _transcribe(audio_path, existing_lyrics)

    return {
        "duration": duration,
        "bpm": round(bpm, 1),
        "key": key,
        "beats": beats,
        "sections": sections,
        "transcription": transcription,
        "lyrics": lyrics,
    }


def _analyze_audio(audio_path: str) -> tuple:
    """Returns (beat_times, bpm, key_str, sections, duration)."""
    y, sr = librosa.load(audio_path, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # Tempo and beats
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    bpm = float(tempo) if np.isscalar(tempo) else float(tempo[0])

    # Key estimation
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)
    note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key_idx = int(np.argmax(chroma_mean))
    key_str = note_names[key_idx]

    # Section segmentation via MFCCs + agglomerative clustering
    sections = _detect_sections(y, sr, duration)

    return beat_times, bpm, key_str, sections, duration


def _detect_sections(y: np.ndarray, sr: int, duration: float) -> list:
    """Return list of {start, end, label} dicts for musical sections."""
    try:
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        # Number of segments: roughly 1 per 20 seconds, min 2 max 10
        n_segments = max(2, min(10, int(duration / 20)))
        bounds, labels = librosa.segment.agglomerative(mfcc, k=n_segments)
        bound_times = librosa.frames_to_time(bounds, sr=sr)

        sections = []
        section_labels = ["intro", "verse", "pre-chorus", "chorus", "bridge", "outro"]
        for i, (start, end) in enumerate(zip(bound_times[:-1], bound_times[1:])):
            label = section_labels[i % len(section_labels)]
            sections.append({
                "start": round(float(start), 3),
                "end": round(float(end), 3),
                "label": label,
            })
        # Ensure last section ends at duration
        if sections:
            sections[-1]["end"] = round(duration, 3)
        return sections
    except Exception:
        # Fallback: one section covering the whole track
        return [{"start": 0.0, "end": round(duration, 3), "label": "section"}]


async def _transcribe(audio_path: str, existing_lyrics: Optional[str]) -> tuple:
    """Returns (word_list, lyrics_str).

    Path A: fal-ai/whisper with chunk_level=word — gives real per-word
    timestamps. Preferred when FAL_API_KEY is set.

    Path B: OpenRouter chat-completions — lyrics text only, no timestamps.
    Used as a fallback when fal isn't configured.
    """
    if settings.fal_api_key:
        try:
            return await _transcribe_fal_whisper(audio_path)
        except Exception as e:
            print(f"[transcribe] fal whisper failed, falling back to OpenRouter: {e}")
            # Fall through to OpenRouter

    try:
        result = await openrouter.transcribe_audio(audio_path)
    except Exception as e:
        if existing_lyrics:
            return [], existing_lyrics
        raise RuntimeError(f"Transcription failed: {e}") from e

    words = []
    for w in result.get("words", []):
        words.append({
            "word": w.get("word", "").strip(),
            "start": round(float(w.get("start", 0)), 3),
            "end": round(float(w.get("end", 0)), 3),
            "confidence": round(float(w.get("probability", 1.0)), 3),
        })

    segments = result.get("segments", [])
    if segments:
        lyrics = "\n".join(seg.get("text", "").strip() for seg in segments)
    else:
        new_text = (result.get("text") or "").strip()
        # Fresh transcription wins; only fall back to existing if model returned nothing
        lyrics = new_text or (existing_lyrics or "")

    return words, lyrics.strip()


async def _transcribe_fal_whisper(audio_path: str) -> tuple:
    """Transcribe via fal-ai/whisper with word-level timestamps."""
    audio_url = await fal_client.upload_file(audio_path)
    request_id = await fal_client.submit(
        "fal-ai/whisper",
        {
            "audio_url": audio_url,
            "task": "transcribe",
            "chunk_level": "word",   # ← word-level timestamps
            "diarize": False,
            "batch_size": 64,
        },
    )
    result = await fal_client.poll("fal-ai/whisper", request_id, timeout=600, interval=5)

    text = (result.get("text") or "").strip()
    chunks = result.get("chunks") or []
    words: list = []
    for c in chunks:
        ts = c.get("timestamp") or [None, None]
        start, end = ts[0], ts[1] if len(ts) > 1 else None
        if start is None or end is None:
            continue
        words.append({
            "word": (c.get("text") or "").strip(),
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "confidence": 1.0,  # fal whisper doesn't return confidence
        })
    return words, text


def words_in_range(words: list, start: float, end: float) -> str:
    """Extract lyrics text for a time range."""
    in_range = [w["word"] for w in words if w["start"] >= start and w["end"] <= end]
    return " ".join(in_range).strip()


def beats_in_range(beats: list, start: float, end: float) -> list:
    return [b for b in beats if start <= b <= end]
