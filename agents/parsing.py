"""Extract JSON from NIM chat responses.

Nemotron models are reasoning models: the completion is chain-of-thought text
terminated by a `</think>` marker, followed by the answer, and the answer is
often wrapped in a ```json fence. Parsing `content` directly therefore fails
even though the model complied with the instruction.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _strip_reasoning(text: str) -> str:
    """Drop everything up to the final end-of-thinking marker."""
    marker = text.rfind("</think>")
    return text[marker + len("</think>") :] if marker != -1 else text


def _first_json_object(text: str) -> str | None:
    """Return the first balanced {...} block, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def json_from_text(text: str, context: str) -> Dict[str, Any]:
    """Pull the JSON payload out of raw model text, or raise ValueError."""
    answer = _strip_reasoning(str(text or "")).strip()

    fenced = _FENCE.search(answer)
    if fenced:
        answer = fenced.group(1).strip()

    for candidate in (answer, _first_json_object(answer)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"the model returned a non-JSON {context}")


def json_from_response(response: Mapping[str, Any], context: str) -> Dict[str, Any]:
    """Pull the JSON payload out of a chat completion, or raise ValueError."""
    choices = response.get("choices") or [{}]
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        # Reasoning models return content=None when they run out of completion
        # budget mid-thought; the reasoning field is the only thing populated.
        content = message.get("reasoning") or ""
    try:
        return json_from_text(str(content), context)
    except ValueError:
        finish = choices[0].get("finish_reason")
        raise ValueError(f"NIM returned a non-JSON {context} (finish_reason={finish})") from None
