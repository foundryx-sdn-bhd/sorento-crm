"""A parser prompt version IDENTICAL in rendered output to an existing one, with registry
variables wherever that version hard-codes a registry list EXACTLY (owner hand test #1405,
item 3). Every list whose registry differs today stays literal and is reported.

Dry run by default: prints the report and the identity check, writes nothing.
`--save` inserts the result as ONE new UNLABELLED version, only if its rendered output is
byte-identical to the source; it never labels, publishes or stages.

    venv/bin/python -m scripts.prompt_dynamic_identical_version --from-version 53
    venv/bin/python -m scripts.prompt_dynamic_identical_version --from-version 53 --save
    venv/bin/python -m scripts.prompt_dynamic_identical_version --verify 55

    venv/bin/python -m scripts.prompt_dynamic_identical_version --verify 57 --against 56

`--verify N` renders version N the way a turn does and compares it with the owner's
production file (`alembic/data/chatbot_semantic_parser.prod-20261001.txt`), printing the
first differing line when they part. `--against M` compares with version M rendered the
same way instead (owner Q-A = (a): the rebuild of his edited plain-text version is checked
against that version, so his edits are not differences). Read-only. The account block is
added to the reference side only, so the version under test must carry it itself; swapping the
two numbers therefore reports a difference at the block.

    venv/bin/python -m scripts.prompt_dynamic_identical_version --owner-edits-from 54 --save

`--owner-edits-from N` (owner, 3 Oct 2026: Q-A (b)) applies the owner's 3 approved text edits
(PR #1405 crew-ask of 2 Oct) to version N and, with `--save`, inserts the result as ONE new
UNLABELLED plain-text version. Each edit is a literal old -> new pair that must occur exactly
once; the line diff against version N is proven to be exactly those 3 lines before anything
is saved. Rebuild from the saved version with `--from-version`, then `--verify ... --against`.

Omit --from-version to start from the `production` version.

Every rebuilt version also carries ACCOUNT_LEDGER_ADDENDUM (#1432, the `account` entity
key) just before the policy blocks, unless the source already has it; "identical" and
`--verify` compare against the source plus that block.

`--verify` also fails when any `{{...}}` survives the render, because no placeholder may reach the parser.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys

PROMPT_NAME = "chatbot_semantic_parser"


def build(db, *, from_version: int | None = None, save: bool = False) -> dict:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services import chatbot_prompt_vars as pv
    from app.services.ai_prompt_registry import _substitute, extract_tokens

    query = db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME)
    if from_version is None:
        source = (
            query.join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
            .filter(AIPromptLabel.label == "production")
            .one()
        )
    else:
        source = query.filter(AIPromptVersion.version == from_version).one()

    # Render the SOURCE the way a turn does too: a source that already uses variables
    # (a wording layer) is compared on its rendered text.
    def rendered(template: str) -> str:
        values = {n: pv.render_value(db, n) for n in extract_tokens(template) & set(pv.VARIABLE_NAMES)}
        return _substitute(template, values)

    # #1432 ACCOUNT-LEDGER merged first: the version carries its `account` block, so the
    # output must equal the source plus that block (a no-op once the source has it).
    before = pv.with_account_block(rendered(source.template))
    template, report = pv.identical_wording_layer(source.template, db)
    template = pv.with_account_block(template)
    after = rendered(template)
    result = {
        "from_version": source.version,
        "identical": before == after,
        "diff": "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), n=1)),
        "report": report,
        "template": template,
        "saved_version": None,
    }
    if not save:
        return result
    if not result["identical"]:
        raise SystemExit("refused: the rendered output differs from the source; nothing saved")
    existing = query.filter(AIPromptVersion.template == template).order_by(AIPromptVersion.version.desc()).first()
    if existing is not None:
        result["saved_version"] = existing.version
        return result
    top = max((v.version for v in query.all()), default=0)
    replaced = sorted({r["variable"] for r in report if r["action"] == "replaced"})
    row = AIPromptVersion(
        name=PROMPT_NAME,
        version=top + 1,
        type="text",
        template=template,
        variables=list(source.variables or []),
        config_json={"identical_to_version": source.version, "identical_report": report},
        commit_message=(
            f"Same rendered prompt as v{source.version}, plus the ACCOUNT-LEDGER block before the "
            f"policy blocks, with registry variables where its "
            f"lists match the registries exactly ({', '.join(replaced) or 'none'}). "
            "Unlabelled: promote only after review."
        ),
    )
    db.add(row)
    db.flush()
    result["saved_version"] = row.version
    return result


#: The owner's 3 approved edits (PR #1405 crew-ask of 2 Oct 2026, comment 5944690012):
#: (label, old, new), each `old` found exactly once in the source.
_SALES_LINE = (
    'Domain sales ("sales"): intents check_sales. Switch words: sales, sales report, top selling, sales analysis, '
    "best selling. customer narrows must_narrow_one; order narrows narrow_to_code; product narrows list_all. "
    "Takes a date window. Escalates to customer_service."
)
_PURCHASE_COST_LINE = (
    'Domain purchase_cost ("last purchase cost"): intents check_po_cost. Switch words: (none). '
    "product narrows narrow_by_type. Escalates to purchasing."
)
OWNER_EDITS: tuple[tuple[str, str, str], ...] = (
    (
        "line 82 domain_hint",
        "| ideate | purchase_cost | null\n",
        "| ideate | purchase_order | purchase_cost | sales | null\n",
    ),
    (
        "line 401 entity hint",
        '|category|brand|attachment_type", "canonical_code"',
        '|category|brand|attachment_type|specification", "canonical_code"',
    ),
    ("policy block sales line", f"\n{_PURCHASE_COST_LINE}\n", f"\n{_PURCHASE_COST_LINE}\n{_SALES_LINE}\n"),
)

#: The text each edit ADDS, by label: a source that already has it is refused, not doubled.
OWNER_EDIT_MARKERS: dict[str, str] = {
    "line 82 domain_hint": "| purchase_order | purchase_cost | sales | null",
    "line 401 entity hint": "|attachment_type|specification",
    "policy block sales line": _SALES_LINE,
}


def apply_owner_edits(source: str) -> tuple[str, list[dict]]:
    """`source` with the owner's 3 approved edits, and what changed. Raises ValueError when an
    edit's text is not found exactly once, or when the result differs from the source in
    anything but those 3 lines (never a guess)."""
    text, changes = source, []
    for label, old, new in OWNER_EDITS:
        marker = OWNER_EDIT_MARKERS.get(label)
        if marker is not None and marker in source:
            raise ValueError(f"{label}: the source already carries this edit; nothing written")
    for label, old, new in OWNER_EDITS:
        if text.count(old) != 1:
            raise ValueError(f"{label}: the text to edit was not found exactly once ({text.count(old)} times)")
        line = text[: text.index(old)].count("\n") + 1 + (1 if old.startswith("\n") else 0)
        text = text.replace(old, new, 1)
        # An insert names the line it follows; a replacement names its own line.
        where = f"after line {line}" if new.count("\n") > old.count("\n") else f"line {line}"
        changes.append({"edit": label, "line": line, "where": where})
    a, b = source.split("\n"), text.split("\n")
    ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
    shape = [(t, i2 - i1, j2 - j1) for t, i1, i2, j1, j2 in ops]
    if shape != [("replace", 1, 1), ("replace", 1, 1), ("insert", 0, 1)]:
        raise ValueError(f"the edited text differs from the source in more than the 3 approved lines: {shape}")
    return text, changes


def owner_edits(db, *, from_version: int, save: bool = False) -> dict:
    """The owner's current version `from_version` with his 3 approved edits; `save` inserts it
    as one new unlabelled plain-text version (idempotent: an identical template is reused)."""
    from app.models.ai_prompt import AIPromptVersion

    query = db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME)
    source = query.filter(AIPromptVersion.version == from_version).one()
    template, changes = apply_owner_edits(source.template)
    result = {
        "from_version": source.version,
        "changes": changes,
        "diff_lines": len(changes),
        "diff": "".join(
            difflib.unified_diff(source.template.splitlines(True), template.splitlines(True), n=0)
        ),
        "saved_version": None,
    }
    if not save:
        return result
    existing = query.filter(AIPromptVersion.template == template).order_by(AIPromptVersion.version.desc()).first()
    if existing is not None:
        result["saved_version"] = existing.version
        return result
    top = max((v.version for v in query.all()), default=0)
    row = AIPromptVersion(
        name=PROMPT_NAME,
        version=top + 1,
        type="text",
        template=template,
        variables=list(source.variables or []),
        config_json={"owner_edits_from_version": source.version, "owner_edits": changes},
        commit_message=(
            f"Owner's 3 approved text edits on v{source.version} (Q-A b, 3 Oct): domain_hint adds "
            "purchase_order and sales, the entity hint adds specification, the policy block adds the "
            "sales line. Unlabelled."
        ),
    )
    db.add(row)
    db.flush()
    result["saved_version"] = row.version
    return result


def verify(db, version: int, snapshot=None, *, against_version: int | None = None) -> dict:
    """Render version `version` (a fixed date for `{{current_date}}`) and compare it with the
    owner's file, or with version `against_version`, rendered with the same date."""
    from app.models.ai_prompt import AIPromptVersion
    from app.services import ai_prompt_registry

    def rendered(v: int) -> str:
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.version == v).one()
        out, _ = ai_prompt_registry.render(db, PROMPT_NAME, current_date="D", override_version_id=row.id)
        return out

    from app.services.chatbot_prompt_vars import with_account_block

    out = rendered(version)
    # The reference plus the account block (#1432), which every rebuilt version carries.
    if against_version is not None:
        want, against = with_account_block(rendered(against_version)), f"v{against_version}"
    else:
        source = snapshot.read_text(encoding="utf-8").replace("{{current_date}}", "D")
        want, against = with_account_block(source), snapshot.name
    leftover_tokens = sorted({"{{" + m.group(1) + "}}" for m in re.finditer(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", out)})
    if out == want and not leftover_tokens:
        return {"version": version, "against": against, "equal": True, "first_difference": None,
                "leftover_tokens": leftover_tokens, "chars": (len(out), len(want))}
    a, b = out.splitlines(), want.splitlines()
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return {
        "version": version,
        "against": against,
        "equal": False,
        "first_difference": None if out == want else {
            "line": i + 1,
            "rendered": a[i] if i < len(a) else "<end>",
            "file": b[i] if i < len(b) else "<end>",
        },
        "leftover_tokens": leftover_tokens,
        "chars": (len(out), len(want)),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-version", type=int, default=None)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--verify", type=int, default=None, metavar="N")
    parser.add_argument("--against", type=int, default=None, metavar="M")
    parser.add_argument("--owner-edits-from", type=int, default=None, metavar="N")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()

    import pathlib

    import app.main  # noqa: F401  registers every model
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        if args.owner_edits_from is not None:
            try:
                r = owner_edits(db, from_version=args.owner_edits_from, save=args.save)
            except ValueError as exc:
                print(f"# refused, nothing written: {exc}")
                return 1
            if args.save:
                db.commit()
            print(f"# source v{r['from_version']}; changed lines: {r['diff_lines']} (the 3 approved edits, nothing else)")
            for c in r["changes"]:
                print(f"- {c['edit']} ({c['where']})")
            print(r["diff"])
            if args.save:
                print(f"# saved as v{r['saved_version']} (unlabelled, plain text)")
            return 0
        if args.verify is not None:
            snapshot = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"
            v = verify(db, args.verify, snapshot, against_version=args.against)
            print(f"# v{v['version']} rendered == {v['against']}: {v['equal']} ({v['chars'][0]} vs {v['chars'][1]} chars)")
            if v["first_difference"]:
                d = v["first_difference"]
                print(f"    first difference at line {d['line']}:\n    rendered:  {d['rendered']!r}\n    reference: {d['file']!r}")
            if v["leftover_tokens"]:
                print(f"# leftover placeholders in v{v['version']}: {v['leftover_tokens']}")
                return 1
            return 0 if v["equal"] else 1
        result = build(db, from_version=args.from_version, save=args.save)
        if args.save:
            db.commit()
        print(f"# source v{result['from_version']}; rendered output identical: {result['identical']}")
        if result["diff"]:
            print(result["diff"])
        for row in result["report"]:
            line = f"line {row['line']}" if row.get("line") else "not found"
            print(f"- {{{{{row['variable']}}}}} ({line}): {row['action']}")
            if row["action"] == "kept literal":
                if row.get("order_or_format_only"):
                    print("    same items, different order or format")
                if row.get("only_in_text"):
                    print(f"    only in the prompt text: {json.dumps(row['only_in_text'], ensure_ascii=False)}")
                if row.get("only_in_registry"):
                    print(f"    only in the registry:    {json.dumps(row['only_in_registry'], ensure_ascii=False)}")
                d = row.get("first_difference")
                if d:
                    print(f"    first difference, item {d['item']}: text {d['text']!r} | registry {d['registry']!r}")
        if args.save:
            print(f"# saved as v{result['saved_version']} (unlabelled)")
        return 0 if result["identical"] else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
