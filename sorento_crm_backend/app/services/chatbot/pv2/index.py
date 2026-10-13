"""The parser v2 INDEX: the canonical keys the model may return, rendered from live data
(CONTRACT-PV2-13oct.md section 2).

Curated, fixed section order, rows by (sort_order, key); no counts, no dates, no ids, so
the text (and its sha) moves only when the vocabulary moves. Rendered at PUBLISH time
(`publish_v2`), never per turn: a turn runs on an immutable version, and the keys
`schema.validate` accepts are that PINNED version's, parsed back from its own text by
`parse_index_keys`. One text, two readers (the model and the validator), so they cannot
disagree.

Line grammar (what `parse_index_keys` reads): every row is `<key> = <label>` optionally
followed by ` | aka a, b` (words that help the MODEL pick the key; code never matches
text with them), ` | unit <u>`, ` | types: a, b` (PRODUCT CLASSES) or ` | values: a, b`
(SPEC ATTRIBUTES). REPORTS rows are `<dataset|measure|dimension|basis> <key> = <label>`,
supplied by `pv2/report_measures.index_lines()` (PV2-REPORTS).

An alias two keys of one kind share is printed only under the winner named in
`chatbot_alias_precedence`; with no winner it is printed under neither and
`publish_v2` refuses until staff decide (`alias_clashes`).
"""
from __future__ import annotations

import hashlib
import importlib
import re
from collections import defaultdict

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.services.chatbot.pv2.schema import IndexKeys

PROMPT_KEY_V2 = "chatbot_semantic_parser_v2"
PUBLISH_LABEL = "staging"  # never "production" tonight (contract section 6/8)
HEAD_CAP_CHARS = 32_000
INDEX_CAP_CHARS = 8_000

S_DOMAINS = "## DOMAINS"
S_KINDS = "## ENTITY KINDS"
S_BRANDS = "## BRANDS"
S_ATTACH = "## ATTACHMENT TYPES (key = name | aka)"
S_CLASSES = "## PRODUCT CLASSES"
S_SPECS = "## SPEC ATTRIBUTES"
S_REPORTS = "## REPORTS"
S_TEAMS = "## TEAMS"
SECTIONS = (S_DOMAINS, S_KINDS, S_BRANDS, S_ATTACH, S_CLASSES, S_SPECS, S_REPORTS, S_TEAMS)

#: Index-listed entity kinds: the model must return one of these sections' keys; never
#: resolved from the phrase (contract section 4).
INDEX_ENTITY_KINDS = ("brand", "product_class", "product_type")
#: v1 entity kinds the v2 Ask carries in its own fields, or replaces with an index kind.
_KINDS_NOT_LISTED = frozenset({"attachment_type", "specification", "category", "attachment"})
#: Spec registry keys that are not attributes: the class and type are what the product IS
#: (PRODUCT CLASSES), the brand is its own kind. Same set `chatbot_parser_prompt` skips.
_SPEC_SKIP = frozenset({"class", "brand", "product_type"})
UNCLASSIFIED = "unclassified"

_SLUG_RE = re.compile(r"[^0-9a-z]+")


def slug(value: str) -> str:
    return _SLUG_RE.sub("_", str(value).strip().lower()).strip("_")


def norm_alias(value: str) -> str:
    """Casefold + collapse whitespace: the one normalisation for alias identity."""
    return " ".join(str(value).casefold().split())


def attachment_type_key(code: str | None, type_name: str) -> str | None:
    """An attachment type's index key: its `code`. A type with no code is not listed (no
    key the resolver could look up; `pv2_0002_alias_seed` fills the codes that matter).
    PV2-RESOLVE looks a key up as `attachment_types.code`."""
    return (code or "").strip() or None


def product_class_key(class_label: str) -> str:
    return slug(class_label)


def _clean(value: str) -> str:
    # Index rows are one line each and `|`/`=`/`,` are the grammar.
    return " ".join(str(value).replace("|", "/").replace("\n", " ").split())


def _alias_list(words) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for w in words or []:
        if not isinstance(w, str):
            continue
        w2 = _clean(w).replace(",", " ").strip()
        n = norm_alias(w2)
        if n and n not in seen:
            seen.add(n)
            out.append(w2)
    return out


# --------------------------------------------------------------------------- readers


def _domains(db: Session) -> list[str]:
    from app.services.chatbot.turn.policy import load_policy

    policy = load_policy(db)
    rows = sorted((d for d in policy.domains if d.supported), key=lambda d: (d.sort_order, d.name))
    return [f"{d.name} = {_clean(d.label)}" for d in rows]


def _kinds(db: Session) -> list[str]:
    from app.services.chatbot.turn.policy import load_policy

    lines = []
    for k in load_policy(db).kinds:
        if k.kind in _KINDS_NOT_LISTED:
            continue
        label = "brand (key from BRANDS)" if k.kind == "brand" else k.kind.replace("_", " ")
        lines.append(f"{k.kind} = {label}")
    lines.append("product_class = product class (key from PRODUCT CLASSES)")
    lines.append("product_type = product type (key from PRODUCT CLASSES types)")
    return lines


def _brands(db: Session) -> list[tuple[str, str, list[str]]]:
    rows = db.execute(
        text(
            "select brand_code, min(brand_name) from brands where is_active "
            "group by brand_code order by brand_code"
        )
    ).all()
    return [(r[0].strip(), _clean(r[1]), []) for r in rows if r[0] and r[0].strip()]


def _attachment_types(db: Session) -> list[tuple[str, str, list[str]]]:
    rows = db.execute(
        text("select code, type_name, search_synonyms from attachment_types order by type_name")
    ).all()
    out: dict[str, tuple[str, str, list[str]]] = {}
    for code, name, syn in rows:
        key = attachment_type_key(code, name)
        if key and key not in out:
            out[key] = (key, _clean(name), _alias_list(syn))
    return [out[k] for k in sorted(out)]


def _classes(db: Session) -> tuple[list[tuple[str, str, list[str]]], dict[str, list[str]]]:
    """(class rows, class key -> product type keys). A type sits under the class most of
    its products carry (ties by class key), so each type is listed once."""
    from app.services.product_class_signal import CLASS_SYNONYMS
    from app.services.product_spec_registry import active_registry, merged_allowed_values

    labels: dict[str, str] = {}
    aliases: dict[str, list[str]] = defaultdict(list)
    for label, syn in db.execute(
        text(
            "select class_label, search_synonyms from product_categories "
            "where class_label is not null and is_active and is_searchable "
            "order by class_label, category_code"
        )
    ).all():
        key = product_class_key(label)
        if not key:
            continue
        labels.setdefault(key, _clean(label))
        aliases[key].extend(syn or [])
    for label, words in CLASS_SYNONYMS.items():
        key = product_class_key(label)
        if key in labels:
            aliases[key].extend(words)

    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for cls, ptype, n in db.execute(
        text(
            "select values->'class'->>'value', values->'product_type'->>'value', count(*) "
            "from product_specifications "
            "where values ? 'class' and values ? 'product_type' group by 1, 2"
        )
    ).all():
        if cls and ptype:
            counts[str(ptype)][product_class_key(cls)] = int(n)

    type_keys: list[str] = []
    for row in active_registry(db):
        if row.spec_key == "product_type":
            type_keys = [str(v) for v in dict.fromkeys(merged_allowed_values(row))]
    for t in counts:
        if t not in type_keys:
            type_keys.append(t)

    by_class: dict[str, list[str]] = defaultdict(list)
    for t in sorted(type_keys):
        placed = {c: n for c, n in counts.get(t, {}).items() if c in labels}
        if placed:
            best = sorted(placed.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            by_class[best].append(t)
        else:
            by_class[UNCLASSIFIED].append(t)
    rows = [(k, labels[k], _alias_list(aliases[k])) for k in sorted(labels)]
    return rows, by_class


def _spec_attributes(db: Session) -> tuple[list[tuple[str, str, list[str]]], dict[str, str]]:
    """(rows, key -> "unit .. | values: .." text) for the SPEC ATTRIBUTES section."""
    from app.services.product_spec_registry import (
        active_registry,
        merged_allowed_values,
        merged_synonyms,
    )

    rows = []
    extra: dict[str, str] = {}
    for row in active_registry(db):
        if row.spec_key in _SPEC_SKIP:
            continue
        data_type = (row.data_type or "").lower()
        parts = []
        if row.unit:
            parts.append(f"unit {_clean(row.unit)}")
        if data_type == "boolean":
            parts.append("yes/no")
        elif data_type != "numeric":
            values = [str(v) for v in dict.fromkeys(merged_allowed_values(row))]
            if values:
                parts.append("values: " + ", ".join(_clean(v) for v in values))
        if parts:
            extra[row.spec_key] = " | ".join(parts)
        own = _alias_list(merged_synonyms(row).get("_self") or [])
        rows.append((row.spec_key, _clean(row.label or row.spec_key), own))
    return rows, extra


def _report_lines() -> list[str]:
    try:
        mod = importlib.import_module("app.services.chatbot.pv2.report_measures")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.chatbot.pv2.report_measures":
            return []  # PV2-REPORTS not merged yet
        raise
    # Not `_clean`: PV2-REPORTS' lines use `|` themselves; only line breaks are folded.
    return [" ".join(str(line).replace("\n", " ").split()) for line in mod.index_lines()]


def _teams() -> list[str]:
    from app.services.chatbot.lanes.escalation import ESCALATION_TEAMS

    return [f"{t} = {t.replace('_', ' ')}" for t in sorted(ESCALATION_TEAMS)]


# --------------------------------------------------------------------------- aliases


def _precedence(db: Session) -> dict[tuple[str, str], str]:
    try:
        with db.begin_nested():
            rows = db.execute(
                text("select kind, alias_norm, winner_key from chatbot_alias_precedence")
            ).all()
    except Exception:  # noqa: BLE001 - a copy before pv2_0001 has no precedence rows
        return {}
    return {(r[0], norm_alias(r[1])): r[2] for r in rows}


def _settle_aliases(
    kind: str, rows: list[tuple[str, str, list[str]]], precedence: dict[tuple[str, str], str]
) -> tuple[dict[str, list[str]], list[tuple[str, str, tuple[str, ...]]]]:
    """Per key, the aliases it may print; plus the open clashes (kind, alias, keys)."""
    owners: dict[str, set[str]] = defaultdict(set)
    for key, label, aliases in rows:
        for a in aliases:
            owners[norm_alias(a)].add(key)
    keep: dict[str, list[str]] = {}
    clashes: list[tuple[str, str, tuple[str, ...]]] = []
    seen_clash: set[str] = set()
    for key, label, aliases in rows:
        kept = []
        for a in aliases:
            n = norm_alias(a)
            holders = owners[n]
            if len(holders) == 1:
                kept.append(a)
                continue
            winner = precedence.get((kind, n))
            if winner in holders:
                if winner == key:
                    kept.append(a)
            elif n not in seen_clash:
                seen_clash.add(n)
                clashes.append((kind, n, tuple(sorted(holders))))
        keep[key] = kept
    return keep, clashes


def _keyed_section(kind, rows, precedence, extra=None):
    keep, clashes = _settle_aliases(kind, rows, precedence)
    lines = []
    for key, label, _aliases in rows:
        parts = [f"{key} = {label}"]
        if extra and extra.get(key):
            parts.append(extra[key])
        if keep.get(key):
            parts.append("aka " + ", ".join(keep[key]))
        lines.append(" | ".join(parts))
    return lines, clashes


def _build(db: Session) -> tuple[list[str], list[tuple[str, str, tuple[str, ...]]]]:
    precedence = _precedence(db)
    clashes: list = []
    lines: list[str] = [S_DOMAINS, *_domains(db), "", S_KINDS, *_kinds(db), ""]

    brand_lines, c = _keyed_section("brand", _brands(db), precedence)
    clashes += c
    lines += [S_BRANDS, *brand_lines, ""]

    att_lines, c = _keyed_section("attachment_type", _attachment_types(db), precedence)
    clashes += c
    lines += [S_ATTACH, *att_lines, ""]

    class_rows, by_class = _classes(db)
    extra = {k: "types: " + ", ".join(v) for k, v in by_class.items() if k != UNCLASSIFIED}
    class_lines, c = _keyed_section("product_class", class_rows, precedence, extra)
    clashes += c
    if by_class.get(UNCLASSIFIED):
        class_lines.append(
            f"{UNCLASSIFIED} = no class on file | types: " + ", ".join(by_class[UNCLASSIFIED])
        )
    lines += [S_CLASSES, *class_lines, ""]

    spec_rows, spec_extra = _spec_attributes(db)
    spec_lines, c = _keyed_section("spec_attribute", spec_rows, precedence, spec_extra)
    clashes += c
    lines += [S_SPECS, *spec_lines, ""]
    lines += [S_REPORTS, *_report_lines(), ""]
    lines += [S_TEAMS, *_teams()]
    return lines, clashes


def render_index(db: Session) -> tuple[str, str]:
    """(index text, sha1 of it). Deterministic for unchanged data."""
    lines, _ = _build(db)
    body = "\n".join(lines) + "\n"
    return body, hashlib.sha1(body.encode("utf-8")).hexdigest()


def alias_clashes(db: Session) -> list[tuple[str, str, tuple[str, ...]]]:
    """Open clashes: (kind, normalised alias, the keys claiming it). Empty = publishable."""
    return _build(db)[1]


# --------------------------------------------------------------------------- parse back


def index_of(template: str) -> str:
    """The index part of a published template (from the `## DOMAINS` line on)."""
    lines = template.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == S_DOMAINS:
            return "".join(lines[i:])
    return ""


def section_lines(template: str, header: str) -> list[str]:
    body = index_of(template)
    out: list[str] = []
    inside = False
    for line in body.splitlines():
        if line in SECTIONS:
            inside = line == header
            continue
        if inside and line.strip():
            out.append(line)
    return out


def _row_key(line: str) -> str | None:
    head = line.split(" = ", 1)[0].strip()
    return head or None


def _row_part(line: str, prefix: str) -> list[str]:
    for part in line.split(" | ")[1:]:
        if part.startswith(prefix):
            return [v.strip() for v in part[len(prefix):].split(",") if v.strip()]
    return []


def parse_index_keys(template: str) -> IndexKeys:
    """The keys a pinned index lists. Reads only the index part, so a head line that
    happens to start with `## ` cannot add keys."""

    def keys(header):
        return [k for k in (_row_key(line) for line in section_lines(template, header)) if k]

    class_lines = section_lines(template, S_CLASSES)
    classes = {k for k in (_row_key(line) for line in class_lines) if k and k != UNCLASSIFIED}
    types = {t for line in class_lines for t in _row_part(line, "types: ")}
    spec_lines = section_lines(template, S_SPECS)
    spec_values = {}
    for line in spec_lines:
        vals = _row_part(line, "values: ")
        if vals:
            spec_values[_row_key(line)] = frozenset(vals)
    report = _report_keys(section_lines(template, S_REPORTS))
    brands = frozenset(keys(S_BRANDS))
    return IndexKeys(
        domains=frozenset(keys(S_DOMAINS)),
        entity_kinds=frozenset(keys(S_KINDS)),
        entity_keys={
            "brand": brands,
            "product_class": frozenset(classes),
            "product_type": frozenset(types),
        },
        attachment_types=frozenset(keys(S_ATTACH)),
        spec_attributes=frozenset(keys(S_SPECS)),
        spec_values=spec_values,
        report_datasets=frozenset(report["dataset"]),
        report_measures=frozenset(report["measure"]),
        report_dimensions=frozenset(report["dimension"]),
        report_bases=frozenset(report["basis"]),
        teams=frozenset(keys(S_TEAMS)),
    )


def _items(text_: str) -> list[str]:
    """`a (Label), b (Label)` or `a, b` -> [a, b]."""
    return [i.split(" (", 1)[0].strip() for i in text_.split(",") if i.split(" (", 1)[0].strip()]


def _report_keys(lines: list[str]) -> dict[str, set[str]]:
    """REPORTS rows, in either shape PV2-REPORTS prints:

    - `dataset <key> = <label> | measures: a (A), b (B)`, then `basis: x, y`,
      `group_by: c, d`, `filters: e, f`, and one `not available yet ...: dimension g, h; ...`
      line whose dimension keys are still accepted (code answers "not available yet");
    - or one row per key: `<dataset|measure|dimension|basis> <key> = <label>`.
    """
    report: dict[str, set[str]] = defaultdict(set)
    for line in lines:
        head, _, rest = line.partition(" = ")
        words = head.split()
        if words and words[0] == "dataset" and len(words) == 2:
            report["dataset"].add(words[1])
            for part in rest.split(" | ")[1:]:
                if part.startswith("measures:"):
                    report["measure"].update(_items(part[len("measures:"):]))
            continue
        if rest and len(words) == 2 and words[0] in ("measure", "dimension", "basis"):
            report[words[0]].add(words[1])
            continue
        label, colon, values = line.partition(":")
        label = label.strip()
        if not colon:
            continue
        if label == "basis":
            report["basis"].update(_items(values))
        elif label in ("group_by", "filters"):
            report["dimension"].update(_items(values))
        elif label.startswith("not available yet"):
            first = values.split(";", 1)[0].strip()
            if first.startswith("dimension "):
                report["dimension"].update(_items(first[len("dimension "):]))
    return report


# --------------------------------------------------------------------------- publish


class PublishRefused(ValueError):
    pass


def publish_v2(
    db: Session,
    head_text: str,
    *,
    label: str | None = PUBLISH_LABEL,
    commit_message: str | None = None,
    user_id: str | None = None,
) -> str:
    """Write an immutable `ai_prompt_versions` row = head + "\\n\\n" + INDEX and point
    `label` at it (default `staging`; `production` is refused tonight). Returns the
    version id. Flushes; the caller commits."""
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    if "{{" in head_text:
        raise PublishRefused("the v2 head takes no variables ({{...}} found)")
    if len(head_text) > HEAD_CAP_CHARS:
        raise PublishRefused(f"the v2 head is {len(head_text)} chars, cap {HEAD_CAP_CHARS}")
    if any(line.rstrip() in SECTIONS for line in head_text.splitlines()):
        raise PublishRefused("the v2 head may not contain an index section header")
    if label == "production":
        raise PublishRefused("no production label for parser v2 (contract section 8)")
    lines, clashes = _build(db)
    if clashes:
        named = "; ".join(f"{kind} alias '{a}' on {', '.join(keys)}" for kind, a, keys in clashes)
        raise PublishRefused(f"alias clash, set a precedence first: {named}")
    body = "\n".join(lines) + "\n"
    if len(body) > INDEX_CAP_CHARS:
        raise PublishRefused(f"the index is {len(body)} chars, cap {INDEX_CAP_CHARS}")
    sha = hashlib.sha1(body.encode("utf-8")).hexdigest()
    next_version = int(
        db.query(func.coalesce(func.max(AIPromptVersion.version), 0))
        .filter(AIPromptVersion.name == PROMPT_KEY_V2)
        .scalar()
        or 0
    ) + 1
    row = AIPromptVersion(
        name=PROMPT_KEY_V2,
        version=next_version,
        type="text",
        template=head_text.rstrip("\n") + "\n\n" + body,
        variables=[],
        config_json={
            "index_sha": sha,
            "head_sha": hashlib.sha1(head_text.encode("utf-8")).hexdigest(),
        },
        commit_message=commit_message or f"parser v2 publish, index {sha[:8]}",
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    if label:
        existing = (
            db.query(AIPromptLabel)
            .filter(AIPromptLabel.name == PROMPT_KEY_V2, AIPromptLabel.label == label)
            .first()
        )
        if existing is None:
            db.add(AIPromptLabel(name=PROMPT_KEY_V2, label=label, version_id=row.id, updated_by=user_id))
        else:
            existing.version_id = row.id
            existing.updated_by = user_id
        db.flush()
        from app.services import ai_prompt_registry

        ai_prompt_registry.bust_cache(PROMPT_KEY_V2)
    return str(row.id)
