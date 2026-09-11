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
from typing import Any, Dict, List, Mapping

from agents.parsing import json_from_text

SYSTEM_PROMPT = """You review a warehouse's slotting against its demand.

You are given measured facts. Never invent a number: every figure you state must
appear in the input.

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
  "metric": "the single headline number, e.g. 8.8% or 74.9m"}]}

Order them most important first. Return at most four. If nothing is worth
raising, return an empty list."""


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
        # Stated explicitly because whether the cap can reach the headroom is the
        # judgement we actually want from the model.
        "headroom_note": (
            f"{metre_picks:,.0f} metre-picks of headroom remain "
            f"({headroom.get('headroom_pct', 0)}% of current). The operator allows at most "
            f"{cap} relocations per plan."
        ),
        "zones": list(zones or []),
    }


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
            "title": str(item.get("title", "")).strip(),
            "detail": str(item.get("detail", "")).strip(),
            "metric": str(item.get("metric", "")).strip(),
        })
    return cleaned
