"""Live parser probe for the crew's r5/r6 live-check turns (NOT a test; never run in CI).

Calls `pv2.call.parse_v2` on the staging prompt for each row of
`tests/chatbot/pv2/probe/livecheck_probe.jsonl` (its exact MEMORY block and message) and compares
the parse with `expect_parse`. Costs one model call per row per repeat.

    venv/bin/python scripts/pv2_livecheck_probe.py                          # every row, once
    venv/bin/python scripts/pv2_livecheck_probe.py --regression -n 3        # the 4 r6 regression rows, 3x
    venv/bin/python scripts/pv2_livecheck_probe.py --case live-S6-8-t2 --version-id <uuid>

Uses DATABASE_URL / the provider key from the backend .env, like the app. Exit code 1 when any row fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Run as `venv/bin/python scripts/pv2_livecheck_probe.py` from the backend dir: no PYTHONPATH needed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "chatbot" / "pv2" / "probe" / "livecheck_probe.jsonl"


DOC_KINDS = {"order", "delivery_order", "customer_order", "sales_order"}


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _number(entity_like) -> str | None:
    """The document number, wherever the parser put it (phrase or doc_number)."""
    return entity_like.get("doc_number") or entity_like.get("phrase")


def _entity_failures(i: int, want: dict, e) -> list[str]:
    """Outcome fields only. A document number may sit in `phrase` or `doc_number`, and the kinds
    order / delivery_order / customer_order / sales_order are interchangeable when the number
    matches (an unnamed "order" probes every visible kind)."""
    have = {"kind": e.kind, "phrase": e.phrase, "doc_number": e.doc_number,
            "quantity": e.quantity.number if e.quantity else None, "refers_to_held": e.refers_to_held}
    bad = []
    wants_doc = want.get("kind") in DOC_KINDS and _number(want)
    if wants_doc:
        if have["kind"] not in DOC_KINDS or _number(have) != _number(want):
            bad.append(f"entity {i} document {have['kind']}:{_number(have)!r} vs {want.get('kind')}:{_number(want)!r}")
        rest = {k: v for k, v in want.items() if k in ("quantity", "refers_to_held")}
    else:
        rest = {k: v for k, v in want.items() if k in ("kind", "phrase", "doc_number", "quantity", "refers_to_held")}
    for key, value in rest.items():
        if have.get(key) != value:
            bad.append(f"entity {i} {key} {have.get(key)!r} != {value!r}")
    return bad


def _ask_failures(want: dict, got) -> list[str]:
    """The act stays strict (it decides the route); domain is ignored on an answer to a question."""
    bad = []
    if "act" in want and got.act not in _as_list(want["act"]):
        bad.append(f"act {got.act!r} not in {_as_list(want['act'])}")
    answering = got.act == "answers_bot_question" or "answers_bot_question" in _as_list(want.get("act"))
    if "domain" in want and not answering and got.domain != want["domain"]:
        bad.append(f"domain {got.domain!r} != {want['domain']!r}")
    if "portal_form" in want and got.portal_form not in _as_list(want["portal_form"]):
        bad.append(f"portal_form {got.portal_form!r} not in {_as_list(want['portal_form'])!r}")
    if "switch_back_to" in want:
        ok = bool(got.switch_back_to) if want["switch_back_to"] == "*" else got.switch_back_to == want["switch_back_to"]
        if not ok:
            bad.append(f"switch_back_to {got.switch_back_to!r} vs {want['switch_back_to']!r}")
    if "entities" in want:
        if len(got.entities) != len(want["entities"]):
            bad.append(f"{len(got.entities)} entities, wanted {len(want['entities'])}")
        for i, (w, e) in enumerate(zip(want["entities"], got.entities)):
            bad += _entity_failures(i, w, e)
    return bad


def check(expect: dict, output) -> list[str]:
    if expect.get("escalation_requested"):
        asked = bool(output.escalation and output.escalation.requested)
        return [] if asked else ["escalation.requested is not set"]
    wanted = expect["asks"]
    # `asks_min`: the listed asks must lead; the model may add the lookup the switch back implies.
    if len(output.asks) < len(wanted) or (not expect.get("asks_min") and len(output.asks) != len(wanted)):
        return [f"{len(output.asks)} asks, wanted {len(wanted)}"]
    bad: list[str] = []
    for i, (w, g) in enumerate(zip(wanted, output.asks)):
        bad += [f"ask {i}: {m}" for m in _ask_failures(w, g)]
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", help="row case name (repeatable)")
    ap.add_argument("--regression", action="store_true", help="only the r6 regression rows")
    ap.add_argument("-n", type=int, default=1, help="repeats per row")
    ap.add_argument("--version-id", default=None, help="pin a prompt version instead of staging")
    args = ap.parse_args()

    from app.database import SessionLocal
    from app.services.chatbot.pv2 import call

    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.case:
        rows = [r for r in rows if r["case"] in args.case]
    if args.regression:
        rows = [r for r in rows if r.get("regression")]
    db = SessionLocal()
    try:
        config = call.resolve_v2_config(db, override_version_id=args.version_id)
    finally:
        db.close()
    print(f"prompt_version {config.prompt_version} model {config.model}; {len(rows)} rows x {args.n}")
    passed = total = 0
    for row in rows:
        ok = 0
        for n in range(args.n):
            total += 1
            try:
                output = call.parse_v2(config, history=[], memory_block=row["memory_block"], text=row["text"]).output
                failures = check(row["expect_parse"], output)
            except Exception as exc:  # noqa: BLE001 - a probe reports, it does not crash
                failures = [f"ERROR {exc}"]
            ok += not failures
            passed += not failures
            summary = [(a.act, a.domain, a.switch_back_to, a.portal_form,
                        [(e.kind, e.phrase, e.doc_number, e.quantity.number if e.quantity else None) for e in a.entities])
                       for a in output.asks] if not failures or not str(failures[0]).startswith("ERROR") else []
            print(("PASS " if not failures else "FAIL ") + f"{row['case']} #{n} {summary} {failures}")
        print(f"  -> {row['case']}: {ok}/{args.n}")
    print(f"TOTAL {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
