"""Set pages: the "more" line, how many products the next page shows, and the page size."""
from __future__ import annotations

#: One WhatsApp message, as the reply counts it, "more" line included. The repo's one budget:
#: `engine.WHATSAPP_MESSAGE_MAX_CHARS` is this value, and the MCP presenter's constant is pinned equal.
MESSAGE_MAX_CHARS = 3900


def more_line(n: int) -> str:
    return f'Reply "more" to see the next {n:,}.'


def next_count(total: int, offset: int, shown: int, cap: int) -> int:
    """How many products the next page shows: what is left, never past the cap."""
    return max(0, min(cap, total - offset - shown))


def page_size(named: int | None, cap: int) -> int:
    """How many products one page lists: the named count, never past the cap."""
    return min(named, cap) if named else cap
