"""Test-only converter (CONTRACT-PV2-13oct.md section 7): the recorded v1 replay turns
(`tests/chatbot/replay_turns/**`) and the console cases (`tests/chatbot/console_cases/*.yaml`)
into PV2 table rows (`tests/chatbot/pv2/tables/converted_*.jsonl`).

A replay turn carries the v1 parser's 59-key verdict, so it converts to a full
`ParserV2Output` (`source: converted:<file>`); its recorded resolver answers become the
row's fake resolver config. A console case carries only text and reply expectations, so
its row has `parser_out: null` until CORE's dev-split recording fills it (section 7: dev
recordings replace converted rows). Converted rows are a best-effort mapping of the v1
reading; they pin apply_turn's action shape, not reply text.

Run: `venv/bin/python -m tests.chatbot.pv2.v1_to_v2` from `sorento_crm_backend/`.
"""
from __future__ import annotations

import glob
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
TABLES = HERE / "tables"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_CJK = re.compile(r"[一-鿿]")

#: v1 entity hint -> PV2 entity kind. Index kinds carry the canonical key; the rest a phrase.
_KIND = {
    "product": "product",
    "customer": "customer",
    "order": "order",
    "attachment": "attachment",
    "inbound_shipment": "inbound_shipment",
    "brand": "brand",
    "warehouse": "warehouse",
    "category": "product_type",
    "promotion": "product",
}
_INDEX_KINDS = {"brand", "product_type"}
#: v1 attachment_type canonical codes -> the attachment type keys the PV2 index seeds
#: (CONTRACT section 4 seeds). An unknown one stays null (the model could not place it).
_ATTACHMENT_TYPE = {
    "photo": "product_photos",
    "certificate": "certification",
    "technical drawing": "technical_specifications",
    "video": "product_videos",
    "manual": "installation_guide",
}
#: v1 branch kinds whose reply is a v1 data answer reached through a lookup.
_LOOKUP_BRANCHES = {"business_query", "check_promotion", "demand_qty", "stock_denied", "access_denied"}


def _now(turn: dict[str, Any]) -> str:
    recorded = (turn.get("source") or {}).get("recorded_at")
    if recorded:
        return str(recorded)
    ts = _inner(turn).get("timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
    return "2026-10-13T10:00:00+08:00"


def _inner(turn: dict[str, Any]) -> dict[str, Any]:
    """The respond.io message object with its text, found by walking `message` keys from
    the envelope (the console and webhook shapes nest it at different depths), with the
    nearest `timestamp` seen on the way."""
    node: Any = turn.get("envelope") or {}
    timestamp = None
    for _ in range(4):
        if not isinstance(node, dict):
            break
        timestamp = node.get("timestamp", timestamp)
        if isinstance(node.get("text"), str):
            return {**node, "timestamp": timestamp}
        node = node.get("message")
    return {"timestamp": timestamp}


def _text(turn: dict[str, Any]) -> str:
    return str(_inner(turn).get("text") or "")


def _language(text: str) -> str:
    return "zh" if _CJK.search(text) else "en"


def _period(verdict: dict[str, Any]) -> dict[str, Any] | None:
    start, end = verdict.get("date_filter_start"), verdict.get("date_filter_end")
    if not (start or end):
        return None
    return {"preset": None, "n": None, "date_from": start, "date_to": end}


def _empty_ask(act: str) -> dict[str, Any]:
    return {
        "act": act,
        "domain": None,
        "switch_back_to": None,
        "entities": [],
        "specs": [],
        "attachment_type": None,
        "period": None,
        "row_pick": None,
        "answer": None,
        "cleared": [],
        "report": None,
        "low_stock": None,
        "ideation": None,
    }


def _entities(verdict: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """(entities, attachment_type key, specs) for the entities THIS message named."""
    ents: list[dict[str, Any]] = []
    specs: list[dict[str, Any]] = []
    attachment_type: str | None = None
    backward = bool((verdict.get("anaphora") or {}).get("backward_reference"))
    for e in verdict.get("entities") or []:
        if e.get("current_message") is False:
            continue
        hint = e.get("hint")
        if hint == "attachment_type":
            attachment_type = _ATTACHMENT_TYPE.get(str(e.get("canonical_code") or "").casefold())
            continue
        if hint == "specification" and e.get("spec_key"):
            value = e.get("spec_value")
            number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            specs.append(
                {
                    "attribute": e.get("spec_key"),
                    "value_key": None if number is not None else (str(value) if value is not None else None),
                    "number": number,
                    "number_to": None,
                    "unit": None,
                    "comparator": "eq",
                    "op": "set",
                    "phrase": e.get("raw"),
                }
            )
            continue
        kind = _KIND.get(str(hint))
        if kind is None:
            continue
        index_kind = kind in _INDEX_KINDS
        ents.append(
            {
                "kind": kind,
                "key": (e.get("canonical_code") or e.get("raw")) if index_kind else None,
                "phrase": e.get("raw"),
                "op": "set",
                "refers_to_held": backward,
            }
        )
    return ents, attachment_type, specs


def _resolver(
    turn: dict[str, Any], ents: list[dict[str, Any]], attachment_type: str | None = None
) -> dict[str, Any]:
    """The fake resolver config from the turn's recorded resolutions: one exact match ->
    exact, several -> ambiguous (options keyed and labelled by code, never a uuid), none ->
    none. Index-kind entities resolve to their own key."""
    by_token: dict[str, list[dict[str, Any]]] = {}
    block = turn.get("resolutions") or {}
    for res in block.get("resolutions") or []:
        by_token[str(res.get("token"))] = list(res.get("matches") or [])
    entities: dict[str, Any] = {}
    for ent in ents:
        lookup = f"{ent['kind']}|{ent['key'] if ent['key'] is not None else ent['phrase']}"
        if ent["kind"] in _INDEX_KINDS:
            entities[lookup] = {
                "status": "exact",
                "refs": [{"kind": ent["kind"], "key": ent["key"], "label": ent["key"]}],
                "options": [],
            }
            continue
        matches = [m for m in by_token.get(str(ent["phrase"]), []) if m.get("canonical_code")]
        codes: list[str] = []
        for m in matches:
            if m["canonical_code"] not in codes:
                codes.append(m["canonical_code"])
        if len(codes) == 1:
            entities[lookup] = {
                "status": "exact",
                "refs": [{"kind": ent["kind"], "key": codes[0], "label": codes[0]}],
                "options": [],
            }
        elif codes:
            entities[lookup] = {
                "status": "ambiguous",
                "refs": [],
                "options": [
                    {
                        "key": code,
                        "label": code,
                        "payload": {"kind": ent["kind"], "key": code, "label": code, "op": "set"},
                    }
                    for code in codes[:8]
                ],
            }
        else:
            entities[lookup] = {"status": "none", "refs": [], "options": []}
    attachment_types: dict[str, Any] = {}
    if attachment_type:
        attachment_types[attachment_type] = {
            "status": "exact",
            "refs": [{"kind": "attachment_type", "key": attachment_type, "label": attachment_type}],
            "options": [],
        }
    return {"entities": entities, "specs": {}, "attachment_types": attachment_types}


def _answer_key(verdict: dict[str, Any]) -> str | None:
    aoq = verdict.get("answers_open_question") or {}
    picks = aoq.get("picks")
    if aoq.get("resolved") and isinstance(picks, list) and picks:
        return str(picks[0])
    if aoq.get("resolved") and picks == "all":
        return "all"
    if verdict.get("is_affirmative") is True:
        return "1"
    if verdict.get("is_affirmative") is False:
        return "no"
    return None


def convert_verdict(verdict: dict[str, Any], *, text: str, has_prior: bool, prior_pending: bool) -> dict[str, Any]:
    """One v1 verdict as a ParserV2Output dict (best-effort, see the module docstring)."""
    message_type = verdict.get("message_type")
    domain = verdict.get("domain_hint")
    ents, attachment_type, specs = _entities(verdict)
    escalation = None
    clarify = {"needed": False, "phrase": None, "about": None}
    routing = verdict.get("routing") or {}
    if message_type == "request_for_help":
        ask = _empty_ask("small_talk")
        escalation = {"requested": True, "team_key": routing.get("suggested_team")}
    elif message_type == "casual":
        ask = _empty_ask("small_talk")
    elif prior_pending and _answer_key(verdict) is not None and not ents:
        ask = _empty_ask("answers_bot_question")
        ask["answer"] = {"option_key": _answer_key(verdict), "free_value": None}
    elif message_type == "clarification" and not ents:
        ask = _empty_ask("unclear")
        clarify = {"needed": True, "phrase": text or None, "about": "other"}
    elif ents or not has_prior:
        ask = _empty_ask("new_topic")
    elif verdict.get("entity_op") == "clear" and not domain:
        ask = _empty_ask("unclear")
        clarify = {"needed": True, "phrase": text or None, "about": "domain"}
    else:
        ask = _empty_ask("refines_held")
    if ask["act"] in ("new_topic", "refines_held"):
        ask["domain"] = domain
        ask["entities"] = ents
        ask["specs"] = specs
        ask["attachment_type"] = attachment_type
        ask["period"] = _period(verdict)
    return {"asks": [ask], "clarify": clarify, "language": _language(text), "escalation": escalation}


def _expect(turn: dict[str, Any], parser_out: dict[str, Any], resolver: dict[str, Any]) -> dict[str, Any]:
    expected = turn.get("expected") or {}
    ask = parser_out["asks"][0]
    actions: list[dict[str, Any]] | None = None
    if parser_out.get("escalation"):
        actions = [{"type": "hand_over"}]
    elif ask["act"] == "small_talk":
        actions = [{"type": "small_talk"}]
    elif ask["act"] == "unclear":
        actions = [{"type": "clarify"}]
    elif ask["act"] == "new_topic" and expected.get("branch_kind") in _LOOKUP_BRANCHES:
        statuses = [v["status"] for v in resolver["entities"].values()]
        if statuses and all(s == "exact" for s in statuses):
            actions = [{"type": "lookup", "domain": ask["domain"]}]
        elif any(s in ("none", "ambiguous") for s in statuses):
            actions = [{"type": "clarify"}]
    reply_must_not = [" · "]
    reply_must_not.extend(sorted({u.lower() for u in expected.get("entity_ids") or [] if _UUID.fullmatch(str(u))}))
    out: dict[str, Any] = {
        "reply_must": [],
        "reply_must_not": reply_must_not,
        "v1": {
            "branch_kind": expected.get("branch_kind"),
            "pending": expected.get("pending"),
            "text": expected.get("text"),
        },
    }
    if actions is not None:
        out["actions"] = actions
    return out


def convert_replays() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(str(TESTS / "replay_turns" / "**" / "*.json"), recursive=True)):
        rel = str(Path(path).relative_to(TESTS))
        case = Path(path).stem
        turns = json.loads(Path(path).read_text())["turns"]
        prior_pending = False
        for i, turn in enumerate(turns, start=1):
            verdict = turn.get("verdict") or {}
            text = _text(turn)
            parser_out = convert_verdict(verdict, text=text, has_prior=i > 1, prior_pending=prior_pending)
            ents = parser_out["asks"][0]["entities"]
            resolver = _resolver(turn, ents, parser_out["asks"][0]["attachment_type"])
            pending = (turn.get("expected") or {}).get("pending")
            rows.append(
                {
                    "case": f"replay/{case}",
                    "turn": i,
                    "now": _now(turn),
                    "state_in": None,
                    "text": text,
                    "index_sha": "converted",
                    "parser_out": parser_out,
                    "source": f"converted:{rel}",
                    "resolver": resolver,
                    "v1_pending_after": pending,
                    "expect": _expect(turn, parser_out, resolver),
                }
            )
            prior_pending = bool(pending)
    return rows


def convert_console_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(str(TESTS / "console_cases" / "*.yaml"))):
        rel = str(Path(path).relative_to(TESTS))
        doc = yaml.safe_load(Path(path).read_text()) or {}
        turn_no = 0
        case_name = None
        for case in doc.get("cases") or []:
            if case.get("cold", False) or case_name is None or case.get("turns"):
                case_name = f"console/{Path(path).stem}/{case.get('name')}"
                turn_no = 0
            steps = case.get("turns") or [{"text": case.get("text"), "expect": case.get("expect")}]
            for step in steps:
                turn_no += 1
                expect = step.get("expect") or {}
                must_not = [" · ", *[str(x) for x in expect.get("reply_not_contains") or []]]
                rows.append(
                    {
                        "case": case_name,
                        "turn": turn_no,
                        "now": "2026-10-13T10:00:00+08:00",
                        "state_in": None,
                        "text": step.get("text"),
                        "index_sha": None,
                        "parser_out": None,
                        "source": f"converted:{rel}",
                        "resolver": None,
                        "live_only": bool(case.get("live_only", False)),
                        "expect": {
                            "reply_must": [str(x) for x in expect.get("reply_contains") or []],
                            "reply_must_not": must_not,
                            "no_uuid": bool(expect.get("no_uuid", True)),
                            "v1": {"branch_kind": expect.get("branch_kind")},
                        },
                    }
                )
    return rows


def _write(name: str, rows: list[dict[str, Any]]) -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / name).open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def main() -> None:
    replays = convert_replays()
    console = convert_console_cases()
    _write("converted_replay.jsonl", replays)
    _write("converted_console.jsonl", console)
    print(f"replay rows: {len(replays)}; console rows: {len(console)}")


if __name__ == "__main__":
    main()
