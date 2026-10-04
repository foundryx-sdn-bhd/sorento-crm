"""PICKER-NO-CAP (owner, 2 Oct 2026): "the chatbot picker is capped at 10 options ...
remove that cap". Every option a picker stores is printed, so every option the dealer
can pick is one they can see.

The stock pickers stored up to `MAX_SLOTS` (20) options but printed only `MAX_NAMED`
(10) of them, closing with "and N others, reply with the full code." - options 11 to 20
were pickable by number yet never shown. The "others" line now only counts matches the
pick does not carry at all (`count` past the stored options).
"""
from __future__ import annotations

import re
import uuid

from app.services.chatbot.dealer_stock import did_you_mean
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.policy_rows import DEFAULT_KIND_ROWS

from tests.chatbot import _ht26_fixtures as ht


def test_family_pick_numbers_every_option_past_ten():
    codes = [f"SRTX1-{i:02d}" for i in range(15)]
    lines = task_mod.pick_question("SRTX1", codes).splitlines()
    assert lines[0] == "SRTX1 matches 15 products. Which one?"
    assert lines[1:] == task_mod.numbered(codes)
    assert not any("others" in line for line in lines)


def test_family_pick_from_the_stock_reply_lists_all_twelve():
    codes = [f"SRTX1-{i:02d}" for i in range(12)]
    reply = task_mod.after_reply(
        (), ht.envelopes(*[ht.row(c) for c in codes]), asked=[ht.asked("srtx1")]
    )
    lines = reply.text.splitlines()
    assert lines[0] == "SRTX1 matches 12 products. Which one?"
    assert lines[1:] == task_mod.numbered(codes)
    assert [o["label"] for o in reply.pick["options"]] == codes


def test_the_others_line_counts_only_matches_the_pick_does_not_carry():
    """`count` past the stored options (the stock tool matched more than `MAX_SLOTS`):
    every stored option is printed, and only the rest are counted."""
    codes = [f"SRTX1-{i:02d}" for i in range(task_mod.MAX_SLOTS)]
    lines = task_mod.pick_question("SRTX1", codes, count=27).splitlines()
    assert lines[0] == "SRTX1 matches 27 products. Which one?"
    assert lines[1:-1] == task_mod.numbered(codes)
    assert lines[-1] == "and 7 others, reply with the full code."


def test_dealer_did_you_mean_numbers_every_candidate_past_ten():
    codes = [f"ELP37{i:02d}" for i in range(14)]
    text, pending = did_you_mean(
        "elp3799",
        [{"product": c, "uuid": ht.uuid_of(c), "entity_type": "product"} for c in codes],
    )
    lines = text.splitlines()
    assert lines[0] == "Couldn't find ELP3799. Did you mean:"
    assert lines[1:] == task_mod.numbered(codes)
    assert [o["label"] for o in pending.options] == codes


# --------------------------------------------------------------------------- #
# The roster cap (`chatbot_entity_kinds.roster_cap`): owner answer (c) - every kind
# starts at the security ceiling (50, review S3), so the incoming search and every other
# gate roster list everything the resolver returned.
# --------------------------------------------------------------------------- #

ROSTER_CEILING = 50


def _seed_caps() -> dict[str, int]:
    """The mapping `engine.py` builds off the policy, from the seed rows."""
    policy = Policy.from_rows(domains=[], kinds=DEFAULT_KIND_ROWS)
    return {row.kind: row.roster_cap for row in policy.kinds}


def _products(n: int) -> list[dict]:
    return [
        {
            "entity_type": "product",
            "uuid": f"prod-{i:03d}",
            "canonical_code": f"ZZTPROD{i:03d}",
            "match_tier": "prefix",
        }
        for i in range(n)
    ]


def _customers(n: int) -> list[dict]:
    return [
        {
            "entity_type": "customer",
            "uuid": f"cust-{i:03d}",
            "canonical_code": f"CUST{i:03d}",
            "company_code": "SRT",
            "display": {"customer_name": f"ZZT CUSTOMER {i:03d} SDN BHD"},
        }
        for i in range(n)
    ]


def test_every_seeded_kind_starts_at_the_ceiling():
    assert {row["kind"]: row["roster_cap"] for row in DEFAULT_KIND_ROWS} == {
        row["kind"]: ROSTER_CEILING for row in DEFAULT_KIND_ROWS
    }


def test_a_kind_row_with_no_roster_cap_reads_as_the_ceiling():
    policy = Policy.from_rows(domains=[], kinds=[{"kind": "product"}])
    assert policy.kind("product").roster_cap == ROSTER_CEILING


def test_incoming_search_lists_all_fifteen_matches():
    gate = gate_mod.run_gate(
        {},
        parser={
            "domain_hint": "incoming",
            "entities": [{"raw": "zzt", "hint": "product", "current_message": True}],
        },
        resolver={
            "resolutions": [{"token": "zzt", "matches": _products(15)}],
            "unresolved_tokens": [],
        },
        roster_caps=_seed_caps(),
    )
    assert gate.get("require_specific") is True, gate
    lines = re.findall(r"^\d+\. .+$", gate["gate_clarification"], re.MULTILINE)
    assert lines == [f"{i + 1}. ZZTPROD{i:03d}" for i in range(15)]
    assert len(gate["compatible_entities"]) == 15


def test_customer_picker_lists_all_fifteen_matches():
    gate = gate_mod.run_gate(
        {},
        parser={
            "domain_hint": "order",
            "entities": [{"raw": "zzt", "hint": "customer", "current_message": True}],
        },
        resolver={
            "resolutions": [{"token": "zzt", "matches": _customers(15)}],
            "unresolved_tokens": [],
        },
        roster_caps=_seed_caps(),
    )
    assert gate.get("require_specific") is True, gate
    assert len(gate.get("compatible_entities") or []) == 15


def test_a_kind_missing_from_the_mapping_is_not_cut_at_ten():
    gate = gate_mod.run_gate(
        {},
        parser={
            "domain_hint": "incoming",
            "entities": [{"raw": "zzt", "hint": "product", "current_message": True}],
        },
        resolver={
            "resolutions": [{"token": "zzt", "matches": _products(15)}],
            "unresolved_tokens": [],
        },
        roster_caps={"customer": 5},
    )
    assert len(re.findall(r"^\d+\. ", gate["gate_clarification"], re.MULTILINE)) == 15


def test_a_new_kind_row_defaults_to_the_ceiling():
    import app.main  # noqa: F401 - registers every model before any query
    from app.models.chatbot_policy import ChatbotEntityKind

    from tests._pg_fixture import blank_session, unique_code

    with blank_session() as db:
        row = ChatbotEntityKind(
            id=str(uuid.uuid4()),
            kind=unique_code("kind"),
            label="ZZT kind",
            resolver_source="zzt_source",
            default_narrowing="optional_filter",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.roster_cap == ROSTER_CEILING


# --------------------------------------------------------------------------- #
# crew-tester chat pass at 83754a68 (PR #1436): "incoming AMS" with 20 matches listed 15.
# The cut was the resolve body's own `limit: 15` (`resolve_gate.resolve_entity_body`):
# the route truncates every token's matches to it, before any roster cap is read. The
# lookup now asks for as many rows as the widest roster may print (the S3 ceiling).
# --------------------------------------------------------------------------- #


def _resolve_ctx(raw: str, hint: str = "product") -> dict:
    return {
        "text": {"message": {"message": {"text": f"incoming {raw}"}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_incoming",
                "domain_hint": "incoming",
                "match_mode": "and",
                "access_levels": [],
                "entities": [
                    {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True}
                ],
            }
        },
    }


def test_the_lookup_asks_for_as_many_rows_as_a_roster_may_print():
    assert resolve_entity_body(_resolve_ctx("QAMS"))["limit"] == ROSTER_CEILING


def test_incoming_search_over_twenty_real_products_lists_all_twenty():
    """End to end below the parser: seeded products -> the chatbot's own resolve body ->
    the resolve route -> the gate with the seeded roster caps -> twenty numbered lines."""
    import app.main  # noqa: F401 - registers every model before any query
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    from tests._pg_fixture import blank_session

    codes = [f"QAMS-CL{i:02d}X" for i in range(20)]
    with blank_session() as db:
        cat = ProductCategory(id=str(uuid.uuid4()), category_code="ZZTPNC", category_name="ZZT")
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZTPNU", uom_name="ZZT")
        db.add_all([cat, uom])
        db.flush()
        for code in codes:
            db.add(
                Product(
                    id=str(uuid.uuid4()),
                    product_code=code,
                    product_name="ZZT product",
                    category_id=cat.id,
                    base_uom_id=uom.id,
                    list_price=1,
                )
            )
        db.flush()

        ctx = _resolve_ctx("QAMS")
        body = resolve_entity_body(ctx)
        body.pop("contact_id", None)
        resolver = resolve_reference_post(
            ResolveReferenceRequest(**body), current_user={"id": None}, db=db
        )
        gate = gate_mod.run_gate(
            {},
            parser=ctx["parse"]["output"],
            resolver=resolver,
            roster_caps=_seed_caps(),
        )

    assert gate.get("require_specific") is True, gate
    lines = re.findall(r"^\d+\. (\S+)", gate["gate_clarification"], re.MULTILINE)
    assert lines == codes
