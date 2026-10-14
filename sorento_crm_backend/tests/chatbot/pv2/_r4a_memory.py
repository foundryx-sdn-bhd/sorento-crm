"""R4a: MEMORY gained a `LAST REPLY:` line (its content is pinned in test_r4a_shown_rows.py). The older
byte-identity pins compare HELD / TOPICS slices of MEMORY against hand-written strings, so they read the slice
without that one line; where it sits in MEMORY is then free."""
from __future__ import annotations

import re


def without_last_reply(text: str) -> str:
    return re.sub(r"^LAST REPLY: .*\n?", "", text, flags=re.M)
