"""When a platform stuck in the reconnect queue escalates to NEEDS_ATTENTION (moved out of ``gateway/run.py``)."""

from __future__ import annotations

from hermes_cli.config_defaults import DEFAULT_CONFIG as _DEFAULT_CONFIG


def _reconnect_attention_after_secs() -> float:
    """``agent.reconnect_attention_after`` of the profile whose scope is bound at call time (the launch
    profile's when unbound). Seconds continuously in the reconnect queue before NEEDS_ATTENTION; retrying
    never stops (transient outages must self-heal), this only makes a permanently-failing loop loud.
    Non-positive disables. Read per call, never cached: one process serves many profiles and a config
    edit must not need a gateway restart (#115635)."""
    from hermes_cli.config import load_config_readonly
    agent_cfg = load_config_readonly().get("agent")
    raw = agent_cfg.get("reconnect_attention_after") if isinstance(agent_cfg, dict) else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULT_CONFIG["agent"]["reconnect_attention_after"])


def _reconnect_needs_attention(info: dict, now: float) -> bool:
    """True when a reconnect-queue entry has waited long enough for NEEDS_ATTENTION.
    ``queued_at`` is re-stamped on each (re)entry, so only *continuous* failure escalates."""
    threshold = _reconnect_attention_after_secs()
    if threshold <= 0:
        return False  # escalation disabled
    queued_at = info.get("queued_at")
    if queued_at is None:
        info["queued_at"] = now
        return False
    return (now - queued_at) >= threshold
