"""MULTI-CODE-DYM (owner, 4 Oct 2026): "treat each product code individually".

"srtwc286 , srtwc6022  srt5764  stock" listed the two found products and ended with a
bare "I could not find srt5764." while "srt5764 stock" alone offers
'Couldn't find "srt5764" (product). Did you mean: 1. SRT57-CR 2. SRT5713 3. SRT5732 ...'.
A missing code in a multi-code reply now gets the same did-you-mean it gets when asked
alone, off the same resolver candidates. Behaviour card:
`documentation/plans/chatbot/multi-code-dym-behaviour-card.md`.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

SUGGEST = [
    {"code": "SRT57-CR", "uuid": "11111111-1111-4111-8111-111111111111", "entity_type": "product"},
    {"code": "SRT5713", "uuid": "22222222-2222-4222-8222-222222222222", "entity_type": "product"},
    {"code": "SRT5732", "uuid": "33333333-3333-4333-8333-333333333333", "entity_type": "product"},
]
OTHER = [
    {"code": "SRTWC991", "uuid": "44444444-4444-4444-8444-444444444444", "entity_type": "product"},
    {"code": "SRTWC992", "uuid": "55555555-5555-4555-8555-555555555555", "entity_type": "product"},
]
CLOSING = "Reply with a code to continue, or would you like me to escalate to warehouse team?"


def _policy() -> Policy:
    row = {
        **_domain_row("inventory", narrowing={"product": "list_all"}),
        "label": "stock",
        "escalation_team_code": "warehouse",
    }
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state(profile: Profile | None = None) -> State:
    return State(focus=Focus(), pending=None, profile=profile or Profile(), turn_no=2)


def _block(code: str, qty: int, n: int | None = None) -> str:
    head = f"{n}. " if n is not None else ""
    return f"{head}*Product Code:* {code}\n*Warehouse:* BUKIT RAJA\n*Quantity On Hand:* {qty}"


def _envelope(
    rows: dict[str, int],
    unresolved: list[str],
    suggestions: dict[str, list[dict[str, Any]]] | None = None,
    *,
    numbered: bool = False,
) -> dict[str, Any]:
    figures = [
        {"fields": [{"label": "Product Code", "value": c}, {"label": "Quantity On Hand", "value": q}]}
        for c, q in rows.items()
    ]
    blocks = [
        _block(c, q, (i + 1) if numbered else None) for i, (c, q) in enumerate(rows.items())
    ]
    return {
        "domain": "inventory",
        "denied": False,
        "entities": list(rows),
        "product_codes": list(rows),
        "figures": figures,
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": unresolved,
        "unresolved_suggestions": {
            raw: {"head": f'Couldn\'t find "{raw}" (product). Did you mean:', "rows": rows}
            for raw, rows in (suggestions or {}).items()
        },
        "error": None,
        "lane_text": "Stock availability:\n\n" + "\n\n".join(blocks),
    }


def _answer(env: dict[str, Any], profile: Profile | None = None):
    return compose([env], _state(profile), _policy(), ctx=None)


# --------------------------------------------------------------------------- #
# Compose: the partial miss
# --------------------------------------------------------------------------- #


def test_partial_miss_offers_the_single_code_did_you_mean():
    answer = _answer(
        _envelope({"SRTWC286": 12, "SRTWC6022": 4}, ["srt5764"], {"srt5764": SUGGEST})
    )
    text = answer.text

    assert "*Product Code:* SRTWC286" in text and "*Product Code:* SRTWC6022" in text
    assert (
        'Couldn\'t find "srt5764" (product). Did you mean:\n1. SRT57-CR\n2. SRT5713\n3. SRT5732'
        in text
    ), text
    assert text.rstrip().endswith(CLOSING), text
    assert "I could not find srt5764" not in text, text
    # The did-you-mean sits after the found products.
    assert text.index("SRTWC6022") < text.index("Couldn't find")


def test_partial_miss_stores_one_product_pick_with_the_escalation_offered():
    answer = _answer(_envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST}))
    question = answer.question

    assert question is not None and question.kind == "product_pick", question
    assert [o["position"] for o in question.options] == [1, 2, 3]
    assert [o["code"] for o in question.options] == ["SRT57-CR", "SRT5713", "SRT5732"]
    assert [o["uuid"] for o in question.options] == [s["uuid"] for s in SUGGEST]
    assert all(o["entity_type"] == "product" for o in question.options)
    assert question.team == "warehouse"
    assert question.payload.get("domain") == "inventory"
    assert question.payload.get("escalate_offered") is True


def test_several_missing_codes_each_get_their_own_list_numbered_on():
    answer = _answer(
        _envelope(
            {"SRTWC286": 12},
            ["srt5764", "srtwc99x"],
            {"srt5764": SUGGEST, "srtwc99x": OTHER},
        )
    )
    text = answer.text

    assert 'Couldn\'t find "srt5764" (product). Did you mean:\n1. SRT57-CR' in text, text
    assert 'Couldn\'t find "srtwc99x" (product). Did you mean:\n4. SRTWC991\n5. SRTWC992' in text, text
    assert text.count(CLOSING) == 1, text
    assert [o["position"] for o in answer.question.options] == [1, 2, 3, 4, 5]
    assert answer.question.options[3]["code"] == "SRTWC991"


def test_numbering_continues_after_numbered_found_blocks():
    """Q1 (a): once found blocks carry numbers (WA-CONCISE), a suggestion never reuses one."""
    answer = _answer(
        _envelope({"SRTWC286": 12, "SRTWC6022": 4}, ["srt5764"], {"srt5764": SUGGEST}, numbered=True)
    )

    assert "Did you mean:\n3. SRT57-CR\n4. SRT5713\n5. SRT5732" in answer.text, answer.text
    assert [o["position"] for o in answer.question.options] == [3, 4, 5]


def test_a_missing_code_with_no_suggestion_keeps_its_own_sentence():
    """Named after the lists, above the closing line (the all-miss reply's order)."""
    answer = _answer(
        _envelope({"SRTWC286": 12}, ["srt5764", "zzq123"], {"srt5764": SUGGEST})
    )
    text = answer.text

    assert (
        'Couldn\'t find "srt5764" (product). Did you mean:\n1. SRT57-CR\n2. SRT5713\n3. SRT5732'
        "\n\nI could not find zzq123.\n" + CLOSING
    ) in text, text
    assert text.count("I could not find") == 1, text


def test_no_suggestion_anywhere_names_the_code_and_offers_the_escalation():
    """Q4 (owner, 4 Oct 2026): the code gets what it gets asked alone - named, and the
    escalation offered (a yes/no over the domain's team)."""
    answer = _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {}))

    assert "I could not find zzq123." in answer.text, answer.text
    assert "Did you mean" not in answer.text
    assert answer.text.rstrip().endswith("Would you like me to escalate to warehouse team?"), answer.text
    assert answer.question is not None and answer.question.kind == "team_pick", answer.question
    assert answer.question.team == "warehouse"


def test_no_suggestion_anywhere_offers_staff_nothing():
    answer = _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {}), Profile(tier="office"))

    assert "I could not find zzq123." in answer.text, answer.text
    assert "escalate" not in answer.text
    assert answer.question is None


def test_no_suggestion_and_no_team_offers_nothing():
    policy_row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    policy = Policy.from_rows(domains=[policy_row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    answer = compose([_envelope({"SRTWC286": 12}, ["zzq123"], {})], _state(), policy, ctx=None)

    assert answer.text.rstrip().endswith("I could not find zzq123."), answer.text
    assert answer.question is None


def test_staff_get_the_did_you_mean_without_an_escalation_offer():
    answer = _answer(
        _envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST}), Profile(tier="office")
    )

    assert "Did you mean:\n1. SRT57-CR" in answer.text, answer.text
    assert answer.text.rstrip().endswith("Reply with a code to continue."), answer.text
    assert "escalate" not in answer.text
    assert answer.question is not None and answer.question.payload.get("escalate_offered") is False


def test_a_barred_contact_is_referred_to_their_salesman():
    from app.services.chatbot.turn.task import REFER_TO_SALESMAN

    answer = _answer(
        _envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST}),
        Profile(escalation_allowed=False),
    )

    assert "Did you mean:\n1. SRT57-CR" in answer.text, answer.text
    assert "escalate" not in answer.text
    assert REFER_TO_SALESMAN in answer.text
    assert answer.question.payload.get("escalate_offered") is False


def test_a_suggestion_already_answered_or_already_listed_is_not_offered_twice():
    """E2 + E4: a code this reply already answered is no suggestion, and one row never
    carries two numbers."""
    dup = [SUGGEST[1], {"code": "SRTWC286", "uuid": "66666666-6666-4666-8666-666666666666", "entity_type": "product"}]
    answer = _answer(
        _envelope({"SRTWC286": 12}, ["srt5764", "srt5713x"], {"srt5764": SUGGEST, "srt5713x": dup})
    )
    text = answer.text

    assert text.count("SRT5713") == 1, text
    assert [o["code"] for o in answer.question.options] == ["SRT57-CR", "SRT5713", "SRT5732"]
    # Nothing left to offer for the second code: it is named the plain way.
    assert "I could not find srt5713x." in text, text


def test_one_suggestion_in_total_arms_no_roster_but_keeps_the_offer():
    """AC-1691: no roster of fewer than two options. The offer printed still answers a
    yes, so it is armed as the domain's team offer."""
    answer = _answer(_envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST[:1]}))

    assert 'Did you mean:\n1. SRT57-CR' in answer.text, answer.text
    assert answer.text.rstrip().endswith(CLOSING), answer.text
    assert answer.question is not None and answer.question.kind == "team_pick", answer.question


def test_a_lane_question_keeps_the_turn_and_the_misses_are_still_named():
    env = _envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST})
    env["lane_ask"] = {
        "kind": "outstanding_scope",
        "last_result_set": [{"idx": 1, "label": "Sales orders", "value": "so"}, {"idx": 2, "label": "Delivery orders", "value": "do"}],
    }
    answer = _answer(env)

    assert answer.question is not None and answer.question.kind == "outstanding_scope"
    assert 'Couldn\'t find "srt5764" (product).' in answer.text, answer.text


# --------------------------------------------------------------------------- #
# All miss (Q5): the single-code paragraphs, one per code
# --------------------------------------------------------------------------- #


def _two_token_resolved(*, second_alts: bool = True) -> dict[str, Any]:
    return {
        "resolutions": [
            {"token": "srt5764", "resolved": False, "matches": [], "alternatives": [_match(r["code"], r["uuid"], "trgm") for r in SUGGEST]},
            {
                "token": "srtwc99x",
                "resolved": False,
                "matches": [],
                "alternatives": [_match(r["code"], r["uuid"], "trgm") for r in OTHER] if second_alts else [],
            },
        ],
        "unresolved_tokens": ["srt5764", "srtwc99x"],
    }


_PARSER = {
    "domain_hint": "inventory",
    "entities": [{"raw": "srt5764", "hint": "product"}, {"raw": "srtwc99x", "hint": "product"}],
}


def test_all_miss_reads_as_one_single_code_paragraph_per_code():
    from app.services.chatbot.lanes.business.answer import build_suggest_offer

    out = build_suggest_offer({}, parser=_PARSER, resolved=_two_token_resolved(), gate={"company_team": "warehouse"})
    text = out["suggest_response"]

    assert text == (
        'Couldn\'t find "srt5764" (product). Did you mean:\n1. SRT57-CR\n2. SRT5713\n3. SRT5732\n\n'
        'Couldn\'t find "srtwc99x" (product). Did you mean:\n4. SRTWC991\n5. SRTWC992\n'
        "Reply with a code to continue, or would you like me to escalate to warehouse team?"
    ), text
    assert [r["idx"] for r in out["suggest_last_result_set"]] == [1, 2, 3, 4, 5]


def test_all_miss_names_a_code_with_no_suggestion():
    from app.services.chatbot.lanes.business.answer import build_suggest_offer

    out = build_suggest_offer(
        {}, parser=_PARSER, resolved=_two_token_resolved(second_alts=False), gate={"company_team": "warehouse"}
    )
    text = out["suggest_response"]

    # The one code with suggestions reads exactly as it does asked alone.
    assert text.startswith('Couldn\'t find "srt5764" (product). Did you mean SRT57-CR, SRT5713, or SRT5732?'), text
    assert "\n\nI could not find srtwc99x.\nReply with a code to continue" in text, text


def test_all_miss_staff_get_no_escalation_clause():
    from app.services.chatbot.lanes.business.answer import build_suggest_offer

    out = build_suggest_offer(
        {}, parser=_PARSER, resolved=_two_token_resolved(), gate={"company_team": "warehouse"}, profile=Profile(tier="office")
    )

    assert out["suggest_response"].endswith("\nReply with a code to continue."), out["suggest_response"]


# --------------------------------------------------------------------------- #
# The candidates: the same ones the single-code did-you-mean offers
# --------------------------------------------------------------------------- #


def _match(code: str, uuid: str, tier: str, entity_type: str = "product") -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "canonical_code": code,
        "uuid": uuid,
        "match_tier": tier,
        "similarity": 0.5,
        "display": {"product_name": code},
    }


def test_did_you_mean_by_token_reads_each_missed_tokens_own_candidates():
    from app.services.chatbot.lanes.business.answer import did_you_mean_by_token

    resolved = {
        "resolutions": [
            {"token": "SRTWC286", "resolved": True, "matches": [_match("SRTWC286", "a" * 8 + "-aaaa-4aaa-8aaa-" + "a" * 12, "exact")], "alternatives": []},
            {
                "token": "SRT5764",
                "resolved": False,
                "matches": [],
                "alternatives": [
                    _match(s["code"], s["uuid"], "trgm") for s in SUGGEST
                ]
                + [_match("SRT5799", "77777777-7777-4777-8777-777777777777", "trgm")]
                + [_match("CUST1", "88888888-8888-4888-8888-888888888888", "trgm", "customer")],
            },
            {"token": "ZZQ123", "resolved": False, "matches": [], "alternatives": []},
        ],
        "unresolved_tokens": ["SRT5764", "ZZQ123"],
    }

    out = did_you_mean_by_token(resolved, {})

    assert list(out) == ["SRT5764"], out
    # The first three, as the single-code reply shows them.
    assert out["SRT5764"] == SUGGEST


def test_the_single_code_offer_still_reads_the_same_candidates():
    """The refactor kept `build_suggest_offer`'s own D1 arm on the shared helper."""
    from app.services.chatbot.lanes.business.answer import build_suggest_offer

    resolved = {
        "resolutions": [
            {"token": "srt5764", "resolved": False, "matches": [], "alternatives": [_match(s["code"], s["uuid"], "trgm") for s in SUGGEST]},
        ],
        "unresolved_tokens": ["srt5764"],
    }
    out = build_suggest_offer(
        {}, parser={"entities": [{"raw": "srt5764", "hint": "product"}]}, resolved=resolved, gate={}
    )

    assert out["suggest_offer"] is True
    assert [r["value"] for r in out["suggest_last_result_set"]] == ["SRT57-CR", "SRT5713", "SRT5732"]


# --------------------------------------------------------------------------- #
# The envelope carries them, keyed by what the customer typed
# --------------------------------------------------------------------------- #


def test_envelope_carries_each_unplaced_tokens_suggestions_under_the_typed_word():
    from app.services.chatbot.turn.plan import FetchSpec
    from app.services.chatbot.turn_runtime import envelope_of

    env = envelope_of(
        {"fetch": {"has_result": True, "answers": [{"fields": []}], "response": "x"}},
        FetchSpec(domain="inventory", entities=[], filters={}, date_window=None),
        [],
        unplaced={"srt5764": "srt5764", "zzq123": "zzq123"},
        suggestions={"SRT5764": SUGGEST},
    )

    assert env["unresolved"] == ["srt5764", "zzq123"]
    assert env["unresolved_suggestions"] == {
        "srt5764": {"head": 'Couldn\'t find "srt5764" (product). Did you mean:', "rows": SUGGEST}
    }


# --------------------------------------------------------------------------- #
# End to end: the real engine and the real resolver
# --------------------------------------------------------------------------- #


class TestRealResolverPartialMiss:
    """The seam chain `resolve_kinds` -> `engine._unplaced_suggestions` ->
    `make_tool_runner` -> `envelope_of` -> `compose`, on seeded codes: one placed code is
    answered, one typo gets its trigram neighbours as a numbered did-you-mean."""

    def test_typo_beside_a_found_code_gets_its_own_did_you_mean(self, session_factory, monkeypatch):
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests._mc_lookup_seed import product as seed_product
        from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact

        placed, near_a, near_b, typo = "ZZTMCDPLACED", "ZZTMCDNEAR12", "ZZTMCDNEAR13", "ZZTMCDNEAR1Q"
        db = session_factory()
        for code in (placed, near_a, near_b):
            seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.commit()
        _seed_contact(session_factory, variables={})

        def entity(raw: str) -> dict[str, Any]:
            return {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[entity(placed), entity(typo)]),
            text_body=f"{placed} {typo} sales order outstanding",
            msg_id="ZZT-mcd-1",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": placed},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert captured, "the placed code must still be fetched"
        assert f"Product: {placed}" in reply, reply
        assert f'Couldn\'t find "{typo}" (product). Did you mean:' in reply, reply
        assert near_a in reply and near_b in reply, reply
        assert f"I could not find {typo}" not in reply, reply


def test_a_name_or_bare_number_left_unplaced_gets_no_escalation_offer():
    """Q4 is for a product CODE: "chin chun" or a bare "1" the resolver could not place
    is named, and offers nothing."""
    for token in ("chin chun", "1"):
        answer = _answer(_envelope({"SRTWC286": 12}, [token], {}))
        assert f"I could not find {token}." in answer.text, answer.text
        assert "escalate" not in answer.text
        assert answer.question is None


def test_numbering_runs_on_after_numbered_items_under_a_group_heading():
    """Review S3: a grouped render ("*Group*\n1. ...") numbers its items inside a
    paragraph, not at its head; suggestions still never reuse a number."""
    env = _envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST})
    env["lane_text"] = "Stock availability:\n\n*Bukit Raja*\n1. SRTWC286 - 12\n\n*Johor*\n2. SRTWC286 - 4"
    answer = _answer(env)

    assert "Did you mean:\n3. SRT57-CR\n4. SRT5713\n5. SRT5732" in answer.text, answer.text


def test_a_lane_question_withholds_the_escalation_and_numbers_on_from_its_options():
    """Review S4: with the lane's question stored, "yes" cannot reach an escalation, so
    none is offered, and the suggestions do not restart at 1 under the lane's list."""
    env = _envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST})
    env["lane_text"] += "\n\nWhich list would you like?\n1. Sales orders\n2. Delivery orders"
    env["lane_ask"] = {
        "kind": "outstanding_detail",
        "last_result_set": [{"idx": 1, "label": "Sales orders", "value": "so"}, {"idx": 2, "label": "Delivery orders", "value": "do"}],
    }
    answer = _answer(env)

    assert answer.question.kind == "outstanding_detail"
    assert "escalate" not in answer.text, answer.text
    assert "Did you mean:\n3. SRT57-CR" in answer.text, answer.text
    assert answer.text.rstrip().endswith("Reply with a code to continue."), answer.text


def test_a_barred_contact_reads_the_salesman_line_once_when_every_section_missed():
    """Review N1: one suggestion, every section missed, escalation barred."""
    from app.services.chatbot.turn.task import REFER_TO_SALESMAN

    env = _envelope({"SRTWC286": 12}, ["srt5764"], {"srt5764": SUGGEST[:1]})
    env.update({"figures": [], "has_result": False, "tool_has_result": False, "miss": ["SRTWC286"]})
    env["lane_text"] = "No matching results found."
    answer = _answer(env, Profile(escalation_allowed=False))

    assert "Did you mean:\n1. SRT57-CR" in answer.text, answer.text
    assert answer.text.count(REFER_TO_SALESMAN) == 1, answer.text
