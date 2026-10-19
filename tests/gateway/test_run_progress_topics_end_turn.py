"""Gateway turn path for a silent end_turn tool batch: the empty final keeps ``turn_exit_reason``.

Split out of ``tests/gateway/test_run_progress_topics.py`` (code-health size ratchet); shares its helpers.
"""

import importlib
import sys
import types

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from tests.gateway.test_run_progress_topics import ProgressCaptureAdapter, _make_runner


class SilentEndTurnToolBatchAgent:
    """Mimics a successful end_turn tool exit with no customer-facing prose."""

    def __init__(self, **kwargs):
        self.tools = []
        self.session_id = kwargs.get("session_id", "sess")

    def run_conversation(self, message, conversation_history=None, task_id=None):
        return {
            "final_response": "",
            "messages": [{"role": "user", "content": message}],
            "api_calls": 2,
            "turn_exit_reason": "end_turn_tool_batch",
            "failed": False,
            "partial": False,
            "completed": True,
        }


@pytest.mark.asyncio
async def test_run_agent_empty_final_preserves_turn_exit_reason(monkeypatch, tmp_path):
    """Regression (#18765 / silent handover): empty ``final_response`` with no
    failure metadata must reach the normal return path so ``turn_exit_reason``
    survives for ``_normalize_empty_agent_response``.
    """
    monkeypatch.delenv("GATEWAY_PROXY_URL", raising=False)
    monkeypatch.setenv("HERMES_TOOL_PROGRESS_MODE", "off")

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = SilentEndTurnToolBatchAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    adapter = ProgressCaptureAdapter()
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="17585",
    )
    session_id = "sess-silent-end-turn"
    session_key = "agent:main:telegram:group:-1001:17585"

    result = await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id=session_id,
        session_key=session_key,
    )

    assert result.get("final_response") == ""
    assert result.get("turn_exit_reason") == "end_turn_tool_batch"
    assert result.get("failed") is False
