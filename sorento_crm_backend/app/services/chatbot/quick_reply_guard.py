"""The safety net every outgoing quick-reply set crosses (QUICK-REPLY-NAMES, 6 Oct 2026).

Prod incident, 6 Oct 2026: the customer picker sent its three full customer names as
WhatsApp quick-reply buttons and Meta refused the whole message with error #131009
"Duplicate button title" - a button title is cut at 20 characters, and two of the
names were one string after the cut. The customer received nothing.

The owner's rule is applied where each set is PRODUCED (`turn/pending.py`,
`lanes/business/answer.py`, `lanes/escalation.py`): a set goes out only when every
title is a fixed short option. This module is the second line, at the seam where the
composed string is about to leave the engine, so a producer added later that forgets
the rule still cannot trip Meta:

* at most `MAX_BUTTONS` titles (WhatsApp shows three; the first three are kept);
* no title longer than `MAX_TITLE_CHARS` once trimmed - ONE over-long title drops the
  WHOLE set, because a partial set would leave the customer guessing which of the
  options printed in the text is the one they can tap;
* no duplicate titles (first occurrence kept, compared case-insensitively, since the
  transport's own duplicate check is what refused the message).

The value is n8n's own shape (AC-507): a comma-joined string or None, never a list.
A set the guard did not have to change is returned byte-identical, so every producer's
own separator ("," or ", ") survives; a set it changed is re-joined with ", ".
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: WhatsApp renders at most three reply buttons under one message.
MAX_BUTTONS = 3

#: Meta's hard limit on one reply button's title. Respond.io truncates past it, and the
#: truncated titles are what Meta's duplicate check compares.
MAX_TITLE_CHARS = 20


def guard(quick_replies: object, *, where: str = "reply") -> str | None:
    """`quick_replies` as it may leave the engine, or None when nothing may.

    `where` names the seam for the one log line written when something is dropped.
    """
    if not isinstance(quick_replies, str) or not quick_replies.strip():
        return None
    titles = [title.strip() for title in quick_replies.split(",")]
    titles = [title for title in titles if title]
    if not titles:
        return None

    too_long = [title for title in titles if len(title) > MAX_TITLE_CHARS]
    if too_long:
        logger.info(
            "chatbot quick replies dropped at %s: %d title(s) over %d chars (%r)",
            where,
            len(too_long),
            MAX_TITLE_CHARS,
            too_long[0],
        )
        return None

    kept: list[str] = []
    seen: set[str] = set()
    for title in titles:
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(title)
    duplicates = len(titles) - len(kept)
    capped = max(0, len(kept) - MAX_BUTTONS)
    kept = kept[:MAX_BUTTONS]

    if duplicates or capped:
        logger.info(
            "chatbot quick replies trimmed at %s: %d duplicate(s) dropped, %d over the cap of %d",
            where,
            duplicates,
            capped,
            MAX_BUTTONS,
        )
        return ", ".join(kept)
    # Unchanged set: hand back the producer's own string, separator included.
    return quick_replies
