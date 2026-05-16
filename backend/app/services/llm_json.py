"""Tolerant JSON parsing for LLM responses.

LLM outputs are notoriously messy even when `response_format=json_object` is
requested:
- Markdown fences (```json ... ```)
- Leading/trailing prose ("Here is the JSON:\n{...}\nLet me know if...")
- Object expected but model returned a one-element array of the object
- Empty / null / wrong-type responses

This module centralizes all six tolerance behaviours in one place so adding
a new LLM-coupled feature doesn't mean re-implementing the same parser yet
again.
"""

from __future__ import annotations
import json
from typing import Literal, Type, Union


def strip_fences_and_prose(raw: str | None) -> str:
    """Trim markdown code fences and any leading/trailing prose around the
    first complete JSON value."""
    if not raw:
        return ""
    text = raw.strip()
    if text.startswith("```"):
        # Take the content between the first two ``` markers
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        # Drop the optional `json` (or other lang) marker on the first line
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    # If there's prose before/after the JSON value, slice to the first
    # opening bracket and the last matching closing bracket.
    start = next((i for i, c in enumerate(text) if c in "[{"), -1)
    if start >= 0:
        end_char = "]" if text[start] == "[" else "}"
        end = text.rfind(end_char)
        if end > start:
            text = text[start:end + 1]
    return text


def parse_llm_json(
    raw: str,
    *,
    expect: Literal["dict", "list"] = "dict",
    context: str = "LLM response",
    unwrap_singleton_list_to_dict: bool = True,
) -> Union[dict, list]:
    """Parse `raw` as JSON, tolerating common LLM quirks.

    Args:
        raw: the raw string returned by the LLM
        expect: "dict" if you want a JSON object, "list" if you want an array
        context: shown in error messages so callers know which feature failed
        unwrap_singleton_list_to_dict: when expect="dict" but the model
            returned a one-element list, unwrap it. Models like Gemini Flash
            occasionally return `[{...}]` instead of `{...}`.

    Raises:
        ValueError with a helpful message including a 300-char snippet of
        the raw response.
    """
    if not raw:
        raise ValueError(f"{context} returned empty/null response")
    text = strip_fences_and_prose(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{context} returned invalid JSON: {e}\nResponse start: {raw[:300]}")

    # Some LLMs wrap a single object in an array. Unwrap when caller wanted a dict.
    if expect == "dict" and isinstance(parsed, list) and unwrap_singleton_list_to_dict:
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}

    expected_type: Type = dict if expect == "dict" else list
    if not isinstance(parsed, expected_type):
        raise ValueError(
            f"{context} returned non-{expect} JSON: got {type(parsed).__name__}. "
            f"Response start: {raw[:300]}"
        )
    return parsed
