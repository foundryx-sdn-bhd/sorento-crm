"""Parser gold runner (CHAT-CODE-LOOKUP R2-1): one gold message x N repeats through the semantic parser only.

usage (cwd = the backend dir of the checkout under test):
  python -m scripts.chatbot_eval.parser_gold live --gold F --out F --max-usd X --require-key-last4 XXXX \
      --prompt-version-id ID --repeats N
      (calls ONLY the parser, on the pinned prompt version, with the user turn the engine builds and the
      candidate block when the version carries CLOSED LISTS; every DB session is rolled back; spend is capped
      by --max-usd and only a provider key ending with --require-key-last4 may spend)

Imports only the stdlib at module level and `app.*` inside functions, like replay.py. Output rows carry ids,
the pass flag, the reasons, the spend and the parse's own routing fields; each row's entities carry `raw`,
a span of the gold message (synthetic and committed, so it holds no real customer text).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
from typing import Any

EXPECT_KEYS = frozenset({"status", "not_status", "domain_hint", "intent_hint", "product", "no_attribute", "customer", "canonical", "attributes_only"})


def load_gold(path: Any) -> list[dict[str, Any]]:
    """The gold rows `{id, message, expect}`; ValueError on bad JSON, a blank message, a repeated id or an unknown expect key."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {n}: not json") from exc
            if not isinstance(row, dict) or not str(row.get("id") or "").strip():
                raise ValueError(f"line {n}: no id")
            if row["id"] in seen:
                raise ValueError(f"line {n}: repeated id {row['id']}")
            if not str(row.get("message") or "").strip():
                raise ValueError(f"line {n}: blank message")
            expect = row.get("expect", {})
            if not isinstance(expect, dict) or set(expect) - EXPECT_KEYS:
                raise ValueError(f"line {n}: unknown expect key")
            seen.add(row["id"])
            rows.append({"id": row["id"], "message": row["message"], "expect": expect})
    return rows


def _fold(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").casefold())


def grade_parse(verdict: dict[str, Any], expect: dict[str, Any]) -> tuple[bool, list[str]]:
    """(passed, reasons) for one parse against one expectation; every failed check is reported."""
    reasons: list[str] = []
    verdict = verdict if isinstance(verdict, dict) else {}
    entities = [e for e in verdict.get("entities") or [] if isinstance(e, dict)]
    if "status" in expect and verdict.get("status") != expect["status"]:
        reasons.append(f"status {verdict.get('status')!r} != {expect['status']!r}")
    if "not_status" in expect and verdict.get("status") in expect["not_status"]:
        reasons.append(f"status {verdict.get('status')!r} is forbidden")
    for key in ("domain_hint", "intent_hint"):
        if key in expect and verdict.get(key) != expect[key]:
            reasons.append(f"{key} {verdict.get(key)!r} != {expect[key]!r}")
    if "product" in expect:
        codes = {_fold(e.get("canonical_code")) for e in entities if e.get("hint") == "product"}
        if _fold(expect["product"]) not in codes:
            reasons.append(f"no product entity {expect['product']!r}")
    if "canonical" in expect:
        picks = [_fold(e.get("canonical_code")) for e in entities if e.get("hint") == "product"]
        if expect["canonical"] is None:
            if any(picks):
                reasons.append("a product entity has a canonical_code")
        elif _fold(expect["canonical"]) not in picks:
            reasons.append(f"no product canonical_code {expect['canonical']!r}")
    if "attributes_only" in expect:
        allowed = {str(a).casefold() for a in expect["attributes_only"] or []}
        extra = sorted({str(a).casefold() for a in verdict.get("requested_attributes") or []} - allowed)
        if extra:
            reasons.append(f"attributes outside the allowed set: {extra}")
    if "no_attribute" in expect:
        asked = {str(a).casefold() for a in verdict.get("requested_attributes") or []}
        if str(expect["no_attribute"]).casefold() in asked:
            reasons.append(f"attribute {expect['no_attribute']!r} requested")
    if "customer" in expect:
        has = any(e.get("hint") == "customer" for e in entities)
        if has != bool(expect["customer"]):
            reasons.append("customer entity " + ("missing" if expect["customer"] else "present"))
    return (not reasons, reasons)


def summarise(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per case id: how many repeats ran and how many passed."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        cell = out.setdefault(str(row["id"]), {"n": 0, "passed": 0})
        cell["n"] += 1
        cell["passed"] += 1 if row.get("passed") else 0
    return out


def _parse_one(message: str, config: Any, factory: Any) -> Any:
    """The parser's emission for one message, with the user turn the engine builds for a turn with no history."""
    from app.models.base import set_company_scope
    from app.services.chatbot import engine, turn_runtime
    from app.services.chatbot.head import parser
    from app.services.chatbot.turn import context as context_mod
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = factory()
    try:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        brands = turn_runtime.active_brands(db)
        promotion_kinds = turn_runtime.promotion_kind_rows(db)
        product_types = turn_runtime.product_type_names(db)
        product_details = turn_runtime.product_detail_values(db)
        lines, _listed, _picks = (
            engine._candidate_lines(db, message, code_lookup=config.code_lookup)
            if config.closed_lists
            else (None, frozenset(), {})
        )
    finally:
        db.close()
    layers = context_mod.ContextLayers(
        level="off", profile_facts=None, summaries=None, earlier_messages=None, previous_response=None,
        current_subject=None, pending_kind=None, pending_options=None, settings_profile_line=None,
        current_message=message, reply_to=None, media_line=None, brands=brands, promotion_kinds=promotion_kinds,
        product_types=product_types, product_details=product_details, candidate_lines=lines,
    )
    user_block, _report = context_mod.assemble(layers)
    return parser.parse(config, user_block)


def run_live(rows: list[dict[str, Any]], *, session_factory: Any, version_id: str, repeats: int, max_usd: float,
             require_key_last4: str, on_row: Any = None) -> list[dict[str, Any]]:
    """Each gold message x `repeats` through the parser alone; stops cleanly at the spend cap or a wrong key."""
    from datetime import date

    from app.services.chatbot.head import parser
    from scripts.chatbot_eval import replay
    from app.services.company_scope import register_company_scope_listeners

    register_company_scope_listeners()  # idempotent; without it set_company_scope filters nothing

    if not replay._valid_last4(require_key_last4):
        raise ValueError("require_key_last4 must be exactly 4 non-blank characters")
    total = replay._new_usage()
    total.update(max_calls=None, max_usd=max_usd, stop=None, require_key_last4=require_key_last4)
    out: list[dict[str, Any]] = []
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.ExitStack() as stack:
            replay._guard_network(stack, [], replay.MODEL_HOSTS)
            usage = replay._new_usage()
            stack.enter_context(replay._metered(usage, total))
            with replay._rolled_back(session_factory) as factory:
                db = factory()
                try:
                    config = parser.resolve_config(db, current_date=date.today().isoformat(), override_version_id=version_id)
                finally:
                    db.close()
                for case in rows:
                    for repeat in range(repeats):
                        before = total["usd"]
                        row: dict[str, Any] = {"id": case["id"], "repeat": repeat}
                        try:
                            verdict = dict(_parse_one(case["message"], config, factory))
                            passed, reasons = grade_parse(verdict, case["expect"])
                            row.update(
                                passed=passed, reasons=reasons,
                                **{k: verdict.get(k) for k in ("status", "domain_hint", "intent_hint", "requested_attributes", "entities")},
                            )
                        except replay._CapStop as stop:
                            total["stop"] = total.get("stop") or stop.reason
                            row.update(passed=False, reasons=[f"stopped: {stop.reason}"])
                        except Exception as exc:  # noqa: BLE001 - the type only, never message text
                            row.update(passed=False, reasons=[f"error: {type(exc).__name__}"])
                        row["usd"] = round(total["usd"] - before, 6)
                        out.append(row)
                        if on_row is not None:
                            on_row(row)
                        if total.get("stop"):
                            return out
    finally:
        logging.disable(previous_disable)
    return out


def _refuse(message: str) -> int:
    print(f"refused: {message}", file=sys.stderr)
    return 2


def _cmd_live(args: argparse.Namespace) -> int:
    if args.max_usd is None or args.max_usd <= 0:
        return _refuse("--max-usd is required and must be positive")
    last4 = args.require_key_last4
    if not isinstance(last4, str) or len(last4) != 4 or any(c.isspace() for c in last4):
        return _refuse("--require-key-last4 is required: exactly 4 non-blank characters")
    if not args.prompt_version_id:
        return _refuse("--prompt-version-id is required (the live run never uses the production label)")
    if args.repeats is None or args.repeats < 1:
        return _refuse("--repeats must be at least 1")
    rows = load_gold(args.gold)
    import app

    if not os.path.abspath(app.__file__).startswith(os.getcwd() + os.sep):
        return _refuse(f"app is imported from {app.__file__}, not from the cwd")
    from app.database import SessionLocal

    with open(args.out, "w") as fh:
        os.chmod(args.out, 0o600)

        def write(row: dict[str, Any]) -> None:
            fh.write(json.dumps(row) + "\n")
            fh.flush()

        results = run_live(
            rows, session_factory=SessionLocal, version_id=args.prompt_version_id, repeats=args.repeats,
            max_usd=args.max_usd, require_key_last4=last4, on_row=write,
        )
    for case_id, cell in summarise(results).items():
        print(f"{case_id} {cell['passed']}/{cell['n']}")
    print(f"total usd {sum(r.get('usd', 0.0) for r in results):.4f}")
    stopped = [r for r in results if any(str(x).startswith("stopped") for x in r.get("reasons", []))]
    return 1 if stopped else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    live = sub.add_parser("live")
    live.add_argument("--gold", required=True)
    live.add_argument("--out", required=True)
    live.add_argument("--max-usd", type=float, default=None)
    live.add_argument("--require-key-last4", default=None)
    live.add_argument("--prompt-version-id", default=None)
    live.add_argument("--repeats", type=int, default=None)
    args = ap.parse_args(argv)
    return _cmd_live(args)


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    sys.exit(main())
