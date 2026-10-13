"""The production parser prompt, tagged per audience (PARSER-PER-AUDIENCE, #1429).

Owner ruling (a): built AT MIGRATION TIME from the template the `production` label points
at, so every #1405 `{{variable}}` and every owner edit since survives. `{{#only <tag>}}`
markers (the table is `app/services/prompt_gates.PROMPT_GATES`) go around each span a
reveal grant removes, and the #1470 LOW STOCK REPORT FILTERS block goes in once, tagged
`low_stock_report`. Published as ONE new UNLABELLED version: no label moves and no
existing version is touched; the owner publishes it himself.

Where the markers go. `alembic/data/chatbot_semantic_parser.prod-20261001.tagged.txt` is
the owner's text of 1 Oct 2026 (`...prod-20261001.txt`) with the markers in place: the
marker spec, read here and by the render tests. Only the heavy instruction blocks are
wrapped (owner ruling 5 Oct 2026: the classification vocabulary, the domain enum, domain
words, status values and intent names, stays for every audience), and each gated domain
has a one-line `{{#without tag}}` stub that a contact without the grant sees instead.
Each marker is anchored to its line of that text and carried onto the production
template line by line:
  * a line the template still has as it was: the marker goes onto it;
  * a line whose list #1405 swapped for a registry `{{variable}}`: a marker pair around the
    swapped list is dropped and a pair outside it is kept, at the same place;
  * anything else (an owner edit under a marker, a pair cut by a variable): fail closed.
The gated section headings (`GATED_HEADINGS`) must all be in the template before, and none
may survive the no-grant render after.

Proven before insert: rendering the result for all gated grants equals
`with_account_block(production template)` plus the low stock block, byte for byte; every
tag is present; the no-grant render carries no gated heading and no low stock block. Any
check failing: nothing is written, a WARNING is logged and `apply` returns None.

Nothing is written without a `production` label (a blank database). Idempotent: does
nothing when a version built from the same production template exists. Downgrade deletes
only this migration's own rows, and only while unlabelled.

Revision ID: ppa_0001_tagged_parser
Revises: maskp_0002_measure_words
Create Date: 2026-10-05
"""
from __future__ import annotations

import difflib
import logging
import pathlib
import re

from alembic import op
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

revision = "ppa_0001_tagged_parser"
down_revision = "maskp_0002_measure_words"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
MARKER_KEY = "ppa_0001_tagged_parser"
DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
SOURCE = DATA / "chatbot_semantic_parser.prod-20261001.txt"
TAGGED = DATA / "chatbot_semantic_parser.prod-20261001.tagged.txt"

#: Section headings a reveal grant removes, with the tag that must wrap each.
GATED_HEADINGS: tuple[tuple[str, str], ...] = (
    ("== PURCHASE ORDERS", "purchase_order"),
    ("== SPO LAST RECEIPT", "spo_allocation"),
    ("== LAST PURCHASE COST", "purchase_cost"),
    ("== LOW STOCK REPORT", "low_stock_report"),
    ("== SALES REPORT", "sales"),
    ("== SALES ANALYSIS", "sales"),
    ("== TOP SELLING", "sales"),
)
LOW_STOCK_TAG = "low_stock_report"
MESSAGE = (
    "The production text tagged per audience: each span a reveal grant removes is wrapped "
    "in {{#only tag}}, and the LOW STOCK REPORT FILTERS block is carried once for contacts "
    "with the low stock report. Renders identical to the production version (plus that "
    "block) for a contact holding all four gated grants. Unlabelled: publish from the "
    "Prompts page."
)

_MARKER_RE = re.compile(r"\{\{\s*#only\s+([A-Za-z0-9_]+)\s*\}\}|\{\{\s*/only\s*\}\}")
#: A stub kept only for a contact WITHOUT the grant, on lines of its own; carried as ONE
#: insertion (it is not in the source text at all).
_STUB_RE = re.compile(r"^\{\{#without ([A-Za-z0-9_]+)\}\}\n.*?\n\{\{/without\}\}\n", re.M | re.S)
_ANY_RE = re.compile(f"{_STUB_RE.pattern}|{_MARKER_RE.pattern}", re.M | re.S)
_TOKEN_RE = re.compile(r"\{\{\s*[a-z_]+\s*\}\}")


class _Refuse(Exception):
    """A check failed: write nothing."""


def _insertions(source: str, tagged: str) -> list[dict]:
    """Every marker of the tagged file, against the source text: `off` (where it goes in
    the source), `text` (what goes there: the whole line for an own-line marker), `pair`,
    `seq` (file order, which breaks ties at one offset) and `back` (an own-line close
    marker belongs to the line BEFORE it; everything else to the line it starts)."""
    out: list[dict] = []
    stack: list[int] = []
    removed = 0
    pieces: list[str] = []
    cursor = 0
    for seq, m in enumerate(_ANY_RE.finditer(tagged)):
        if m.group(1):  # a whole stub: its own pair, placed like an own-line open marker
            pieces.append(tagged[cursor : m.start()])
            out.append({"off": m.start() - removed, "text": m.group(0), "pair": seq, "seq": seq, "back": False})
            removed += m.end() - m.start()
            cursor = m.end()
            continue
        line_start = tagged.rfind("\n", 0, m.start()) + 1
        nl = tagged.find("\n", m.end())
        line_end = len(tagged) if nl == -1 else nl + 1
        own = not tagged[line_start : m.start()].strip() and not tagged[m.end() : line_end].strip()
        start, end = (line_start, line_end) if own else (m.start(), m.end())
        if start < cursor:
            raise _Refuse("two markers share a line with one alone on it")
        if m.group(2):
            pair = seq
            stack.append(seq)
        else:
            if not stack:
                raise _Refuse("stray {{/only}} in the tagged file")
            pair = stack.pop()
        pieces.append(tagged[cursor:start])
        out.append({"off": start - removed, "text": tagged[start:end], "pair": pair, "seq": seq,
                    "back": own and not m.group(1)})
        removed += end - start
        cursor = end
    pieces.append(tagged[cursor:])
    if stack:
        raise _Refuse("unclosed {{#only}} in the tagged file")
    if "".join(pieces) != source:
        raise _Refuse("the tagged file is not the source file plus markers")
    return out


def _starts(lines: list[str]) -> list[int]:
    out, at = [], 0
    for line in lines:
        out.append(at)
        at += len(line)
    return out


def _carry(source: str, tagged: str, template: str) -> str:
    """`template` with the tagged file's markers carried onto it (see the module doc)."""
    ins = _insertions(source, tagged)
    src_lines = source.splitlines(keepends=True)
    tpl_lines = template.splitlines(keepends=True)
    src_starts, tpl_starts = _starts(src_lines), _starts(tpl_lines)

    def anchor(item: dict) -> tuple[int, int]:
        probe = item["off"] - 1 if item["back"] else item["off"]
        line = max(0, _bisect(src_starts, probe))
        return line, item["off"] - src_starts[line]

    placed: dict[int, int | None] = {}  # seq -> template offset, None when dropped
    contained: dict[int, tuple] = {}
    ops = difflib.SequenceMatcher(None, src_lines, tpl_lines, autojunk=False).get_opcodes()
    for item in ins:
        line, col = anchor(item)
        op = next((o for o in ops if o[1] <= line < o[2]), None)
        if op is None:
            raise _Refuse(f"marker at source line {line + 1} has no line in the template")
        kind, a1, a2, b1, b2 = op
        if kind == "equal":
            placed[item["seq"]] = tpl_starts[b1 + line - a1] + col
            continue
        if kind != "replace":
            raise _Refuse(f"source line {line + 1} carrying a marker is gone from the template")
        src_hunk, tpl_hunk = "".join(src_lines[a1:a2]), "".join(tpl_lines[b1:b2])
        p = len(_common_prefix(src_hunk, tpl_hunk))
        s = len(_common_prefix(src_hunk[p:][::-1], tpl_hunk[p:][::-1]))
        e = len(src_hunk) - s
        middle = tpl_hunk[p : len(tpl_hunk) - s]
        if not _TOKEN_RE.search(middle) or _TOKEN_RE.sub("", middle).strip():
            raise _Refuse(f"source line {line + 1} carrying a marker was edited in the template")
        o = src_starts[line] - src_starts[a1] + col
        base = tpl_starts[b1] if b1 < len(tpl_starts) else len(template)
        contained[item["seq"]] = (op, o, p, e, src_hunk)
        if o <= p:
            placed[item["seq"]] = base + o
        elif o >= e:
            placed[item["seq"]] = base + o - len(src_hunk) + len(tpl_hunk)
        else:
            placed[item["seq"]] = None

    by_pair: dict[int, list[dict]] = {}
    for item in ins:
        by_pair.setdefault(item["pair"], []).append(item)
    keep: list[dict] = []
    for pair, items in by_pair.items():
        if len(items) == 1:  # a stub
            if placed[items[0]["seq"]] is None:
                raise _Refuse(f"a stub at source line {anchor(items[0])[0] + 1} falls inside a registry variable")
            keep.append(items[0])
            continue
        opener, closer = items
        a, b = contained.get(opener["seq"]), contained.get(closer["seq"])
        if a and b and a[0] == b[0] and _in_list(a[1], b[1], a[2], a[3], a[4]):
            continue  # the pair sits in a list a registry variable now renders
        if placed[opener["seq"]] is None or placed[closer["seq"]] is None:
            raise _Refuse(
                f"a marker pair at source line {anchor(opener)[0] + 1} is cut by a registry variable"
            )
        keep.extend((opener, closer))
    out = template
    for item in sorted(keep, key=lambda i: (placed[i["seq"]], i["seq"]), reverse=True):
        at = placed[item["seq"]]
        out = out[:at] + item["text"] + out[at:]
    return out


def _in_list(o1: int, o2: int, p: int, e: int, hunk: str) -> bool:
    """True when the span `o1..o2` of a replaced hunk is the list a variable took (`p..e`)
    give or take the list's separators and line breaks: the pair goes with the list."""
    if not (o1 < e and o2 > p):
        return False
    outside = hunk[o1 : min(o2, p)] + hunk[max(o1, e) : o2]
    return not re.sub(r"[\s|,]", "", outside)


def _bisect(starts: list[int], offset: int) -> int:
    import bisect

    return bisect.bisect_right(starts, offset) - 1


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


def _with_low_stock_block(text: str, *, wrap: bool = True) -> str:
    """The #1470 block once, tagged `low_stock_report` (`wrap`), just before the policy
    blocks (beneath the ACCOUNT-LEDGER block; at the end when there are none). A template
    already carrying the code's block has that one wrapped instead."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, LOW_STOCK_FILTERS_ADDENDUM

    block = LOW_STOCK_FILTERS_ADDENDUM.strip("\n")
    wrapped = "{{#only " + LOW_STOCK_TAG + "}}\n" + block + "\n{{/only}}" if wrap else block
    count = text.count(block)
    if count > 1 or (count == 0 and "LOW STOCK REPORT FILTERS" in text):
        raise _Refuse("the template carries a low stock filters block that is not the code's")
    if count == 1:
        return text.replace(block, wrapped, 1)
    if BLOCKS_BEGIN in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        return f"{head}{wrapped}\n\n{BLOCKS_BEGIN}{rest}"
    return f"{text.rstrip(chr(10))}\n\n{wrapped}\n"


def build(template: str) -> str:
    """The tagged version of `template`, or raise `_Refuse`. Pure: no database."""
    from app.services import chatbot_prompt_vars
    from app.services.prompt_gates import PROMPT_GATES, render_for_audience

    if _MARKER_RE.search(template) or "{{#without" in template:
        raise _Refuse("the production template already carries audience markers")
    missing = [title for title, _ in GATED_HEADINGS if title not in template]
    if missing:
        raise _Refuse(f"gated heading(s) missing from the production template: {missing}")
    base = chatbot_prompt_vars.with_account_block(template)
    source = SOURCE.read_text(encoding="utf-8")
    tagged = _carry(source, TAGGED.read_text(encoding="utf-8"), base)
    tagged = _with_low_stock_block(tagged)

    everything = [g.grant for g in PROMPT_GATES]
    if render_for_audience(tagged, everything) != _with_low_stock_block(base, wrap=False):
        raise _Refuse("the full-audience render differs from the production template")
    tags = set(re.findall(r"\{\{#only (\w+)\}\}", tagged))
    if tags != {g.tag for g in PROMPT_GATES}:
        raise _Refuse(f"tags carried {sorted(tags)} are not every gate's")
    bare = render_for_audience(tagged, [])
    left = [title for title, _ in GATED_HEADINGS if title in bare]
    if left or "LOW STOCK REPORT FILTERS" in bare:
        raise _Refuse(f"gated text survives the no-grant render: {left or ['LOW STOCK REPORT FILTERS']}")
    for title, tag in GATED_HEADINGS:
        grant = next(g.grant for g in PROMPT_GATES if g.tag == tag)
        others = [g.grant for g in PROMPT_GATES if g.grant != grant]
        if title in render_for_audience(tagged, others):
            raise _Refuse(f"{title} is not wrapped in {tag}")
    return tagged


def apply(bind) -> int | None:
    from app.models.ai_prompt import AIPromptVersion

    session = Session(bind=bind)
    try:
        live = session.execute(
            sql(
                "SELECT v.version, v.variables, v.template FROM ai_prompt_versions v "
                "JOIN ai_prompt_labels l ON l.version_id = v.id "
                "WHERE l.name = :n AND l.label = 'production'"
            ),
            {"n": PROMPT_NAME},
        ).first()
        if live is None:
            logger.warning("tagged parser: no production label on %s; nothing written", PROMPT_NAME)
            return None
        try:
            template = build(live[2] or "")
        except _Refuse as why:
            logger.warning("tagged parser: %s; nothing written", why)
            return None
        exists = session.execute(
            sql(
                "SELECT version FROM ai_prompt_versions WHERE name = :n AND (template = :t "
                "OR (config_json->>:k = 'true' AND config_json->>'from_production_version' = :pv)) "
                "LIMIT 1"
            ),
            {"n": PROMPT_NAME, "t": template, "k": MARKER_KEY, "pv": str(int(live[0]))},
        ).scalar()
        if exists is not None:
            logger.info("tagged parser already published as v%s; nothing to do", exists)
            return None
        top = session.execute(
            sql("SELECT max(version) FROM ai_prompt_versions WHERE name = :n"), {"n": PROMPT_NAME}
        ).scalar()
        version = int(top or 0) + 1
        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=version,
                type="text",
                template=template,
                variables=list(live[1] or ["current_date"]),
                config_json={
                    MARKER_KEY: True,
                    "from_production_version": int(live[0]),
                    "full_audience_identical": True,
                },
                commit_message=MESSAGE,
            )
        )
        session.commit()
        return version
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def remove(bind) -> None:
    """Delete this migration's own rows (and round 1's, published from the static file),
    and only while nobody has labelled them."""
    bind.execute(
        sql(
            "DELETE FROM ai_prompt_versions v WHERE v.name = :n "
            "AND (v.config_json->>:k = 'true' OR v.config_json->>'tagged_sha256' IS NOT NULL) "
            "AND NOT EXISTS (SELECT 1 FROM ai_prompt_labels l WHERE l.version_id = v.id)"
        ),
        {"n": PROMPT_NAME, "k": MARKER_KEY},
    )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    remove(op.get_bind())
