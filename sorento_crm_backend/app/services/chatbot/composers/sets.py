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


#: The range a cut code-lookup list adds to its intro (Lists L1); it states no total, because the
#: probe behind such a list may have stopped at its cap.
RANGE_CAPPED = "(showing {first} to {last})"
#: The last line of a code list (n = the codes listed): the matches shown may not be all of them.
NARROW = "Those are the first {n} matches. Send more of the code to narrow it."


def ranged(sentence: str, fragment: str) -> str:
    """`fragment` put ahead of the sentence's final stop (none: appended after a space); no space
    before a full-width bracket."""
    text = sentence.rstrip()
    sep = "" if fragment[:1] == "\uff08" else " "
    if text and text[-1] in ".!?\u3002\uff01\uff1f":
        return f"{text[:-1]}{sep}{fragment}{text[-1]}"
    return f"{text} {fragment}"
