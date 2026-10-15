"""Per-send options ``TelegramAdapter.send()`` reads from ``metadata``, kept out of the adapter facade:
an inline keyboard (``reply_markup`` or ``buttons``) and a link-preview override (``disable_link_preview``)."""

from __future__ import annotations

from typing import Any, Optional


def _sdk(name: str) -> Any:
    """A PTB name as the adapter module binds it (it rebinds them after a lazy install)."""
    from plugins.platforms.telegram import adapter

    return getattr(adapter, name)


class TelegramSendMetadataMixin:
    """Turn ``send()`` metadata into Bot API kwargs for ``TelegramAdapter``."""

    def _link_preview_disabled(self, metadata: Optional[dict[str, Any]] = None) -> bool:
        """Return whether this send should disable Telegram link previews.

        ``metadata["disable_link_preview"]`` overrides the adapter-level
        ``disable_link_previews`` extra when the key is present.
        """
        if metadata is not None and "disable_link_preview" in metadata:
            return bool(metadata.get("disable_link_preview"))
        return bool(getattr(self, "_disable_link_previews", False))

    def _link_preview_kwargs(self, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self._link_preview_disabled(metadata):
            return {}
        LinkPreviewOptions = _sdk("LinkPreviewOptions")
        if LinkPreviewOptions is not None:
            return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        return {"disable_web_page_preview": True}

    def _coerce_send_reply_markup(self, value: Any) -> Any:
        """Turn send() metadata ``reply_markup`` into an InlineKeyboardMarkup.

        Accepts an already-built markup object, Telegram's
        ``{"inline_keyboard": [...]}`` shape, or structured rows of button
        dicts (``text`` plus ``callback_data`` and/or ``url``).
        """
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            rows = value
        elif isinstance(value, dict) and "inline_keyboard" in value:
            rows = value.get("inline_keyboard")
        else:
            return value
        if not isinstance(rows, (list, tuple)):
            return None
        InlineKeyboardButton, InlineKeyboardMarkup = _sdk("InlineKeyboardButton"), _sdk("InlineKeyboardMarkup")
        built: list[list[Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            buttons: list[Any] = []
            for btn in row:
                if isinstance(InlineKeyboardButton, type) and isinstance(
                    btn, InlineKeyboardButton
                ):
                    buttons.append(btn)
                    continue
                if not isinstance(btn, dict):
                    continue
                text = btn.get("text")
                if not text:
                    continue
                kwargs: dict[str, Any] = {}
                if btn.get("callback_data") is not None:
                    kwargs["callback_data"] = str(btn["callback_data"])
                if btn.get("url") is not None:
                    kwargs["url"] = str(btn["url"])
                if not kwargs:
                    continue
                buttons.append(InlineKeyboardButton(str(text), **kwargs))
            if buttons:
                built.append(buttons)
        if not built:
            return None
        return InlineKeyboardMarkup(built)

    def _send_reply_markup_kwargs(
        self, metadata: Optional[dict[str, Any]], chunk_index: int
    ) -> dict[str, Any]:
        """Attach inline keyboard markup to the first chunk only."""
        if chunk_index != 0 or not metadata:
            return {}
        markup = self._coerce_send_reply_markup(self._send_markup_source(metadata))
        if markup is None:
            return {}
        return {"reply_markup": markup}

    @staticmethod
    def _send_markup_source(metadata: dict[str, Any]) -> Any:
        """The keyboard a send asks for: ``reply_markup`` when set, else ``buttons`` as rows.

        ``buttons`` takes the shape of #16544 and #95594: a flat list of button dicts is one row; a
        list holding any list is a grid, where a bare button dict becomes a row of its own.
        """
        if metadata.get("reply_markup") is not None:
            return metadata["reply_markup"]
        buttons = metadata.get("buttons")
        if not isinstance(buttons, (list, tuple)) or not buttons:
            return None
        if not any(isinstance(b, (list, tuple)) for b in buttons):
            return [buttons]
        return [b if isinstance(b, (list, tuple)) else [b] for b in buttons]
