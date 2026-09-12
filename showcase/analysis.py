"""What the measured slotting numbers mean, judged by Nemotron rather than by thresholds.

The arithmetic stays in `showcase.kpis`: those numbers are facts and must not be
invented. What a fixed rule cannot do is decide whether a gap is worth acting on.
`forward_pick_coverage < 60` fires whether the layout is genuinely bad or already
optimal for the demand it serves, and it has no idea that the operator's move cap
puts most of the remaining headroom out of reach. That judgement is what the model
is for.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping

from agents.parsing import json_from_text

SYSTEM_PROMPT = """You review a warehouse's slotting against its demand.

You are given measured facts. Never invent a number: every figure you state must
appear in the input.

You are writing for a warehouse operations manager, not for an engineer. Use
plain operational language and never quote the field names from the input:
say "forward pick coverage is 8.8%", not "forward_pick_coverage_pct is 8.8".
Say "the 30-move cap", not "max_moves 30".

Report what is wrong and what it costs the operation. Do not prescribe specific
moves or name slots to relocate between: the optimiser decides that, and a remedy
that sounds reasonable in prose is often wrong once the constraints are solved.

Judge each finding against picker travel and how well demand is served.

`addressable` is true only when relocating SKUs within the move cap would
measurably improve the situation. Set it to false when:
  - there is no meaningful headroom left, so the layout is already as good as the
    demand allows, or
  - the remaining headroom needs far more moves than the cap permits, or
  - the cause is not slotting at all (replenishment, maintenance, locked stock).

Return only JSON:
{"problems": [{"id": "kebab-case-id", "severity": "high|medium|low",
  "addressable": true|false, "title": "short statement of fact",
  "detail": "one or two sentences naming the numbers behind it",
  "metric": {"value": 8.8, "unit": "%"}}]}

`metric` is the single figure that best captures the finding, split into a bare
number and its unit. `value` must be a JSON number with no formatting: write
1096.9, not "1,096.9 km". `unit` is one of "%", "m", "km", "slots", "moves",
"picks" or "metre-picks". The panel renders it, so never put words in it.

In `detail`, round large figures to something a person can read: write
"1.36 million metre-picks", not "1360895.4".

Order them most important first. Return at most four. If nothing is worth
raising, return an empty list."""

# The panel renders the figure itself, so the model only has to pick one.
_UNITS = {"%", "m", "km", "slots", "moves", "picks", "metre-picks"}
_UNIT_ALIASES = {
    "percent": "%", "pct": "%",
    "metres": "m", "meters": "m", "metre": "m", "meter": "m",
    "kilometres": "km", "kilometers": "km", "km/day": "km",
    "metre picks": "metre-picks", "metre-picks/day": "metre-picks",
    "slot": "slots", "move": "moves", "pick": "picks",
}


def _facts(
    kpis: Mapping[str, Any],
    headroom: Mapping[str, Any],
    constraints: Mapping[str, Any],
    zones: List[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    cap = int(constraints.get("max_moves", 30))
    metre_picks = float(headroom.get("headroom_metre_picks", 0.0))
    return {
        "kpis": dict(kpis),
        "slotting_headroom": dict(headroom),
        "operator_constraints": {
            "max_moves": cap,
            "cold_chain_locked": bool(constraints.get("cold_chain_locked", True)),
            "labour_minutes_per_window": constraints.get("labour_minutes_per_window"),
            "locked_skus": len(constraints.get("locked_skus") or []),
        },
        # Whether the cap can reach the headroom is the judgement we want, so the
        # inputs for it are named here. A ready-made sentence would come straight
        # back as the finding's detail.
        "cap_versus_headroom": {
            "headroom_metre_picks": round(metre_picks, 1),
            "headroom_pct_of_current": headroom.get("headroom_pct", 0),
            "relocations_allowed_per_plan": cap,
        },
        "zones": list(zones or []),
    }


# Asking for operator language is not enough on its own: the model reaches for
# the input's field names whenever it quotes a figure. Rewriting them is
# deterministic, where a retry is not.
_FIELD_WORDS = {
    "avg_distance_per_pick_m": "average distance per pick",
    "forward_pick_coverage_pct": "forward pick coverage",
    "daily_travel_km": "daily picker travel",
    "daily_picks": "daily picks",
    "slot_utilisation_pct": "slot utilisation",
    "blocked_slots": "blocked slots",
    "lines_below_reorder": "lines below reorder",
    "headroom_metre_picks": "headroom",
    "headroom_pct_of_current": "headroom",
    "headroom_pct": "headroom",
    "current_metre_picks": "current travel effort",
    "best_metre_picks": "best achievable travel effort",
    "relocations_allowed_per_plan": "the move cap",
    "labour_minutes_per_window": "the labour budget per window",
    "cold_chain_locked": "the cold-chain lock",
    "locked_skus": "locked SKUs",
    "max_moves": "the move cap",
    "avg_distance_m": "average distance",
}
_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# Bare figures come back as "318582.9", which nobody reads as three hundred
# thousand. Asking for rounding in the prompt did not stick.
_BIG_NUMBER = re.compile(r"(?<![\d,.])\d{5,}(?:\.\d+)?(?!\d|,\d|\.\d)")


def _humanise(text: str) -> str:
    """Replace input field names with the words an operations manager uses."""
    for field, phrase in _FIELD_WORDS.items():
        text = re.sub(rf"\b{re.escape(field)}\b", phrase, text)
    text = _SNAKE_CASE.sub(lambda m: m.group(0).replace("_", " "), text)
    return _BIG_NUMBER.sub(lambda m: f"{round(float(m.group(0))):,}", text)


def _metric(raw: Any) -> Dict[str, Any] | None:
    """The figure for the panel, as a number the UI can format itself.

    Older prose like "51.0% forward pick coverage" still turns up, so it is
    salvaged when a number can be read off the front. Anything that yields no
    number returns None and the panel shows no chip, rather than a stray word.
    """
    value: Any = None
    unit = ""
    if isinstance(raw, dict):
        value = raw.get("value")
        unit = str(raw.get("unit", "")).strip()
    elif isinstance(raw, (int, float)):
        value = raw
    elif isinstance(raw, str):
        match = re.match(r"\s*(-?[\d,]*\.?\d+)\s*([^\s\d]*)", raw)
        if match:
            value = match.group(1).replace(",", "")
            unit = match.group(2)

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    unit = _UNIT_ALIASES.get(unit.lower(), unit)
    return {"value": round(number, 1), "unit": unit if unit in _UNITS else ""}


def analyse_slotting(
    model,
    kpis: Mapping[str, Any],
    headroom: Mapping[str, Any],
    constraints: Mapping[str, Any],
    zones: List[Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Ask the model to interpret the measured state. Raises if it will not answer."""
    response = model.invoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(_facts(kpis, headroom, constraints, zones), default=str)},
        ]
    )
    content = getattr(response, "content", "") or ""
    if not isinstance(content, str):
        content = str(content)
    if not content.strip():
        extra = getattr(response, "additional_kwargs", None) or {}
        reasoning = len(extra.get("reasoning_content") or "")
        metadata = getattr(response, "response_metadata", None) or {}
        raise ValueError(
            "the model produced no answer for the slotting analysis "
            f"(finish_reason={metadata.get('finish_reason')}, {reasoning} chars of reasoning); "
            "it ran out of completion budget before it stopped thinking"
        )
    payload = json_from_text(content, "slotting analysis")
    problems = payload.get("problems")
    if not isinstance(problems, list):
        raise ValueError("the slotting analysis did not contain a problems list")

    cleaned: List[Dict[str, Any]] = []
    for index, item in enumerate(problems[:5], 1):
        if not isinstance(item, dict):
            continue
        cleaned.append({
            "id": str(item.get("id") or f"finding-{index}"),
            "severity": str(item.get("severity", "medium")).lower(),
            "addressable": bool(item.get("addressable")),
            "title": _humanise(str(item.get("title", "")).strip()),
            "detail": _humanise(str(item.get("detail", "")).strip()),
            "metric": _metric(item.get("metric")),
        })
    return cleaned
