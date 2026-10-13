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

from tests.chatbot._turn_helpers import make_office_access_type
from tests.chatbot.test_engine import CONTACT_ID
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


def test_exact_codes_only_compares_the_code_the_reader_read_out_of_a_raw_only_token():
    """Console pass on a1eae3bb (the prod verdict shape, canonical_code null): the resolver
    placed ZZTWT5608 through its code reader, but this join compared the folded token
    "tapzztwt5608" with the code, kept nothing, and the dealer got "Here's what you want:"
    with no stock line. The reader's value is the code the catalogue matched."""
    from app.services.chatbot.engine import _read_values

    parse = {"entities": [_entity("TAP ZZTWT5608", None)]}
    candidates = [_candidate(TAP), _candidate("ZZTWT5608-FRG")]
    kinds = {"TAPZZTWT5608": {"product": 2}}
    resolved = {"resolutions": [{"token": "TAPZZTWT5608", "fallback": {"asked": True, "kind": "code", "value": TAP, "matched": 1}}]}
    read_values = _read_values(resolved)
    assert read_values == {"tapzztwt5608": "zztwt5608"}

    out = _exact_codes_only(parse, kinds, {"product": candidates}, list(candidates), {}, read_values=read_values)
    assert out is not None
    kinds_out, candidates_out, compatible, placed = out
    assert [c["canonical_code"] for c in candidates_out["product"]] == [TAP]
    assert kinds_out == {"TAPZZTWT5608": {"product": 1}}
    assert placed == {}
    # Without the reader's value the same token is a code-like miss (what a1eae3bb did).
    _, _, _, placed_before = _exact_codes_only(parse, kinds, {"product": candidates}, list(candidates), {})
    assert placed_before == {"tapzztwt5608": "TAP ZZTWT5608"}
    assert _read_values({"resolutions": [{"token": "ZZTWT5608"}]}) == {}
    assert _read_values(None) == {}


def test_a_read_out_code_becomes_the_entitys_canonical_code_for_the_rest_of_the_turn():
    """`engine._with_read_codes`: the verdict `turn_apply` reads and the parse output the
    resolver joins read both get the code the reader read out, on the entity whose raw
    folds to the resolver's token, only where the parser left `canonical_code` null; a
    `family` reading, a non-product entity and a turn the reader never answered are left
    exactly as they came (same objects)."""
    from app.services.chatbot.engine import _with_read_codes

    tap = {"raw": "TAP ZZTWT5608", "hint": "product", "canonical_code": None, "quantity": 5}
    # The parser sometimes copies the whole phrase into the field: the same resolver token.
    copied = {"raw": "TAP ZZTWT5608", "hint": "product", "canonical_code": "TAP ZZTWT5608", "quantity": 5}
    basin = {"raw": "basin", "hint": "category", "canonical_code": None}
    bare = {"raw": "ZZTWT5608-GM", "hint": "product", "canonical_code": "ZZTWT5608-GM", "quantity": 10}
    verdict = {"domain_hint": "inventory", "entities": [tap, basin, bare, copied]}
    parse = {"entities": [tap, bare]}
    resolved = {"resolutions": [
        {"token": "TAPZZTWT5608", "fallback": {"asked": True, "kind": "code", "value": TAP, "matched": 1}},
        {"token": "TOILET8066", "fallback": {"asked": True, "kind": "family", "value": "8066", "matched": 3}},
        {"token": "ZZTWT5608GM"},
    ]}

    out_verdict, out_parse = _with_read_codes(verdict, parse, resolved)

    assert out_verdict["entities"][0] == {**tap, "canonical_code": TAP}
    assert out_verdict["entities"][1] is basin and out_verdict["entities"][2] is bare
    assert out_verdict["entities"][3] == {**copied, "canonical_code": TAP}
    assert out_parse["entities"] == [{**tap, "canonical_code": TAP}, bare]
    assert tap["canonical_code"] is None, "nothing is mutated in place"
    assert out_verdict["domain_hint"] == "inventory"
    same_v, same_p = _with_read_codes(verdict, parse, {"resolutions": [{"token": "TAPZZTWT5608"}]})
    assert same_v is verdict and same_p is parse
    # A reading the catalogue did not answer (`matched` 0) is not written: the miss is
    # quoted as typed (security review on 4ce61a6c, nit 3).
    unmatched = {"resolutions": [{"token": "TAPZZTWT5608", "fallback": {"asked": True, "kind": "code", "value": "ZZTWT5699", "matched": 0}}]}
    same_v, same_p = _with_read_codes(verdict, parse, unmatched)
    assert same_v is verdict and same_p is parse
    assert _with_read_codes(verdict, parse, None) == (verdict, parse)


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


def test_a_non_product_entity_keeps_its_raw_key_so_a_canonical_miss_stays_silent_as_on_main():
    """The canonical-first join is for product codes. An `attachment_type` word "cert" with
    the parser's canonical "certificate" (no attachment-type row answers it; certificates
    are certificate rows, and the certificate predicate answers the ask) kept main's raw key
    and its miss silent; this lane leaves that exactly as it was
    (`test_attribute_asks_round8.py::test_f2_incoming_and_certificate_sets_read_like_the_product_code_answer[cert]`)."""
    entities = [
        _entity(TYPED, TAP),
        {"raw": "cert", "hint": "attachment_type", "canonical_code": "certificate"},
    ]
    assert turn_runtime._typed_by_token(entities) == {"zztwt5608": TYPED, "cert": "cert"}
    assert turn_runtime.unplaced_tokens(entities, {"unresolved_tokens": ["certificate"]}) == {}
    assert turn_runtime.unplaced_tokens(entities, {"unresolved_tokens": ["cert"]}) == {"cert": "cert"}
    assert turn_runtime.unplaced_tokens(entities, {"unresolved_tokens": [TAP]}) == {"zztwt5608": TYPED}


def test_typed_product_unplaced_joins_the_canonical_miss_the_way_the_map_is_keyed():
    """#1506's "did THIS message's own product token miss?" reads the map
    `named_unplaced` builds, which this lane keys canonical-first for a product. Keyed by
    the raw alone, "TAP ZZTX9999" (canonical ZZTX9999, missed) looked up "tapzztx9999" in
    a map holding "zztx9999", answered False, and the composer header and the
    `set_page` carry stayed silent on a miss the reply itself names."""
    verdict = {"entities": [_entity(MISS_TYPED, MISS)]}
    resolved = {"resolutions": [{"token": MISS, "matches": []}], "unresolved_tokens": [MISS]}
    unplaced = turn_runtime.named_unplaced(verdict["entities"], resolved)
    assert unplaced == {"zztx9999": MISS_TYPED}
    assert turn_runtime.typed_product_unplaced(verdict, unplaced) is True


def test_a_described_set_miss_on_a_canonical_token_quotes_the_typed_phrase_not_a_fragment():
    """#1506's rule C quotes the dealer's own product token when it missed, never the set
    reader's fragment words. Its join read the raw against the resolver's tokens, which
    this lane sends canonical-first: "TAP ZZTX9999" (canonical ZZTX9999, missed) never
    joined, and the miss line fell back to the reader's fragment ("Couldn't find: "tap"")."""
    from tests.chatbot.test_miss_says_what_it_could_not_find import _compose, _rendered

    out = _compose(
        parser_entities=[_entity(MISS_TYPED, MISS)],
        resolutions=[{"token": MISS, "matches": []}],
        unresolved=[MISS],
        compat=[],
        predicate={"qualifying_total": 0, "unrecognized_terms": ["tap"], "require": {"stock": True}},
    )
    rendered = _rendered(out)
    assert f'Couldn\'t find: "{MISS_TYPED}".' in rendered, rendered
    assert '"tap"' not in rendered, rendered


def test_typed_product_unplaced_still_joins_by_raw_when_canonical_is_null():
    verdict = {"entities": [_entity("ZZTX-9999", None)]}
    unplaced = turn_runtime.named_unplaced(verdict["entities"], {"resolutions": [], "unresolved_tokens": ["ZZTX9999"]})
    assert turn_runtime.typed_product_unplaced(verdict, unplaced) is True


def test_typed_product_unplaced_is_still_not_a_miss_on_a_brand_word():
    verdict = {"entities": [_entity("zzt brand", "ZZTBRAND", hint="brand")]}
    assert turn_runtime.typed_product_unplaced(verdict, {"zztbrand": "zzt brand"}) is False
    assert turn_runtime.typed_product_unplaced(verdict, {"zztbrand2": "zzt brand"}) is False


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


def test_the_refinement_word_check_asks_no_model(monkeypatch):
    """`_refinement_product_resolves` is a yes/no check on a refinement WORD against the
    catalogue, inside a turn whose subject already resolved: it opts out of the shared
    route's code reader (reviewer finding 4)."""
    from app.services.chatbot.lanes.business import _refinement_product_resolves
    from app.services.chatbot.lanes.business import services as business_services

    bodies: list[dict[str, Any]] = []

    class _Services:
        def resolve_entity(self, body):
            bodies.append(dict(body))
            return {"resolutions": [{"token": body["tokens"][0], "matches": []}], "unresolved_tokens": body["tokens"]}

    monkeypatch.setattr(business_services, "production_services", lambda db, **kw: _Services())
    assert _refinement_product_resolves(object(), "cheaper") is False
    assert bodies and bodies[0]["semantic_fallback"] is False, bodies


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
    # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
    make_office_access_type(session_factory, CONTACT_ID)


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

    def test_the_fallback_answers_the_stock_line_in_the_availability_console(self, session_factory, monkeypatch, stub_access):
        """The owner's journey end to end on the prod verdict shape ("TAP ZZTWT5608" as one
        product entity, canonical_code null, quantity 5) through the availability-only
        dealer console: real engine, real resolver, real stock decision, the reader stubbed
        to read the code. Measured on a1eae3bb before the fix: the route ran the counted
        set ("Couldn't find a TAP ZZTWT5608 with stock (checked ZZTWT5608)"), then the
        engine's exact-codes join kept nothing ("Here's what you want:" with no tool call)."""
        from app.services import resolver_code_reader
        from app.services.resolver_code_reader import CodeReading
        from tests.chatbot._avail_mode_console import AvailConsole, _seed as seed_codes
        from tests.chatbot._r9_engine_console import product, stock

        console = AvailConsole(session_factory, monkeypatch, stub_access, phone="+60900000097")
        console.codes.update(seed_codes(session_factory, [TAP, "ZZTWT5608-FRG", "ZZTWT5608-GM"]))
        console.uuid_of = {code: pid for pid, code in console.codes.items()}
        asked: list[Any] = []

        def fake_read_codes(db, missed, *, message_text, user_id, dry_run):
            asked.append(list(missed))
            return {token: CodeReading("code", TAP) for token, _ in missed}

        monkeypatch.setattr(resolver_code_reader, "read_codes", fake_read_codes)

        text = console.say("TAP ZZTWT5608 x 5", stock(product("TAP ZZTWT5608", 5)))

        assert asked == [[("TAPZZTWT5608", "TAP ZZTWT5608")]], asked
        assert text.startswith("ZZTWT5608 x 5:"), text
        assert "couldn't find" not in text.lower() and "could not find" not in text.lower(), text
        ids = [pid for name, args in console.tool_calls for pid in args.get("product_ids") or []]
        assert ids == [console.uuid_of[TAP]], console.tool_calls

    def test_a_family_the_reader_read_out_reaches_the_availability_picker(self, session_factory, monkeypatch, stub_access):
        """`family` readings are deliberately NOT written onto the entity (`_with_read_codes`
        takes `code` only), so the engine's exact-codes join reads them through
        `read_values` (reviewer on 4ce61a6c, should-fix 2): "TOILET WC8066" misses every
        code tier, the reader reads the family fragment WC8066, the substring probe places
        the three ZZTWC8066 codes, and the availability console steers to the which-one
        picker instead of "Couldn't find"."""
        from app.services import resolver_code_reader
        from app.services.resolver_code_reader import CodeReading
        from tests.chatbot._avail_mode_console import AvailConsole, _seed as seed_codes
        from tests.chatbot._r9_engine_console import product, stock

        family = ["ZZTWC8066-RL", "ZZTWC8066-SH", "ZZTWC8066-SH-150"]
        console = AvailConsole(session_factory, monkeypatch, stub_access, phone="+60900000095")
        console.codes.update(seed_codes(session_factory, [TAP, *family]))
        console.uuid_of = {code: pid for pid, code in console.codes.items()}
        monkeypatch.setattr(
            resolver_code_reader, "read_codes",
            lambda db, missed, **kw: {token: CodeReading("family", "WC8066") for token, _ in missed},
        )

        text = console.say("TOILET WC8066 got stock?", stock(product("TOILET WC8066")))

        assert "couldn't find" not in text.lower() and "could not find" not in text.lower(), text
        assert all(code in text for code in family), text
        assert TAP not in text

    def test_the_owners_fifteen_line_stock_check_reaches_the_tool_with_fourteen_codes(self, session_factory, monkeypatch):
        """The prod message of 6 Oct 20:02 MYT, codes ZZT-prefixed: 15 product lines, one
        duplicate (SRTWT8258-GM twice) and the TAP line with canonical_code filled as the
        addendum now has it. Every unique code reaches the tool (14 uuids), nothing is
        reported not found, and the reader is never asked (a clean turn). A strict xfail
        until NARROW-CODE-OWN-LINE (#1516) merged: `narrow_code_tokens` read the other
        lines' variant suffixes (FRG, GM) as descriptors for the bare SRTWT5608 and the base
        code's line was lost (13 uuids); it reads the code's own line only now."""
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


def test_the_entities_only_arm_sends_dry_run_through_to_the_resolver(session_factory, seeded, monkeypatch):
    """D14 (reviewer blocker on 17301020): a test or console turn writes nothing outside
    `chatbot.turns`; the arm's resolve body carries `dry_run` so the shared route's code
    reader keeps its usage row out. The harness turn is a test turn (`is_test`)."""
    from tests.chatbot import test_outstanding_lane as outstanding_lane
    from tests.chatbot.test_engine import _envelope as _bare_envelope
    from tests.chatbot.test_entities_only_arm import MATCHES, PRODUCT_A_CODE
    from tests.chatbot.test_outstanding_lane import _resolve_services

    # The harness envelope is a LIVE turn; this one is the console's (`is_test`), the
    # shape `Envelope.dry_run` reads.
    monkeypatch.setattr(outstanding_lane, "_envelope", lambda: _bare_envelope(is_test=True))
    bodies: list[dict[str, Any]] = []
    inner = _resolve_services(MATCHES)

    def spy(body):
        bodies.append(dict(body))
        return inner.resolve_entity(body)

    services = type(inner)(access_types=inner.access_types, resolve_entity=spy, probe=inner.probe)
    qf = _parser_output(domain_hint=None, intent_hint=None, asks=[], entities=[_entity(PRODUCT_A_CODE, PRODUCT_A_CODE)])
    _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=PRODUCT_A_CODE, msg_id="ZZT-canon-eo-dry", matches=MATCHES,
        resolve_services=services,
    )

    assert bodies, "the arm resolved nothing"
    assert bodies[-1].get("dry_run") is True, bodies[-1]
    assert bodies[-1]["raw_tokens"] == [PRODUCT_A_CODE]


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
