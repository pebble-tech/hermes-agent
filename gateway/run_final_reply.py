"""How a finished turn's result reads as a final reply: Hermes' ``(empty)`` sentinel, silent end_turn
batches, hidden-reasoning retry exhaustion and the resume-pending clear (moved out of ``gateway/run.py``)."""

from __future__ import annotations

from agent.i18n import t


def _is_synthetic_empty_terminal_response(agent_result: dict, response: str) -> bool:
    """True when ``response == "(empty)"`` is Hermes' terminal sentinel."""
    if response != "(empty)" or not isinstance(agent_result, dict):
        return False

    messages = agent_result.get("messages")
    if not isinstance(messages, list):
        return False

    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return bool(msg.get("_empty_terminal_sentinel"))
    return False


def _is_literal_empty_model_text(agent_result: dict, response: str) -> bool:
    """True when the model literally output ``(empty)`` rather than Hermes' sentinel."""
    if response != "(empty)" or not isinstance(agent_result, dict):
        return False

    messages = agent_result.get("messages")
    if not isinstance(messages, list):
        return False

    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return not msg.get("_empty_terminal_sentinel")
    return False


def _empty_sentinel_user_message(agent_result: dict, response: str) -> str | None:
    """Shared explainer for Hermes' ``(empty)`` sentinel; ``None`` for literal model text."""
    if response != "(empty)" or _is_literal_empty_model_text(agent_result, response):
        return None

    from agent.turn_explainers import EMPTY_RESPONSE_EXPLANATION

    model = str(agent_result.get("model") or "").strip() or t("gateway.errors.empty_response_model_label")
    return t("gateway.shared.warn_passthrough", error=EMPTY_RESPONSE_EXPLANATION.format(model=model))


def _end_turn_batch_stays_silent(agent_result: dict, response: str, synthetic_empty: bool) -> bool:
    """end_turn batches stay silent when the closer is the persistable ``(empty)`` placeholder (no
    ``_empty_terminal_sentinel``; that flag would rewind the tool batch on persist). Visible handoff
    text still passes through."""
    return agent_result.get("turn_exit_reason") == "end_turn_tool_batch" and (
        synthetic_empty
        or not (response or "").strip()
        or (response or "").strip() == "(empty)"
    )


def _is_gateway_hidden_reasoning_incomplete_turn(agent_result: dict) -> bool:
    """Detect retry-exhausted turns with hidden reasoning but no visible answer.
    The loop returns the retry-exhaustion sentinel as BOTH ``final_response`` and ``error``, so a
    non-empty ``final_response`` proves nothing; any text other than the sentinel is a real answer."""
    if (not isinstance(agent_result, dict) or agent_result.get("failed")
            or agent_result.get("interrupted") or not agent_result.get("partial")):
        return False
    error_text = str(agent_result.get("error", "") or "").strip()
    if "remained incomplete after" not in error_text.lower():
        return False
    final_response = str(agent_result.get("final_response") or "").strip()
    return not final_response or final_response == error_text


def _should_clear_resume_pending_after_turn(agent_result: dict) -> bool:
    """True only when a gateway turn really completed successfully.
    ``resume_pending`` is a durable restart-recovery marker; a soft interrupt can look like a normal
    empty result, and clearing then loses the signal."""
    if not isinstance(agent_result, dict) or agent_result.get("interrupted"):
        return False
    if agent_result.get("failed") or agent_result.get("partial") or agent_result.get("error"):
        return False
    return agent_result.get("completed") is not False
