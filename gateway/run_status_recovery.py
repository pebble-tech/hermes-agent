"""Recovery-class agent statuses that ``display.diagnostic_status: off`` keeps out of chat."""

from __future__ import annotations

import re

_RECOVERY_STATUS_RE = re.compile(
    r"("
    r"model returned empty after tool calls.{0,80}nudging to continue"
    r"|thinking-only response.{0,80}prefilling to continue"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _is_recovery_status(event_type: str, text: str) -> bool:
    """True for recovery-class statuses (empty-after-tools nudge, thinking prefill)."""
    if str(event_type or "").strip().lower() == "recovery":
        return True
    return bool(_RECOVERY_STATUS_RE.search(text))
