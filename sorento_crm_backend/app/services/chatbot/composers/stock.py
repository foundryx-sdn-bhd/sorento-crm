"""Stock reply pieces: the miss line, the "none" line and the stock block rows."""
from __future__ import annotations

from typing import Any

from app.services.chatbot import composers, jsc
from app.services.chatbot.composers import format as fmt


def miss_line(misses: list[str]) -> str:
    """The line naming codes nothing placed. Gate on drops the colon (one dealer wording)."""
    if composers.enabled():
        from app.services.chatbot.composers import misses as composers_misses

        return composers_misses.not_found(misses).text if any(not fmt.is_uuid(m) for m in misses) else ""
    return f"Couldn't find: {', '.join(misses)}."


def none_for(word: str, codes: list[str]) -> str:
    return f"No {word} for {', '.join(codes)}."


def dealer_block(item: Any) -> str:
    """An answered dealer item: its title, then the discontinued marker and each
    `Alternative:` line, as `fetch._item_line` prints it unnumbered in English."""
    line = jsc.nullish_str(jsc.get(item, "title")).strip()
    if fmt.is_uuid(line):
        line = ""
    flags = jsc.get(item, "flags")
    if jsc.truthy(flags):
        if jsc.truthy(jsc.get(flags, "discontinued")):
            line += "\n⚠️  *(PRODUCT DISCONTINUED)*"
        for alternative_line in jsc.array(jsc.get(flags, "alternative_lines")):
            line += f"\nAlternative: {jsc.nullish_str(alternative_line)}"
    return line


def block(n: int, item: Any) -> str:
    """A staff stock row numbered `n`, the way `fetch._item_line` prints it in English."""
    def shown(f: Any) -> Any:
        v = jsc.get(f, "granted_value") if jsc.get(f, "granted_value") is not None else jsc.get(f, "value")
        return None if fmt.is_uuid(v) else v

    field_lines = "\n".join(fmt.label_line(fmt.label(f), shown(f)) for f in (jsc.get(item, "fields") or []))
    if not field_lines:
        title = jsc.nullish_str(jsc.get(item, "title")).strip()
        field_lines = "" if fmt.is_uuid(title) else title
    line = f"{n}. {field_lines}"
    flags = jsc.get(item, "flags")
    if jsc.truthy(flags):
        if jsc.truthy(jsc.get(flags, "discontinued")):
            line += "\n⚠️  *(PRODUCT DISCONTINUED)*"
        for alternative_line in jsc.array(jsc.get(flags, "alternative_lines")):
            line += f"\nAlternative: {jsc.nullish_str(alternative_line)}"
        for alternative_code in jsc.array(jsc.get(flags, "alternatives")):
            line += f"\n*Alternative:* {jsc.nullish_str(alternative_code)}"
        if jsc.truthy(jsc.get(flags, "expired")):
            line += "\n⚠️  *(EXPIRED)*"
        if jsc.truthy(jsc.get(flags, "unallocated")):
            line += "\n\U0001f6a9  *(PENDING ALLOCATION)*"
        elif jsc.truthy(jsc.get(flags, "partially_allocated")):
            line += "\n\U0001f6a9  *(PARTIAL ALLOCATION)*"
    return line
