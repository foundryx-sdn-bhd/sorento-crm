"""File reply pieces: the attached sentence, where it goes once, and the one send de-dup rule."""
from __future__ import annotations

from typing import Any

from app.services.chatbot import jsc

ATTACHED_SENTENCE = "I have attached the file(s) below."


def sendable(entries: list) -> list:
    """What is sent: a url-less entry is dropped (a send with no link is a dead action), then an
    entry whose url OR `fileHash` was already seen. A non-dict entry is kept as it came."""
    kept: list[Any] = []
    seen_urls: set[Any] = set()
    seen_hashes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        url = entry.get("url")
        if not jsc.truthy(url):
            continue
        file_hash = jsc.nullish_str(entry.get("fileHash")).strip()
        if url in seen_urls or (file_hash and file_hash in seen_hashes):
            continue
        seen_urls.add(url)
        if file_hash:
            seen_hashes.add(file_hash)
        kept.append(entry)
    return kept


def place_attached(parts: list[str], opened: list[bool], printed: str, has_files: bool) -> list[str]:
    """The attached sentence once: the first section that opens with `printed` keeps it, later
    ones lose that paragraph, and with files and no opener it closes the reply."""
    out: list[str] = []
    kept = False
    for part, opens in zip(parts, opened):
        if opens and kept:
            rest = part[len(printed) + 2 :] if part.startswith(printed + "\n\n") else ""
            if rest:
                out.append(rest)
            continue
        kept = kept or opens
        out.append(part)
    if has_files and not kept:
        out.append(printed)
    return out
