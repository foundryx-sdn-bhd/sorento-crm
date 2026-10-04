"""Phase 2 RED tests for the attribute-first-asks lane, slices S1-S3 - work items B1,
B2, C4, E1, F1 (S1), D1-D3/F3 (S2), E2/F2 (S3).

`documentation/plans/chatbot/attribute-first-asks-acceptance-criteria.md` AC-1303,
AC-1304, AC-1315, AC-1316, AC-1318, AC-1319, AC-1320, AC-1323, AC-1326.
`documentation/plans/chatbot/PLAN-attribute-first-asks.md` - Shape, Work items
B1/B2/C4/E1/F1/D1-D3/F3/E2/F2.

Written BEFORE `app/services/chatbot/lanes/business/predicate.py` exists. An `ImportError`
naming that module is the expected red reason for the tests that import it directly;
`resolve_gate.py` / `gate.py` / `fetch.py` / `answer.py` are ported and DO exist today, so
tests against them fail on a wrong VALUE (missing dict key, wrong count, wrong text) rather
than an import - each test's own docstring says which.

Every module is imported LOCALLY inside its own test (not at file scope), so one missing
module can only fail the tests that actually depend on it, never the whole file's collection.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

import pytest

from tests.chatbot.set_reply import legacy_lines, one_line_header, row_blocks, row_codes  # noqa: F401
from tests._pg_fixture import blank_session

# S4 fixtures: reused, not rebuilt (same convention `test_complete_turn.py` and
# `test_s6_s7_integration.py` follow) - `stub_parser` / `stub_access` are the exact
# seams every other engine-level chatbot test stubs the LLM and access check at.
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name


# --------------------------------------------------------------------------- #
# B1 - AC-1303: derive_require is a pure map off the parser's own output.       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "parser_output, expected",
    [
        ({"intent_hint": "check_stock"}, {"stock": True}),
        ({"intent_hint": "check_incoming"}, {"incoming": True}),
        ({"intent_hint": "check_promotion"}, {"promotion": True}),
        (
            {
                "intent_hint": "check_product_attachment",
                "entities": [{"hint": "attachment_type", "raw": "cert"}],
            },
            {"certificate": True},
        ),
        (
            {
                "intent_hint": "check_product_attachment",
                "entities": [{"hint": "attachment_type", "raw": "photo"}],
            },
            {"attachment_type": "photo"},
        ),
        ({"intent_hint": "check_order"}, None),
        ({"intent_hint": None}, None),
    ],
)
def test_derive_require_maps_intents_and_entity_hints(parser_output, expected):
    from app.services.chatbot.lanes.business.predicate import derive_require

    assert derive_require(parser_output) == expected


# --------------------------------------------------------------------------- #
# Revive 26 Sep (AC-1303 / AC-1313 under main's v3 verdict): `requested_attributes` #
# is read BEFORE the intent, so a cert PHRASE there must split its scheme exactly   #
# as an attachment_type raw does, and a bare "certificate" attribute must not hide  #
# a scheme an attachment_type entity beside it carries.                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "parser_output, expected",
    [
        ({"requested_attributes": ["PPS cert"]}, {"certificate": {"scheme": "PPS"}}),
        ({"requested_attributes": ["sirim certificate"]}, {"certificate": {"scheme": "sirim"}}),
        ({"requested_attributes": ["cert"]}, {"certificate": True}),
        ({"requested_attributes": ["certificate"]}, {"certificate": True}),
        ({"requested_attributes": ["stock"]}, {"stock": True}),
        ({"requested_attributes": ["incoming"]}, {"incoming": True}),
        ({"requested_attributes": ["width"]}, None),
        (
            {
                "requested_attributes": ["certificate"],
                "intent_hint": "check_product_attachment",
                "entities": [{"hint": "attachment_type", "raw": "PPS cert"}],
            },
            {"certificate": {"scheme": "PPS"}},
        ),
    ],
)
def test_derive_require_reads_v3_requested_attributes(parser_output, expected):
    from app.services.chatbot.lanes.business.predicate import derive_require

    assert derive_require(parser_output) == expected


# --------------------------------------------------------------------------- #
# B1/D2 - AC-1303 (S2 half): a scheme word is split off the SAME attachment_type   #
# raw mechanically - no message-text matching beyond `_CERT_RE`, no DB lookup at   #
# parse time (the `certificate_scheme` lookup set is read later, server-side, by   #
# `_leg_certificate`).                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("pps cert", {"certificate": {"scheme": "pps"}}),
        ("watermark certificate", {"certificate": {"scheme": "watermark"}}),
        # Already asserted by S1 (kept here so the whole scheme-splitting contract
        # lives in one parametrize): the raw IS the cert word alone, nothing left
        # over to name a scheme.
        ("cert", {"certificate": True}),
    ],
)
def test_derive_require_extracts_a_scheme_word_from_the_cert_raw(raw, expected):
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [{"hint": "attachment_type", "raw": raw}],
    }
    assert derive_require(parser_output) == expected


# --------------------------------------------------------------------------- #
# Console pass 6 (R21, AC-1345): the head's entity reuse hands over an          #
# attachment_type raw already canonicalised to the AttachmentType NAME          #
# ("Certification"), not the customer's own word - every inflection of the     #
# cert word must still be read as the bare leg, never survive as a "scheme".   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Certification", {"certificate": True}),
        ("certification", {"certificate": True}),
        ("certificates", {"certificate": True}),
        ("certs", {"certificate": True}),
        ("CERTIFICATIONS", {"certificate": True}),
        ("PPS certification", {"certificate": {"scheme": "PPS"}}),
        # Regression: "sijil" (the Malay word) is already one of the covered
        # inflections (R21's own rule names it explicitly) - a raw made only of
        # it must stay the bare leg too, not fall through to the generic
        # attachment_type branch the way an unrelated document label would.
        ("sijil", {"certificate": True}),
    ],
)
def test_derive_require_treats_every_cert_inflection_as_the_bare_leg(raw, expected):
    """AC-1345/R21: measured live - the head's `entity_op: reuse` re-used the
    previous turn's attachment_type entity, canonicalised to the AttachmentType
    NAME ("Certification"), and `_cert_scheme_from_raw` only strips "cert",
    "certificate" and "sijil" verbatim - every OTHER inflection (certs,
    certificate-s, certification(s), any case) survives as a bogus "scheme",
    so "which tap has cert" -> "which water tap has cert" (clarify) -> "more"
    (which re-asks the previous certificate question) answered "The register
    has no Certification certificates."

    RED for "Certification"/"certification"/"certificates"/"certs"/
    "CERTIFICATIONS": `derive_require` returns `{"certificate": {"scheme":
    <the raw itself>}}`, not the bare `{"certificate": True}` this AC demands.
    "PPS certification" must still split "PPS" out as the scheme once
    "certification" itself is recognised as a bare cert word.
    """
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [{"hint": "attachment_type", "raw": raw}],
    }
    assert derive_require(parser_output) == expected


def test_derive_predicate_words_still_strips_a_reused_certification_raw():
    """AC-1345/R21: whatever `derive_require` decides the leg is, the reused
    "Certification" raw must still leave `derive_predicate_words`' own list -
    the described-set remainder must never be asked to bind an AttachmentType
    NAME as a class/product_type word.

    Green today - `derive_predicate_words` collects every attachment_type raw
    unconditionally (`_attachment_type_raws`), so this half of the contract was
    never broken; kept alongside the red cases above so the fix's own test
    file proves the whole turn, not only the leg shape.
    """
    from app.services.chatbot.lanes.business.predicate import derive_predicate_words, derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [{"hint": "attachment_type", "raw": "Certification"}],
    }
    require = derive_require(parser_output)
    assert "Certification" in derive_predicate_words(parser_output, require)


# --------------------------------------------------------------------------- #
# Owner regression (PR #833, R28, AC-1353): the head can drop the             #
# attachment_type entity entirely (console run 10 - one entity, raw "PPS",     #
# canonical_code null, normalised away to `entities: []`) - `derive_require`   #
# must still read the bare certificate leg off `user_goal` / the message text  #
# alone when there is NO attachment raw at all, the same fallback R4 already   #
# uses for a scheme-only raw.                                                  #
# --------------------------------------------------------------------------- #


def test_derive_require_reads_a_bare_certificate_leg_with_no_attachment_raw_at_all():
    """AC-1353/R28(1): intent `check_product_attachment`, `entities: []` (the
    head dropped the attachment_type entity entirely), `user_goal: "trying to
    find which item has PPS cert"` - must still yield the bare `{"certificate":
    True}` leg (the resolver then recovers "PPS" from the remainder, AC-1338).

    RED: `derive_require`'s `check_product_attachment` branch returns `None`
    immediately when `_attachment_type_raws` is empty (`if not raws: return
    None`) - it never reads `user_goal` at all when there is no raw, so the
    turn stays forward and the gate asks for an attachment type. Measured
    live: "which item has PPS cert" (console run 10) answered "Please provide
    the attachment type for the requested product".
    """
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [],
        "user_goal": "trying to find which item has PPS cert",
    }
    assert derive_require(parser_output) == {"certificate": True}


def test_derive_require_reads_a_bare_certificate_leg_off_the_message_text_fallback():
    """AC-1353/R28(2): the same no-attachment-raw shape, but `user_goal` is
    None - the fallback reads `message_text` instead (the same `message_text`
    keyword R4/AC-1328 already added for the scheme-only-raw case).

    RED for the SAME reason as the sibling test: `derive_require` returns
    `None` before it ever reaches the `user_goal or message_text` read, since
    that read sits inside the `if not raws:` early-return's dead code.
    """
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [],
        "user_goal": None,
    }
    assert derive_require(parser_output, message_text="which item has PPS cert") == {"certificate": True}


def test_derive_require_stays_forward_with_no_attachment_raw_and_no_cert_word():
    """AC-1353/R28(3) control: `entities: []` and neither `user_goal` nor the
    message text carries a cert word ("send me the photo of item") - the turn
    must stay `None` (forward, unchanged) exactly as it does today. Guards
    the R28 fix against reading EVERY no-attachment-raw check_product_
    attachment turn as a certificate question.

    Green today (and must stay green) - `derive_require` already returns
    `None` here, for the (currently) right structural reason; kept so the
    fix's own test file proves the negative case alongside the two positive
    ones above.
    """
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [],
        "user_goal": "trying to find the photo of item",
    }
    assert derive_require(parser_output, message_text="send me the photo of item") is None


def test_derive_predicate_words_strips_the_cert_word_found_in_the_message():
    """AC-1353/R28(4): with no attachment_type raw at all, `derive_predicate_
    words` has nothing of its own to strip (`_attachment_type_raws` is empty)
    - it must instead find and strip the cert word the MESSAGE TEXT itself
    carried ("cert" in "which item has PPS cert"), or the described-set reader
    sees "item pps cert" and reports it unrecognized.

    RED: `derive_predicate_words` takes no `message_text` keyword today -
    calling it with one raises `TypeError`, the accepted red shape for this
    AC (measured, no such parameter exists in the current signature).
    """
    from app.services.chatbot.lanes.business.predicate import derive_predicate_words, derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [],
        "user_goal": None,
    }
    require = derive_require(parser_output, message_text="which item has PPS cert")
    words = derive_predicate_words(parser_output, require, message_text="which item has PPS cert")
    assert "cert" in words, words


# --------------------------------------------------------------------------- #
# Reviewer round 4, should-fix (R33, AC-1358): with NO attachment_type raw at   #
# all, the R28 fallback tests `_CERTIFICATE_RE` against `user_goal`/            #
# `message_text` VERBATIM - `_CERTIFICATE_RE = re.compile(r"cert|certificate")` #
# is a bare substring match, so "certainly" and "concert" false-positive as a  #
# certificate question. And "sijil" (already a recognised bare-cert word       #
# everywhere a raw exists) is never tried at all in this no-raw branch, only   #
# `_CERTIFICATE_RE` is.                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message_text, expected",
    [
        # RED: "certainly" contains the substring "cert" - `_CERTIFICATE_RE`
        # has no word boundary, so this false-positives as a certificate ask.
        ("certainly, send me the drawing for the basin", None),
        # RED for the same reason: "concert" contains "cert".
        ("concert hall basin photo", None),
        # Green control: a genuine cert word, no false-positive substring risk.
        ("is this certified?", {"certificate": True}),
        ("which item has PPS cert", {"certificate": True}),
        # RED: "sijil" (Malay for "certificate") is a recognised bare-cert word
        # everywhere an attachment_type raw exists (`_BARE_CERT_WORDS`), but the
        # no-raw fallback branch only tests `_CERTIFICATE_RE` (English "cert"/
        # "certificate" only) against `user_goal`/`message_text` - "ada sijil
        # untuk basin?" matches neither, so `derive_require` returns `None`
        # instead of the bare certificate leg.
        ("ada sijil untuk basin?", {"certificate": True}),
    ],
)
def test_derive_require_does_not_false_positive_on_a_cert_shaped_substring(message_text, expected):
    """AC-1358/R33 (reviewer round 4, should-fix): `derive_require` with
    `entities: []`, `user_goal: None`, and `message_text` as the ONLY source -
    the no-attachment-raw fallback (R28) must recognise a genuine cert
    question ("is this certified?", "which item has PPS cert", the Malay
    "ada sijil untuk basin?") and must NOT fire on a word that merely
    contains the substring "cert" inside an unrelated word ("certainly",
    "concert").
    """
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [],
        "user_goal": None,
    }
    assert derive_require(parser_output, message_text=message_text) == expected


def test_derive_predicate_words_strips_the_bare_cert_word_without_punctuation():
    """AC-1358/R33: `derive_predicate_words(..., message_text="is this
    certified?")` must strip the bare word "certified" - WITHOUT the question
    mark - so the described-set reader never sees a stray "?" glued onto a
    class/spec word.

    RED: the no-raw branch splits `message_text` on whitespace and keeps
    whatever `_CERTIFICATE_RE` matched verbatim (`cleaned = word.strip()`,
    which strips surrounding WHITESPACE only, never punctuation) - the word
    survives as "certified?", not "certified".
    """
    from app.services.chatbot.lanes.business.predicate import derive_predicate_words, derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [],
        "user_goal": None,
    }
    require = derive_require(parser_output, message_text="is this certified?")
    words = derive_predicate_words(parser_output, require, message_text="is this certified?")
    assert "certified" in words, words


# --------------------------------------------------------------------------- #
# R4 (fix round 2, AC-1328): a scheme-only attachment_type raw ("PPS") carries  #
# no `_CERT_RE` word of its own (that regex names a BODY - cert/ikram/span/     #
# sirim/bomba/ms####/halal - never the bare register spelling), so             #
# `derive_require` must mirror `derive_routing`'s `is_cert`: ALSO certificate   #
# when the intent is check_product_attachment and `_CERTIFICATE_RE` matches    #
# `user_goal` or the message text.                                             #
# --------------------------------------------------------------------------- #


def test_derive_require_reads_a_scheme_only_attachment_raw_as_a_certificate():
    """AC-1328 (fix round 2, R4): "which item has PPS cert" - the parser emitted
    ONE `attachment_type` entity, raw "PPS" (no cert-word entity at all) - must
    still map to `{"certificate": {"scheme": "PPS"}}`, the same way
    `derive_routing`'s `is_cert` already treats a `check_product_attachment`
    intent whose `user_goal` matches `_CERTIFICATE_RE` as a certificate question
    regardless of what the attachment_type raw itself says.

    Two halves: `user_goal` carries the sentence (the parser's own field,
    `derive_routing` reads the exact same one), and - when `user_goal` is absent
    - the message text reaches `derive_require` through a `message_text` keyword,
    the same seam `resolve_entity_body` already has to hand (`_query_text(ctx)`).

    RED: `derive_require` only tests `_CERT_RE` against the raw itself ("PPS"
    matches none of cert/ikram/span/sirim/bomba/ms####/halal), so today it falls
    to the generic `{"attachment_type": "PPS"}` branch - the actual console
    finding ("I don't know 'PPS' as a product type").
    """
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [{"hint": "attachment_type", "raw": "PPS", "canonical_code": None}],
        "user_goal": "which item has PPS cert",
    }
    assert derive_require(parser_output) == {"certificate": {"scheme": "PPS"}}

    parser_output_no_goal = {
        "intent_hint": "check_product_attachment",
        "entities": [{"hint": "attachment_type", "raw": "PPS", "canonical_code": None}],
        "user_goal": None,
    }
    assert derive_require(
        parser_output_no_goal, message_text="which item has PPS cert"
    ) == {"certificate": {"scheme": "PPS"}}


# --------------------------------------------------------------------------- #
# B2 - AC-1304 / AC-1322: resolve_entity_body gains require + predicate_words ONLY   #
# when derive_require returns something, every other key stays byte-identical. #
# --------------------------------------------------------------------------- #


def _ctx_for_intent(intent_hint: str | None) -> dict[str, Any]:
    """Two ctx dicts identical except `intent_hint` - domain_hint is held CONSTANT so
    the "every other key equal" assertion is meaningful (a varying domain would also
    vary `body["domain"]`, which is not what this test is about)."""
    return {
        "text": {"message": {"message": {"text": "check"}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": intent_hint,
                "domain_hint": "inventory",
                "match_mode": "or",
                "access_levels": [],
                "entities": [{"raw": "basin", "hint": "product"}],
            }
        },
    }


def test_resolve_entity_body_adds_require_and_predicate_words_only_when_derived():
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    body_stock = resolve_entity_body(_ctx_for_intent("check_stock"))
    body_order = resolve_entity_body(_ctx_for_intent("check_order"))

    assert body_stock.get("require") == {"stock": True}
    assert body_stock.get("predicate_words") == ["stock"]
    assert "require" not in body_order
    assert "predicate_words" not in body_order

    # Fix round 9 on PR #833: `set_list_max` travels with the require, like its words.
    assert body_stock.get("set_list_max") == 50 and "set_list_max" not in body_order
    keys = (set(body_stock) | set(body_order)) - {"require", "predicate_words", "set_list_max"}
    for key in keys:
        assert body_stock.get(key) == body_order.get(key), key


# --------------------------------------------------------------------------- #
# Round 3 re-check (R25, AC-1349): `needs_tier_ask` fires only for a contact    #
# entitled to MORE than one tier, so a single-tier contact's resolve body       #
# sends the parser's own bare `access_levels` - never the tier gate's own      #
# RECOMPOSED names - and the promotion leg counts what the parser tokens       #
# translate to, which can be the wrong tier once a brand qualifies the code.    #
# --------------------------------------------------------------------------- #


def _tier_ctx(access_levels: list[str]) -> dict[str, Any]:
    return {
        "text": {"message": {"message": {"text": "which tap has promo"}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_promotion",
                "domain_hint": "promotion",
                "match_mode": "and",
                "access_levels": access_levels,
                "entities": [],
            }
        },
    }


def test_resolve_entity_body_uses_the_tier_gates_recomposed_access_levels():
    """AC-1349/R25(a): when the tier gate ran and produced any recomposed
    names, `resolve_entity_body`'s own `access_levels` must be exactly those
    names - never the parser's bare tokens - so a stated tier translates to
    the CORRECT brand-qualified code (`['Sorento Dealer'] -> {'dealer'}`)
    rather than whatever `_access_level_codes` makes of the raw parser token
    alone.

    RED today: `resolve_entity_body` accepts no `tier_gate` keyword at all -
    the call itself raises `TypeError: resolve_entity_body() got an
    unexpected keyword argument 'tier_gate'`, the accepted red shape for this
    AC (measured, no such parameter exists in the current signature).
    """
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    ctx = _tier_ctx(["dealer"])
    body = resolve_entity_body(ctx, tier_gate={"access_levels_recomposed": ["Sorento Dealer"]})
    assert body.get("access_levels") == ["Sorento Dealer"], body


def test_resolve_entity_body_falls_back_to_parser_tokens_with_no_tier_gate():
    """AC-1349/R25(b): `tier_gate=None` (the tier gate never ran on this turn)
    or a tier gate that ran but recomposed NOTHING (an empty list - no
    entitlement, no stated tier) must both keep today's behaviour: the
    parser's own `access_levels`, unchanged.

    RED today for the SAME reason as the sibling test: the keyword itself
    does not exist yet, so both calls raise `TypeError` before either
    fallback path is reached.
    """
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    ctx = _tier_ctx(["dealer"])

    body_none = resolve_entity_body(ctx, tier_gate=None)
    assert body_none.get("access_levels") == ["dealer"], body_none

    body_empty = resolve_entity_body(ctx, tier_gate={"access_levels_recomposed": []})
    assert body_empty.get("access_levels") == ["dealer"], body_empty


# --------------------------------------------------------------------------- #
# C4 - AC-1326: a qualifying predicate bypasses the ambiguity picker AND the    #
# product_attachment "subject did not resolve" block.                          #
# --------------------------------------------------------------------------- #


def _predicate_world() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Three `spec_search`-tier product matches for an unresolved raw ("water tap"),
    plus a qualifying `predicate` block - the exact shape AC-1326 describes."""
    uuids = [str(uuid.uuid4()) for _ in range(3)]
    matches = [
        {
            "uuid": u,
            "entity_type": "product",
            "canonical_code": f"ZZT-BIDET-{i}",
            "match_tier": "spec_search",
        }
        for i, u in enumerate(uuids)
    ]
    resolver = {
        "resolutions": [{"token": "water tap", "resolved": False, "matches": matches}],
        "unresolved_tokens": ["water tap"],
        "predicate": {
            "qualifying_total": 3,
            "truncated": False,
            "unrecognized_terms": [],
            "require": {"certificate": True},
        },
    }
    parser = {
        "domain_hint": "product_attachment",
        "entities": [
            {"hint": "product", "raw": "water tap"},
            {"hint": "attachment_type", "raw": "cert"},
        ],
    }
    return resolver, parser, uuids


def test_gate_bypasses_ambiguity_and_subject_block_when_predicate_present():
    from app.services.chatbot.lanes.business.gate import run_gate

    resolver, parser, uuids = _predicate_world()

    out = run_gate(dict(resolver), parser=parser, resolver=resolver)

    assert out["require_specific"] is False, out.get("gate_reason")
    assert out["gate_passed"] is True, out.get("gate_reason")
    product_entities = [e for e in out["compatible_entities"] if e["entity_type"] == "product"]
    assert {e["uuid"] for e in product_entities} == set(uuids)


# --------------------------------------------------------------------------- #
# E1 - AC-1315: a HAS turn's fetch passes every qualifying id to the SAME domain #
# tool.                                                                         #
#                                                                                #
# AC-1316 REVISED (console findings, 11 Sep 2026, committed 3779b32d6): "the    #
# tool's row limit stays at its default" - a page is five PRODUCTS, never five  #
# ROWS (measured live: `limit=5` on the stock tool cut the answer to 5          #
# warehouse rows spanning 4 products under a header saying "Showing 5"). The    #
# `assert args.get("limit") == 5` this test used to carry is the SUPERSEDED     #
# half of that AC; fixed here in the same round as the new                     #
# `test_has_fetch_pages_five_products_not_five_rows` below, which is the one    #
# that actually proves the id-slicing this world (3 ids, all shown) is too      #
# small to prove.                                                              #
# --------------------------------------------------------------------------- #


def test_has_turn_fetch_passes_all_ids_to_the_domain_tool():
    from app.services.chatbot.lanes.business import fetch
    from app.services.chatbot.lanes.business.gate import run_gate

    resolver, parser, uuids = _predicate_world()
    gate_out = run_gate(dict(resolver), parser=parser, resolver=resolver)

    args = fetch.entity_ids_transformer(
        {
            "entities": gate_out["compatible_entities"],
            "tool": "crm_master_product_attachments_list",
            "semantic_input": {"contact_id": "1", "space_id": "364817"},
            "predicate": resolver["predicate"],
        }
    )

    assert set(args.get("product_ids") or []) == set(uuids)
    # Reviewer B3 on PR #833: the row cap is the tool's maximum, never the product count.
    assert args.get("limit") == fetch.SET_ROW_LIMIT["crm_master_product_attachments_list"], args.get("limit")


def test_has_fetch_lists_products_not_rows():
    """AC-1315/AC-1316, no paging (owner ruling, 26 Sep 2026): with SEVEN qualifying
    product ids the fetch step carries ALL seven (a set that fits one message is listed
    in full), a named count (`top_n`) cuts the PRODUCT list, and `limit` - the tool's
    own ROW cap, rows can outnumber products - is never the product count: it is the
    tool's maximum (`fetch.SET_ROW_LIMIT`, reviewer B3 on PR #833; left at the tool's
    default 50 it cut a 40-product stock set to about 8). Both domains of the original
    console finding: the stock tool and the cert tool.
    """
    from app.services.chatbot.lanes.business import fetch

    ids = [str(uuid.uuid4()) for _ in range(7)]
    entities = [
        {"uuid": pid, "entity_type": "product", "canonical_code": f"ZZT-PAGE-{i}"}
        for i, pid in enumerate(ids)
    ]
    predicate = {"require": {"stock": True}, "qualifying_total": 7, "truncated": False, "unrecognized_terms": []}

    for tool, require in (
        ("crm_inventory_stock_balance_list", {"stock": True}),
        ("crm_master_product_attachments_list", {"certificate": True}),
    ):
        args = fetch.entity_ids_transformer(
            {
                "entities": entities,
                "tool": tool,
                "semantic_input": {"contact_id": "1", "space_id": "364817"},
                "predicate": {**predicate, "require": require},
            }
        )
        assert args.get("product_ids") == ids, (tool, args.get("product_ids"))
        assert args.get("limit") == fetch.SET_ROW_LIMIT[tool], (tool, args.get("limit"))

        named = fetch.entity_ids_transformer(
            {
                "entities": entities,
                "tool": tool,
                "semantic_input": {"contact_id": "1", "space_id": "364817", "top_n": 3},
                "predicate": {**predicate, "require": require},
            }
        )
        assert named.get("product_ids") == ids[:3], (tool, named.get("product_ids"))
        assert named.get("limit") == fetch.SET_ROW_LIMIT[tool], (tool, named.get("limit"))


# --------------------------------------------------------------------------- #
# F1 - AC-1319: qualifying_total=0 enters the existing miss flow, naming the    #
# described set and the predicate.                                             #
# --------------------------------------------------------------------------- #


def test_zero_qualifying_enters_the_existing_miss_flow_naming_the_set():
    """Full lane run (the `_run_lane` pattern from `tests/chatbot/test_warehouse_entity.py`,
    reproduced locally rather than imported across files): three products named
    "* BIDET", none certified, real resolver + real gate, then the render function
    `not_found_error_message` (`answer.py`) named at PLAN work item F1.

    Today `derive_require` is not wired into `resolve_entity_body` at all, so no
    `require` ever reaches the resolver for this turn, the certificate leg never runs,
    and the rendered text carries none of the predicate-shaped copy this AC demands -
    the right red reason for a full-pipeline gap, not a single missing function.
    """
    from app.models.base import set_company_scope
    from app.models.company import Company
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from app.services.chatbot.lanes.business import resolve_gate
    from app.services.chatbot.lanes.business.answer import not_found_error_message
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings
    from tests._pg_fixture import blank_session, unique_code

    codes = ["ACC-BIDET", "CAB-BIDET", "SRT-BIDET"]

    with blank_session() as db:
        company = Company(id=str(uuid.uuid4()), code=unique_code("ZZTBD")[:50], name=unique_code("ZZTBD"))
        db.add(company)
        db.flush()
        category = ProductCategory(
            id=str(uuid.uuid4()),
            category_code=unique_code("CAT")[:50],
            category_name="ZZT bidet category",
            company_id=company.id,
        )
        uom = UnitOfMeasure(
            id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id
        )
        db.add_all([category, uom])
        db.flush()

        for code in codes:
            db.add(
                Product(
                    id=str(uuid.uuid4()),
                    product_code=code,
                    product_name=code,
                    description="BIDET SPRAY SET",
                    category_id=category.id,
                    base_uom_id=uom.id,
                    list_price=10,
                    is_active=True,
                    company_id=company.id,
                )
            )
        db.commit()

        set_company_scope(db, frozenset({company.id}))

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(
                ResolveReferenceRequest(**payload), current_user=principal, db=db
            )

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        ctx = {
            "text": {"message": {"message": {"text": "which sorento bidet has cert"}}},
            "contact": {"id": "999"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_product_attachment",
                    "domain_hint": "product_attachment",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [
                        {"raw": "bidet", "hint": "product"},
                        {"raw": "cert", "hint": "attachment_type"},
                    ],
                }
            },
        }

        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    assert text.startswith("Couldn't find"), text
    assert "certificate" in text.lower(), text
    for code in codes:
        assert code in text, text
    assert "did you mean" in text.lower() or "escalate" in text.lower(), text


# --------------------------------------------------------------------------- #
# Fix round 3 - R16/AC-1340: the zero-qualifying miss copy never reads "a a    #
# match" - the subject is brand + product + category/product_type raws, then  #
# the predicate's class labels; when ALL of those are empty the sentence is    #
# "Couldn't find any product with <predicate>.", never "a a match".            #
# --------------------------------------------------------------------------- #


def test_zero_copy_names_the_category_raw_not_a_a_match():
    """AC-1340/R16(1): a category-hint entity ("bathroom accessory") must be the
    zero-copy's subject - the same standing brand/product raws already have.

    RED: the subject builder (`answer.py`'s `subject_words`) reads only
    `brand` + `hint == "product"` raws - a `hint == "category"` entity is
    invisible to it, so `subject_words` stays empty, `subject` falls to the
    literal "a match", and the sentence reads "Couldn't find a a match with
    stock." - never naming "bathroom accessory" at all.
    """
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    parser = {
        "domain_hint": "product_query",
        "entities": [{"raw": "bathroom accessory", "hint": "category"}],
    }
    gate = {
        "gate_passed": True,
        "predicate": {
            "qualifying_total": 0,
            "unrecognized_terms": [],
            "require": {"stock": True},
            "class_labels": ["Bathroom Accessory"],
        },
    }
    msg = not_found_error_message({}, parser=parser, resolved={}, gate=gate)
    text = (msg.get("escalate_message") or "").strip()

    assert "a a match" not in text.lower(), text
    assert "a match" not in text.lower(), text
    assert text.startswith("Couldn't find a bathroom accessory with stock."), text


def test_zero_copy_says_any_product_when_nothing_names_the_subject():
    """AC-1340/R16(2): no brand/product/category raw AND no `class_labels` at
    all - the sentence must read "Couldn't find any product with <predicate>.",
    never "Couldn't find a a match with <predicate>.".

    RED: `subject_words` stays empty (as in the category case above) and
    falls to the literal "a match" regardless - the sentence reads "Couldn't
    find a a match with stock.", never "any product".
    """
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    parser = {"domain_hint": "product_query", "entities": []}
    gate = {
        "gate_passed": True,
        "predicate": {
            "qualifying_total": 0,
            "unrecognized_terms": [],
            "require": {"stock": True},
            "class_labels": [],
        },
    }
    msg = not_found_error_message({}, parser=parser, resolved={}, gate=gate)
    text = (msg.get("escalate_message") or "").strip()

    assert "a a match" not in text.lower(), text
    assert "a match" not in text.lower(), text
    assert text.startswith("Couldn't find any product with"), text


# --------------------------------------------------------------------------- #
# F3 - AC-1321 (S2): a scheme miss names the schemes on file, not a generic "no  #
# certificate matched these".                                                   #
# --------------------------------------------------------------------------- #


def test_scheme_miss_reply_names_the_schemes_on_file():
    """AC-1321: "which item has watermark cert" against a register that holds PPS
    and SPAN (and an EMPTY `certificate_scheme` lookup set, so "watermark" resolves
    to nothing) must answer "no watermark certificates" and name both schemes on
    file, never the generic zero-qualifying miss.

    Full-pipeline red, deliberately: TODAY `derive_require` has not learned to split
    a scheme word off the SAME attachment_type raw (work item B1/D2 above) - "watermark
    cert" maps to a bare `{"certificate": True}`, so the certificate leg runs
    UNSCOPED, both seeded certified products qualify, and the reply this AC describes
    never renders at all (neither "no watermark certificates" nor "PPS"/"SPAN" appear
    anywhere in the text) - the correct red reason for a whole-pipeline gap spanning
    B1, D2 and the scheme-miss copy, not a single missing function.
    """
    from app.models.base import set_company_scope
    from app.models.certificate import Certificate, CertificateProduct
    from app.models.company import Company
    from app.models.lookup import LookupSet
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from app.services.chatbot.lanes.business import resolve_gate
    from app.services.chatbot.lanes.business.answer import not_found_error_message
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings
    from tests._pg_fixture import blank_session, unique_code

    with blank_session() as db:
        company = Company(id=str(uuid.uuid4()), code=unique_code("ZZTSC")[:50], name=unique_code("ZZTSC"))
        db.add(company)
        db.flush()
        category = ProductCategory(
            id=str(uuid.uuid4()),
            category_code=unique_code("CAT")[:50],
            category_name="ZZT scheme category",
            company_id=company.id,
        )
        uom = UnitOfMeasure(
            id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id
        )
        db.add_all([category, uom])
        db.flush()

        # D3: the migration ships the set EMPTY - the owner enters options later.
        db.add(
            LookupSet(
                id=str(uuid.uuid4()), tenant_id=None, set_key="certificate_scheme",
                name="Certificate Scheme", is_active=True,
            )
        )
        db.flush()

        for code, scheme in (("ZZT-CERT-PPS", "PPS"), ("ZZT-CERT-SPAN", "SPAN")):
            product = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=code,
                description="ZZT CERTIFIED ITEM",
                category_id=category.id,
                base_uom_id=uom.id,
                list_price=10,
                is_active=True,
                company_id=company.id,
            )
            db.add(product)
            db.flush()
            cert = Certificate(
                id=str(uuid.uuid4()),
                scheme=scheme,
                certificate_number=unique_code("CERTNO")[:120],
                status="active",
                company_id=company.id,
            )
            db.add(cert)
            db.flush()
            db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=product.id))
        db.commit()

        set_company_scope(db, frozenset({company.id}))

        def resolve_entity(body: dict) -> dict:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(
                ResolveReferenceRequest(**payload), current_user=principal, db=db
            )

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        ctx = {
            "text": {"message": {"message": {"text": "which item has watermark cert"}}},
            "contact": {"id": "999"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_product_attachment",
                    "domain_hint": "product_attachment",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [{"raw": "watermark cert", "hint": "attachment_type"}],
                }
            },
        }

        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    assert "no watermark certificates" in text.lower(), text
    assert "PPS" in text, text
    assert "SPAN" in text, text


# --------------------------------------------------------------------------- #
# S3 - AC-1316, AC-1318, AC-1320, AC-1323 (work items E2, F2).
#
# Written BEFORE `answer.build_set_header` / `answer.set_noun_for` exist. Every full
# lane-run test below is expected to fail for a WHOLE-PIPELINE reason, spelled out in
# its own docstring, the same convention `test_zero_qualifying_enters_the_existing_
# miss_flow_naming_the_set` (S1) and `test_scheme_miss_reply_names_the_schemes_on_file`
# (S2) above use: `answer.build_set_header` does not exist (ImportError on the two
# direct unit tests), AND `lanes/business/__init__.py::run_fetch`'s own trigger dict
# (built at "the read", ~line 327) never reads `gate.get("predicate")` at all - only
# `fetch.entity_ids_transformer`'s OWN `trig.get("predicate")` branch honours it
# (S1's own `test_has_turn_fetch_passes_all_ids_to_the_domain_tool_with_limit_5` proves
# the transformer alone, by calling it directly rather than through `run_fetch`). So
# through the real production seam a HAS turn's fetch never learns `require` /
# `qualifying_total` at all today: no `limit=5` is ever applied, and there is nothing
# for a header to be built FROM even once `build_set_header` exists. Both gaps are S3's
# own E2 work, not a separate slice's: `qualifying_total` and the actually-shown count
# must reconcile in the SAME header line, so threading the predicate through
# `run_fetch`'s trigger is what makes the header possible at all.
#
# CONTRACT CONTRADICTION (see `test_unknown_term_clarifies_with_nearest_names`'s own
# docstring for the full measurement): a bare CLASS word with no product entity
# ("which tap has cert") does not actually bind to class "Tap" anywhere in the current
# pipeline - `resolve_product_set` counts EVERY active product satisfying the `require`
# leg when neither `product_ids` nor a `filter_specs` clause describes the set, which is
# what happens here. The cert/stock counts the tests below assert (7, 3, 1, 2) are
# therefore right for the WRONG reason - every product this suite seeds happens to be a
# class-Tap product, so "no class filter" and "class = Tap" are indistinguishable. This
# is a real gap in AC-1306/C2 (S1), surfaced here because S3's header text is the first
# place a class NOUN is ever read back to the customer; it is not this slice's to fix,
# but the coder should know the qualifying_total these tests exercise is not proof the
# class scoping itself works.
# --------------------------------------------------------------------------- #


def _seed_registry(db) -> None:
    """The class / product_type vocabulary the described-set binding reads
    (`product_spec_search.filter_specs` -> `ProductSpecifications.values`), seeded
    exactly as `tests/test_product_predicate_service.py`'s own fixture does. No custom
    company is created and no `company_id` is ever passed below - every owned row relies
    on the `before_insert` auto-stamp against the single-company default test scope
    (`tests/conftest.py::_default_company_scope_for_tests`, Sorento), the same
    convention that file's fixture already uses successfully."""
    from app.services.product_class_signal import backfill_category_signals
    from app.services.product_spec_registry import seed_spec_registry

    backfill_category_signals(db)
    seed_spec_registry(db)


def _seed_category_and_uom(db) -> tuple[str, str]:
    from app.models.product import ProductCategory, UnitOfMeasure
    from tests._pg_fixture import unique_code

    category = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT category"
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:20], uom_name="Each")
    db.add_all([category, uom])
    db.flush()
    return category.id, uom.id


def _tap_product(db, *, category_id: str, uom_id: str, code: str | None = None):
    """A product whose DESCRIPTION contains the class-Tap trigger word ("TAP"), so
    `derive_for_code` - the same real spec-derivation pipeline `resolve_product_set`
    reads through `ProductSpecifications.values["class"]` - binds it to class "Tap"
    with no hand-built spec row (`product_spec_derivation.py`'s `("TAP", "Tap")` rule)."""
    from app.models.product import Product
    from app.services.product_spec_derivation import derive_for_code
    from tests._pg_fixture import unique_code

    code = code or unique_code("ZZTAP")[:50]
    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=code,
        description=f"{code} CHROME BASIN TAP",
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=10,
        is_active=True,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


def _basin_product(db, *, category_id: str, uom_id: str, code: str | None = None):
    """A class-Wash-Basin contrast product - the class word must be the description's
    TRAILING noun (`product_spec_derivation._class_from_description` only matches the
    tail: "500MM" after "WASH BASIN" would break the match), so this carries no
    dimension suffix, unlike `_tap_product`'s own description."""
    from app.models.product import Product
    from app.services.product_spec_derivation import derive_for_code
    from tests._pg_fixture import unique_code

    code = code or unique_code("ZZBSN")[:50]
    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=code,
        description=f"{code} WHITE WASH BASIN",
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=10,
        is_active=True,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


def _certificate_for(db, *, product_id: str, valid_until=None, scheme: str = "ZZT-CERT"):
    from app.models.certificate import Certificate, CertificateProduct, CertificateRevision
    from tests._pg_fixture import unique_code

    cert = Certificate(
        id=str(uuid.uuid4()),
        scheme=scheme,
        certificate_number=unique_code("CERTNO")[:60],
        status="active",
    )
    db.add(cert)
    db.flush()
    if valid_until is not None:
        rev = CertificateRevision(
            id=str(uuid.uuid4()), certificate_id=cert.id, revision_no=1, valid_until=valid_until
        )
        db.add(rev)
        db.flush()
        cert.current_revision_id = rev.id
        db.flush()
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=product_id))
    db.flush()
    return cert


def _warehouse(db):
    from app.models.inventory import Warehouse
    from tests._pg_fixture import unique_code

    row = Warehouse(
        id=str(uuid.uuid4()),
        warehouse_code=unique_code("WH")[:50],
        warehouse_name="ZZT Warehouse",
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _stock_for(db, *, product_id: str, warehouse_id: str, on_hand: int = 10):
    from app.models.inventory import Stock

    row = Stock(
        id=str(uuid.uuid4()),
        product_id=product_id,
        warehouse_id=warehouse_id,
        quantity_on_hand=on_hand,
        quantity_reserved=0,
    )
    db.add(row)
    db.flush()
    return row


def _run_has_lane(db, ctx: dict[str, Any], *, fake_call_tool) -> tuple[dict[str, Any], dict[str, Any]]:
    """resolve -> gate -> fetch, the real production seams (`resolve_gate.run` then
    `lanes.business.run_fetch`), with only the MCP call stubbed. Returns
    `(resolve_gate_output, fetch_fragment)`."""
    from app.services.chatbot.lanes import business
    from app.services.chatbot.lanes.business import resolve_gate
    from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings

    def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        payload = {**body, "spec_fallback": False, "understand_phrase": False}
        principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
        return resolve_reference_post(ResolveReferenceRequest(**payload), current_user=principal, db=db)

    services = ResolveGateServices(
        access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
    )
    out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")

    fetch_services = FetchServices(mcp_call=fake_call_tool)
    fragment = business.run_fetch(out, services=fetch_services, dry_run=False, space_id="364817")
    return out, fragment


def _cert_fake_call_tool(db):
    """Mirrors `sorento_crm_mcp.presenters._product_attachments`' field labels for a
    certificate-bearing row (Product Code / Attachment Type / File Name / Certificate
    Number / Valid Until / Validity), over the seeded register - no MCP server, no
    network."""
    import json

    from app.models.certificate import Certificate, CertificateProduct, CertificateRevision
    from app.models.product import Product

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        product_ids = list(args.get("product_ids") or [])
        rows = (
            db.query(Product, Certificate, CertificateRevision)
            .join(CertificateProduct, CertificateProduct.product_id == Product.id)
            .join(Certificate, Certificate.id == CertificateProduct.certificate_id)
            .outerjoin(CertificateRevision, CertificateRevision.id == Certificate.current_revision_id)
            .filter(Product.id.in_(product_ids))
            .order_by(Product.product_code)
            .all()
        )
        limit = args.get("limit")
        if isinstance(limit, int):
            rows = rows[:limit]
        items = []
        for product, cert, revision in rows:
            valid_until = revision.valid_until if revision is not None else None
            expired = bool(valid_until and valid_until < date.today())
            items.append(
                {
                    "title": product.product_code,
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": product.product_code},
                        {"key": "attachment_type", "label": "Attachment Type", "value": "Certification"},
                        {"key": "file_name", "label": "File Name", "value": f"{product.product_code}.pdf"},
                        {
                            "key": "certificate_number",
                            "label": "Certificate Number",
                            "value": cert.certificate_number,
                        },
                        {
                            "key": "valid_until",
                            "label": "Valid Until",
                            "value": valid_until.isoformat() if valid_until else None,
                        },
                        {"key": "validity", "label": "Validity", "value": "Expired" if expired else "Valid"},
                    ],
                    "flags": {"expired": expired},
                }
            )
        return json.dumps(
            {
                "result_type": "product_attachments",
                "intro": "I have attached the file(s) below.",
                "items": items,
                "has_result": bool(items),
            }
        )

    return fake_call_tool


def _stock_fake_call_tool(db):
    """Mirrors `sorento_crm_mcp.presenters._stock`'s field labels (Product Code /
    Warehouse / System Location / Quantity On Hand); never emits a Sellable /
    Outstanding field, matching a contact with no `inventory.sellable` field reveal."""
    import json

    from app.models.inventory import Stock, Warehouse
    from app.models.product import Product

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        product_ids = list(args.get("product_ids") or [])
        rows = (
            db.query(Product, Stock, Warehouse)
            .join(Stock, Stock.product_id == Product.id)
            .join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .filter(Product.id.in_(product_ids))
            .order_by(Product.product_code)
            .all()
        )
        items = [
            {
                "title": product.product_code,
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": product.product_code},
                    {"key": "warehouse", "label": "Warehouse", "value": warehouse.warehouse_name},
                    {"key": "system_location", "label": "System Location", "value": warehouse.warehouse_code},
                    {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": stock.quantity_on_hand},
                ],
                "flags": {},
            }
            for product, stock, warehouse in rows
        ]
        return json.dumps(
            {
                "result_type": "stock",
                "intro": "Stock details found for the requested products.",
                "items": items,
                "has_result": bool(items),
            }
        )

    return fake_call_tool


_ITEM_NUM_RE = re.compile(r"^\d+\.\s+")


def _block_for(text: str, code: str) -> str:
    """The item paragraph naming `code` ("*Product Code:* <code>" through the next
    blank line), position number stripped - two replies numbering the SAME product
    differently (1 vs 2 items on the page) must not fail this on the number alone."""
    for chunk in text.split("\n\n"):
        # Round 4 R3: a set row names its code on line 1, "N. <name> (<code>)".
        if f"*Product Code:* {code}" in chunk or chunk.strip().split("\n")[0].endswith(f"({code})"):
            lines = chunk.strip().split("\n")
            lines[0] = _ITEM_NUM_RE.sub("", lines[0])
            return "\n".join(lines)
    raise AssertionError(f"no item block for {code!r} in reply: {text!r}")


def _cert_ctx(text: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "text": {"message": {"message": {"text": text}}},
        "contact": {"id": "999"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_product_attachment",
                "domain_hint": "product_attachment",
                "match_mode": "or",
                "access_levels": [],
                "entities": entities,
            }
        },
    }


@pytest.mark.parametrize(
    "qualifying_total, shown, set_noun, require, expected",
    [
        (
            1256,
            20,
            "taps",
            {"certificate": True},
            "Here's what you want: taps with certificates (1,256, showing 1 to 20)",
        ),
        (3, 3, "taps", {"certificate": True}, "Here's what you want: taps with certificates (3)"),
        (1, 1, "tap", {"certificate": True}, "Here's what you want: tap with certificates (1)"),
        (7, 7, "taps", {"stock": True}, "Here's what you want: taps with stock (7)"),
        (2, 2, "taps", {"attachment_type": "Product Photos"}, "Here's what you want: taps with product photos (2)"),
        (4, 4, "sinks", {"incoming": True}, "Here's what you want: sinks with incoming stock (4)"),
        (4, 4, "sinks", {"promotion": True}, "Here's what you want: sinks with a promotion (4)"),
        (
            9,
            9,
            "taps",
            {"certificate": True, "stock": True},
            "Here's what you want: taps with certificates and stock (9)",
        ),
    ],
)
def test_build_set_header_strings(qualifying_total, shown, set_noun, require, expected):
    """AC-1316 (S3, work item E2): the header line, as a pure string function.

    RED: `answer.build_set_header` does not exist - `ImportError`.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    answers use the product-code rows under one intro line, so the header is the
    product-code answer's own intro with the described set and its count in place of
    "the requested products", never "N noun have leg.".
    """
    from app.services.chatbot.lanes.business.answer import build_set_header

    assert build_set_header(qualifying_total, shown, set_noun, require) == expected


@pytest.mark.parametrize(
    "class_labels, expected",
    [
        (["Tap"], "taps"),
        (["Wash Basin"], "wash basins"),
        (["Water Closet"], "water closets"),
        ([], "products"),
        (["Tap", "Shower"], "products"),
    ],
)
def test_set_noun_for(class_labels, expected):
    """AC-1316 (S3, work item E2): the header's noun, off the described set's class
    label(s) - two classes fall back to the generic "products" (D5's set answer has no
    single noun to say).

    RED: `answer.set_noun_for` does not exist - `ImportError`.
    """
    from app.services.chatbot.lanes.business.answer import set_noun_for

    assert set_noun_for(class_labels) == expected


def test_set_answer_carries_the_header_and_lists_every_product():
    """AC-1316, no paging (owner ruling, 26 Sep 2026): a HAS turn's reply opens with
    "<qualifying_total> <set noun> have <predicate noun>." before the existing
    certificate block, and a set that fits one message lists EVERY product - seven
    certified class-Tap products, no explicit product entity at all (the described set
    comes purely from the class binding on "which tap has cert", per AC-1306/C2).
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        for _ in range(7):
            product = _tap_product(db, category_id=category_id, uom_id=uom_id)
            _certificate_for(db, product_id=product.id)
        db.commit()

        ctx = _cert_ctx(
            "which tap has cert", [{"raw": "cert", "hint": "attachment_type"}]
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line naming the described set and
    # its count, never "N noun have leg.".
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: taps with certificates (7)", reply
    # Round 7 on PR #833 (owner hand test, item 7): certificate rows keep the normal
    # attachment structure, led by "*Product Code:*".
    from tests.chatbot.set_reply import full_row_codes

    assert len(full_row_codes(reply)) == 7, reply


def test_set_answer_is_scoped_to_the_class_word():
    """AC-1306 + AC-1316 (captain follow-up, after the S3 red-test report): a HAS turn's
    described set must be scoped to the class word in the message, not to "every
    certified product regardless of class" - the gap
    `tests/test_resolve_predicate.py::test_class_word_in_query_scopes_the_described_set`
    pins directly against the resolver. World: two certified class-Tap products AND one
    certified class-Wash-Basin product, so a reply naming "2 taps" and omitting the
    basin code is proof of real scoping, not an all-Tap world's coincidence (the
    accidental-pass the S3 tester's own module-section banner comment flags for the
    siblings of this test).

    RED: the header line does not exist yet (same E2 gap as every other test in this
    module), so `lines[0]` fails first; AND even once a header renders, today's
    `qualifying_total` would be 3 (every certified product, unscoped - see the
    resolver-level test above for the root cause), not 2, and the basin code would
    appear in the reply's product list rather than being correctly excluded.
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        tap1 = _tap_product(db, category_id=category_id, uom_id=uom_id)
        tap2 = _tap_product(db, category_id=category_id, uom_id=uom_id)
        basin = _basin_product(db, category_id=category_id, uom_id=uom_id)
        for product in (tap1, tap2, basin):
            _certificate_for(db, product_id=product.id)
        db.commit()

        ctx = _cert_ctx(
            "which tap has cert", [{"raw": "cert", "hint": "attachment_type"}]
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""
        basin_code = basin.product_code

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: taps with certificates (2)", reply
    assert basin_code not in reply, reply


def test_set_answer_header_omits_showing_when_all_fit():
    """AC-1316: when `qualifying_total` is 5 or fewer the header omits "Showing" -
    three certified class-Tap products, all three render.

    RED: the header line is entirely absent from today's reply (see the module-section
    docstring above).
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        for _ in range(3):
            product = _tap_product(db, category_id=category_id, uom_id=uom_id)
            _certificate_for(db, product_id=product.id)
        db.commit()

        ctx = _cert_ctx(
            "which tap has cert", [{"raw": "cert", "hint": "attachment_type"}]
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: taps with certificates (3)", reply
    assert "Showing" not in reply, reply


def test_expired_only_certificate_still_counts_and_is_flagged():
    """AC-1318: "has cert" counts any ACTIVE register certificate regardless of date
    validity (D6) - one class-Tap product whose only certificate's current revision
    expired years ago still qualifies, and the render keeps flagging it exactly as
    today (`fetch._item_line`'s existing "(EXPIRED)" flag and the presenter's own
    "Validity: Expired" field - neither is new in this slice).

    RED: only the header line is missing (a single qualifying product never hits the
    limit-wiring gap the other tests above name), so this is the cleanest single-reason
    red of the set - `lines[0]` is the tool's `intro`, not "1 tap has certificates.".
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        product = _tap_product(db, category_id=category_id, uom_id=uom_id)
        _certificate_for(db, product_id=product.id, valid_until=date(2020, 1, 1))
        db.commit()

        ctx = _cert_ctx(
            "which tap has cert", [{"raw": "cert", "hint": "attachment_type"}]
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: taps with certificates (1)", reply
    # Round 7 on PR #833: the normal attachment row, with its own expiry flag.
    assert "*Validity:* Expired" in reply and "*(EXPIRED)*" in reply, reply


def test_unknown_term_clarifies_with_nearest_names():
    """AC-1320 (work item F2): a HAS turn whose free term binds NO class /
    product_type / brand (AC-1301's own "water tap" example - "tap" alone is a class
    keyword, but the two-word phrase as a whole is not) must clarify with the nearest
    vocabulary, never answer the generic miss copy.

    RED, and NOT for the reason this docstring first guessed (kept below as a
    CONTRACT CONTRADICTION for the coder, per the tester brief): `unrecognized_terms`
    is EMPTY in production for this turn, not `["water tap"]`. Measured directly
    (`resolve_gate.run(...)["resolved"]["predicate"]` == `{"require": {"certificate":
    True}, "qualifying_total": 0, "truncated": False, "unrecognized_terms": []}`).
    Root cause: `app/api/v1/system/references.py`'s require branch calls
    `derive_search_inputs(db, query_text, specs=[], free_terms=[], ...)` and DELIBERATELY
    drops the function's own returned free terms (its comment: "merging that in fed the
    raw sentence to filter_specs's honesty check... reporting 'which kitchen sinks have
    stock' itself as unrecognized"), then calls `resolve_product_set(..., free_terms=
    payload.free_terms)` - and `resolve_entity_body` never populates a `free_terms` key
    on the resolve body at all, so this is `[]` on every chatbot turn. A bare class word
    like "tap" is bound only through `filter_specs`'s OWN `free_terms` argument
    (`resolve_classes_for_term`, exercised by `test_filter_specs_resolves_a_class_term`
    directly) - which this call path never reaches either. So for THIS turn `specs=[]`
    AND `free_terms=[]`, `described_given` is False, and `resolve_product_set` falls to
    its "nothing was given to describe the set" branch, which - for a `require`-only
    call with no described-set input at all - counts every ACTIVE product satisfying the
    leg with NO restriction (measured: `product_predicate_service.py` ~line 292-303,
    the `described = []` case skips the `query.filter(...)` call entirely). That is what
    also makes `test_set_answer_carries_the_header_and_shows_five` and its siblings above
    "pass" their product count today - every certified/stocked product THIS suite seeds
    happens to be a class-Tap product, so "count everything, no class filter" and "count
    only taps" are indistinguishable there. Here the world has ONLY class-Tap products
    too (by design - AC-1320 needs "tap" in the vocabulary for its "Did you mean"
    suggestion), so `qualifying_total` is 0 regardless, and the actual reply is
    "Couldn't find a water tap with a certificate. Would you like me to escalate to
    customer service team?" - AC-1319's S1 honest-zero copy, not AC-1320's. F2 cannot be
    "just" a new clarify-copy branch: it also has to make a bare class word reach
    `unrecognized_terms` at all (a content-word extraction over `query_text`, fed to
    `filter_specs`'s `free_terms` - NOT the raw derive_search_inputs echo the S1 comment
    above correctly refuses to use, which flags whole phrases like "which kitchen sinks
    have stock" as unrecognized whole-cloth).
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        _tap_product(db, category_id=category_id, uom_id=uom_id)
        _tap_product(db, category_id=category_id, uom_id=uom_id)
        db.commit()

        from app.services.chatbot.lanes.business import resolve_gate
        from app.services.chatbot.lanes.business.answer import not_found_error_message
        from app.services.chatbot.lanes.business.services import ResolveGateServices
        from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
        from app.config import settings

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(
                ResolveReferenceRequest(**payload), current_user=principal, db=db
            )

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        ctx = _cert_ctx(
            "which water tap has cert",
            [
                {"raw": "water tap", "hint": "product"},
                {"raw": "cert", "hint": "attachment_type"},
            ],
        )
        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    # Amended to fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"):
    # no product types are offered as lines either (the nearest and the most common labels
    # are retired); the word is the "Couldn't find" line of the one reply structure.
    assert text.startswith("Here's what you want: water tap\n\nCouldn't find: \"water tap\"."), text
    assert "•" not in text, text
    assert "Did you mean" not in text and "I don't know" not in text, text


def test_stock_set_answer_matches_forward_block_for_a_dealer():
    """AC-1323: field-reveal gating is unchanged between a forward turn and a HAS
    turn - a dealer contact with NO field reveals (`ctx["access"]` absent, so
    `include_sellable` never sets and the render never carries a Sellable /
    Outstanding field either way) sees the SAME stock block for the same product on
    both. Two class-Tap products with stock in one warehouse; the forward turn types
    a real code (predicate skipped per AC-1305, byte-identical to today), the HAS turn
    asks "which tap has stock" (predicate runs, both qualify).

    RED: the two blocks already match today (nothing in this slice touches the block
    body), so the only failing assertion is the header - the HAS reply's first line is
    the tool's own `intro`, never "2 taps have stock.".
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        p1 = _tap_product(db, category_id=category_id, uom_id=uom_id)
        p2 = _tap_product(db, category_id=category_id, uom_id=uom_id)
        warehouse = _warehouse(db)
        _stock_for(db, product_id=p1.id, warehouse_id=warehouse.id)
        _stock_for(db, product_id=p2.id, warehouse_id=warehouse.id)
        db.commit()

        fake_call_tool = _stock_fake_call_tool(db)

        ctx_forward = {
            "text": {"message": {"message": {"text": f"{p1.product_code} stock"}}},
            "contact": {"id": "998"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "inventory",
                    "match_mode": "and",
                    "access_levels": [],
                    "entities": [
                        {
                            "raw": p1.product_code,
                            "hint": "product",
                            "canonical_code": p1.product_code,
                            "confident": True,
                        }
                    ],
                }
            },
        }
        ctx_has = {
            "text": {"message": {"message": {"text": "which tap has stock"}}},
            "contact": {"id": "998"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "inventory",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [{"raw": "tap", "hint": "product"}],
                }
            },
        }

        out_forward, fragment_forward = _run_has_lane(db, ctx_forward, fake_call_tool=fake_call_tool)
        out_has, fragment_has = _run_has_lane(db, ctx_has, fake_call_tool=fake_call_tool)

        assert out_forward.get("_exit_kind") == "continue", out_forward.get("gate_reason")
        assert out_has.get("_exit_kind") == "continue", out_has.get("gate_reason")

        reply_forward = (fragment_forward.get("fetch") or {}).get("response") or ""
        reply_has = (fragment_has.get("fetch") or {}).get("response") or ""

    block_forward = _block_for(reply_forward, p1.product_code)
    block_has = _block_for(reply_has, p1.product_code)

    # Field reveal is the same either way. Round 4 R3 on PR #833: the set row is two lines
    # at most (name with code, then the facts the ask was about), so it says a SUBSET of
    # the forward block's fields and never a field the forward block does not reveal.
    def _fields(block: str) -> set[str]:
        return {m.strip().rstrip(",") for m in re.findall(r"\*[^*]+:\* [^,|\n]+", block)}

    assert _fields(block_has) and _fields(block_has) <= _fields(block_forward), (reply_forward, reply_has)
    assert "Sellable" not in reply_forward, reply_forward
    assert "Sellable" not in reply_has, reply_has
    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines_has = reply_has.splitlines()
    assert lines_has and lines_has[0] == "Here's what you want: taps with stock (2)", reply_has


# --------------------------------------------------------------------------- #
# Console fix round 2 (11 Sep 2026, PLAN-attribute-first-asks.md R2/R3/R6/R9/   #
# R10, AC-1327/AC-1329) - full lane runs, real resolver (`_run_has_lane`, the   #
# same seam S3's tests above use). Every world below seeds a WORD-token         #
# forward hit alongside the real certified/stocked set, so a picker built off   #
# the forward hit (rather than the predicate) is provably wrong, not merely     #
# untested.                                                                     #
# --------------------------------------------------------------------------- #


def _wash_basin_category(db):
    """A category whose CODE `backfill_category_signals` recognises as
    `<brand>-WB` (`product_class_signal.CLASS_SUFFIXES["WB"] == "Wash Basin"`),
    so `resolve_classes_for_term(db, "basin")` finds it through the category's
    OWN `search_synonyms` (`CLASS_SYNONYMS["Wash Basin"]` lists "basin"
    verbatim) - not only through `stored_class_labels`' whole-label match,
    which "basin" alone can never satisfy against the two-word label "Wash
    Basin". `_seed_category_and_uom`'s own generic `CAT...` code carries no
    such mapping, which is why a test that needs the bare word "basin" to
    resolve needs this category instead of that one.
    """
    from app.services.product_class_signal import backfill_category_signals
    from app.models.product import ProductCategory

    category = ProductCategory(
        id=str(uuid.uuid4()), category_code="ZZT-WB", category_name="ZZT Wash Basin"
    )
    db.add(category)
    db.flush()
    backfill_category_signals(db)
    db.refresh(category)
    return category


def test_set_answer_replaces_the_found_line_and_no_picker_forms():
    """AC-1327/AC-1326 (R2/R3, fix round 2): "which tap has cert" against a world
    with a genuine forward hit for the word token "tap" (two products whose CODE
    contains "TAP" as a plain substring, neither certified) must still answer the
    3-certified-tap SET, never a "Found: ..." + "Please choose" picker built off
    that forward hit.

    RED: nothing on the require path strips the "tap" token's own forward product
    matches once HAS has run (same gap `test_has_removes_word_token_product_
    matches_from_the_forward_result` pins at the resolver level) - the gate's own
    per-token ambiguity block (`gate.py`, `REQUIRE_SPECIFIC_DOMAINS`) only skips a
    group whose matches are ALL `spec_search` tier, and the "tap" resolution's two
    forward substring matches are tier "substring" - so it falls to `still_
    ambiguous` and the reply carries the "Found: ..." / "Please choose" picker
    text instead of the header this AC demands - measured directly: `gate_reason`
    is "'product_attachment' ambiguous (no single exact match); user must pick"
    and `_exit_kind` is "not_found", never "continue".
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)

        def _plain(code, name):
            from app.models.product import Product

            row = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=name,
                description=f"{name} DESCRIPTION",
                category_id=category_id,
                base_uom_id=uom_id,
                list_price=10,
                is_active=True,
            )
            db.add(row)
            db.flush()
            return row

        _plain("ZZT-COLD-TAP", "ZZT COLD TAP")
        _plain("ZZT-HOT-TAP", "ZZT HOT TAP")

        for _ in range(3):
            product = _tap_product(db, category_id=category_id, uom_id=uom_id)
            _certificate_for(db, product_id=product.id)
        db.commit()

        ctx = _cert_ctx(
            "which tap has cert",
            [
                {"hint": "category", "raw": "tap"},
                {"hint": "attachment_type", "raw": "cert", "canonical_code": "certificate"},
            ],
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: taps with certificates (3)", reply
    assert "Found:" not in reply, reply
    assert "Please choose" not in reply, reply
    assert "needs to be more specific" not in reply, reply


def test_brand_and_category_words_give_a_set_answer_not_a_picker():
    """AC-1327 (R3, fix round 2): "which sorento bidet has cert" - a brand word
    plus a category word, both forward-hitting NON-certified products (three
    bidets, one branded Sorento, none certified) - alongside a FOURTH, unrelated
    Sorento product whose derived spec `product_type` is "bidet" and which DOES
    carry a certificate - must answer the 1-qualifying SET, never keep the
    3-bidet picker the forward hits would otherwise build.
    """
    from app.models.product import Brand, Product
    from tests._pg_fixture import unique_code

    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)

        sorento = Brand(id=str(uuid.uuid4()), brand_code=unique_code("ZZT-SRT")[:20], brand_name="SORENTO")
        cabana = Brand(id=str(uuid.uuid4()), brand_code=unique_code("ZZT-CAB")[:20], brand_name="CABANA")
        db.add_all([sorento, cabana])
        db.flush()

        def _bidet(code, brand_id):
            row = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=code,
                description="BIDET SPRAY SET",
                category_id=category_id,
                base_uom_id=uom_id,
                brand_id=brand_id,
                list_price=10,
                is_active=True,
            )
            db.add(row)
            db.flush()
            return row

        _bidet("ACC-BIDET", cabana.id)
        _bidet("CABANA-BIDET", cabana.id)
        srt_bidet = _bidet("SRT-BIDET", sorento.id)

        cert_product = Product(
            id=str(uuid.uuid4()),
            product_code="SRTWT5875",
            product_name="SRTWT5875",
            description="SORENTO CHROME BIDET SPRAY",
            category_id=category_id,
            base_uom_id=uom_id,
            brand_id=sorento.id,
            list_price=10,
            is_active=True,
        )
        db.add(cert_product)
        db.flush()
        from app.services.product_spec_derivation import derive_for_code

        derive_for_code(db, "SRTWT5875")
        _certificate_for(db, product_id=cert_product.id)
        db.commit()

        ctx = _cert_ctx(
            "which sorento bidet has cert",
            [
                {"hint": "brand", "raw": "sorento"},
                {"hint": "category", "raw": "bidet"},
                {"hint": "attachment_type", "raw": "cert", "canonical_code": "certificate"},
            ],
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""
        cert_product_code = cert_product.product_code
        srt_bidet_code = srt_bidet.product_code

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line naming the described set
    # (brand and category both still named, inside the phrase).
    lines = legacy_lines(reply)
    # W2 (owner hand test round 2): the line leads with what was identified.
    assert lines and lines[0] == "Here's what you want: Sorento bidets with certificates (1)", reply
    assert lines[0].startswith("Here's what you want: Sorento"), reply
    assert cert_product_code in reply, reply
    assert "Please choose" not in reply, reply
    assert srt_bidet_code not in reply, reply


def test_unrecognised_label_clarifies_as_a_document_type():
    """AC-1329 (R6, fix round 2): "which basin has photo" against an EMPTY
    `attachment_type_alias` set must clarify "photo" as a DOCUMENT type, naming
    the product-facing AttachmentType names on file - never the product-type
    sentence ("I don't know 'photo' as a product type"), which answers the wrong
    question (the unrecognised word names a FILE LABEL, not a class/product_type
    word).
    """
    from app.models.product import ProductAttachment, UnitOfMeasure
    from app.models.resources import Attachment, AttachmentType
    from tests._pg_fixture import unique_code

    with blank_session() as db:
        _seed_registry(db)
        category = _wash_basin_category(db)
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:20], uom_name="Each")
        db.add(uom)
        db.flush()
        basin = _basin_product(db, category_id=category.id, uom_id=uom.id)
        attachment_type = AttachmentType(
            id=str(uuid.uuid4()),
            code="PRODUCT_PHOTOS",
            type_name="Product Photos",
            allowed_extensions="jpg,png",
        )
        db.add(attachment_type)
        db.flush()
        # AC-1329's product-facing rule: a type must appear in `product_attachments`
        # to be listed - linked to the basin here the same way `_linked_type` does
        # in `test_document_types_listed_are_product_facing_only`, or this type is
        # correctly excluded and the "Product Photos" assertion below fails for a
        # reason unrelated to what this test is actually about.
        attachment = Attachment(
            id=str(uuid.uuid4()),
            original_filename="basin-photo.jpg",
            stored_filename="basin-photo.jpg",
            file_path="https://cdn/basin-photo.jpg",
            attachment_type_id=attachment_type.id,
        )
        db.add(attachment)
        db.flush()
        db.add(
            ProductAttachment(id=str(uuid.uuid4()), product_id=basin.id, attachment_id=attachment.id)
        )
        db.commit()

        from app.services.chatbot.lanes.business import resolve_gate
        from app.services.chatbot.lanes.business.answer import not_found_error_message
        from app.services.chatbot.lanes.business.services import ResolveGateServices
        from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
        from app.config import settings

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(
                ResolveReferenceRequest(**payload), current_user=principal, db=db
            )

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        # ATTACHMENT-MULTI (tester re-run 2 Oct 2026): "photo" now RESOLVES on this leg (the
        # type's own name/description, product-facing types only), so the unrecognised label
        # is a word that names no type at all.
        ctx = _cert_ctx(
            "which basin has hologram",
            [
                {"hint": "category", "raw": "basin"},
                {"hint": "attachment_type", "raw": "hologram"},
            ],
        )
        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    assert "I don't know 'hologram' as a document type" in text, text
    assert "Types I know:" in text, text
    assert "Product Photos" in text, text
    assert "as a product type" not in text, text


# --------------------------------------------------------------------------- #
# Second live console pass (11 Sep 2026, PLAN-attribute-first-asks.md) - two    #
# polish items on top of the already-fixed R6/E2 work.                         #
# --------------------------------------------------------------------------- #


def test_document_types_listed_are_product_facing_only():
    """AC-1329 (R6 polish, second console pass): "Types I know:" must name only
    AttachmentType rows that actually appear in `product_attachments` - never
    the whole AttachmentType table. World: three product-facing types (Product
    Photos, Technical Specifications, Certification), each linked to the one
    seeded basin product through a real `product_attachments` row; two more
    types (Container Status, Complaint Document) exist in the table but carry
    NO product link at all - internal document classes a customer never asks
    about.

    RED: `_attachment_type_names_on_file` (`product_predicate_service.py`)
    reads every `AttachmentType.type_name` with no join to `product_attachments`
    at all, so both unlinked types leak into the reply - measured live: "which
    item has PPS cert" listed Complaint Document, Container Status, GRN, Portal
    Submission alongside the real product document classes.
    """
    from app.models.product import ProductAttachment, UnitOfMeasure
    from app.models.resources import Attachment, AttachmentType
    from tests._pg_fixture import unique_code

    with blank_session() as db:
        _seed_registry(db)
        category = _wash_basin_category(db)
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:20], uom_name="Each")
        db.add(uom)
        db.flush()
        basin = _basin_product(db, category_id=category.id, uom_id=uom.id)

        def _linked_type(code: str, name: str) -> None:
            at = AttachmentType(id=str(uuid.uuid4()), code=code, type_name=name, allowed_extensions="pdf")
            db.add(at)
            db.flush()
            attachment = Attachment(
                id=str(uuid.uuid4()),
                original_filename=f"{code}.pdf",
                stored_filename=f"{code}.pdf",
                file_path=f"https://cdn/{code}.pdf",
                attachment_type_id=at.id,
            )
            db.add(attachment)
            db.flush()
            db.add(
                ProductAttachment(id=str(uuid.uuid4()), product_id=basin.id, attachment_id=attachment.id)
            )
            db.flush()

        _linked_type("PRODUCT_PHOTOS", "Product Photos")
        _linked_type("TECH_SPEC", "Technical Specifications")
        _linked_type("CERTIFICATION", "Certification")

        # Unlinked - carried in the table, never attached to any product.
        db.add(
            AttachmentType(
                id=str(uuid.uuid4()),
                code="CONTAINER_STATUS",
                type_name="Container Status",
                allowed_extensions="pdf",
            )
        )
        db.add(
            AttachmentType(
                id=str(uuid.uuid4()),
                code="COMPLAINT_DOC",
                type_name="Complaint Document",
                allowed_extensions="pdf",
            )
        )
        db.commit()

        from app.services.chatbot.lanes.business import resolve_gate
        from app.services.chatbot.lanes.business.answer import not_found_error_message
        from app.services.chatbot.lanes.business.services import ResolveGateServices
        from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
        from app.config import settings

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(
                ResolveReferenceRequest(**payload), current_user=principal, db=db
            )

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        # ATTACHMENT-MULTI (tester re-run 2 Oct 2026): "photo" now RESOLVES on this leg (the
        # type's own name/description, product-facing types only), so the unrecognised label
        # is a word that names no type at all.
        ctx = _cert_ctx(
            "which basin has hologram",
            [
                {"hint": "category", "raw": "basin"},
                {"hint": "attachment_type", "raw": "hologram"},
            ],
        )
        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    assert "Product Photos" in text, text
    assert "Technical Specifications" in text, text
    assert "Certification" in text, text
    assert "Container Status" not in text, text
    assert "Complaint Document" not in text, text


# --------------------------------------------------------------------------- #
# Third console pass (11 Sep 2026, PLAN-attribute-first-asks.md R12, AC-1331)  #
# - a parser variant folds a two-word product description into ONE product    #
# entity ("Sorento bidet" -> token "Sorentobidet"), which never matches any    #
# real product code. The certificate leg still finds the real product through #
# the brand + class binding, but the reply took the miss copy anyway.         #
# --------------------------------------------------------------------------- #


def test_unresolved_word_token_is_the_description_not_a_miss():
    """AC-1331/R12 (third console pass): when `predicate.qualifying_total > 0`,
    an unresolved WORD token (the parser's single product entity "Sorento
    bidet", folded to "Sorentobidet" and matching no real code) must never be
    listed as a miss - it IS the described set's own description, and the set
    answer must render.

    World: one Sorento-branded, certified product whose description contains
    "BIDET" (`derive_for_code` reads `product_type: "bidet"` off it), plus two
    unrelated, uncertified products (a class-Tap and a class-Wash-Basin one) so
    the set answer naming exactly the one product is not a coincidence of an
    all-matching world.

    GREEN TODAY under this exact world, measured directly: `resolve_gate.run`
    exits `continue` (`gate_reason` "ok"), and the fetch renders "1 bidet has
    certificates." followed by the certificate block for ZZTWT5875 - never the
    "Couldn't find" / "Here's what you want: ... But no certificate matched
    these" combiner (`answer.py`'s `build_breakdown_msg`, reached only when the
    gate does NOT pass). AC-1326's own `predicate_bypass`
    (`isinstance(resolver.get("predicate"), dict)`) already short-circuits the
    ambiguity picker AND the product_attachment "subject did not resolve" block
    that would otherwise fail this turn on the unresolved "Sorentobidet" token,
    so this exact input shape does not reproduce R12's console finding here.
    Kept as the regression guard AC-1331 asks for, reported honestly per this
    file's own "CONTRACT CONTRADICTION" convention (see the S3 banner earlier
    in this file) rather than forced red by inventing a different world; if the
    live turn's actual parser output carried something this construction does
    not (a different domain_hint, a missing `canonical_code`, an additional
    entity), that is this test's own finding to hand back, not a reason to
    keep guessing inputs until it breaks.
    """
    from app.models.product import Brand, Product
    from app.services.product_spec_derivation import derive_for_code
    from tests._pg_fixture import unique_code

    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)

        sorento = Brand(id=str(uuid.uuid4()), brand_code=unique_code("ZZT-SRT")[:20], brand_name="SORENTO")
        db.add(sorento)
        db.flush()

        target = Product(
            id=str(uuid.uuid4()),
            product_code="ZZTWT5875",
            product_name="ZZTWT5875",
            description="SORENTO CHROME BIDET SPRAY SET",
            category_id=category_id,
            base_uom_id=uom_id,
            brand_id=sorento.id,
            list_price=10,
            is_active=True,
        )
        db.add(target)
        db.flush()
        derive_for_code(db, "ZZTWT5875")
        _certificate_for(db, product_id=target.id)

        # Two unrelated, UNCERTIFIED products - so a reply naming only ZZTWT5875
        # is proof of real scoping, not a coincidence of an all-matching world.
        _tap_product(db, category_id=category_id, uom_id=uom_id)
        _basin_product(db, category_id=category_id, uom_id=uom_id)
        db.commit()

        ctx = _cert_ctx(
            "which sorento bidet has cert",
            [
                {"hint": "product", "raw": "Sorento bidet"},
                {"hint": "attachment_type", "raw": "cert", "canonical_code": "certificate"},
            ],
        )
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line naming the described set
    # and its count (1) rather than "1 <noun> has <leg>.".
    lines = legacy_lines(reply)
    assert lines and "(1)" in lines[0], reply
    # Amended to fix round 9 on PR #833: the intro names its leg once.
    assert lines[0].startswith("Here's what you want: ") and lines[0].count("certificates") == 1, reply
    assert "ZZTWT5875" in reply, reply
    assert "Couldn't find" not in reply, reply
    assert "no certificate matched" not in reply, reply


def test_set_header_names_the_scheme():
    """AC-1316 (second console pass): a scheme-narrowed certificate require must
    name the SCHEME in the set header, not the bare "certificates" noun - "which
    item has PPS cert" must read "940 products have PPS certificates. ...", never
    lose the scheme the customer asked about. A bare `{"certificate": True}` require
    is untouched (no scheme to name).

    RED: `build_set_header`'s own `_header_predicate_phrase` maps every
    `require["certificate"]` value (bool OR the `{"scheme": ...}` dict) to the
    same flat "certificates" noun via `_HEADER_PREDICATE_NOUN` - the scheme
    value is never read - so the scheme case renders "940 products have
    certificates. Showing 5.", losing the "PPS" the customer asked about
    (measured live on "which item has PPS cert").

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    answers use the product-code rows under one intro line naming the described set and
    its count; the scheme is still named, now inside that phrase.
    """
    from app.services.chatbot.lanes.business.answer import build_set_header

    assert build_set_header(940, 5, "products", {"certificate": {"scheme": "PPS"}}) == (
        "Here's what you want: products with PPS certificates (940, showing 1 to 5)"
    )
    assert build_set_header(2, 2, "taps", {"certificate": True}) == (
        "Here's what you want: taps with certificates (2)"
    )


def test_common_product_types_fallback_is_never_empty_under_contact_scope():
    """AC-1329 (R10, fix round 2): "which zzqx has cert" (a nonsense product word,
    no class/product_type binds) must clarify with a "Try a product type such
    as ..." fallback drawn from the catalogue's OWN class labels under the
    request's real scope, ordered by how common each class is in that scope -
    never the generic "a class or product type I know" placeholder, and never
    empty. World: 3 taps, 2 wash basins, 1 water closet, so frequency order is
    tap, wash basin, water closet.

    GREEN TODAY, kept as the regression guard AC-1329/R10 asks for: measured
    directly against this exact world, `_common_class_labels`
    (`product_predicate_service.py`) already returns the frequency-ordered
    labels under the single-company scope `blank_session()` seeds into (the
    conftest default), so this scenario does not reproduce R10's console
    finding - the plan's own cause ("returned nothing under the live CONTACT
    scope") names a multi-company / real-contact scope this fixture cannot
    build. Reported honestly rather than forced red (the file's own
    "CONTRACT CONTRADICTION" convention, see the S3 banner above); the test 8
    sibling right above (`test_unrecognised_label_clarifies_as_a_document_
    type`) already pins the OTHER half of R10/AC-1329 - an attachment-label
    miss (`_leg_attachment_type`'s `_UnrecognizedLabel`) carries no
    `common_class_labels` at all, so a caller that reaches the generic
    product-type sentence via THAT path always sees the empty placeholder,
    whatever the catalogue holds. If the coder's fix genuinely needs a
    multi-company repro to fail here, that is this test's own finding to hand
    back, not a reason to invent different inputs until it breaks.
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        for _ in range(3):
            _tap_product(db, category_id=category_id, uom_id=uom_id)
        for _ in range(2):
            _basin_product(db, category_id=category_id, uom_id=uom_id)

        from app.models.product import Product
        from app.services.product_spec_derivation import derive_for_code
        from tests._pg_fixture import unique_code

        wc_code = unique_code("ZZWC")[:50]
        wc = Product(
            id=str(uuid.uuid4()),
            product_code=wc_code,
            product_name=wc_code,
            description=f"{wc_code} SORENTO WATER CLOSET",
            category_id=category_id,
            base_uom_id=uom_id,
            list_price=10,
            is_active=True,
        )
        db.add(wc)
        db.flush()
        derive_for_code(db, wc_code)
        db.commit()

        from app.services.chatbot.lanes.business import resolve_gate
        from app.services.chatbot.lanes.business.answer import not_found_error_message
        from app.services.chatbot.lanes.business.services import ResolveGateServices
        from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
        from app.config import settings

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(
                ResolveReferenceRequest(**payload), current_user=principal, db=db
            )

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        ctx = _cert_ctx(
            "which zzqx has cert",
            [
                {"hint": "product", "raw": "zzqx"},
                {"hint": "attachment_type", "raw": "cert", "canonical_code": "certificate"},
            ],
        )
        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    # Amended to fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"):
    # no product types are offered as lines either (the nearest and the most common labels
    # are retired); the word is the "Couldn't find" line of the one reply structure.
    assert text.startswith("Here's what you want: zzqx\n\nCouldn't find: \"zzqx\"."), text
    assert "•" not in text, text
    assert "a class or product type I know" not in text and "Try a product type" not in text, text


def test_shown_counts_products_not_rows():
    """AC-1330 (R8, fix round 2): "which basin got stock" over five class-Wash-
    Basin products, each carrying stock rows in THREE warehouses (15 rows total),
    must count PRODUCTS in the header - `shown` is distinct product codes
    rendered, never tool rows. A set that fits one message lists every product
    under the count alone; a named count of four over seven lists four distinct
    codes (twelve rows) and says "Here are the first 4." (no paging, owner ruling
    26 Sep 2026).

    Entities carry `hint: "product"` for "basin" (not "category") - matching
    `test_stock_set_answer_matches_forward_block_for_a_dealer`'s own convention
    just above: `inventory` is not in `_DOMAIN_HINT_EXPANSIONS`, so a `category`
    hint there widens only to `promotion` (never `product`), and "basin" would
    reach no product probe at all.
    """
    with blank_session() as db:
        _seed_registry(db)
        category = _wash_basin_category(db)
        _, uom_id = _seed_category_and_uom(db)
        warehouses = [_warehouse(db) for _ in range(3)]
        products = [_basin_product(db, category_id=category.id, uom_id=uom_id) for _ in range(5)]
        for product in products:
            for warehouse in warehouses:
                _stock_for(db, product_id=product.id, warehouse_id=warehouse.id)
        db.commit()

        ctx = {
            "text": {"message": {"message": {"text": "which basin got stock"}}},
            "contact": {"id": "999"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "inventory",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [{"hint": "product", "raw": "basin"}],
                }
            },
        }
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_stock_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: wash basins with stock (5)", reply
    assert "Showing" not in reply, reply

    with blank_session() as db:
        _seed_registry(db)
        category = _wash_basin_category(db)
        _, uom_id = _seed_category_and_uom(db)
        warehouses = [_warehouse(db) for _ in range(3)]
        products = [_basin_product(db, category_id=category.id, uom_id=uom_id) for _ in range(7)]
        for product in products:
            for warehouse in warehouses:
                _stock_for(db, product_id=product.id, warehouse_id=warehouse.id)
        db.commit()

        ctx = {
            "text": {"message": {"message": {"text": "which basin got stock"}}},
            "contact": {"id": "999"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "inventory",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [{"hint": "product", "raw": "basin"}],
                    "top_n": 4,
                }
            },
        }
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_stock_fake_call_tool(db))

        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line; a named count says how many
    # of the total are shown ("(7, showing 1 to 4)") in place of "Here are the first 4.".
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: wash basins with stock (7, showing 1 to 4)", reply
    shown_codes = _s4_codes_in(reply)
    assert len(shown_codes) == 4, reply


# --------------------------------------------------------------------------- #
# S4 - whole-turn harness. The "more" paging these helpers were written for is gone
# (owner ruling, 26 Sep 2026: no paging; tests/chatbot/test_counted_set_no_paging.py
# pins what replaced it). They stay for the engine-level cases below.
#
# Every full-turn test wires the SAME three seams `test_s6c_engine_paths.py` /
# `test_s6_s7_integration.py` use: `business_services.production_services` (resolve),
# `business_services.fetch_services` (the MCP call) and
# `business_services.answer_services_for` (the miss-flow probes, stubbed empty since
# none of these turns is expected to reach them) - real `resolve_gate` / `gate` /
# `fetch` / `answer` code runs, only the network is stubbed. `chatbot_completed_
# lanes = ["business_query"]` plus both switches on so the CRM answers in one
# `run_turn` call rather than delegating to n8n.
# --------------------------------------------------------------------------- #


def _s4_no_probe_answer_services():
    from app.services.chatbot.lanes.business.services import AnswerServices

    def _mcp_probe(name: str, args: dict) -> Any:
        return {"answers": [], "has_result": False}

    def _family_fetch(query: str) -> Any:
        return {"data": []}

    return AnswerServices(mcp_probe=_mcp_probe, family_fetch=_family_fetch)


def _s4_real_resolve_entity(db):
    """The SAME closure `_run_has_lane` (S3) uses, calling the real resolver endpoint
    function against the seeded world - reused here because engine-level tests need a
    `ResolveGateServices` bundle, not a bare callable."""
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings

    def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        payload = {**body, "spec_fallback": False, "understand_phrase": False}
        principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
        return resolve_reference_post(ResolveReferenceRequest(**payload), current_user=principal, db=db)

    return resolve_entity


def _s4_wire_engine(session_factory, monkeypatch, *, resolve_entity, fetch_mcp_call):
    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
    from tests.chatbot.conftest import set_chatbot_switches

    set_chatbot_switches(session_factory, business_lane=True, ordering=True)
    db = session_factory()
    setting = db.query(SystemSetting).first()
    setting.chatbot_completed_lanes = ["business_query"]
    db.commit()

    bundle = ResolveGateServices(
        access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
    )
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=fetch_mcp_call)
    )
    monkeypatch.setattr(
        engine_mod.business_services, "answer_services_for", lambda session_factory: _s4_no_probe_answer_services()
    )
    return engine_mod


def _s4_envelope(*, contact_id: str, message_id: str, text: str):
    from tests.chatbot.test_engine import _envelope

    return _envelope(
        contact={
            "id": contact_id,
            "firstName": "ZZT",
            "custom_fields": [{"name": "is_human_intervened", "value": "false"}],
        },
        message={
            "event_type": "message.received",
            "contact": {"id": contact_id},
            "message": {
                "messageId": message_id,
                "contactId": contact_id,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": text},
            },
        },
        is_test=False,
    )


def _s4_seed_contact(session_factory, *, contact_id: str, session_vars: dict[str, Any]) -> None:
    import json

    from sqlalchemy import text as sa_text

    db = session_factory()
    db.execute(
        sa_text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": contact_id, "phone": f"+6000{abs(hash(contact_id)) % 10**7:07d}", "sv": json.dumps(session_vars)},
    )
    db.commit()


def _s4_session_vars(session_factory, contact_id: str) -> dict[str, Any]:
    import json

    from sqlalchemy import text as sa_text

    db = session_factory()
    row = db.execute(
        sa_text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": contact_id},
    ).first()
    raw = row.session_vars if row is not None else {}
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


def _s4_contact_id(tag: str) -> str:
    return f"ZZT-s4-{tag}-{uuid.uuid4().hex[:8]}"


def _s4_codes_in(reply: str) -> set[str]:
    # A forward block names "*Product Code:* <code>"; a round 4 set row "N. <name> (<code>)".
    return set(re.findall(r"\*Product Code:\* (\S+)", reply)) | set(row_codes(reply))


def _s4_cert_parser_output(**overrides: Any):
    from tests.chatbot.test_engine import _parser_output

    base = dict(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        entities=[
            {
                "raw": "cert",
                "hint": "attachment_type",
                "canonical_code": None,
                "current_message": True,
                "confident": True,
            }
        ],
    )
    base.update(overrides)
    return _parser_output(**base)


def _s4_seed_seven_taps(db) -> list[str]:
    codes: list[str] = []
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    for _ in range(7):
        product = _tap_product(db, category_id=category_id, uom_id=uom_id)
        _certificate_for(db, product_id=product.id)
        codes.append(product.product_code)
    db.commit()
    return codes


def _s4_seed_seven_taps_pps(db) -> tuple[list[str], list[str]]:
    """Same world as `_s4_seed_seven_taps`, but every tap's certificate carries
    scheme "PPS" - a scheme-narrowed require then qualifies all seven,
    letting a "more" page test reuse this world unchanged. Returns (product
    codes, certificate ids), both order-parallel."""
    codes: list[str] = []
    cert_ids: list[str] = []
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    for _ in range(7):
        product = _tap_product(db, category_id=category_id, uom_id=uom_id)
        cert = _certificate_for(db, product_id=product.id, scheme="PPS")
        codes.append(product.product_code)
        cert_ids.append(cert.id)
    db.commit()
    return codes, cert_ids


def _cert_capturing_fake_call_tool(db, calls: list[dict[str, Any]]):
    """Wraps `_cert_fake_call_tool` to also record every MCP call (name, args)
    so a test can inspect the `certificate_ids` argument the fetch step
    actually sent, without duplicating its rendering logic."""

    inner = _cert_fake_call_tool(db)

    def fake_call_tool(name: str, args: dict[str, Any]) -> Any:
        calls.append({"name": name, "args": dict(args)})
        return inner(name, args)

    return fake_call_tool


# --------------------------------------------------------------------------- #
# Owner regression (PR #833, R29, AC-1354): a scheme-narrowed certificate      #
# leg must pass the qualifying certificates' OWN ids to the tool, alongside    #
# the listed product_ids - a bare certificate leg passes nothing extra.        #
# --------------------------------------------------------------------------- #


def test_scheme_narrowed_certificate_predicate_passes_certificate_ids_to_the_tool(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1354/R29: "which tap has PPS cert" must call
    `crm_master_product_attachments_list` with `certificate_ids` alongside
    the first-five `product_ids` - only the PPS files should ever render, not
    every certificate file the qualifying products hold.

    RED: `entity_ids_transformer` never reads `predicate.certificate_ids` -
    it only ever slices `product_ids` for a HAS turn - so the tool args carry
    no `certificate_ids` key at all.
    """
    contact_id = _s4_contact_id("certids")
    db = session_factory()
    codes, cert_ids = _s4_seed_seven_taps_pps(db)
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})

    calls: list[dict[str, Any]] = []
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_capturing_fake_call_tool(db, calls),
    )
    stub_parser(
        _s4_cert_parser_output(
            entities=[
                {
                    "raw": "PPS cert",
                    "hint": "attachment_type",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ]
        )
    )
    stub_access()

    turn1 = engine_mod.run_turn(
        _s4_envelope(contact_id=contact_id, message_id="ZZT-certids-1", text="which tap has PPS cert"),
        session_factory=session_factory,
    )
    assert turn1.status == "done", turn1.error
    assert len(calls) == 1, calls
    turn1_args = calls[0]["args"]
    assert set(turn1_args.get("certificate_ids") or []) == set(cert_ids), turn1_args
    assert turn1_args.get("product_ids"), turn1_args


def test_bare_certificate_predicate_passes_its_own_certificate_ids(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1354/R29, AMENDED by round 7 on PR #833 (owner hand test of rounds 4 to 6,
    item 7: "when i ask about cert, you give me photos"): a BARE certificate leg passes
    the qualifying products' own certificate ids too, every scheme, so the attachments
    call lists certificate files and never a product's photos.
    """
    from app.models.certificate import Certificate, CertificateProduct

    contact_id = _s4_contact_id("nocertids")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    for i in range(7):
        product = _tap_product(db, category_id=category_id, uom_id=uom_id)
        # A certificate_number built WITHOUT the substring "cert" - the shared
        # `_certificate_for` helper's own `unique_code("CERTNO")` collides
        # with the bare word "cert" through an UNRELATED general entity-
        # resolution probe (the register's `certificate_number` column, not
        # this AC's predicate/require mechanism), which would make this
        # control world carry a stray `certificate_ids` entry for a reason
        # that has nothing to do with R29.
        cert = Certificate(
            id=str(uuid.uuid4()), scheme="ZZT-SIRIM", certificate_number=f"ZZT-{i:06d}", status="active"
        )
        db.add(cert)
        db.flush()
        db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=product.id))
    db.commit()
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})

    calls: list[dict[str, Any]] = []
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_capturing_fake_call_tool(db, calls),
    )
    stub_parser(_s4_cert_parser_output())
    stub_access()

    turn1 = engine_mod.run_turn(
        _s4_envelope(contact_id=contact_id, message_id="ZZT-nocertids-1", text="which tap has cert"),
        session_factory=session_factory,
    )
    assert turn1.status == "done", turn1.error
    assert len(calls) == 1, calls
    certified = {
        row[0]
        for row in db.query(CertificateProduct.certificate_id)
        .join(Certificate, Certificate.id == CertificateProduct.certificate_id)
        .filter(Certificate.scheme == "ZZT-SIRIM")
        .all()
    }
    assert set(calls[0]["args"].get("certificate_ids") or []) == certified, calls[0]["args"]


# --------------------------------------------------------------------------- #
# Owner regression (PR #833, R34, AC-1359): the head can drop BOTH the         #
# category entity ("tap") AND the attachment_type entity ("PPS cert")          #
# entirely - `derive_require` still recovers the bare certificate leg off      #
# `user_goal`/`message_text` (R28) and the server-side `recover_certificate_   #
# scheme` still promotes it to a scheme-narrowed leg off the described-set     #
# remainder (R14) - so the resolver genuinely qualifies real rows, yet the     #
# gate's own REQUIRED_TYPES check (`product_attachment` requires an            #
# `attachment_type` among resolved entities OR parser hints - both empty       #
# here) still fails the gate, and `not_found_error_message`'s own              #
# `missing_attachment_type` computation asks for the attachment type again,    #
# discarding the predicate's own answer.                                      #
# --------------------------------------------------------------------------- #


def test_certificate_predicate_suppresses_the_attachment_type_ask_when_it_qualifies(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1359/R34: measured live on the stored turn "any tap has PPS cert" -
    the parser gave category "tap" + attachment_type "PPS cert", the head
    dropped BOTH (derived entities `[]`), the resolver still ran with
    `require={"certificate": {"scheme": "PPS"}}` (recovered off the message
    text), `qualifying_total=1`, `class_labels=["Tap"]`, and one real
    `certificate_ids` entry - yet the reply asked "Please provide the
    attachment type for the requested product ...", discarding a predicate
    that already qualified a real row.

    With `entities: []` (both dropped), one Tap product on file carrying a
    PPS-scheme certificate, and `message_text`/`user_goal` = "any tap has PPS
    cert" - the reply must be the counted SET ANSWER (`build_set_header`'s own
    shape, "<N> taps have/has PPS certificates") and must NOT contain the
    attachment-type ask.

    RED: `app/services/chatbot/lanes/business/gate.py`'s `REQUIRED_TYPES`
    check for `product_attachment` (an `attachment_type` must be among the
    resolved entities OR the parser's own entity hints) runs unconditionally,
    with no exemption for a resolver result that already carries a
    `predicate` block - so `gate_passed` goes False purely for lack of an
    attachment_type entity, even though the certificate predicate genuinely
    qualified a row. `not_found_error_message`'s own `missing_attachment_type`
    (`app/services/chatbot/lanes/business/answer.py` ~line 2415) then fires
    off that same `gate_passed=False`, with no check of its own `resolved`
    argument's `predicate` either, and emits the attachment-type ask.
    """
    from app.models.certificate import Certificate, CertificateProduct

    contact_id = _s4_contact_id("certsuppress")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    product = _tap_product(db, category_id=category_id, uom_id=uom_id)
    # A certificate_number built WITHOUT the substring "cert" - see the sibling
    # bare-leg control test above for why (`_certificate_for`'s own default
    # collides with a bare "cert" word through an unrelated general
    # entity-resolution probe).
    cert = Certificate(
        id=str(uuid.uuid4()), scheme="PPS", certificate_number="ZZT-000001", status="active"
    )
    db.add(cert)
    db.flush()
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=product.id))
    db.commit()

    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_fake_call_tool(db),
    )
    stub_parser(
        _s4_cert_parser_output(
            # Main's v3 verdict (the head normaliser that dropped both entities is
            # retired): the class word is a `product_type` entity - without one the
            # resolver never runs (`turn_runtime.resolve_kinds`) - and the verdict states
            # what was asked ABOUT (`requested_attributes`), which is what satisfies
            # `narrow_by_type`, so the engine's own "which kind of file?" never fires.
            entities=[
                {
                    "raw": "tap",
                    "hint": "product_type",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            requested_attributes=["PPS cert"],
            user_goal="any tap has PPS cert",
        )
    )
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(
            contact_id=contact_id,
            message_id="ZZT-certsuppress-1",
            text="any tap has PPS cert",
        ),
        session_factory=session_factory,
    )
    assert turn.status == "done", turn.error
    text = (turn.reply or {}).get("text") or ""
    assert "Please provide the attachment type" not in text, text
    assert "Which kind of file do you need?" not in text, text
    assert re.search(r"\btaps?\b", text, re.IGNORECASE), text
    assert "PPS certificates" in text, text


def test_no_certificate_predicate_still_misses_honestly_with_no_attachment_type_ask(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1359/R34 (second case): the SAME dropped-entities shape, but nothing
    on file qualifies (`qualifying_total=0`, no matches) - the reply must be
    the AC-1319 miss copy ("Couldn't find..."), still never the
    attachment-type ask.

    RED today, same reason as the sibling positive case above, NOT the
    pre-existing-green control it reads as at first glance: `gate.py`'s
    `REQUIRED_TYPES` check for `product_attachment` fails the gate purely off
    "no attachment_type entity resolved AND no attachment_type parser hint" -
    it never inspects the resolver's own `predicate`/`qualifying_total` at
    all, so it fails identically whether the predicate qualified 1 row or 0.
    `not_found_error_message`'s `missing_attachment_type` then fires off that
    same `gate_passed=False` with no predicate check of its own either -
    measured, not assumed: this case emits the SAME "Please provide the
    attachment type..." wrongly, not the miss copy the AC calls for.
    """
    contact_id = _s4_contact_id("certsuppressmiss")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    # A Tap product on file, but carrying NO certificate at all - "PPS" names
    # no scheme anything qualifies, so `qualifying_total` is 0.
    _tap_product(db, category_id=category_id, uom_id=uom_id)
    db.commit()

    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_fake_call_tool(db),
    )
    stub_parser(
        _s4_cert_parser_output(
            # Main's v3 verdict (the head normaliser that dropped both entities is
            # retired): the class word is a `product_type` entity - without one the
            # resolver never runs (`turn_runtime.resolve_kinds`) - and the verdict states
            # what was asked ABOUT (`requested_attributes`), which is what satisfies
            # `narrow_by_type`, so the engine's own "which kind of file?" never fires.
            entities=[
                {
                    "raw": "tap",
                    "hint": "product_type",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
            requested_attributes=["PPS cert"],
            user_goal="any tap has PPS cert",
        )
    )
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(
            contact_id=contact_id,
            message_id="ZZT-certsuppressmiss-1",
            text="any tap has PPS cert",
        ),
        session_factory=session_factory,
    )
    assert turn.status == "done", turn.error
    text = (turn.reply or {}).get("text") or ""
    assert "Please provide the attachment type" not in text, text
    assert "Which kind of file do you need?" not in text, text
    assert "Couldn't find" in text or "don't know" in text.lower() or "on file" in text.lower(), text


# --------------------------------------------------------------------------- #
# Reviewer round 5 (AC-1359): negative controls for R34's `has_document_leg`   #
# guard - a genuinely gate_passed=False, no-attachment-type turn must still    #
# ask, whether it carries NO predicate at all, or a predicate whose OWN        #
# require names a non-document leg (`stock`). Pins the guard's own boundary    #
# so a future over-broad `has_document_leg` (e.g. "any predicate at all"       #
# rather than "a certificate/attachment_type leg specifically") fails here    #
# instead of silently swallowing a genuine attachment-type ask.                #
# --------------------------------------------------------------------------- #


def test_no_document_leg_at_all_still_asks_for_the_attachment_type(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1359/R34 control (A): `entities: []`, no attachment-type entity, and
    a message that carries no cert word at all - `derive_require` returns
    `None` (no predicate whatsoever), so `gate.py`'s `REQUIRED_TYPES` check
    fails the gate for the ORIGINAL, correct reason (no document type named,
    nothing to recover) - the ask must still fire.

    Green today, kept as the regression guard so R34's fix stays narrowed to
    a genuine certificate/attachment_type leg, never "any predicate present".
    """
    contact_id = _s4_contact_id("nodocleg")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    _tap_product(db, category_id=category_id, uom_id=uom_id)
    db.commit()

    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_fake_call_tool(db),
    )
    stub_parser(
        _s4_cert_parser_output(
            entities=[],
            user_goal="send me something for the tap",
        )
    )
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(
            contact_id=contact_id,
            message_id="ZZT-nodocleg-1",
            text="send me something for the tap",
        ),
        session_factory=session_factory,
    )
    assert turn.status == "done", turn.error
    text = (turn.reply or {}).get("text") or ""
    # Main's turn engine asks this itself (`turn/compose._ASK_HEADERS["attachment_type_ask"]`).
    assert "Which kind of file do you need?" in text, text


def test_a_non_document_predicate_still_asks_for_the_attachment_type(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1359/R34 control (D): a resolver predicate IS present, but its own
    `require` names only a non-document leg (`{"stock": True}`) - stock is not
    an answer to "what document type", so the gate must still fail and the
    ask must still fire. Pins `has_document_leg` to checking the require's
    OWN keys (`certificate`/`attachment_type`), never "a predicate exists at
    all".

    Green today, kept as the regression guard alongside control (A).
    """
    contact_id = _s4_contact_id("nondocleg")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    _tap_product(db, category_id=category_id, uom_id=uom_id)
    db.commit()

    def fake_resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": [],
            "resolutions": [],
            "unresolved_tokens": [],
            "predicate": {
                "require": {"stock": True},
                "qualifying_total": 3,
                "class_labels": ["Tap"],
                "candidates": [],
            },
        }

    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=fake_resolve_entity,
        fetch_mcp_call=_cert_fake_call_tool(db),
    )
    stub_parser(
        _s4_cert_parser_output(
            entities=[],
            user_goal="any tap available",
        )
    )
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(
            contact_id=contact_id,
            message_id="ZZT-nondocleg-1",
            text="any tap available",
        ),
        session_factory=session_factory,
    )
    assert turn.status == "done", turn.error
    text = (turn.reply or {}).get("text") or ""
    # Main's turn engine asks this itself (`turn/compose._ASK_HEADERS["attachment_type_ask"]`).
    assert "Which kind of file do you need?" in text, text


# --------------------------------------------------------------------------- #
# Fix round (console findings, 11 Sep 2026, committed 3779b32d6) - AC-1316,     #
# AC-1320: a "category" entity the head retypes into an unresolved PRODUCT      #
# token must still yield a set answer, never fall through to a clarify; and     #
# a term with genuinely NO nearest class-label candidate must offer the three   #
# most common labels, never the empty "Did you mean the product types I        #
# know?".                                                                       #
# --------------------------------------------------------------------------- #


def test_category_entity_yields_a_set_answer_not_a_clarify():
    """AC-1316/AC-1320: world of 3 certified class-Tap products and 1 certified
    class-Wash-Basin product (product codes carry NO literal "tap"/"basin"
    substring, so a pass here cannot be the accidental LOOKUP-by-code-substring
    shortcut S3's own module banner warns about - the scoping has to come from
    the class binding). Message "which tap has cert" with an EXPLICIT product
    entity {"hint": "product", "raw": "tap"} that will not resolve (the shape
    the head produces when it retypes a parser "category" entity) plus the
    attachment_type entity carrying a `canonical_code` - reply opens "3 taps
    have certificates." and never says "I don't know" anywhere.

    NOT RED as constructed - measured directly against this exact world and
    entity shape (probe dropped after confirming): the reply already reads "3
    taps have certificates." with no clarify text. Reported to the captain as
    a finding rather than forced: this council finding may already be covered
    by the existing C2/C4 wiring, or it needs a shape this construction does
    not reach. Kept as the AC-1316/AC-1320 regression guard the captain named.
    """
    with blank_session() as db:
        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        from app.models.product import Product
        from app.services.product_spec_derivation import derive_for_code
        from tests._pg_fixture import unique_code

        def _certified(description: str) -> Product:
            code = unique_code("ZQ")[:20]
            product = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=code,
                description=f"{unique_code('ZQ')[:10]} {description}",
                category_id=category_id,
                base_uom_id=uom_id,
                list_price=10,
                is_active=True,
            )
            db.add(product)
            db.flush()
            derive_for_code(db, code)
            _certificate_for(db, product_id=product.id)
            return product

        for _ in range(3):
            _certified("CHROME TAP")
        _certified("WHITE WASH BASIN")
        db.commit()

        ctx = {
            "text": {"message": {"message": {"text": "which tap has cert"}}},
            "contact": {"id": "999"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_product_attachment",
                    "domain_hint": "product_attachment",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [
                        {"raw": "tap", "hint": "product"},
                        {"raw": "cert", "hint": "attachment_type", "canonical_code": "certificate"},
                    ],
                }
            },
        }
        out, fragment = _run_has_lane(db, ctx, fake_call_tool=_cert_fake_call_tool(db))
        assert out.get("_exit_kind") == "continue", out.get("gate_reason")
        reply = (fragment.get("fetch") or {}).get("response") or ""

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    lines = legacy_lines(reply)
    assert lines and lines[0] == "Here's what you want: taps with certificates (3)", reply
    assert "i don't know" not in reply.lower(), reply


def test_clarify_with_no_candidate_offers_common_product_types():
    """AC-1320: a term with NO nearest class-label candidate at all ("zzqx" -
    `difflib.get_close_matches` against Tap / Wash Basin / Water Closet at
    cutoff 0.6 returns nothing, and no exact word match either) must clarify
    with "Try a product type such as <a>, <b>, <c>." naming the world's own
    common class labels, never the contentless "Did you mean the product
    types I know?".

    RED: `not_found_error_message` (`answer.py`) has exactly one branch for
    `qualifying_total == 0` with a non-empty `unrecognized_terms` - when
    `predicate.suggestions` is empty it falls back to the literal string "the
    product types I know" ("Did you mean the product types I know?"); no
    "Try a product type such as" text exists anywhere in the file.
    """
    with blank_session() as db:
        # A term nothing binds to ("zzqx") falls through every probe to the last-
        # resort trigram fallback across EVERY entity type, including transporters
        # - whose `similarity()` call needs `pg_trgm`, installed in `public`.
        # `blank_session()`'s own `search_path` deliberately excludes `public` (so
        # raw SQL cannot leak onto the real tables); appending it here is scoped to
        # THIS test's `SET LOCAL` and never widens what any other test can reach.
        from sqlalchemy import text as sa_text

        current_search_path = db.execute(sa_text("SHOW search_path")).scalar()
        db.execute(sa_text(f"SET LOCAL search_path TO {current_search_path}, public"))

        _seed_registry(db)
        category_id, uom_id = _seed_category_and_uom(db)
        from app.models.product import Product
        from app.services.product_spec_derivation import derive_for_code
        from tests._pg_fixture import unique_code

        def _seeded(description: str) -> Product:
            code = unique_code("ZQ")[:20]
            product = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=code,
                description=f"{unique_code('ZQ')[:10]} {description}",
                category_id=category_id,
                base_uom_id=uom_id,
                list_price=10,
                is_active=True,
            )
            db.add(product)
            db.flush()
            derive_for_code(db, code)
            _certificate_for(db, product_id=product.id)
            return product

        _seeded("CHROME TAP")
        _seeded("WHITE WASH BASIN")
        _seeded("AUTO WATER CLOSET")
        db.commit()

        from app.services.chatbot.lanes.business import resolve_gate
        from app.services.chatbot.lanes.business.answer import not_found_error_message
        from app.services.chatbot.lanes.business.services import ResolveGateServices
        from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
        from app.config import settings

        def resolve_entity(body: dict) -> dict:
            payload = {**body, "spec_fallback": False, "understand_phrase": False}
            principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
            return resolve_reference_post(ResolveReferenceRequest(**payload), current_user=principal, db=db)

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=resolve_entity, probe=lambda **_: None
        )

        ctx = {
            "text": {"message": {"message": {"text": "which zzqx has cert"}}},
            "contact": {"id": "999"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_product_attachment",
                    "domain_hint": "product_attachment",
                    "match_mode": "or",
                    "access_levels": [],
                    "entities": [
                        {"raw": "zzqx", "hint": "product"},
                        {"raw": "cert", "hint": "attachment_type"},
                    ],
                }
            },
        }
        out = resolve_gate.run(ctx, "resolve", {}, services=services, space_id="364817")
        parser = ctx["parse"]["output"]
        resolved = out.get("resolved") or {}
        gate = out.get("gate") or {}

        msg = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        text = (msg.get("escalate_message") or "").strip()

    # Amended to fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"):
    # no product types are offered as lines either (the nearest and the most common labels
    # are retired); the word is the "Couldn't find" line of the one reply structure.
    assert text.startswith("Here's what you want: zzqx\n\nCouldn't find: \"zzqx\"."), text
    for label in ("tap", "wash basin", "water closet"):
        assert label not in text.lower(), text
    assert "Did you mean the product types I know?" not in text, text


# --------------------------------------------------------------------------- #
# Correctness review (11 Sep 2026, PLAN-attribute-first-asks.md REV-S3,        #
# AC-1337) - two hand-synced copies across the module boundary                #
# (`references.py` sits outside `app.services.chatbot`, so it keeps its own    #
# copies rather than importing across it - see that file's own docstrings).   #
# --------------------------------------------------------------------------- #


def test_id_cap_and_cert_regex_are_pinned():
    """AC-1337/REV-S3: `answer.SET_ID_CAP` and `references._SET_ID_CAP`
    must stay equal, and `references._CERT_WORD_RE` must stay the same pattern
    as `predicate._CERT_RE` (its home since main retired `head/output_exchange`) -
    both are hand-synced copies across the module boundary (`references.py`'s own
    docstrings say so), so a value that drifts silently caps the counted set
    differently from the recount or lets a cert-shaped word slip past the
    described-set stripping.

    Green today - both copies were kept in lockstep by hand at write time.
    Kept as the regression guard the drift would otherwise need a console
    turn to surface.
    """
    from app.api.v1.system import references
    from app.services.chatbot.lanes.business import answer
    from app.services.chatbot.lanes.business.predicate import _CERT_RE

    assert answer.SET_ID_CAP == references._SET_ID_CAP
    assert references._CERT_WORD_RE.pattern == _CERT_RE.pattern


# --------------------------------------------------------------------------- #
# Correctness review - REV-N2/AC-1337: irregular pluralisation, and the        #
# header's singular is the class label itself, never a stripped plural.       #
# --------------------------------------------------------------------------- #


def test_set_noun_for_irregular_plurals():
    """AC-1337/REV-N2: `set_noun_for` must pluralise via a small irregular map
    (accessory -> accessories) before falling back to a bare "+s", and
    `build_set_header`'s own singular is the class label ITSELF, never a
    plural with the trailing "s" stripped off - the naive strip is what
    `fetch.py`'s own call site does today ("bathroom accessories"[:-1] ->
    "bathroom accessorie", never the real singular "bathroom accessory").

    RED (first assertion only): `set_noun_for` today does
    `f"{labels[0].strip().lower()}s"` unconditionally, so "Bathroom Accessory"
    becomes "bathroom accessorys", not "bathroom accessories". "Jacuzzi" -> "s"
    already happens to spell "jacuzzis" correctly with the naive rule, so that
    assertion is not itself red - kept because AC-1337 names it explicitly, and
    a future irregular-map refactor must not silently break it.

    The third assertion calls `build_set_header` directly with the class label
    as `set_noun` (bypassing `fetch.py`'s own singular-stripping call site
    entirely) - `build_set_header` itself has no pluralisation logic of its
    own, so this is GREEN today; it pins the CONTRACT `fetch.py`'s call site
    must honour once its own "-1 char" strip is replaced with the real
    singular.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    answers use the product-code rows under one intro line, so `build_set_header`'s
    singular still reads the class label itself, now inside that line's phrase.
    """
    from app.services.chatbot.lanes.business.answer import build_set_header, set_noun_for

    assert set_noun_for(["Bathroom Accessory"]) == "bathroom accessories"
    assert set_noun_for(["Jacuzzi"]) == "jacuzzis"
    assert build_set_header(1, 1, "bathroom accessory", {"stock": True}) == (
        "Here's what you want: bathroom accessory with stock (1)"
    )


# --------------------------------------------------------------------------- #
# R30/R31 (AC-1355, AC-1356), the "_Matched on:" line on a set answer: retired  #
# with the line itself. Main's turn engine re-architecture (#952) deleted       #
# `tail/compile_state._matched_on_line` and no reply carries a Match line any   #
# more; the control below still pins that a set answer never grows one.         #
# --------------------------------------------------------------------------- #


def test_set_answer_with_no_spec_words_carries_no_match_line(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """AC-1355/R30 control: "which tap has cert" (no spec bindings at all)
    must carry NO "_Matched on:" line - the certificate leg names no spec
    key for `_matched_on_line` to intersect against.

    Kept after main (#952) retired the Match line altogether: a set answer must
    never grow one back.
    """
    contact_id = _s4_contact_id("nomatchline")
    db = session_factory()
    _s4_seed_seven_taps(db)
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})

    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_fake_call_tool(db),
    )
    stub_parser(_s4_cert_parser_output())
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(contact_id=contact_id, message_id="ZZT-nomatchline-1", text="which tap has cert"),
        session_factory=session_factory,
    )
    assert turn.status == "done", turn.error
    text = (turn.reply or {}).get("text") or ""
    assert "_Matched on:" not in text, text
