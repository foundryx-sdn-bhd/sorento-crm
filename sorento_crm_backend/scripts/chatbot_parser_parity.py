#!/usr/bin/env python
"""Live parity between the OLD and the S1b SLIM chatbot parser prompt (AC-153, AC-155).

S1b deletes about 40% of the parser's system message. The replay corpus cannot grade that
edit: every replay fixture feeds `_parser_raw`, an emission the OLD prompt produced, so it
proves the post-processor and says nothing about the model. This script is the missing
half. It takes real turns out of the capture corpus, sends each one TWICE through the live
parser (once per prompt version, temperature 0, same model, same `current_date`), and
diffs the declared output keys.

What it reports, per key:

* AGREEMENT, so a silent behaviour change cannot hide inside an aggregate;
* every DISAGREEMENT with the input text that produced it, so the owner can triage each
  one as `improvement` or `regression` rather than being handed a percentage;
* the same diff again AFTER `output_exchange`, which is the answer that actually reaches
  the customer. Several deleted sections were rules the post-processor overwrites, so the
  raw emission is EXPECTED to differ there while the final answer does not. Reading only
  the raw table would call a deliberate deletion a regression.

Input groups (all three run by default):

* `corpus`  - real captures, sampled deterministically from a seed.
* `guards`  - the turns the `regression-guards/` set protects. Those fixture files pin a
  parser emission and the previous reply but carry NO user message, so the guarded
  behaviour (escalate word, named team, company pick, positional pick) is exercised here
  through the REAL captures that do carry one. Named explicitly, never sampled.
* `malay`   - Malay and mixed Malay-English (AC-155). The corpus has no Malay capture at
  all (measured: 249 real captures, zero), so these are real corpus turns with the message
  translated and the REAL previous state kept. That is still a fair parity test, because
  both prompts see the identical input and agreement is what is being measured, but it is
  NOT a claim about ground truth and it is labelled `synthetic-from-corpus` in the output.

Usage:

    venv/bin/python scripts/chatbot_parser_parity.py                 # dry run, lists inputs
    venv/bin/python scripts/chatbot_parser_parity.py --live-llm      # calls the provider
    venv/bin/python scripts/chatbot_parser_parity.py --live-llm --n 50 --json out.json

Costs real tokens: 2 calls per input. Not part of pytest, not part of CI.

--memory-cases (AC-MEM068, plan 8.2)
=====================================

A second, unrelated mode of this same script: `tests/chatbot/fixtures/parser_memory_cases.json`
(30 cases, each an assembled user block plus its structured inputs and an expected verdict
shape) through the REAL parser call, TWICE per case - once with the memory blocks the fixture
carries, once with the SAME inputs rendered at level `off` (memory stripped) - and grades each
against `expected`. No provider call happens unless this flag is given; the shape of the
fixture itself is covered with no network at all by
`tests/chatbot/test_parser_memory_cases_fixture.py`.

    venv/bin/python scripts/chatbot_parser_parity.py --memory-cases
    venv/bin/python scripts/chatbot_parser_parity.py --memory-cases --prompt-version 4
    venv/bin/python scripts/chatbot_parser_parity.py --memory-cases --cases path/to/cases.json

Exits 0 only when at least 27/30 are correct WITH memory and at least 24/30 are WRONG
ablated (the plan's own bars) - a case that is not wrong under ablation does not test memory.
`--prompt-version <n>` pins a specific `chatbot_semantic_parser` registry version by its
integer version number; unset defaults to the NEWEST version ever published to that name,
never the `production` label (a candidate prompt is exactly what this mode exists to grade
before it is promoted).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# The corpus turns the `regression-guards/` set protects, by behaviour. Each is a REAL
# capture carrying both a user message and the previous conversation state.
GUARD_FIXTURES: dict[str, str] = {
    "parser-15025626": "bare escalate word on an open offer (b-hb1 13039258 shape)",
    "parser-15074683": "bare ESCALATE, upper case",
    "parser-15074293": "misspelled 'YES ESCALTE' still an acceptance",
    "parser-15024720": "escalate NAMING a team: the one team_source=explicit case",
    "parser-15111167": "escalate to marketing product team, with a topic",
    "parser-15142072": "'yes, escalate.' with punctuation",
    "parser-15143883": "'yes escalate' the model read as business_query",
    "b56-t4-parser": "bare '1' on a member offer: a positional pick, not a code",
    "parser-15114106": "positional reply qualifying a RESULT item",
    "parser-15125372": "positional reply against a did-you-mean set",
    "parser-15129616": "position 17, well past a short list",
    "parser-15105557": "person_mention plus two product codes plus a finish attribute",
    "parser-15110448": "order quantity: the have-take form",
    "parser-15108480": "order_status delivered",
    "parser-15110339": "order_status outstanding",
    "parser-15121180": "the only broaden/clear turn in the corpus",
    "parser-15120197": "demand_qty plus a real date window",
    "parser-15164413": "resource_attachment, the domain the routing map used to carry",
}

# AC-155. Real corpus turns, message translated into Malay or mixed Malay-English, real
# previous state kept. `source` names the capture the state and the intent come from.
MALAY_INPUTS: list[dict[str, str]] = [
    {"source": "parser-15025803", "message": "CBFAL5570 bila sampai?"},
    {"source": "parser-15030192", "message": "SRTW2000 ada stok tak?"},
    {"source": "parser-15099311", "message": "PS202609-0096 dah hantar ke belum?"},
    {"source": "parser-15073334", "message": "tolong hantar lukisan teknikal SRTUB206"},
    {"source": "parser-15102530", "message": "CWCX604-S-RL masuk bila, ada ETA?"},
    {"source": "parser-15025626", "message": "eskalasi kepada team"},
    {"source": "parser-15107032", "message": "senarai order belum hantar untuk PS202609-0063"},
    {"source": "parser-15105557", "message": "Encik Zhi Yang, CKS806 saiz berapa?"},
]


# --------------------------------------------------------------------------- #
# Corpus loading
# --------------------------------------------------------------------------- #


def corpus_dir() -> Path:
    from tests.chatbot import _corpus

    root = _corpus.corpus_root()
    if root is None:
        sys.exit(
            "no fixture corpus: set CHATBOT_FIXTURES_DIR to "
            "<n8n checkout>/n8n-workflows-init/tests/fixtures"
        )
    return root / "nodes" / "sub-semantic-parser"


def load_captures() -> dict[str, dict]:
    """Every real capture that carries a user message AND the LLM's raw emission."""
    out: dict[str, dict] = {}
    base = corpus_dir()
    for sub in ("output_exchange", "suggest-follow-up"):
        directory = base / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if not data.get("execution"):
                continue
            parent_items = (data.get("ctx") or {}).get("When Executed by Another Workflow") or []
            if not parent_items:
                continue
            parent = parent_items[0].get("json") or {}
            message = parent.get("latest_user_message")
            if not message or not str(message).strip():
                continue
            agent = (data.get("ctx") or {}).get("AI Agent") or []
            raw = (agent[0].get("json") or {}).get("output") if agent else None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = None
            if not isinstance(raw, dict):
                continue
            out.setdefault(
                path.stem,
                {
                    "id": path.stem,
                    "message": str(message),
                    "state": parent.get("previous_conversation_state") or {},
                    "parent": parent,
                    "captured_at": (data.get("source") or {}).get("captured_at") or "",
                    "baseline": raw,
                },
            )
    return out


def build_inputs(captures: dict[str, dict], *, n: int, seed: int, groups: set[str]) -> list[dict]:
    chosen: list[dict] = []
    used: set[str] = set()

    if "guards" in groups:
        for fixture_id, why in GUARD_FIXTURES.items():
            cap = captures.get(fixture_id)
            if cap is None:
                print(f"  warn: guard fixture {fixture_id} absent from this corpus, skipped")
                continue
            chosen.append({**cap, "group": "guards", "why": why})
            used.add(fixture_id)

    if "malay" in groups:
        for row in MALAY_INPUTS:
            cap = captures.get(row["source"])
            if cap is None:
                print(f"  warn: malay source {row['source']} absent from this corpus, skipped")
                continue
            chosen.append(
                {
                    **cap,
                    "id": f"malay-{row['source']}",
                    "message": row["message"],
                    "baseline": None,  # a translated message has no captured emission
                    "group": "malay",
                    "why": "synthetic-from-corpus: real state, message translated (AC-155)",
                }
            )

    if "corpus" in groups and n > 0:
        pool = sorted(k for k in captures if k not in used)
        rng = random.Random(seed)
        for fixture_id in rng.sample(pool, min(n, len(pool))):
            chosen.append({**captures[fixture_id], "group": "corpus", "why": ""})
    return chosen


# --------------------------------------------------------------------------- #
# The two parser calls
# --------------------------------------------------------------------------- #


def resolve_prompts(db, *, current_date: str) -> tuple[str, str, str, str]:
    """(old_text, old_label, new_text, new_label), both through the registry."""
    import sys
    from pathlib import Path

    from app.models.ai_prompt import AIPromptVersion
    from app.services.ai_prompt_registry import PROMPT_KEYS, render
    from app.services.chatbot.head.parser import PROMPT_KEY

    # SEMANTIC_PARSER_PROMPT_SLIM retired from the live module (chatbot turn
    # re-architecture S0, AC-1506) - the historical body this script compares against
    # lives on in alembic/_legacy_prompt_bodies.py, the immutable copy the old
    # migrations that published it also read from.
    _alembic_root = Path(__file__).resolve().parent.parent / "alembic"
    if str(_alembic_root) not in sys.path:
        sys.path.insert(0, str(_alembic_root))
    from _legacy_prompt_bodies import SEMANTIC_PARSER_PROMPT_SLIM_V1

    old_text, old_version = render(db, PROMPT_KEY, current_date=current_date)
    old_label = f"production (v{old_version})" if old_version else "fallback constant"

    row = (
        db.query(AIPromptVersion)
        .filter(
            AIPromptVersion.name == PROMPT_KEY,
            AIPromptVersion.template == SEMANTIC_PARSER_PROMPT_SLIM_V1,
        )
        .first()
    )
    if row is not None:
        new_text, new_version = render(
            db, PROMPT_KEY, override_version_id=row.id, current_date=current_date
        )
        new_label = f"unlabelled (v{new_version})"
    else:
        spec = PROMPT_KEYS[PROMPT_KEY]  # noqa: F841 - kept for the variable list below
        new_text = SEMANTIC_PARSER_PROMPT_SLIM_V1.replace("{{current_date}}", current_date)
        new_label = "module constant (migration 475 not applied on this database)"
    return old_text, old_label, new_text, new_label


def parse_pair(config_old, config_new, user_block: str) -> tuple[dict | None, dict | None, str]:
    from app.services.chatbot.head.parser import ParserError, parse

    err = ""
    try:
        left = parse(config_old, user_block)
    except ParserError as exc:
        left, err = None, f"old: {exc}"
    try:
        right = parse(config_new, user_block)
    except ParserError as exc:
        right = None
        err = (err + " | " if err else "") + f"new: {exc}"
    return left, right, err


def post_process_both(item: dict, left: dict, right: dict) -> tuple[dict | None, dict | None]:
    """The same two emissions through the real post-processor, which is what a customer
    actually gets. Several deleted prompt sections are rules this stage re-imposes."""
    from app.services.chatbot.head.output_exchange import ParserOutputError, post_process

    parent = item["parent"]
    out: list[dict | None] = []
    for raw in (left, right):
        try:
            # `post_process` returns the n8n item WRAPPER; the parsed object is under
            # `output`. Diffing the wrapper would compare two dicts that share no declared
            # key at all and report a flawless 100%, which is exactly the false green this
            # script exists to prevent.
            result = post_process({"output": json.loads(json.dumps(raw))}, {}, parent)
            out.append((result or {}).get("output"))
        except (ParserOutputError, Exception):  # noqa: B014 - any failure is "no answer"
            out.append(None)
    return out[0], out[1]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def norm(value):
    """JSON round trip so `1` and `1.0`, tuples and lists, compare the way the wire does."""
    return json.loads(json.dumps(value, sort_keys=True))


def diff_keys(left: dict, right: dict, keys) -> list[str]:
    return [k for k in keys if norm(left.get(k)) != norm(right.get(k))]


def table(title: str, agree: Counter, total: Counter, keys) -> None:
    print(f"\n{title}")
    print(f"  {'key':<24} {'agree':>7} {'of':>5} {'%':>7}")
    worst = []
    for key in sorted(keys):
        n = total[key]
        a = agree[key]
        pct = 100.0 * a / n if n else 100.0
        print(f"  {key:<24} {a:>7} {n:>5} {pct:>6.1f}%")
        if a != n:
            worst.append(key)
    overall_a = sum(agree.values())
    overall_n = sum(total.values())
    pct = 100.0 * overall_a / overall_n if overall_n else 100.0
    print(f"  {'ALL KEYS':<24} {overall_a:>7} {overall_n:>5} {pct:>6.1f}%")
    if worst:
        print(f"  keys with any disagreement: {', '.join(worst)}")


# --------------------------------------------------------------------------- #
# --memory-cases (AC-MEM068, plan 8.2)
# --------------------------------------------------------------------------- #

DEFAULT_MEMORY_CASES_PATH = BACKEND_ROOT / "tests" / "chatbot" / "fixtures" / "parser_memory_cases.json"
MEMORY_CASES_PASS_WITH = 27
MEMORY_CASES_PASS_ABLATED_WRONG = 24


def load_memory_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        cases = json.load(fh)
    if len(cases) != 30:
        print(f"warn: {path} holds {len(cases)} cases, plan 8.2 expects about 30")
    return cases


def _case_layers(case: dict, *, level: str):
    from app.services.chatbot.turn import context as context_mod

    return context_mod.ContextLayers(
        level=level,
        profile_facts=case["profile_facts"],
        summaries=case["summaries"],
        earlier_messages=case["earlier_messages"],
        previous_response=case["previous_response"],
        current_subject=case["current_subject"],
        pending_kind=None,
        pending_options=None,
        settings_profile_line=None,
        current_message=case["current_message"],
        reply_to=None,
        media_line=None,
    )


def resolve_memory_cases_prompt(db, *, prompt_version: int | None, current_date: str):
    """(`ParserConfig`, resolved version number) for the chosen `chatbot_semantic_parser`
    registry version - a specific one via `--prompt-version`, or (default) the NEWEST
    version ever published to that name, deliberately never the `production` label: a
    candidate prompt not yet promoted is exactly what this mode exists to grade."""
    from app.models.ai_prompt import AIPromptVersion
    from app.services.chatbot.head.parser import PROMPT_KEY, ParserError, resolve_config

    query = db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_KEY)
    if prompt_version is not None:
        row = query.filter(AIPromptVersion.version == prompt_version).first()
        if row is None:
            raise ParserError(f"no {PROMPT_KEY!r} version {prompt_version} in the registry")
    else:
        row = query.order_by(AIPromptVersion.version.desc()).first()
        if row is None:
            raise ParserError(f"no {PROMPT_KEY!r} versions published in the registry at all")
    config = resolve_config(db, current_date=current_date, override_version_id=row.id)
    return config, row.version


def _entity_matches(actual_entities: list, expected_entity: dict) -> bool:
    """One expected entity found in the verdict's own `entities`, by `canonical_code`
    OR `raw` (case-insensitive, either side may carry the value the other names - the
    live model is free to resolve a raw token to its code or leave it raw), and,
    when named, the same `current_message` flag."""
    want_raw = str(expected_entity.get("raw") or "").strip().lower()
    want_code = str(expected_entity.get("canonical_code") or "").strip().lower()
    want_cm = expected_entity.get("current_message")
    for entity in actual_entities or []:
        if not isinstance(entity, dict):
            continue
        got_raw = str(entity.get("raw") or "").strip().lower()
        got_code = str(entity.get("canonical_code") or "").strip().lower()
        got_values = {v for v in (got_raw, got_code) if v}
        want_values = {v for v in (want_raw, want_code) if v}
        if not (got_values & want_values):
            continue
        if want_cm is not None and bool(entity.get("current_message")) != bool(want_cm):
            continue
        return True
    return False


def grade_verdict(verdict: dict | None, expected: dict) -> tuple[bool, list[str]]:
    """`True` plus an empty reason list when every non-null `expected` field is
    satisfied; the caller counts wrong-under-ablation the same way, since a case
    that stays "correct" without memory is not testing memory at all."""
    if verdict is None:
        return False, ["parser call failed"]
    reasons: list[str] = []
    if expected.get("message_type") is not None and verdict.get("message_type") != expected["message_type"]:
        reasons.append(f"message_type: got {verdict.get('message_type')!r}, want {expected['message_type']!r}")
    if expected.get("domain_hint") is not None and verdict.get("domain_hint") != expected["domain_hint"]:
        reasons.append(f"domain_hint: got {verdict.get('domain_hint')!r}, want {expected['domain_hint']!r}")
    # `profile_statements`, the list the parser emits (reviewer pass at d89110c0, S17:
    # the singular key this used to read is in neither the fixture nor the schema, so
    # every stated case passed free). Each wanted statement must be present, by key and,
    # when named, by value.
    for want in expected.get("profile_statements") or []:
        got = [s for s in (verdict.get("profile_statements") or []) if isinstance(s, dict)]
        if not any(
            s.get("key") == want.get("key") and ("value" not in want or s.get("value") == want["value"])
            for s in got
        ):
            reasons.append(f"profile_statements: missing {want}, got {got}")
    expected_entities = expected.get("entities")
    if expected_entities is not None:
        actual_entities = verdict.get("entities") or []
        if expected_entities == []:
            if actual_entities:
                reasons.append(f"entities: expected none, got {actual_entities}")
        else:
            for wanted in expected_entities:
                if not _entity_matches(actual_entities, wanted):
                    reasons.append(f"entities: missing {wanted}")
    return (not reasons), reasons


def counts_for_ablation(case: dict) -> bool:
    """A case the ablation bar counts: one whose answer needs memory. A stated fact
    (`expected.profile_statements`) is read off the current message alone, so it stays
    right with memory ablated and says nothing about memory (S17)."""
    return bool(case.get("needs_memory")) and not (case.get("expected") or {}).get("profile_statements")


def run_memory_cases(args) -> int:
    from app.database import SessionLocal
    from app.services.chatbot.head.parser import ParserError, parse
    from app.services.chatbot.turn import context as context_mod

    cases_path = Path(args.cases) if args.cases else DEFAULT_MEMORY_CASES_PATH
    if not cases_path.is_file():
        print(f"STOP: no fixture at {cases_path}")
        return 2
    cases = load_memory_cases(cases_path)

    os.environ.setdefault("no_proxy", "*")
    current_date = args.current_date or "Friday, 04 September 2026"
    db = SessionLocal()
    try:
        try:
            config, resolved_version = resolve_memory_cases_prompt(
                db, prompt_version=args.prompt_version, current_date=current_date
            )
        except ParserError as exc:
            print(f"STOP: {exc}")
            return 2
    finally:
        db.close()

    print(
        f"chatbot_semantic_parser v{resolved_version}  provider={config.provider} "
        f"model={config.model}  cases={len(cases)}"
    )

    with_memory_correct = 0
    ablated_wrong = 0
    ablation_cases = sum(1 for case in cases if counts_for_ablation(case))
    # The bar keeps its share (24 of 30) over the cases it now counts.
    ablated_wrong_bar = math.ceil(MEMORY_CASES_PASS_ABLATED_WRONG * ablation_cases / 30)
    for index, case in enumerate(cases, 1):
        user_block_with_memory, _report = context_mod.assemble(_case_layers(case, level="full"))
        user_block_ablated, _report_off = context_mod.assemble(_case_layers(case, level="off"))

        try:
            verdict_with = parse(config, user_block_with_memory)
        except ParserError as exc:
            verdict_with, err_with = None, str(exc)
        else:
            err_with = ""
        try:
            verdict_ablated = parse(config, user_block_ablated)
        except ParserError as exc:
            verdict_ablated, err_ablated = None, str(exc)
        else:
            err_ablated = ""

        correct_with, reasons_with = grade_verdict(verdict_with, case["expected"])
        correct_ablated, reasons_ablated = grade_verdict(verdict_ablated, case["expected"])
        if correct_with:
            with_memory_correct += 1
        if not correct_ablated and counts_for_ablation(case):
            ablated_wrong += 1

        with_flag = "OK" if correct_with else "WRONG"
        ablated_flag = "OK" if correct_ablated else "WRONG"
        print(f"[{index}/{len(cases)}] {case['id']:<40} with-memory={with_flag:<5} ablated={ablated_flag}")
        if not correct_with:
            for reason in reasons_with or [err_with]:
                print(f"    with-memory: {reason}")
        if correct_ablated:
            print(f"    ablated STILL CORRECT (does not test memory): {reasons_ablated or err_ablated}")

    print(
        f"\nwith memory: {with_memory_correct}/{len(cases)} correct; "
        f"ablated: {ablated_wrong}/{ablation_cases} wrong"
    )
    passed = with_memory_correct >= MEMORY_CASES_PASS_WITH and ablated_wrong >= ablated_wrong_bar
    print(
        f"bars: with-memory >= {MEMORY_CASES_PASS_WITH}/30 "
        f"({'PASS' if with_memory_correct >= MEMORY_CASES_PASS_WITH else 'FAIL'}), "
        f"ablated-wrong >= {ablated_wrong_bar}/{ablation_cases} "
        f"({'PASS' if ablated_wrong >= ablated_wrong_bar else 'FAIL'})"
    )
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live-llm", action="store_true", help="actually call the provider")
    ap.add_argument("--n", type=int, default=50, help="fresh corpus sample size")
    ap.add_argument("--seed", type=int, default=1963)
    ap.add_argument("--groups", default="guards,malay,corpus")
    ap.add_argument("--model", default=None, help="override the configured model")
    ap.add_argument("--json", dest="json_out", default=None, help="write the full run here")
    ap.add_argument("--limit", type=int, default=0, help="stop after N inputs (smoke)")
    ap.add_argument(
        "--control",
        action="store_true",
        help=(
            "send the OLD prompt down BOTH lanes. This is the noise floor: whatever it "
            "disagrees with itself about is the model, not the edit, and an old-vs-new "
            "number is unreadable without it."
        ),
    )
    ap.add_argument(
        "--memory-cases",
        action="store_true",
        help=(
            "run tests/chatbot/fixtures/parser_memory_cases.json through the live parser "
            "instead of the corpus-parity mode above (AC-MEM068, plan 8.2); every other "
            "flag in this group is ignored"
        ),
    )
    ap.add_argument(
        "--prompt-version",
        type=int,
        default=None,
        help=(
            "--memory-cases only: a chatbot_semantic_parser registry VERSION NUMBER to "
            "grade. Default: the newest version ever published to that name, never the "
            "production label"
        ),
    )
    ap.add_argument(
        "--cases",
        default=None,
        help="--memory-cases only: fixture path (default tests/chatbot/fixtures/parser_memory_cases.json)",
    )
    ap.add_argument(
        "--current-date",
        default=None,
        help="--memory-cases only: override the prompt's {{current_date}} (default a fixed date)",
    )
    args = ap.parse_args()

    if args.memory_cases:
        return run_memory_cases(args)

    os.environ.setdefault("no_proxy", "*")
    groups = {g.strip() for g in args.groups.split(",") if g.strip()}

    captures = load_captures()
    print(f"corpus: {len(captures)} real captures with a message and a raw emission")
    inputs = build_inputs(captures, n=args.n, seed=args.seed, groups=groups)
    if args.limit:
        inputs = inputs[: args.limit]
    by_group = Counter(i["group"] for i in inputs)
    print(f"inputs: {len(inputs)} ({', '.join(f'{k}={v}' for k, v in sorted(by_group.items()))})")

    if not args.live_llm:
        print("\nDRY RUN (no provider calls). Inputs:")
        for item in inputs:
            note = f"  [{item['why']}]" if item["why"] else ""
            msg = item["message"].split("reply to:")[0].strip().replace("\n", " / ")[:80]
            print(f"  {item['group']:<7} {item['id']:<24} {msg!r}{note}")
        print("\nre-run with --live-llm to make the calls")
        return 0

    from app.database import SessionLocal
    from app.services.ai_assistant_service import AIAssistantConfigService
    from app.services.chatbot.head.parser import (
        DECLARED_KEYS,
        ParserConfig,
        build_user_block,
    )
    from app.services.llm_provider import resolve_api_key

    db = SessionLocal()
    try:
        config = AIAssistantConfigService(db).get()
        if config is None:
            print("STOP: no AI assistant configuration row in this database.")
            return 2
        provider = config.provider
        model = args.model or config.model
        api_key = resolve_api_key(config, provider)
        if not api_key:
            print(
                f"STOP: no API key configured for provider {provider!r} in the AI assistant "
                "config row. Nothing was called and no key was invented."
            )
            return 2
        current_date = "Friday, 04 September 2026"
        old_text, old_label, new_text, new_label = resolve_prompts(db, current_date=current_date)
    finally:
        db.close()

    print(f"provider={provider} model={model} temperature=0")
    print(f"  OLD prompt: {old_label}, {len(old_text)} chars")
    print(f"  NEW prompt: {new_label}, {len(new_text)} chars")
    print(f"  size delta: {len(old_text) - len(new_text)} chars "
          f"({100.0 * (len(old_text) - len(new_text)) / len(old_text):.1f}%)")

    base = ParserConfig(
        system_prompt=old_text,
        prompt_version=None,
        provider=provider,
        model=model,
        api_key=api_key,
    )
    config_old = base
    config_new = base if args.control else replace(base, system_prompt=new_text)
    if args.control:
        print("  CONTROL RUN: both lanes use the OLD prompt (measuring model noise only)")

    keys = sorted(DECLARED_KEYS)
    raw_agree, raw_total = Counter(), Counter()
    qf_agree, qf_total = Counter(), Counter()
    disagreements: list[dict] = []
    failures: list[dict] = []
    records: list[dict] = []

    for index, item in enumerate(inputs, 1):
        user_block = build_user_block(
            previous_response=(item["state"] or {}).get("response"),
            latest_user_message=item["message"],
            pending_kind=None,
        )
        left, right, err = parse_pair(config_old, config_new, user_block)
        if left is None or right is None:
            failures.append({"id": item["id"], "error": err})
            print(f"[{index}/{len(inputs)}] {item['id']}: FAILED ({err})")
            continue

        raw_diff = diff_keys(left, right, keys)
        for key in keys:
            raw_total[key] += 1
            if key not in raw_diff:
                raw_agree[key] += 1

        qf_left, qf_right = post_process_both(item, left, right)
        qf_diff: list[str] = []
        if qf_left is not None and qf_right is not None:
            qf_diff = diff_keys(qf_left, qf_right, keys)
            for key in keys:
                qf_total[key] += 1
                if key not in qf_diff:
                    qf_agree[key] += 1

        records.append(
            {
                "id": item["id"],
                "group": item["group"],
                "message": item["message"],
                "raw_diff": raw_diff,
                "qf_diff": qf_diff,
                "old": {k: left.get(k) for k in raw_diff},
                "new": {k: right.get(k) for k in raw_diff},
                # full emissions, so a re-analysis never needs a second paid run
                "old_full": left,
                "new_full": right,
                "qf_old": {k: (qf_left or {}).get(k) for k in qf_diff},
                "qf_new": {k: (qf_right or {}).get(k) for k in qf_diff},
            }
        )
        if raw_diff or qf_diff:
            disagreements.append(records[-1])
        flag = "SAME" if not raw_diff else ("raw:" + ",".join(raw_diff))
        flag2 = "" if not qf_diff else "  ANSWER-DIFF:" + ",".join(qf_diff)
        print(f"[{index}/{len(inputs)}] {item['id']:<24} {flag}{flag2}")

    table("RAW parser emission, agreement per key", raw_agree, raw_total, keys)
    table("AFTER output_exchange (what the customer gets)", qf_agree, qf_total, keys)

    print(f"\ninputs run: {len(records)}  failures: {len(failures)}")
    for failure in failures:
        print(f"  FAILED {failure['id']}: {failure['error']}")

    print(f"\nDISAGREEMENTS ({len(disagreements)} of {len(records)} inputs), for triage:")
    if not disagreements:
        print("  none")
    for row in disagreements:
        msg = row["message"].split("reply to:")[0].strip().replace("\n", " / ")[:110]
        print(f"\n  {row['group']}/{row['id']}  {msg!r}")
        for key in row["raw_diff"]:
            print(f"    raw    {key}: {json.dumps(row['old'][key])} -> {json.dumps(row['new'][key])}")
        for key in row["qf_diff"]:
            print(
                f"    ANSWER {key}: {json.dumps(row['qf_old'][key])} -> "
                f"{json.dumps(row['qf_new'][key])}"
            )
        print("    triage: improvement | regression | noise   <- owner fills this in")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "provider": provider,
                    "model": model,
                    "old_prompt": {"label": old_label, "chars": len(old_text)},
                    "new_prompt": {"label": new_label, "chars": len(new_text)},
                    "raw_agreement": {k: [raw_agree[k], raw_total[k]] for k in keys},
                    "qf_agreement": {k: [qf_agree[k], qf_total[k]] for k in keys},
                    "records": records,
                    "failures": failures,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nfull run written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
