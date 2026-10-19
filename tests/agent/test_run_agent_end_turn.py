"""end_turn tool batches: the turn stops without a follow-up nudge and persists a closer.

Split out of ``tests/agent/test_run_agent.py`` (code-health size ratchet); shares its fixtures.
"""

from unittest.mock import patch

from tests.agent import test_run_agent as _base
from tests.agent.test_run_agent import _mock_response, _mock_tool_call

# Reuse the facade's fixtures by name (an import would shadow-redefine ``agent`` — F811).
agent = _base.agent
_mock_plugin_discovery = _base._mock_plugin_discovery


class TestEndTurnToolBatch:
    _setup_agent = _base.TestRunConversation._setup_agent

    def test_end_turn_tool_batch_stops_without_followup_nudge(self, agent):
        self._setup_agent(agent)
        tc = _mock_tool_call(name="web_search", arguments="{}", call_id="c1")
        resp1 = _mock_response(
            content="Let me check with my side and come back to you shortly.",
            finish_reason="tool_calls",
            tool_calls=[tc],
        )
        agent.client.chat.completions.create.return_value = resp1

        status_messages = []

        def _capture_status(msg):
            status_messages.append(msg)

        with (
            patch("model_tools.handle_function_call", return_value='{"ok": true}'),
            patch("tools.registry.registry.is_end_turn", return_value=True),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_buffer_status", side_effect=_capture_status),
            patch.object(agent, "_emit_status", side_effect=_capture_status),
        ):
            result = agent.run_conversation("image")

        assert result["completed"] is True
        assert result["api_calls"] == 1
        assert result["final_response"] == "Let me check with my side and come back to you shortly."
        assert result["messages"][-1]["role"] == "assistant"
        assert result["messages"][-1]["content"] == (
            "Let me check with my side and come back to you shortly."
        )
        assert result["messages"][-1].get("_empty_terminal_sentinel") is not True
        assert not any("nudging to continue" in m.lower() for m in status_messages)

    def test_end_turn_tool_in_mixed_batch_stops_without_followup_nudge(self, agent):
        self._setup_agent(agent)
        agent.valid_tool_names.add("read_file")
        tc1 = _mock_tool_call(name="web_search", arguments="{}", call_id="c1")
        tc2 = _mock_tool_call(name="read_file", arguments='{"path":"README.md"}', call_id="c2")
        resp1 = _mock_response(
            content="I checked the details and will hand this over now.",
            finish_reason="tool_calls",
            tool_calls=[tc1, tc2],
        )
        agent.client.chat.completions.create.return_value = resp1

        status_messages = []

        def _capture_status(msg):
            status_messages.append(msg)

        def _is_end_turn(name):
            return name == "read_file"

        with (
            patch("model_tools.handle_function_call", return_value='{"ok": true}'),
            patch("tools.registry.registry.is_end_turn", side_effect=_is_end_turn),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_buffer_status", side_effect=_capture_status),
            patch.object(agent, "_emit_status", side_effect=_capture_status),
        ):
            result = agent.run_conversation("image")

        assert result["completed"] is True
        assert result["api_calls"] == 1
        assert result["final_response"] == "I checked the details and will hand this over now."
        assert result["messages"][-1]["role"] == "assistant"
        assert result["messages"][-1]["content"] == (
            "I checked the details and will hand this over now."
        )
        assert result["messages"][-1].get("_empty_terminal_sentinel") is not True
        assert not any("nudging to continue" in m.lower() for m in status_messages)

    def test_mixed_batch_with_end_turn_suppresses_empty_response_nudge(self, agent):
        # Counter-example: [kb_search (non-end_turn), trigger_handover (end_turn)].
        # With "all" semantics, kb_search would cause the nudge to fire.
        # With "any" semantics, trigger_handover's presence suppresses it.
        # We bypass _tool_batch_has_end_turn so the loop continues past tool
        # execution and reaches the _prior_any_end_turn guard.
        self._setup_agent(agent)
        agent.valid_tool_names.add("kb_search")
        tc1 = _mock_tool_call(name="kb_search", arguments="{}", call_id="c1")
        tc2 = _mock_tool_call(name="trigger_handover", arguments="{}", call_id="c2")
        resp1 = _mock_response(
            content="Let me look that up and hand over.",
            finish_reason="tool_calls",
            tool_calls=[tc1, tc2],
        )
        resp2 = _mock_response(content="", finish_reason="stop")
        resp3 = _mock_response(content="Done.", finish_reason="stop")
        agent.client.chat.completions.create.side_effect = [resp1, resp2, resp3]

        status_messages = []

        def _capture_status(msg):
            status_messages.append(msg)

        def _is_end_turn(name):
            return name == "trigger_handover"

        with (
            patch("model_tools.handle_function_call", return_value='{"ok": true}'),
            patch("tools.registry.registry.is_end_turn", side_effect=_is_end_turn),
            patch.object(agent, "_tool_batch_has_end_turn", return_value=False),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_buffer_status", side_effect=_capture_status),
            patch.object(agent, "_emit_status", side_effect=_capture_status),
        ):
            agent.run_conversation("help me")

        assert not any("nudging to continue" in m.lower() for m in status_messages)

    def test_non_end_turn_tools_still_fire_empty_response_nudge(self, agent):
        # Baseline: when no end_turn tool is present, an empty response after
        # tool calls should still trigger the recovery nudge.
        self._setup_agent(agent)
        tc = _mock_tool_call(name="web_search", arguments="{}", call_id="c1")
        resp1 = _mock_response(content="", finish_reason="tool_calls", tool_calls=[tc])
        resp2 = _mock_response(content="", finish_reason="stop")
        resp3 = _mock_response(content="Done.", finish_reason="stop")
        agent.client.chat.completions.create.side_effect = [resp1, resp2, resp3]

        status_messages = []

        def _capture_status(msg):
            status_messages.append(msg)

        with (
            patch("model_tools.handle_function_call", return_value='{"result": "data"}'),
            patch("tools.registry.registry.is_end_turn", return_value=False),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_buffer_status", side_effect=_capture_status),
            patch.object(agent, "_emit_status", side_effect=_capture_status),
        ):
            agent.run_conversation("search for something")

        assert any("nudging to continue" in m.lower() for m in status_messages)

    def test_empty_assistant_placeholder_is_persistable_closer(self, agent):
        """The end_turn closer must not use the failure sentinel flag.

        ``_empty_terminal_sentinel`` is stripped before persist and then
        rewinds the tool batch. The helper has to survive that drop so a
        silent handover stays ``tool → assistant`` on resume.
        """
        closer = agent._build_empty_assistant_placeholder()
        assert closer["role"] == "assistant"
        assert closer["content"] == "(empty)"
        assert closer.get("_empty_terminal_sentinel") is not True

        replay = [
            {"role": "user", "content": "handover"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "trigger_handover", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
            dict(closer),
        ]
        agent._drop_trailing_empty_response_scaffolding(replay)
        assert any(m.get("role") == "tool" for m in replay)
        assert replay[-1]["content"] == "(empty)"

    def test_end_turn_visible_handoff_persists_clean_text(self, agent):
        """Visible handoff text must be the durable closer, not ``(empty)``."""
        self._setup_agent(agent)
        agent.valid_tool_names = set(agent.valid_tool_names or []) | {"trigger_handover"}
        handoff = "Let me check with my side and come back to you shortly."
        tc = _mock_tool_call(name="trigger_handover", arguments="{}", call_id="c1")
        resp1 = _mock_response(
            content=handoff,
            finish_reason="tool_calls",
            tool_calls=[tc],
        )
        agent.client.chat.completions.create.return_value = resp1

        persisted = []

        def _capture_persist(messages, conversation_history):
            persisted.append([dict(m) for m in messages])

        with (
            patch("model_tools.handle_function_call", return_value='{"ok": true}'),
            patch("tools.registry.registry.is_end_turn", return_value=True),
            patch.object(agent, "_persist_session", side_effect=_capture_persist),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("please hand this over")

        assert result["final_response"] == handoff
        assert result["turn_exit_reason"] == "end_turn_tool_batch"
        assert persisted, "end_turn turn must persist a transcript"
        closer = persisted[-1][-1]
        assert closer["role"] == "assistant"
        assert closer["content"] == handoff
        assert closer.get("_empty_terminal_sentinel") is not True

        replay = [dict(m) for m in persisted[-1]]
        agent._drop_trailing_empty_response_scaffolding(replay)
        assert replay[-1]["content"] == handoff
        assert any(m.get("role") == "tool" for m in replay)

    def test_silent_end_turn_batch_keeps_persistable_closer(self, agent):
        """Silent end_turn batches keep a persistable closer through replay drop."""
        self._setup_agent(agent)
        agent.valid_tool_names = set(agent.valid_tool_names or []) | {"trigger_handover"}
        tc = _mock_tool_call(name="trigger_handover", arguments="{}", call_id="c1")
        resp1 = _mock_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[tc],
        )
        agent.client.chat.completions.create.return_value = resp1

        persisted = []

        def _capture_persist(messages, conversation_history):
            persisted.append([dict(m) for m in messages])

        with (
            patch("model_tools.handle_function_call", return_value='{"ok": true}'),
            patch("tools.registry.registry.is_end_turn", return_value=True),
            patch.object(agent, "_persist_session", side_effect=_capture_persist),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("handover")

        assert result["final_response"] == ""
        assert result["turn_exit_reason"] == "end_turn_tool_batch"
        assert persisted, "silent end_turn turn must persist a transcript"
        closer = persisted[-1][-1]
        assert closer["role"] == "assistant"
        assert closer["content"] == "(empty)"
        assert closer.get("_empty_terminal_sentinel") is not True
        assert any(m.get("role") == "tool" for m in persisted[-1])

        replay = [dict(m) for m in persisted[-1]]
        agent._drop_trailing_empty_response_scaffolding(replay)
        assert any(m.get("role") == "tool" for m in replay)
        assert replay[-1]["content"] == "(empty)"

    def test_empty_response_emits_status_for_gateway(self, agent):
        """_buffer_status is called during empty retries so gateway users see feedback."""
        self._setup_agent(agent)
        agent.base_url = "http://127.0.0.1:1234/v1"

        empty_resp = _mock_response(content=None, finish_reason="stop")
        # 4 empty: 1 original + 3 retries, all empty, no fallback
        agent.client.chat.completions.create.side_effect = [
            empty_resp, empty_resp, empty_resp, empty_resp,
        ]

        status_messages = []

        def _capture_status(msg):
            status_messages.append(msg)

        with (
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_buffer_status", side_effect=_capture_status),
            patch.object(agent, "_emit_status", side_effect=_capture_status),
        ):
            result = agent.run_conversation("answer me")

        # #34452: explanation replaces the bare "(empty)" sentinel, but the
        # status emissions during retries are unchanged.
        assert result["final_response"] != "(empty)"
        assert "No reply:" in result["final_response"]
        # Repeated empty now skips remaining retries (charge guard). Expect the
        # first retry status, the skip notice, and a terminal failure status.
        retry_msgs = [m for m in status_messages if "retrying" in m.lower()]
        assert len(retry_msgs) >= 1, f"Expected at least 1 retry status, got: {status_messages}"
        skip_msgs = [m for m in status_messages if "skipping further retries" in m.lower() or "repeatedly returning empty" in m.lower()]
        assert len(skip_msgs) >= 1, f"Expected skip-remaining-retries status, got: {status_messages}"
        failure_msgs = [m for m in status_messages if "no content" in m.lower() or "no fallback" in m.lower()]
        assert len(failure_msgs) >= 1, f"Expected at least 1 failure status, got: {status_messages}"
