"""Tests for Telegram adapter.send() reply_markup and per-send preview metadata."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter(extra=None) -> TelegramAdapter:
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class TestTelegramSendMarkupAndPreview:
    @pytest.mark.asyncio
    async def test_send_attaches_structured_reply_markup(self):
        adapter = _make_adapter()
        adapter._rich_send_disabled = True
        mock_msg = MagicMock()
        mock_msg.message_id = 7
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        rows = [[{"text": "Do it", "callback_data": "task:go"}]]
        result = await adapter.send(
            "12345",
            "hello",
            metadata={"reply_markup": rows},
        )

        assert result.success is True
        kwargs = adapter._bot.send_message.call_args.kwargs
        assert kwargs["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_send_passes_through_existing_markup(self):
        adapter = _make_adapter()
        adapter._rich_send_disabled = True
        mock_msg = MagicMock()
        mock_msg.message_id = 8
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)
        sentinel = object()

        result = await adapter.send(
            "12345",
            "hello",
            metadata={"reply_markup": sentinel},
        )

        assert result.success is True
        assert adapter._bot.send_message.call_args.kwargs["reply_markup"] is sentinel

    @pytest.mark.asyncio
    async def test_send_keyboard_on_first_chunk_only(self):
        adapter = _make_adapter()
        adapter._rich_send_disabled = True
        adapter.MAX_MESSAGE_LENGTH = 80
        adapter._bot.send_message = AsyncMock(
            side_effect=lambda **kwargs: MagicMock(message_id=1)
        )

        content = ("chunk content " * 20).strip()
        result = await adapter.send(
            "123",
            content,
            metadata={
                "reply_markup": [[{"text": "Go", "callback_data": "task:go"}]],
            },
        )

        assert result.success is True
        assert adapter._bot.send_message.await_count > 1
        first = adapter._bot.send_message.await_args_list[0].kwargs
        last = adapter._bot.send_message.await_args_list[-1].kwargs
        assert first.get("reply_markup") is not None
        assert "reply_markup" not in last

    @pytest.mark.asyncio
    async def test_send_honors_per_send_preview_disable(self):
        adapter = _make_adapter()
        adapter._rich_send_disabled = True
        adapter._disable_link_previews = False
        mock_msg = MagicMock()
        mock_msg.message_id = 9
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        await adapter.send(
            "12345",
            "see https://example.com",
            metadata={"disable_link_preview": True},
        )

        kwargs = adapter._bot.send_message.call_args.kwargs
        assert "link_preview_options" in kwargs or kwargs.get(
            "disable_web_page_preview"
        ) is True
        assert adapter._link_preview_disabled({"disable_link_preview": True}) is True
        assert adapter._link_preview_kwargs({"disable_link_preview": True})

    @pytest.mark.asyncio
    async def test_rich_path_skipped_when_markup_present(self):
        adapter = _make_adapter(extra={"rich_messages": True})
        adapter._bot.do_api_request = AsyncMock(
            return_value=SimpleNamespace(message_id=123)
        )
        adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        adapter._bot.send_chat_action = AsyncMock()

        table = (
            "| Case | Status |\n"
            "|---|---|\n"
            "| rich | ok |\n"
        )
        result = await adapter.send(
            "12345",
            table,
            metadata={
                "reply_markup": [[{"text": "Go", "callback_data": "task:go"}]],
            },
        )

        assert result.success is True
        adapter._bot.do_api_request.assert_not_called()
        assert adapter._bot.send_message.call_args.kwargs.get("reply_markup") is not None


@pytest.fixture
def keyboard(monkeypatch):
    """Record built keyboards as plain values (PTB is mocked in this suite)."""
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.InlineKeyboardButton",
        lambda text, callback_data=None, url=None: (text, callback_data, url),
    )
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.InlineKeyboardMarkup",
        lambda rows: SimpleNamespace(inline_keyboard=rows),
    )


def _keyboard(markup):
    """Rows of (text, callback_data, url) from a recorded keyboard."""
    return markup.inline_keyboard


async def _send_and_get_markup(metadata, content="hello"):
    adapter = _make_adapter()
    adapter._rich_send_disabled = True
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    result = await adapter.send("12345", content, metadata=metadata)
    assert result.success is True
    return adapter._bot.send_message.call_args.kwargs.get("reply_markup")


@pytest.mark.usefixtures("keyboard")
class TestTelegramSendButtonsAlias:
    """``metadata["buttons"]`` in the #16544 / #95594 shape."""

    @pytest.mark.asyncio
    async def test_flat_list_is_one_row(self):
        markup = await _send_and_get_markup({
            "buttons": [
                {"text": "Ship", "callback_data": "ship:1"},
                {"text": "Docs", "url": "https://example.com"},
            ],
        })

        assert _keyboard(markup) == [
            [("Ship", "ship:1", None), ("Docs", None, "https://example.com")],
        ]

    @pytest.mark.asyncio
    async def test_grid_is_rows_and_a_bare_button_is_its_own_row(self):
        markup = await _send_and_get_markup({
            "buttons": [
                [{"text": "Ship 1", "callback_data": "ship:1"}, {"text": "Ship 2", "callback_data": "ship:2"}],
                {"text": "Skip", "callback_data": "skip"},
            ],
        })

        assert _keyboard(markup) == [
            [("Ship 1", "ship:1", None), ("Ship 2", "ship:2", None)],
            [("Skip", "skip", None)],
        ]

    @pytest.mark.asyncio
    async def test_reply_markup_wins_over_buttons(self):
        markup = await _send_and_get_markup({
            "reply_markup": [[{"text": "From markup", "callback_data": "m"}]],
            "buttons": [{"text": "From buttons", "callback_data": "b"}],
        })

        assert _keyboard(markup) == [[("From markup", "m", None)]]

    @pytest.mark.asyncio
    async def test_invalid_entries_are_skipped(self):
        markup = await _send_and_get_markup({
            "buttons": [
                [{"text": "Keep", "callback_data": "keep"}, "not a dict", {"callback_data": "no-text"}],
                [{"text": "No action"}],
                [],
            ],
        })

        assert _keyboard(markup) == [[("Keep", "keep", None)]]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("buttons", [[], "not a list", {"text": "x"}, [{"text": "No action"}]])
    async def test_no_usable_buttons_sends_no_keyboard(self, buttons):
        assert await _send_and_get_markup({"buttons": buttons}) is None

    @pytest.mark.asyncio
    async def test_buttons_skip_rich_and_go_on_the_first_chunk(self):
        adapter = _make_adapter(extra={"rich_messages": True})
        adapter.MAX_MESSAGE_LENGTH = 80
        adapter._bot.do_api_request = AsyncMock(return_value=SimpleNamespace(message_id=123))
        adapter._bot.send_message = AsyncMock(side_effect=lambda **kwargs: MagicMock(message_id=1))
        adapter._bot.send_chat_action = AsyncMock()

        content = "| Case | Status |\n|---|---|\n" + "| rich | ok |\n" * 12
        result = await adapter.send(
            "12345", content, metadata={"buttons": [{"text": "Go", "callback_data": "go"}]},
        )

        assert result.success is True
        adapter._bot.do_api_request.assert_not_called()
        calls = adapter._bot.send_message.await_args_list
        assert len(calls) > 1
        assert _keyboard(calls[0].kwargs["reply_markup"]) == [[("Go", "go", None)]]
        assert all("reply_markup" not in call.kwargs for call in calls[1:])
