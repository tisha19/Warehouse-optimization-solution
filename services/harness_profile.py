"""Chat models for the orchestrator and the specialists, with the Nemotron harness attached.

deepagents ships a harness profile tuned for Nemotron 3 Ultra -- twelve middleware
and a ~3k character system prompt suffix that between them repair text-shaped tool
calls, strip stray reasoning tags, retry rate limits and stop runaway loops. It is
registered against the eight model specs the public providers advertise, and every
one of them spells the model `nemotron-3-ultra-550b-a55b`.

The endpoint NVIDIA hosted for us answers to `nvidia/nvidia/nemotron-3-ultra`, which
matches none of those specs. `_harness_profile_for_model` then returns an *empty*
profile rather than raising, so the entire harness silently does nothing. Registering
the shipped profile under the spec our client actually reports is what turns it on.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, List

from services.config import ProductionConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

# The orchestrator re-solves until nothing addressable is left, so it makes far
# more model calls and repeats solve_slotting far more often than the shipped
# budget of 16/48/3 allows. Those defaults would abort a healthy run.
MAX_MODEL_CALLS = 80
MAX_TOOL_RESULTS = 240
MAX_REPEATED_TOOL_CALLS = 12

# Nemotron spends tokens on reasoning before it emits any answer, so a small
# budget returns empty content with finish_reason="length" instead of failing
# loudly. Hosted Lightning needed 152 completion tokens just to reply "OK".
SUPERVISOR_MAX_TOKENS = 16384
SPECIALIST_MAX_TOKENS = 8192
# Judging the whole warehouse makes Lightning reason for thousands of tokens
# before it writes anything; at the specialist budget it runs out mid-thought and
# returns empty content with finish_reason="length".
ANALYSIS_MAX_TOKENS = 32768

_registered: List[str] = []

# The hosted endpoint occasionally answers a model call with a 503 from its own
# upstream, or simply stops responding mid-generation. deepagents ships a retry
# for 429s only, so without this a single blip ends a run that is otherwise
# minutes from finishing.
_TRANSIENT_MARKERS = (
    "[502]", "[503]", "[504]",
    "upstream connect error", "Service Unavailable",
    # A long reasoning turn can outlast the read timeout; that is a stalled
    # connection, not a rejected request, and the same call usually succeeds.
    "Read timed out", "ReadTimeout", "ConnectTimeout", "ConnectionError",
    "Connection aborted", "RemoteDisconnected",
)
TRANSIENT_RETRY_DELAYS = (2.0, 6.0, 15.0)
# A reasoning turn at these token budgets regularly runs past the client's
# 60s default, which surfaces as a read timeout on a call that was fine.
REQUEST_TIMEOUT_SECONDS = float(os.getenv("NIM_REQUEST_TIMEOUT_SECONDS", "240"))


def _is_transient(error: BaseException) -> bool:
    text = f"{type(error).__name__}: {error}"
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _transient_retry_middleware():
    import time

    from langchain.agents.middleware.types import AgentMiddleware

    class TransientUpstreamRetryMiddleware(AgentMiddleware):
        """Retry a model call when the hosted endpoint returns a transient 5xx."""

        name = "TransientUpstreamRetryMiddleware"

        def wrap_model_call(self, request, handler):  # type: ignore[override]
            for attempt, delay in enumerate((*TRANSIENT_RETRY_DELAYS, None)):
                try:
                    return handler(request)
                except Exception as error:  # noqa: BLE001 - re-raised unless transient
                    if delay is None or not _is_transient(error):
                        raise
                    time.sleep(delay)
            raise RuntimeError("unreachable")

    return TransientUpstreamRetryMiddleware()


def _shipped_profile():
    """Rebuild deepagents' Nemotron profile with a budget sized for our loop."""
    from deepagents import HarnessProfile
    from deepagents.profiles.harness import _nvidia_nemotron_3_ultra as ultra

    missing = [
        name
        for name in ("_SYSTEM_PROMPT_SUFFIX", "_READ_FILE_DESCRIPTION_OVERRIDE", "_build_extra_middleware")
        if not hasattr(ultra, name)
    ]
    if missing:
        raise RuntimeError(
            "deepagents' Nemotron profile has changed shape; cannot re-register it "
            f"for the hosted model (missing {', '.join(missing)})."
        )

    def middleware() -> "List[AgentMiddleware]":
        items = list(ultra._build_extra_middleware())
        for index, item in enumerate(items):
            if isinstance(item, ultra.NemotronProgressBudgetMiddleware):
                items[index] = ultra.NemotronProgressBudgetMiddleware(
                    max_model_calls=MAX_MODEL_CALLS,
                    max_tool_results=MAX_TOOL_RESULTS,
                    max_repeated_tool_calls=MAX_REPEATED_TOOL_CALLS,
                )
        items.append(_transient_retry_middleware())
        return items

    return HarnessProfile(
        system_prompt_suffix=ultra._SYSTEM_PROMPT_SUFFIX,
        tool_description_overrides={"read_file": ultra._READ_FILE_DESCRIPTION_OVERRIDE},
        extra_middleware=middleware,
    )


def register_nemotron_profile(model_id: str) -> List[str]:
    """Attach the Nemotron harness to `model_id`. Returns the keys registered."""
    from deepagents import register_harness_profile

    # ChatNVIDIA reports its provider capitalised, but the lookup is exact, so
    # both spellings are registered rather than relying on which one wins.
    keys = [f"NVIDIA:{model_id}", f"nvidia:{model_id}"]
    profile = _shipped_profile()
    for key in keys:
        if key in _registered:
            continue
        register_harness_profile(key, profile)
        _registered.append(key)
    return keys


def supervisor_model(config: ProductionConfig | None = None) -> "ChatNVIDIA":
    """Nemotron 3 Ultra, hosted, with the harness profile attached."""
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    config = config or ProductionConfig.from_env()
    if not (config.nim_supervisor_base_url and config.nim_supervisor_model and config.nim_supervisor_api_key):
        raise RuntimeError(
            "NIM_SUPERVISOR_BASE_URL, NIM_SUPERVISOR_MODEL and NIM_SUPERVISOR_API_KEY "
            "must be set for the orchestrator."
        )
    register_nemotron_profile(config.nim_supervisor_model)
    return ChatNVIDIA(
        base_url=config.nim_supervisor_base_url,
        model=config.nim_supervisor_model,
        api_key=config.nim_supervisor_api_key,
        temperature=0.2,
        max_tokens=SUPERVISOR_MAX_TOKENS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def specialist_model(config: ProductionConfig | None = None, max_tokens: int = SPECIALIST_MAX_TOKENS, callbacks=None) -> "ChatNVIDIA":
    """Nemotron 3.5 Lightning, hosted, for the specialist subagents."""
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    config = config or ProductionConfig.from_env()
    base = config.nim_subagent_base_url or config.nim_base_url
    model = config.nim_subagent_model or config.nim_model
    key = config.nim_api_key or config.nim_supervisor_api_key
    if not (base and model and key):
        raise RuntimeError("NIM_SUBAGENT_BASE_URL / _MODEL and an API key must be set for the specialists.")
    # Lightning is a Nemotron reasoning model too, so it needs the same
    # text-tool-call repair and reasoning-tag cleanup the harness provides.
    register_nemotron_profile(model)
    return ChatNVIDIA(
        base_url=base,
        model=model,
        api_key=key,
        temperature=0.2,
        max_tokens=max_tokens,
        timeout=REQUEST_TIMEOUT_SECONDS,
        callbacks=callbacks,
    )


def profile_report(model) -> dict:
    """What the harness actually resolved for a model, for health checks and the UI."""
    from deepagents.profiles.harness import harness_profiles as hp

    hp._ensure_harness_profiles_loaded()
    profile = hp._harness_profile_for_model(model, None)
    if profile is None:
        return {"attached": False, "middleware": [], "prompt_suffix_chars": 0}
    items = hp._resolve_middleware_seq(profile.extra_middleware) if profile.extra_middleware else []
    return {
        "attached": bool(items) or bool(profile.system_prompt_suffix),
        "middleware": [getattr(item, "name", type(item).__name__) for item in items],
        "prompt_suffix_chars": len(profile.system_prompt_suffix or ""),
    }
