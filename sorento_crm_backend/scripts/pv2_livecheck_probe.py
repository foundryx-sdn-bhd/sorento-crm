"""Live parser probe for the crew's r5/r6 live-check turns (NOT a test; never run in CI).

Calls `pv2.call.parse_v2` on the staging prompt for each row of
`tests/chatbot/pv2/fixtures/livecheck_probe.jsonl` (its exact MEMORY block and message) and compares
the parse with `expect_parse`. Costs one model call per row per repeat.

    PYTHONPATH=. python scripts/pv2_livecheck_probe.py                      # every row, once
    PYTHONPATH=. python scripts/pv2_livecheck_probe.py --regression -n 3    # the 4 r6 regression rows, 3x
    PYTHONPATH=. python scripts/pv2_livecheck_probe.py --case live-S6-8-t2 --version-id <uuid>

Uses DATABASE_URL / the provider key from the backend .env, like the app. Exit code 1 when any row fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "chatbot" / "pv2" / "fixtures" / "livecheck_probe.jsonl"


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _ask_failures(want: dict, got) -> list[str]:
    bad = []
    if "act" in want and got.act not in _as_list(want["act"]):
        bad.append(f"act {got.act!r} not in {_as_list(want['act'])}")
    if "domain" in want and got.domain != want["domain"]:
        bad.append(f"domain {got.domain!r} != {want['domain']!r}")
    if "portal_form" in want and got.portal_form != want["portal_form"]:
        bad.append(f"portal_form {got.portal_form!r} != {want['portal_form']!r}")
    if "switch_back_to" in want:
        ok = bool(got.switch_back_to) if want["switch_back_to"] == "*" else got.switch_back_to == want["switch_back_to"]
        if not ok:
            bad.append(f"switch_back_to {got.switch_back_to!r} vs {want['switch_back_to']!r}")
    if "answered" in want:
        said = bool(got.answer and (got.answer.option_key or got.answer.free_value))
        if said != want["answered"]:
            bad.append(f"answered {said} != {want['answered']}")
    if "entities" in want:
        if len(got.entities) != len(want["entities"]):
            bad.append(f"{len(got.entities)} entities, wanted {len(want['entities'])}")
        for i, (w, e) in enumerate(zip(want["entities"], got.entities)):
            have = {"kind": e.kind, "phrase": e.phrase, "doc_number": e.doc_number,
                    "quantity": e.quantity.number if e.quantity else None, "refers_to_held": e.refers_to_held}
            for key, value in w.items():
                if have.get(key) != value:
                    bad.append(f"entity {i} {key} {have.get(key)!r} != {value!r}")
    return bad


def check(expect: dict, output) -> list[str]:
    wanted = expect["asks"]
    # `asks_min`: the listed asks must lead; the model may add more (a switch back plus the lookup it implies).
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
