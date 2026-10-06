"""RESOLVE-SEMANTIC-FALLBACK, the chatbot half: the parser now fills `canonical_code`
with the bare code when a type / colour / brand word sits beside it ("TAP SRTWT5608" ->
canonical_code "SRTWT5608", raw unchanged), and every chatbot site that reads a product
token reads `canonical_code` FIRST, `raw` second - the order `resolve_gate._token_of`
already uses to build the resolver's token. The sites that read `raw` first on main:

* `engine._exact_codes_only` (the availability-mode filter) folded the RAW, so the
  product the resolver placed off the canonical token was thrown away and reported
  "Couldn't find";
* `turn_runtime.unplaced_tokens` / `not_checked_tokens` / `absorbed_tokens` keyed the
  message's tokens by RAW, so a miss on the canonical token never joined to the typed
  phrase and the "I could not find" line went silent (the #1501 / #1502 class);
* `lanes/business/__init__._resolve_report_product_and_location` matched the resolver's
  code against the RAW casefold, so a report never found its typed subject;
* the entities-only arm (`engine._run_entities_only_arm`) sent and keyed RAWS.

The reply still quotes the word the customer typed (the raw), never the folded key.

The end-to-end tests run the real engine with the real resolver over seeded ZZT codes
(`test_outstanding_lane._run_turn(real_resolver=True)`), and the fallback through the
engine with the code reader's provider call stubbed at `resolver_code_reader.read_codes`.
Fake ZZT fixture codes only.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.engine import _exact_codes_only

from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_entities_only_arm import _focus_products
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact, _parser_output

TAP = "ZZTWT5608"
TYPED = "TAP ZZTWT5608"
MISS_TYPED = "TAP ZZTX9999"
MISS = "ZZTX9999"


def _entity(raw: str, canonical: str | None, hint: str = "product") -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": canonical, "current_message": True, "confident": True}


def _candidate(code: str) -> dict[str, Any]:
    return {"uuid": f"uuid-{code}", "raw": code, "canonical_code": code, "code": code, "entity_type": "product"}


# --------------------------------------------------------------------------- #
# engine._exact_codes_only
# --------------------------------------------------------------------------- #


def test_exact_codes_only_keeps_the_product_the_canonical_token_placed():
    """One entity, raw "TAP ZZTWT5608", canonical "ZZTWT5608"; the resolver placed
    ZZTWT5608 off the canonical token. Nothing changes: None."""
    parse = {"entities": [_entity(TYPED, TAP)]}
    candidates = [_candidate(TAP)]
    kinds = {TAP: {"product": 1}}

    out = _exact_codes_only(parse, kinds, {"product": candidates}, list(candidates), {})

    assert out is None, f"the filter dropped a product the resolver placed: {out}"


def test_exact_codes_only_names_a_canonical_miss_by_the_typed_raw():
    parse = {"entities": [_entity(TYPED, TAP), _entity(MISS_TYPED, MISS)]}
    candidates = [_candidate(TAP)]
    kinds = {TAP: {"product": 1}}

    out = _exact_codes_only(parse, kinds, {"product": candidates}, list(candidates), {})

    assert out is not None
    _kinds, kept, compatible, unplaced = out
    assert [c["code"] for c in kept["product"]] == [TAP]
    assert [e["code"] for e in compatible] == [TAP]
    assert unplaced == {"zztx9999": MISS_TYPED}, "keyed by the resolver's token, valued with the typed phrase"


def test_exact_codes_only_still_reads_raw_when_canonical_is_null():
    parse = {"entities": [_entity(TAP, None)]}
    candidates = [_candidate(TAP)]
    assert _exact_codes_only(parse, {TAP: {"product": 1}}, {"product": candidates}, list(candidates), {}) is None


# --------------------------------------------------------------------------- #
# turn_runtime joins
# --------------------------------------------------------------------------- #


def test_unplaced_tokens_join_the_canonical_miss_to_the_typed_phrase():
    resolved = {"resolutions": [{"token": MISS, "matches": []}], "unresolved_tokens": [MISS]}
    assert turn_runtime.unplaced_tokens([_entity(MISS_TYPED, MISS)], resolved) == {"zztx9999": MISS_TYPED}


def test_unplaced_tokens_still_join_by_raw_when_canonical_is_null():
    resolved = {"resolutions": [], "unresolved_tokens": ["ZZTX9999"]}
    assert turn_runtime.unplaced_tokens([_entity("ZZTX-9999", None)], resolved) == {"zztx9999": "ZZTX-9999"}


def test_not_checked_tokens_join_the_canonical_cut_to_the_typed_phrase():
    resolved = {"resolutions": [], "unresolved_tokens": [], "truncated_tokens": [TAP]}
    assert turn_runtime.not_checked_tokens([_entity(TYPED, TAP)], resolved) == {"zztwt5608": TYPED}


def test_absorbed_tokens_read_the_canonical_token():
    """Two typed products; the resolver answered the second's canonical token only with
    the first's exact code: the second is unplaced, quoted as typed."""
    entities = [_entity("ZZTSWT3001", "ZZTSWT3001"), _entity("WC ZZTSWT3001-GM", "ZZTSWT3001-GM")]
    resolved = {
        "resolutions": [
            {"token": "ZZTSWT3001", "matches": [{"canonical_code": "ZZTSWT3001", "entity_type": "product"}]},
            {"token": "ZZTSWT3001GM", "matches": [{"canonical_code": "ZZTSWT3001", "entity_type": "product"}]},
        ],
        "unresolved_tokens": [],
    }
    assert turn_runtime.absorbed_tokens(entities, resolved) == {"zztswt3001gm": "WC ZZTSWT3001-GM"}


# --------------------------------------------------------------------------- #
# The resolver body carries the typed text beside the token
# --------------------------------------------------------------------------- #


def _ctx(entities: list[dict[str, Any]], text: str) -> dict[str, Any]:
    return {
        "text": {"message": {"message": {"text": text}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_stock",
                "domain_hint": "inventory",
                "match_mode": "or",
                "access_levels": [],
                "entities": entities,
            }
        },
    }


def test_resolve_entity_body_sends_raw_tokens_parallel_to_tokens():
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    body = resolve_entity_body(_ctx([_entity(TYPED, TAP), _entity("hanlim", None, hint="customer")], "TAP ZZTWT5608 x 5 for hanlim"))

    assert body["tokens"] == [TAP, "hanlim"]
    assert body["raw_tokens"] == [TYPED, "hanlim"]
    assert body["query"] == "TAP ZZTWT5608 x 5 for hanlim"


# --------------------------------------------------------------------------- #
# The report's typed subject
# --------------------------------------------------------------------------- #


def test_the_report_subject_is_matched_on_the_canonical_code():
    from app.services.chatbot.lanes.business import _resolve_report_product_and_location

    parse_output = {"entities": [_entity(TYPED, TAP)]}
    resolved_entities = [{"entity_type": "product", "code": TAP, "uuid": "u1"}]
    semantic_input: dict[str, Any] = {}

    _resolve_report_product_and_location(parse_output, resolved_entities, semantic_input, db=None)

    assert semantic_input.get("outstanding_product_code") == TAP


# --------------------------------------------------------------------------- #
# End to end: the real engine, the real resolver
# --------------------------------------------------------------------------- #


def _seed(session_factory, *codes: str) -> None:
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    for code in codes:
        seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.commit()
    _seed_contact(session_factory, variables={})


def _uuids_passed(captured) -> int:
    diagnostics = [args.get("_diagnostics") or {} for _name, args in captured]
    return max((d.get("total_uuids_passed", 0) for d in diagnostics), default=0)


class TestRealResolver:
    def test_a_type_word_beside_the_code_reaches_the_tool_as_that_code(self, session_factory, monkeypatch):
        _seed(session_factory, TAP)

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(TYPED, TAP)]),
            text_body="TAP ZZTWT5608 x 5 sales order outstanding",
            msg_id="ZZT-canon-1",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": TAP},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert _uuids_passed(captured) == 1, captured
        assert "could not find" not in reply.lower(), reply
        assert "couldn't find" not in reply.lower(), reply

    def test_a_miss_on_the_canonical_token_is_named_as_typed(self, session_factory, monkeypatch):
        """Reader stubbed to `none`: the existing not-found line, quoting the phrase the
        dealer typed, never the folded token and never silence."""
        from app.services import resolver_code_reader
        from app.services.resolver_code_reader import CodeReading

        _seed(session_factory, TAP)
        monkeypatch.setattr(
            resolver_code_reader,
            "read_codes",
            lambda db, missed, **kw: {token: CodeReading("none", None) for token, _ in missed},
        )

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(TYPED, TAP), _entity(MISS_TYPED, MISS)]),
            text_body="TAP ZZTWT5608 x 5 TAP ZZTX9999 x 1 sales order outstanding",
            msg_id="ZZT-canon-2",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": TAP},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert _uuids_passed(captured) == 1, captured
        assert MISS_TYPED in reply, reply
        assert "zztx9999" not in reply.replace(MISS_TYPED, ""), f"the folded token leaked: {reply}"

    def test_the_fallback_resolves_a_raw_only_entity_through_the_engine(self, session_factory, monkeypatch):
        """The parser did not split (canonical_code null, the prompt not yet promoted):
        the shared route asks the reader about the folded token and the turn still
        reaches the tool with ZZTWT5608's uuid."""
        from app.services import resolver_code_reader
        from app.services.resolver_code_reader import CodeReading

        _seed(session_factory, TAP)
        asked: list[Any] = []

        def fake_read_codes(db, missed, *, message_text, user_id, dry_run):
            asked.append((list(missed), message_text, dry_run))
            return {token: CodeReading("code", TAP) for token, _ in missed}

        monkeypatch.setattr(resolver_code_reader, "read_codes", fake_read_codes)

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(TYPED, None)]),
            text_body="TAP ZZTWT5608 x 5 sales order outstanding",
            msg_id="ZZT-canon-3",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": TAP},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert asked and asked[0][0] == [("TAPZZTWT5608", TYPED)], asked
        assert asked[0][1] == "TAP ZZTWT5608 x 5 sales order outstanding"
        # The route's `dry_run` reaches the reader as sent (pinned in
        # `tests/test_resolver_semantic_fallback.py`); this harness turn is a live one.
        assert asked[0][2] is False
        assert _uuids_passed(captured) == 1, captured
        assert "could not find" not in reply.lower(), reply

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "pre-existing on origin/main, outside this lane (PR #833's `product_code_family."
            "narrow_code_tokens` on the resolve route): in a multi-line message the variant "
            "suffix words of OTHER lines (FRG, GM) are read as descriptors for the bare base "
            "code, so SRTWT5608 is narrowed to SRTWT5608-FRG and the base code's own line is "
            "lost; 13 uuids reach the tool, not 14. Reported on PR #1511; this test flips "
            "green the day that narrowing reads only the code's own line."
        ),
    )
    def test_the_owners_fifteen_line_stock_check_reaches_the_tool_with_fourteen_codes(self, session_factory, monkeypatch):
        """The prod message of 6 Oct 20:02 MYT, codes ZZT-prefixed: 15 product lines, one
        duplicate (SRTWT8258-GM twice) and the TAP line with canonical_code filled as the
        addendum now has it. Every unique code reaches the tool (14 uuids), nothing is
        reported not found, and the reader is never asked (a clean turn)."""
        from app.services import resolver_code_reader

        lines = [
            ("ZZTWT7408", 5), ("ZZTWT6808", 5), ("ZZTWT162", 5), ("ZZTWT8258-GM", 5), ("ZZTWT8258-GM", 5),
            ("ZZTWT8212", 5), ("ZZTWT6832-RG", 5), ("ZZTWT5608", 5), ("ZZTWT5608-FRG", 5), ("ZZTWT5608-GM", 10),
            ("ZZTWT6201", 5), ("ZZTWT8301-RG", 5), ("ZZTWT8301-GMB", 5), ("ZZTWT8303-RG", 5), ("ZZTWT8303-GMB", 5),
        ]
        unique = list(dict.fromkeys(code for code, _ in lines))
        assert len(unique) == 14
        _seed(session_factory, *unique)
        monkeypatch.setattr(
            resolver_code_reader, "read_codes", lambda *a, **k: pytest.fail("the reader was asked on a clean turn")
        )
        entities = [
            {**_entity(f"TAP {code}" if code == "ZZTWT5608" else code, code), "quantity": qty} for code, qty in lines
        ]
        text_body = "\n".join(f"{'TAP ' if c == 'ZZTWT5608' else ''}{c} x {q}" for c, q in lines) + "\n\ncheck stock"

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=entities),
            text_body=text_body + " sales order outstanding",
            msg_id="ZZT-canon-15",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": unique[0]},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert _uuids_passed(captured) == 14, captured
        assert "could not find" not in reply.lower(), reply
        assert "couldn't find" not in reply.lower(), reply
        assert "not checked" not in reply.lower(), reply

    def test_a_clean_turn_never_asks_the_reader(self, session_factory, monkeypatch):
        from app.services import resolver_code_reader

        _seed(session_factory, TAP)
        monkeypatch.setattr(
            resolver_code_reader, "read_codes", lambda *a, **k: pytest.fail("the reader was asked on a clean turn")
        )

        _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(TAP, TAP)]),
            text_body="ZZTWT5608 sales order outstanding",
            msg_id="ZZT-canon-4",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": TAP},
            real_resolver=True,
        )


# --------------------------------------------------------------------------- #
# The entities-only arm (bare codes, no domain)
# --------------------------------------------------------------------------- #


def test_the_entities_only_arm_places_the_canonical_code_on_the_typed_row(session_factory, seeded, monkeypatch):
    from tests.chatbot.test_entities_only_arm import MATCHES, PRODUCT_A_CODE, PRODUCT_A_UUID

    qf = _parser_output(domain_hint=None, intent_hint=None, asks=[], entities=[_entity(f"TAP {PRODUCT_A_CODE}", PRODUCT_A_CODE)])
    result, _captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=f"TAP {PRODUCT_A_CODE}", msg_id="ZZT-canon-eo-1", matches=MATCHES,
    )

    products = _focus_products(session_factory)
    row = next((p for p in products if p.get("raw") == f"TAP {PRODUCT_A_CODE}"), None)
    assert row is not None, products
    assert row.get("uuid") == PRODUCT_A_UUID, row
    reply = (result.reply or {}).get("text") or ""
    assert "could not" not in reply.lower() and "couldn't" not in reply.lower(), reply
