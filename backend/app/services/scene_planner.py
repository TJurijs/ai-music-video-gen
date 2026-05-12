"""LLM-based scene planner.

Takes a song's analysis data + project style + characters and produces
a list of Scene objects aligned to musical sections and beats.
"""

import json
from typing import Optional
from app.services import openrouter
from app.services.audio_analysis import words_in_range, beats_in_range
from app.services.llm_json import parse_llm_json, strip_fences_and_prose


SCENE_PLAN_SYSTEM = """You are a music video director and cinematographer.
Given song analysis + an optional story seed + a fixed cast of characters,
write a scene-by-scene plan that tells ONE coherent story through SIMPLE, OBSERVABLE ACTIONS.

CRITICAL — How the rendering pipeline works:
- For each scene, we render a STILL IMAGE (image_prompt → photorealistic frame).
- For each scene, we then render a VIDEO that starts ON THAT STILL and plays out the motion of ONE self-contained shot.
- Scenes are joined by HARD CUTS in the final assembly. No frame anchoring between scenes — adjacent clips are independent shots.

CRITICAL — What the underlying video models can and CAN'T render. Plan accordingly:
✓ RELIABLE — write actions like these:
  - Gross body motion: walks, turns, runs, sits, kneels, falls, reaches, lifts, throws, pushes, points
  - Head + gaze: turns head, looks at X, looks away, closes eyes
  - Camera moves: pan, push-in, dolly, truck, crane, tilt, handheld shake
  - Environment: rain falls, smoke billows, lights flicker, neon glows, wind catches fabric
  - Object interactions: picks up X, opens Y, drops Z, lights a cigarette
✗ UNRELIABLE — these get butchered or ignored, AVOID them:
  - Internal/emotional states: "longing", "remembers", "introspective", "conflicted"
  - Micro-expressions: "subtle smile", "tear forms", "eyes flicker with memory"
  - Narrative abstractions: "the past haunts her", "she chooses freedom"
  - Sub-second gestures: tiny finger movements, single-frame eye-flicks
  - Anything that requires reading the character's mind
The model can only render what a CAMERA WOULD SEE. Tell the story through what characters DO and WHERE THEY GO.

OUTPUT FORMAT — image_prompt and video_prompt MUST use this tagged structure:

  [STYLE]
  <COMPACT distillation of the project style as a comma-separated cue list. 1–3 short lines, ~30 words max. NEVER copy the project style prose verbatim — extract the essential visual cues (film stock, palette, lens, lighting feel, grain, references). Per-scene deltas only when the scene genuinely shifts (e.g. interior tungsten vs exterior neon).>

  [SCENE]
  <ONE observable moment / ONE observable action. Camera framing + characters (by name) + setting + simple action verb. This is where 80% of the prompt content lives — [STYLE] is intentionally short so [SCENE] gets the model's attention.>

If a single clip genuinely needs TWO distinct beats (e.g. character walks in, then opens a door), tag them [SCENE 1] and [SCENE 2] inside that prompt. Use sparingly — default is one [SCENE] per clip. Most scenes are one shot, one action.

[STYLE] distillation — bad vs good:
  ✗ BAD (overwhelms the scene): "The visual style is defined by a high-contrast 'Electric Noir' aesthetic, featuring a triadic color palette of neon cobalt, acid magenta, and hazardous sulfur-yellow set against deep, crushed-black shadows. Lighting is characterized by harsh, low-key silhouettes with ultra-bright colored rim lighting and frequent volumetric god-rays piercing through thick haze and heavy rain, using anamorphic lenses..."
  ✓ GOOD (compact cues): "Cyberpunk noir, anamorphic lens with horizontal blue streaks, neon cobalt + acid magenta + sulfur yellow on crushed blacks, volumetric god-rays through haze, 35mm grain."

Hard rules — non-negotiable:
1. Narrative arc through actions. Story advances via what characters DO, not what they feel. Beginning = arrival / setup. Middle = a turn / encounter / decision shown through action. End = resolution shown through action (departs / falls / embraces / shatters).
2. Character continuity. Use ONLY characters from the provided cast. Refer to each by EXACT name every time they appear in shot — this name binds the scene to their reference portrait downstream.
3. Setting + wardrobe continuity. Locations and looks introduced early should persist; new locations need a transition reason.
4. Image_prompt = opening frame. A pose that AFFORDS the video_prompt's motion. No description of inner state.
5. Video_prompt = observable verbs only. Body verbs, camera moves, environment shifts. NO emotion words.
6. Always name characters in BOTH prompts — never "she" / "the singer" / "the figure".

Respond ONLY with a valid JSON array of scene objects."""

SCENE_PLAN_USER = """Song: "{title}" {artist_line}
Style/Mood: {style}
Aspect Ratio: {aspect_ratio}
{tempo_line}

Cast (use these names verbatim — and only these):
{characters}

{theme_block}{story_seed_block}{lyrics_block}Musical sections and their lyrics:
{sections_with_lyrics}

Create {scene_count} scenes aligned to the music.
Cinematic rules:
- Align scene boundaries to beat markers or section boundaries when possible
- Match visual energy to musical energy (quiet verse = intimate, chorus = wide/dynamic)
- Vary shot types (wide, medium, close-up, POV, aerial) but keep characters recognizable
- If lipsync is possible (a character is singing on-camera), set lipsync_suggested=true

LYRICS DRIVE WHAT WE SEE — this is critical:
- For EVERY scene, read its lyrics_segment carefully. Find the CONCRETE imagery in it: places, objects, weather, body actions, materials, named characters, specific events.
- Reflect that concrete imagery DIRECTLY in the scene's image_prompt and/or video_prompt. The visual should make a viewer who hears the lyric think "yes, that".
  - Lyric "she walked through the rain" → scene shows walking in rain
  - Lyric "broken glass on the floor" → frame includes broken glass on the floor
  - Lyric "burning through the night" → fire / flame / glowing embers in the shot
  - Lyric "I held the ring tight" → character grips a ring
  - Lyric "the city was silent" → wide shot of an empty city street
  - Lyric "she lit a cigarette" → character lighting a cigarette
- For abstract / metaphorical lines ("my heart aches", "lost in time", "the world burns inside me"), DO NOT render literally. Use the line's MOOD to color the lighting / posture / camera pacing — but the on-screen action should be something concrete from elsewhere in the scene's lyric or from the story seed.
- Instrumental sections (no lyrics for those seconds): drive the visual entirely from the story seed's arc. Don't invent unrelated imagery.

The relationship between story seed and lyrics:
- The STORY SEED is the through-line — the overarching arc the video tells.
- The LYRICS are the per-scene flavor — concrete imagery to ground each beat.
- They work together: the seed says WHERE the story is going; the lyrics say WHAT we see along the way.
- If a lyric and the seed seem to disagree, follow the lyric for THIS scene — the seed's arc continues in the next.

Plan each scene as a SELF-CONTAINED SHOT joined by hard cuts. Tell the story through OBSERVABLE ACTIONS the video model can actually render — not internal states the model will misread.

Return a JSON array. Every image_prompt and video_prompt MUST use the [STYLE] / [SCENE] tagged format:

[
  {{
    "order": 1,
    "audio_start": 0,
    "audio_end": 12,
    "description": "Lena watches the city burn from a rooftop.",
    "image_prompt": "[STYLE]\\n1990s 35mm cross-processed film, deep cyan shadows, warm amber highlights, heavy film grain, photorealistic 8K.\\n\\n[SCENE]\\nWide shot: Lena (auburn hair, black leather jacket) stands at the rooftop ledge frame-left, facing the city. Distant fires glow orange across the skyline; smoke rises into the night. Golden rim light catches her silhouette.",
    "video_prompt": "[STYLE]\\n1990s 35mm cross-processed, film grain, photorealistic.\\n\\n[SCENE]\\nLena watches the burning city; flames flicker in the distance. The camera pushes in slowly. Embers drift past her in slow motion.",
    "lyrics_segment": "burning through the night",
    "lipsync_suggested": false,
    "camera": "wide → push-in",
    "mood": "epic, contemplative",
    "characters_in_scene": ["Lena"]
  }},
  {{
    "order": 2,
    "audio_start": 12,
    "audio_end": 20,
    "description": "Lena steps over broken glass and opens a door.",
    "image_prompt": "[STYLE]\\n1990s 35mm, magenta neon practicals, film grain, photorealistic.\\n\\n[SCENE]\\nMedium shot: Lena facing camera-right, standing in a corridor strewn with shards of broken glass that catch the neon light. A door upstage. Shallow depth of field.",
    "video_prompt": "[STYLE]\\n1990s 35mm, magenta neon, photorealistic.\\n\\n[SCENE 1]\\nLena walks forward; broken glass crunches and glints under her boots. The camera trucks with her left-to-right.\\n\\n[SCENE 2]\\nLena reaches the door, grips the handle, and pulls it open.",
    "lyrics_segment": "stepping over what we broke",
    "lipsync_suggested": false,
    "camera": "medium tracking",
    "mood": "resolute",
    "characters_in_scene": ["Lena"]
  }},
  ...
]
(Notice: scene 1's lyric "burning through the night" → fires, smoke, embers. Scene 2's lyric "stepping over what we broke" → broken glass underfoot. The lyrics' concrete imagery shows up as on-screen content.)

Rules of thumb for each scene:
- Pick ONE clear action for the scene (or two if the duration is long and the music supports two beats).
- Image_prompt = the pose at frame 1. Composition + framing + setting + lighting. No motion, no inner state.
- Video_prompt = the action played out. Simple verbs (walks, turns, looks, reaches, falls, lights, opens, throws). No emotions, no thinking, no memory.
- Use [SCENE 1] / [SCENE 2] inside one video_prompt ONLY when two clearly distinct beats fit in the clip. Default to one [SCENE].
- The [STYLE] block in each prompt should carry the project's visual style consistently. Per-scene tweaks (e.g. "now indoors, tungsten warmth replaces magenta neon") only when the scene actually shifts environments."""


async def auto_plan_scenes(
    title: str,
    artist: str,
    style: str,
    aspect_ratio: str,
    bpm: float,
    key: str,
    sections: list,
    beats: list,
    words: list,
    characters: list,
    target_scene_duration: float = 8.0,
    duration: float = 0.0,
    llm_model: str | None = None,
    story_seed: str | None = None,
    theme_analysis: dict | None = None,
    full_lyrics: str | None = None,
) -> list:
    """Generate a scene plan and return list of scene dicts."""

    sections_with_lyrics = _build_sections_text(sections, words, beats)
    scene_count = max(3, int(duration / target_scene_duration)) if duration else len(sections) * 2

    characters_text = "\n".join(
        f"  - {c['name']}: {c['description']}" for c in characters
    ) if characters else "  (none defined — invent ONE protagonist consistent with the song's mood and use the same character throughout)"

    artist_line = f"by {artist}" if artist else ""

    if story_seed and story_seed.strip():
        story_seed_block = (
            f"Story direction (the user's narrative seed — anchor the plan to this):\n"
            f"  {story_seed.strip()}\n\n"
        )
    else:
        story_seed_block = ""

    # Theme analysis (mood, narrative, visual_world) was generated in a
    # separate pass after transcription. Feed it back so the planner respects
    # the song's actual narrative instead of re-deriving from raw lyrics.
    if theme_analysis and isinstance(theme_analysis, dict):
        theme_lines = []
        for key_, label in [
            ("theme", "Central theme"),
            ("narrative", "Narrative summary"),
            ("mood", "Emotional mood"),
            ("visual_world", "Visual world"),
            ("suggested_visual_style", "Suggested visual style"),
        ]:
            val = theme_analysis.get(key_)
            if val and isinstance(val, str) and val.strip():
                theme_lines.append(f"  {label}: {val.strip()}")
        chars = theme_analysis.get("characters_in_lyrics") or []
        if chars and isinstance(chars, list):
            theme_lines.append(f"  Characters mentioned in lyrics: {', '.join(chars)}")
        if theme_lines:
            theme_block = "Lyric / theme analysis (anchor the visual story to this):\n" + "\n".join(theme_lines) + "\n\n"
        else:
            theme_block = ""
    else:
        theme_block = ""

    if full_lyrics and full_lyrics.strip():
        # Cap to avoid context blow-up on long songs
        capped = full_lyrics.strip()
        if len(capped) > 4000:
            capped = capped[:4000] + "\n[lyrics truncated]"
        lyrics_block = f"Full lyrics:\n```\n{capped}\n```\n\n"
    else:
        lyrics_block = ""

    tempo_line = _format_tempo_line(bpm, key)

    user_msg = SCENE_PLAN_USER.format(
        title=title,
        artist_line=artist_line,
        style=style or "cinematic, modern music video",
        aspect_ratio=aspect_ratio,
        characters=characters_text,
        tempo_line=tempo_line,
        sections_with_lyrics=sections_with_lyrics,
        scene_count=scene_count,
        story_seed_block=story_seed_block,
        theme_block=theme_block,
        lyrics_block=lyrics_block,
    )

    raw = await openrouter.chat(
        messages=[
            {"role": "system", "content": SCENE_PLAN_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        model=llm_model,
        json_mode=True,
    )

    scenes = _parse_json_scenes(raw)
    return scenes


def _parse_json_scenes(raw: str) -> list:
    """Parse the scene-planner LLM response.

    The base `parse_llm_json` handles fences + prose. We add tolerance for
    the model wrapping the array inside `{"scenes": [...]}` (or any other
    one-key object with a list value) instead of returning a bare list."""
    text = strip_fences_and_prose(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Scene planner returned invalid JSON: {e}\n{raw[:500]}")
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return parsed.get("scenes") or next((v for v in parsed.values() if isinstance(v, list)), [])
    raise ValueError(f"Scene planner returned unexpected JSON type {type(parsed).__name__}: {raw[:300]}")


async def soften_prompt(
    raw_prompt: str,
    style: str = "",
    error_message: str = "",
    llm_model: str = "google/gemini-3-flash-preview",
) -> dict:
    """Rewrite a video/image prompt to bypass content-filter rejection while
    preserving the cinematic intent. Used when the video model returns
    "content may have been filtered" or similar rejection.

    Returns: {"softened": "..."}
    """
    raw = (raw_prompt or "").strip()
    if not raw:
        return {"softened": raw_prompt or ""}

    err = (error_message or "").strip()
    err_block = f"\n\nThe rejection message was: {err}" if err else ""

    user_prompt = f"""A video generation model REJECTED the following prompt for content-policy reasons (likely flagged for: religious figures like Lucifer/demons/hell, weapons, violence, sexual imagery, gore, real public figures, or branded content).{err_block}

Rewrite the prompt to bypass the filter while preserving:
- Cinematic intent (camera movement, lighting, framing, atmosphere)
- Character names exactly as written (these trigger reference-image attachment downstream)
- The narrative beat and emotional tone
- The overall visual style

Specifically:
- Replace "Lucifer" / "demon" / "devil" / "hell" with neutral mythological / atmospheric language ("the figure", "the architect", "the tempter", "the underworld" → "the deep cavern", etc.) — keep the character NAME if it's been assigned (e.g. "MorgathX" stays as "MorgathX") but remove explicit demonic descriptors
- Replace blood / gore / weapons with metaphorical equivalents (red light, charged atmosphere, ritual objects)
- Tone down explicit violence (a "scream of agony" → "a strangled gasp"; "tears blood" → "tears burn")
- Keep "ring", "fire", "ruby", "wandering", "horse", "moon", "snow" — these are not flagged
- DO NOT make it bland — keep all cinematic details, just swap triggering words for evocative non-triggering ones
- Output should be roughly the same length and detail level as the input

Original prompt:
{raw}

{f"Visual style guide: {style}" if style else ""}

Return JSON only: {{"softened": "...the rewritten prompt..."}}"""

    raw_resp = await openrouter.chat(
        messages=[{"role": "user", "content": user_prompt}],
        model=llm_model,
        json_mode=True,
    )
    return parse_llm_json(raw_resp, context="Soften")


async def expand_style_description(
    raw_style: str,
    llm_model: str = "google/gemini-3-flash-preview",
) -> dict:
    """Take a short rough style/mood (e.g. 'cyberpunk pc game' or 'old VHS')
    and return a COMPACT visual-style cue list that gets stamped into every
    image/video [STYLE] block.

    The output is intentionally short (~12 words, single line) — long style
    prose drowns out the scene-specific action in render prompts and risks
    contradicting per-scene cues. The cue list distills only what the video
    model needs to lock the look.

    Returns: {"expanded": "..."}
    """
    raw = (raw_style or "").strip()
    if not raw:
        return {"expanded": ""}

    user_prompt = f"""HARD CONSTRAINT: Your output MUST be a single line, ≤20 words total, comma-separated descriptors only. NO full sentences. NO prose. If your draft is over 20 words, rewrite it shorter before submitting.

User-supplied style/mood for a music video: "{raw}"

Distill this into a compact visual style cue list. This string gets stamped into every image and video render prompt's [STYLE] block — it must be short so it doesn't drown out the per-scene action.

Include one cue from each category, comma-separated:
- Era / film-stock (e.g. "1990s 35mm cross-processed", "VHS bleed", "modern digital sharp")
- Palette (2-3 specific hues, e.g. "neon cobalt + acid magenta on crushed blacks")
- Lens / camera (e.g. "anamorphic with blue streaks", "14mm handheld", "shallow DoF 35mm")
- One genre/reference anchor if it sharpens the look (e.g. "Blade Runner", "Wong Kar-wai")

Examples of correct output shape and length:
  Input: "old VHS horror" → "VHS bleed + tracking glitches, murky green + ochre on crushed blacks, shaky 14mm handheld, Blair Witch aesthetic."
  Input: "Wes Anderson pastel" → "Kodak 35mm, pale pink + pistachio + mustard, symmetrical flat-lay composition, wide-angle, Wes Anderson aesthetic."
  Input: "1990s grunge MTV" → "1990s 16mm cross-processed, desaturated olive + mustard, 14mm fisheye handheld, MTV grunge aesthetic."

Return JSON only: {{"expanded": "...one-line cue list, ≤20 words..."}}"""

    raw_resp = await openrouter.chat(
        messages=[{"role": "user", "content": user_prompt}],
        model=llm_model,
        json_mode=True,
    )
    return parse_llm_json(raw_resp, context="Style AI Expand")


async def expand_character_description(
    name: str,
    current_description: str,
    style: str,
    theme_analysis: dict | None = None,
    llm_model: str = "google/gemini-3-flash-preview",
) -> dict:
    """Deepen a character description and align it with project style + song
    theme. Used to make characters feel like they belong in the music video.

    Returns: {"description": "...", "trigger_word": "..."}
    """
    theme = theme_analysis or {}
    theme_lines = []
    for k, label in [
        ("theme", "Song theme"),
        ("narrative", "Song narrative"),
        ("mood", "Song mood"),
        ("visual_world", "Visual world"),
    ]:
        val = theme.get(k)
        if val and isinstance(val, str) and val.strip():
            theme_lines.append(f"  {label}: {val.strip()}")
    theme_block = ("\n".join(theme_lines) + "\n") if theme_lines else "(no song theme available)\n"

    prompt = f"""You are a casting director for a music video. Deepen the
following character so they feel like a fully realized person in this song's world.

Project style: {style or "cinematic, modern music video"}
Song context:
{theme_block}
Character name: {name}
Current description: {current_description or "(empty — invent from scratch consistent with the song's mood)"}

CRITICAL — Describe the PERSON only, not the world around them.

A character description is a REFERENCE PORTRAIT spec. The image model uses it to render a head-and-shoulders portrait that gets attached to scene prompts later as an identity anchor. So:
- WHO they are (face, build, hair, eyes, skin, distinguishing marks)
- WHAT they wear (wardrobe materials and silhouettes)
- WHAT they carry / their default posture

The SCENE supplies the world around them (location, lighting, atmosphere, camera). Don't double-describe those in the character.

✓ DO include — describes the person:
  - Age range, build, posture default
  - Skin tone, face shape, jaw, brow, cheekbones, eye color + shape, hair color + length + styling
  - Makeup as personal styling: kohl-rimmed eyes, smudged eyeliner, dark lipstick
  - Wardrobe in real materials: leather trench, mesh tank, silk shirt, denim, wool, vinyl — describe color and silhouette
  - Real accessories: heavy signet ring, cigarette, chain necklace, leather gloves, antique watch
  - Distinguishing marks: scar, tattoo design (real ink), birthmark
  - Default expression: stern, watchful, hunched, predatory stillness

✗ DO NOT include — those belong to the scene, not the character:
  - Location / setting: "rain-slick alley", "volcanic cavern", "hotel corridor"
  - Environmental lighting: "rim-lit by magenta neon", "framed by neon practicals"
  - Atmospheric effects: "anamorphic blue lens flares", "swirling fog", "falling rain"
  - Camera / framing cues: "wide shot", "shallow depth of field"
  - Story moments / action: "lighting a cigarette as he turns"
  - These override or fight the scene at render time.

✗ DO NOT write these into the character — they render as cartoon:
  - "synthetic skin", "matte obsidian", "polymer", "chrome skin", "metallic flesh"
  - "fiber-optic lines on body", "circuit tattoos that glow", "hex-coded markings"
  - "optic sensors", "scanner eyes", "data-stream eyes", "overclocked"
  - "polygonal", "low-poly", "rendered in [engine]"
  - "holographic" objects as core props (use physical objects)
  - Sound descriptors ("hum", "buzz") — image cannot render sound
  - Abstract concepts ("ageless deity", "embodiment of X")

✗ DO NOT name real people — actors, models, musicians, public figures:
  - Image generators refuse celebrity likenesses and return text-only refusals
  - WRONG: "Cillian Murphy jawline", "a young Mads Mikkelsen"
  - RIGHT: describe the actual features ("sharp angular jaw + slicked-back jet-black hair + pale skin")

Rewrite rules:
- 40–80 words, tight prose, no fluff
- Cover age, build, face shape, skin, hair, eyes, signature wardrobe (real fabric), one defining accessory, default posture
- Mood expressed via WARDROBE + POSTURE + EXPRESSION — never via setting
- Keep the character recognizably HUMAN — a stylish person, not the style itself made flesh
- DO NOT change the name — deepen, don't replace
- SCRUB any celebrity / real-person references from the existing description. If it says "Cillian Murphy jawline" or any real person's name, REPLACE with the underlying feature shape. Output must contain ZERO real person names.
- SCRUB any setting / atmospheric prose from the existing description. If it says "rim-lit by neon practicals in a rain-slick alley", remove that entirely — the scene supplies the world.

Example of the right shape (~55 words, person-only, no setting):
  "Late-30s lean and tall man, sharp angular jawline, pale skin, slicked-back peroxide-blonde hair, deep-set grey eyes with smudged charcoal kohl. Floor-length matte black sheepskin trench coat over a ribbed silk tank top, grime-streaked denim, polished leather boots. Heavy silver signet ring on his right hand, carries a cigarette. Stern, motionless default expression."

Return JSON only:
{{
  "description": "...60–120 word photoreal-human description...",
  "trigger_word": "...optional short slug for LoRA later, lowercase, no spaces..."
}}"""
    raw = await openrouter.chat(
        messages=[{"role": "user", "content": prompt}],
        model=llm_model,
        json_mode=True,
    )
    return parse_llm_json(raw, context="Character AI Expand")


def _format_tempo_line(bpm: float, key: str) -> str:
    """Translate raw BPM + key into a directorial cue the LLM can act on."""
    if bpm <= 0:
        tempo_desc = "(unknown tempo)"
    elif bpm < 70:
        tempo_desc = "very slow / ballad — favor static or floating shots, long takes, contemplative pacing"
    elif bpm < 95:
        tempo_desc = "slow / mid — slow tracking shots, lingering close-ups, room to breathe"
    elif bpm < 115:
        tempo_desc = "moderate — balanced cuts, mix of static and movement"
    elif bpm < 140:
        tempo_desc = "upbeat — confident motion, dynamic camera, frequent cuts"
    elif bpm < 170:
        tempo_desc = "fast — quick cuts, kinetic camera, energetic action"
    else:
        tempo_desc = "very fast / aggressive — rapid cuts, jump cuts, intense kinetic visuals"

    # Interpret minor vs major. Without explicit minor detection we lean on
    # convention: keys ending in m or specific minor keys often signal minor.
    # librosa returns just a note letter so we can't tell — so leave key as
    # raw info and let the LLM cross-reference with the lyric mood.
    return f"BPM: {bpm:.0f} ({tempo_desc}) | Key: {key}"


def _build_sections_text(sections: list, words: list, beats: list) -> str:
    lines = []
    for s in sections:
        lyrics = words_in_range(words, s["start"], s["end"])
        beat_count = len(beats_in_range(beats, s["start"], s["end"]))
        lines.append(
            f"  [{s['label']}] {s['start']:.1f}s–{s['end']:.1f}s "
            f"({beat_count} beats) | \"{lyrics[:80]}\""
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Song theme analysis — runs after transcription to understand the narrative
# ---------------------------------------------------------------------------

THEME_ANALYSIS_PROMPT = """You are analyzing a song's lyrics to inform a music video.
Read the lyrics carefully. Even if they're in a non-English language, work in the original language.

Song title: {title}
Artist: {artist}

Lyrics:
{lyrics}

Return JSON only with these keys (English summaries):
{{
  "theme": "short phrase capturing the central idea",
  "narrative": "1-2 sentence story summary of what happens in the song",
  "mood": "emotional tone — e.g. melancholic-yet-defiant, ecstatic, mournful",
  "characters_in_lyrics": ["character / persona 1", "character / persona 2", ...],
  "visual_world": "setting, atmosphere, time period suggestion",
  "suggested_visual_style": "art direction recommendation that fits the mood"
}}"""


async def analyze_song_theme(title: str, artist: str, lyrics: str) -> dict:
    """Returns dict with theme, narrative, mood, characters_in_lyrics, visual_world, suggested_visual_style."""
    raw = await openrouter.chat(
        messages=[{
            "role": "user",
            "content": THEME_ANALYSIS_PROMPT.format(
                title=title or "(untitled)",
                artist=artist or "(unknown)",
                lyrics=lyrics or "(no lyrics)",
            ),
        }],
        json_mode=True,
    )
    return _parse_json_object(raw)


# ---------------------------------------------------------------------------
# Character suggestion — propose characters from theme + visual style
# ---------------------------------------------------------------------------

CHAR_SUGGEST_PROMPT = """You are casting a music video. Propose {count} characters based on the song's narrative and the chosen visual style.

VISUAL STYLE FOR THIS VIDEO:
{visual_style}

Song theme: {theme}
Narrative: {narrative}
Mood: {mood}
Visual world: {visual_world}
Characters mentioned in lyrics: {chars_in_lyrics}

CRITICAL — Describe the PERSON only, not the world around them.

A character description is a REFERENCE PORTRAIT spec. The image model uses it to render a head-and-shoulders portrait that gets attached to scene prompts later as an identity anchor. So:
- WHO they are (face, build, hair, eyes, skin, distinguishing marks)
- WHAT they wear (wardrobe materials and silhouettes)
- WHAT they carry / their default posture / one signature gesture

The SCENE supplies the world around them (location, lighting, atmosphere, camera). Don't double-describe those in the character — it bloats the description and fights the actual scene at render time.

✓ DO include — describes the person:
  - Age range, build (lean / wiry / heavyset / athletic), height impression, posture default
  - Skin tone, face shape, jaw, brow, cheekbones, nose, eye color + shape, hair color + length + styling
  - Makeup as personal styling: smudged eyeliner, dark lipstick, kohl-rimmed eyes
  - Wardrobe in real materials: leather trench, mesh tank, silk shirt, denim, wool overcoat, vinyl raincoat — describe color and silhouette
  - Real accessories worn / carried: heavy ring, cigarette, chain, knife, antique watch, leather gloves
  - Distinguishing marks: scar, tattoo design (real ink, not glowing), birthmark
  - Default expression / posture: stern, watchful, hunched, predatory stillness

✗ DO NOT include — those belong to the scene, not the character:
  - Location / setting: "rain-slick alley", "volcanic cavern", "hotel corridor", "rooftop"
  - Environmental lighting: "rim-lit by magenta neon", "harsh sodium-streetlamp shadows", "framed by neon practicals"
  - Atmospheric effects: "anamorphic blue lens flares", "swirling fog", "falling rain", "smoke billowing"
  - Camera / framing cues: "wide shot", "shallow depth of field", "Dutch tilt"
  - Story moments / action: "lighting a cigarette as he turns", "walking through the alley"
  - These all override or fight the scene at render time — leave them for scene prompts.

✗ DO NOT use in character bodies — image models render them as cartoon:
  - "synthetic skin", "matte obsidian", "polymer", "chrome", "metallic skin"
  - "fiber-optic lines on body", "glowing circuit tattoos", "hex-coded markings on skin"
  - "optic sensors", "scanner eyes", "data-stream eyes", "overclocked"
  - "polygonal", "low-poly", "rendered in [game engine]"
  - "holographic" objects as core props (use physical objects)
  - Sound descriptors ("neon hum", "buzz") — image cannot render sound
  - Abstract concepts ("ageless deity", "embodiment of X", "personification of Y")

✗ DO NOT name real people — actors, models, musicians, public figures:
  - Image generators refuse celebrity likenesses and return text-only refusals
  - WRONG: "Cillian Murphy jawline", "a young Mads Mikkelsen", "Bowie-esque"
  - RIGHT: describe the actual feature shapes ("sharp angular jaw, hooded brow, pale skin, grey-blue eyes")

Rules:
- Names: short, evocative, DISTINCTIVE — used as substring matches downstream. No "Man" / "The Singer". Two characters must not share a substring (no "Lena" + "Lenny").
- Descriptions: 40–80 words. Tight prose, no fluff. Cover age range, build, face, hair, eyes, skin, wardrobe (real fabric), one defining accessory, default posture. Style anchors via wardrobe + personal styling, not setting.
- The character's mood lives in posture / expression / wardrobe choices — not in setting prose.

Example of the right shape (~55 words, person-only, no setting):
  "Late-30s lean and tall man, sharp angular jawline, pale skin, slicked-back peroxide-blonde hair, deep-set grey eyes with smudged charcoal kohl. Floor-length matte black sheepskin trench coat over a ribbed silk tank top, grime-streaked denim, polished leather boots. Heavy silver signet ring on his right hand, carries a cigarette. Stern, motionless default expression."

Return ONLY a JSON array (no prose, no markdown):
[
  {{
    "name": "short evocative name (single word or two)",
    "description": "40–80 word person-only description, no setting or atmosphere...",
    "role_in_song": "how this character fits the narrative"
  }},
  ...
]"""


async def suggest_characters(
    theme_data: dict,
    visual_style: str,
    count: int = 3,
) -> list[dict]:
    """Generate character proposals from a song's theme analysis + visual style."""
    raw = await openrouter.chat(
        messages=[{
            "role": "user",
            "content": CHAR_SUGGEST_PROMPT.format(
                theme=theme_data.get("theme", ""),
                narrative=theme_data.get("narrative", ""),
                mood=theme_data.get("mood", ""),
                visual_world=theme_data.get("visual_world", ""),
                chars_in_lyrics=", ".join(theme_data.get("characters_in_lyrics", [])) or "(none)",
                visual_style=visual_style or theme_data.get("suggested_visual_style", "cinematic"),
                count=count,
            ),
        }],
        json_mode=True,
    )
    parsed = _parse_json_scenes(raw)  # reuses tolerant array parser
    return parsed if isinstance(parsed, list) else []


def _parse_json_object(raw: str) -> dict:
    """Parse a JSON object from LLM output, tolerating fences/prose."""
    return parse_llm_json(raw, context="Theme analysis")


async def generate_scene_prompts(
    description: str,
    style: str,
    characters: list,
    lyrics: str,
    previous_scene: Optional[str] = None,
    next_scene: Optional[str] = None,
    previous_image_prompt: Optional[str] = None,
    next_image_prompt: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    llm_model: str = "google/gemini-3-flash-preview",
) -> dict:
    """Expand a scene description into detailed image_prompt + video_prompt
    for ONE self-contained shot.

    Scenes are joined by hard cuts in the final video — there's no frame
    anchor between scene N's ending and scene N+1's beginning. This scene's
    image_prompt is its own opening frame; its video_prompt is the motion
    that happens within that single shot.

    Adjacent scene context is passed to the LLM purely for narrative
    coherence (so the story arcs read naturally) — NOT to bridge frames.
    """
    char_text = "\n".join(f"  - {c['name']}: {c['description']}" for c in characters) if characters else "  (none defined)"

    # Adjacent-scene context is for *narrative* continuity only — the LLM
    # uses it to write a description that fits the song's flow, but each
    # scene is still a standalone shot.
    prev_ctx = ""
    if previous_scene or previous_image_prompt:
        prev_ctx = "PREVIOUS scene (for story flow only — not for frame-matching):\n"
        if previous_scene:
            prev_ctx += f"  Description: {previous_scene}\n"
        if previous_image_prompt:
            prev_ctx += f"  Their image_prompt: {previous_image_prompt}\n"
        prev_ctx += "\n"

    next_ctx = ""
    if next_scene or next_image_prompt:
        next_ctx = "NEXT scene (for story flow only — not for frame-matching):\n"
        if next_scene:
            next_ctx += f"  Description: {next_scene}\n"
        if next_image_prompt:
            next_ctx += f"  Their image_prompt: {next_image_prompt}\n"
        next_ctx += "\n"

    dur = f"Scene duration: {duration_seconds:.1f}s — pace the action to fit\n\n" if duration_seconds else ""

    prompt = f"""{prev_ctx}{next_ctx}{dur}Project style: {style or "cinematic music video"}

Cast (use these names verbatim when characters are on screen):
{char_text}

Lyrics in this scene: "{lyrics}"
Scene description: {description}

How rendering works:
- image_prompt → still image. The OPENING frame of this scene's video clip.
- video_prompt → video clip that starts on that still and plays out the motion within ONE self-contained shot.
- Adjacent scenes are joined by hard cuts. Don't try to bridge to the next scene's pose.

REALITY CHECK — what the video model can actually render. Plan accordingly:
✓ RELIABLE: gross body motion (walk, turn, run, sit, kneel, fall, reach, lift, throw, push, point); head/gaze (turns head, looks at X, closes eyes); camera moves (pan, push-in, dolly, truck, crane, tilt); environment (rain, smoke, flicker, neon, wind); object interactions (picks up, opens, drops, lights).
✗ UNRELIABLE — AVOID: internal/emotional states ("longing", "remembers", "introspective"); micro-expressions ("subtle smile", "tear forms"); narrative abstractions ("the past haunts her"); sub-second gestures. The model can only render what a CAMERA WOULD SEE.

OUTPUT FORMAT — both prompts MUST use this tagged structure:

  [STYLE]
  <COMPACT distillation of the project style — comma-separated cue list, 1–3 short lines, ~30 words max. NEVER copy the project style prose verbatim. Extract just the essential visual cues (film stock, palette, lens, lighting, grain, references). Per-scene tweaks only when genuinely warranted.>

  [SCENE]
  <ONE observable moment / ONE observable action. Camera framing + characters (by name) + setting + simple action verb. The bulk of the prompt content belongs here.>

If this clip genuinely needs TWO distinct beats (e.g. character walks in, then opens a door), use [SCENE 1] and [SCENE 2] inside the video_prompt. Use sparingly — most scenes are one shot, one action.

[STYLE] distillation — bad vs good:
  ✗ BAD (drowns the scene): "The visual style is defined by a high-contrast 'Electric Noir' aesthetic, featuring a triadic color palette of neon cobalt, acid magenta, and hazardous sulfur-yellow set against deep, crushed-black shadows. Lighting is characterized by harsh, low-key silhouettes with ultra-bright colored rim lighting and frequent volumetric god-rays..."
  ✓ GOOD (compact cues): "Cyberpunk noir, anamorphic lens with blue streaks, neon cobalt + acid magenta + sulfur yellow on crushed blacks, volumetric god-rays through haze, 35mm grain."

image_prompt — the OPENING FRAME as a held pose (no motion):
- Set up the pose so the video_prompt's action can flow from it
- Name characters by their cast name (never "she" / "the singer" / "he")
- Specify framing, lens, depth of field, lighting (key/fill/rim, color temp, practicals)
- Photorealistic / cinematic quality cues
- Do NOT describe motion, blur, inner states, or micro-expressions

video_prompt — the MOTION within this single shot:
- Name the same characters by cast name
- Use simple observable verbs only (walks, turns, looks, reaches, falls, opens, throws)
- Camera movement + body action + environment shift, paced to {duration_seconds or "the scene's"}s
- Resolve within the shot — don't try to land on the next scene's pose
- NO emotional or internal descriptions — just what the camera sees

Example output (single-beat scene):
{{
  "image_prompt": "[STYLE]\\n1990s 35mm cross-processed film, deep cyan shadows, magenta neon practicals, heavy grain, photorealistic 8K.\\n\\n[SCENE]\\nWide shot: Lena (auburn hair, black leather jacket) stands at the rooftop ledge frame-left, facing the city. Golden-hour rim light from camera right. Shallow depth of field.",
  "video_prompt": "[STYLE]\\n1990s 35mm cross-processed, magenta neon, photorealistic.\\n\\n[SCENE]\\nLena turns her head and looks toward camera. The camera dollies forward two feet. Rain falls in slow motion."
}}

Return JSON:
{{
  "video_prompt": "...",
  "image_prompt": "..."
}}"""

    raw = await openrouter.chat(
        messages=[{"role": "user", "content": prompt}],
        model=llm_model,
        json_mode=True,
    )
    # Surface JSON failures so callers can SKIP this scene rather than
    # silently overwriting existing prompts with the bare description.
    return parse_llm_json(raw, context="AI Expand")
