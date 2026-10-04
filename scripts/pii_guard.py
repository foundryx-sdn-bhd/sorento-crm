#!/usr/bin/env python3
"""Fail when a tracked file holds personal data. The repo is PUBLIC.

Flags, in every tracked text file (UTF-8, or UTF-16 with a byte-order mark;
office files are scanned member by member):
  - a Malaysian mobile number (+60 1x / 60 1x / 01x, spaces or dashes allowed)
    that is not an agreed fake value (see `is_fake_phone`);
  - any value under a phone-like key (phone, phoneNumber, phone_no, wa_id,
    whatsapp, msisdn, mobile, contact_phone...), in JSON, YAML or key=value
    form and whatever its shape, unless it is an agreed fake value;
  - a lorry plate written next to a plate label ("Lorry Plate", lorry_plate,
    vehicle no, no kenderaan) that is not a fake `PLATE-<n>`;
  - a tracked raw capture path (`.playwright-mcp/`, `*.har`, `*.rdb`), which
    carries prod-copy pages, auth headers or queue payloads.

The agreed fake phones are exactly three shapes: a run of four zeros straight
after the 60 1x prefix (`+60100000001`, `+60 17-000 0501`, which also covers
ids turned into phones as `+60` plus `90000nnnn`), the classic
`012-345 6789` and nothing else of that family, and one digit repeated through
the whole subscriber number (`+60 11-111 1111`). Plates `PLATE-1`; names
`CUSTOMER A`, `DRIVER B`, `CONTACT C`; emails `*@example.com`. Findings print
the path, line and kind only, never the value.

Stdlib only, so the CI fast gate runs it without installing anything:
    python3 scripts/pii_guard.py
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

# A +60 / %2B60 / 0060 / 60 / 0 / (0 prefix, then 1x, then 7 or 8 subscriber
# digits. The lookarounds keep a match from starting or ending inside a longer
# number or a decimal fraction; a letter or underscore in front is fine
# (`contact_60...`).
_PHONE = re.compile(
    r"(?<![\d+])(?<!\d\.)(?:(?:\+|%2[Bb]|00)?60[\s-]?|\(?0)(1\d)\)?[\s-]?(\d{3,4})[\s-]?(\d{4})(?!\d|\.\d)"
)

_PLATE_LABEL = (
    r"(?:(?:lorry|car|vehicle|truck)[\s_]?(?:plate|no\b\.?|number)"
    # a bare "plate" only as a key or label ("plate": / Plate No:), not prose
    r"|\bplate(?:[\s_]?(?:no|number))?(?=\\?\"?\s*[:=])|no\.?\s?(?:kenderaan|plat))"
)
_PLATE_HINT = re.compile(r"plat|lorry|vehicle|truck|car[\s_]?no|car[\s_]?number", re.IGNORECASE)
_PLATE = re.compile(
    _PLATE_LABEL
    # The gap may span a line break: pretty-printed JSON puts "label" and
    # "value" on separate lines.
    + r"[^A-Za-z0-9]{0,40}(?:value[^A-Za-z0-9]{0,8})?"
    + r"([A-Z]{1,3}\s?\d{1,4}(?:\s?[A-Z]{1,2})?)(?![A-Za-z0-9-])",
    re.IGNORECASE,
)

# A value under a phone-like key, in any shape (a recorder may build a "phone"
# that does not look like a mobile, e.g. from a contact id).
_KEY_HINT = re.compile(r"phone|wa_id|whatsapp|msisdn|mobile", re.IGNORECASE)
_KEYED_PHONE = re.compile(
    r"(?:phone|wa_id|whatsapp|msisdn|mobile)\w*"
    r"""\\?["']?\s*[:=]\s*\\?["']?"""
    r"(\+?(?:60|0)\d[\d\s-]{6,14}\d)(?![\d-])",
    re.IGNORECASE,
)

_FORBIDDEN_PREFIXES = (".playwright-mcp/",)
_FORBIDDEN_SUFFIXES = (".har", ".rdb")

_BINARY_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".xls",
    ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz", ".mp4", ".mov",
)
# Office files are zips of XML: scanned member by member.
_OOXML_SUFFIXES = (".xlsx", ".xlsm", ".docx", ".pptx")
# This guard's own sources describe the patterns.
_SKIP = ("scripts/pii_guard.py",)


@dataclass(frozen=True)
class Finding:
    kind: str
    line: int


def is_fake_phone(digits: str) -> bool:
    """True only for the agreed fake shapes: a zero run of four straight after the
    60xx prefix (+60 17-000 0501), exactly the classic 012-345 6789, or one digit
    repeated through the whole subscriber number (+60 11-111 1111)."""
    sub = re.sub(r"\D", "", digits)
    if sub.startswith("0"):
        sub = "6" + sub
    tail = sub[4:]
    return tail.startswith("0000") or sub == "60123456789" or len(set(tail)) == 1


def scan_text(text: str) -> list[Finding]:
    found: list[Finding] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        for m in _PHONE.finditer(line):
            if not is_fake_phone("601" + m.group(1)[1] + m.group(2) + m.group(3)):
                found.append(Finding("phone", lineno))
        if not _KEY_HINT.search(line):
            continue
        for m in _KEYED_PHONE.finditer(line):
            if not _PHONE.search(m.group(1)) and not is_fake_phone(m.group(1)):
                found.append(Finding("phone", lineno))
    if not _PLATE_HINT.search(text):
        return found
    for m in _PLATE.finditer(text):
        found.append(Finding("plate", text.count("\n", 0, m.start(1)) + 1))
    return found


def scan_bytes(path: str, data: bytes) -> list[Finding]:
    """Office files: every XML member. Anything else with no NUL byte in its
    head is text, decoded leniently so a non-UTF-8 file is still read."""
    if path.lower().endswith(_OOXML_SUFFIXES):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return [
                    f
                    for name in z.namelist()
                    if name.endswith(".xml")
                    for f in scan_text(z.read(name).decode("utf-8", "replace"))
                ]
        except zipfile.BadZipFile:
            return []
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return scan_text(data.decode("utf-16", "replace"))
    if b"\0" in data[:8000]:
        return []
    return scan_text(data.decode("utf-8", "replace"))


def forbidden_path(path: str) -> bool:
    return path.startswith(_FORBIDDEN_PREFIXES) or path.endswith(_FORBIDDEN_SUFFIXES)


def format_finding(path: str, finding: Finding) -> str:
    return f"{path}:{finding.line}: {finding.kind}"


def scan_repo(root: Path) -> list[tuple[str, Finding]]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout.decode()
    problems: list[tuple[str, Finding]] = []
    for path in filter(None, out.split("\0")):
        if forbidden_path(path):
            problems.append((path, Finding("raw-capture-path", 0)))
            continue
        if path in _SKIP or path.lower().endswith(_BINARY_SUFFIXES):
            continue
        try:
            data = (root / path).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        problems.extend((path, f) for f in scan_bytes(path, data))
    return problems


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    problems = scan_repo(root)
    for path, finding in problems:
        print(format_finding(path, finding))
    if problems:
        print(
            f"pii-guard: {len(problems)} finding(s). The repo is public: replace with "
            "fake values (see scripts/pii_guard.py docstring).",
            file=sys.stderr,
        )
        return 1
    print("pii-guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
