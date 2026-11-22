"""Text cleanup shared by everything the bot writes to GitHub."""

import re

_SPACE = re.compile(r"\s+")


def plain(text: str, limit: int) -> str:
    """One line, clipped, with `@` mentions defused so feed or model text cannot notify anyone."""
    flat = _SPACE.sub(" ", text).strip().replace("@", "@\u200b")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
