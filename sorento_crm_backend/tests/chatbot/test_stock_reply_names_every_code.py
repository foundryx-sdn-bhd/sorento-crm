"""Prod turn f0a2 (1 Oct 2026): "Srtswt3001 / Srtswt3001-gm stock". Both codes resolved,
SRTSWT3001 had no stock rows, and the reply listed only SRTSWT3001-GM without a word
about SRTSWT3001. A resolved code with no row in a stock answer is named, once, on one
line; an unresolved token keeps its own "I could not find" sentence.
"""
from __future__ import annotations

from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

_NO_STOCK_PREFIX = "No stock found for"


def _policy() -> Policy:
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state() -> State:
    return State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)


def _fig(code: str, qty: int) -> dict:
    return {
        "fields": [
            {"label": "Product Code", "value": code},
            {"label": "Warehouse", "value": "BUKIT RAJA"},
            {"label": "Quantity On Hand", "value": qty},
        ]
    }


def _row_text(code: str, qty: int) -> str:
    return f"*Product Code:* {code}\n*Warehouse:* BUKIT RAJA\n*Quantity On Hand:* {qty}"


def _envelope(
    entities: list[str],
    rows: dict[str, int],
    unresolved: list[str] | None = None,
    *,
    customers: list[str] | None = None,
    footer: bool = False,
) -> dict:
    figures = [_fig(code, qty) for code, qty in rows.items()]
    lane_text = (
        "Stock availability:\n" + "\n\n".join(_row_text(c, q) for c, q in rows.items())
        if rows
        else "No matching results found."
    )
    if footer:
        lane_text += "\n\n_Data last updated: 1 Oct 2026 10:05_"
    return {
        "domain": "inventory",
        "denied": False,
        "entities": [*entities, *(customers or [])],
        # `turn_runtime.envelope_of`: the product subjects alone.
        "product_codes": list(entities),
        "figures": figures,
        "files": [],
        "miss": [] if rows else entities,
        "has_result": bool(rows),
        "tool_has_result": bool(rows),
        "unresolved": unresolved or [],
        "error": None,
        "lane_text": lane_text,
    }


def _text(env: dict) -> str:
    return compose([env], _state(), _policy(), ctx=None).text


def test_resolved_code_with_no_row_is_named():
    text = _text(_envelope(["SRTSWT3001", "SRTSWT3001-GM"], {"SRTSWT3001-GM": 12}))

    assert "SRTSWT3001-GM" in text and "12" in text, "the real row must still print"
    assert "No stock found for SRTSWT3001." in text, text
    assert text.count(_NO_STOCK_PREFIX) == 1, text


def test_every_code_with_rows_adds_no_line():
    text = _text(_envelope(["SRTSWT3001", "SRTSWT3001-GM"], {"SRTSWT3001": 5, "SRTSWT3001-GM": 12}))

    assert _NO_STOCK_PREFIX not in text, text


def test_whole_section_miss_says_it_once():
    text = _text(_envelope(["SRTSWT3001", "SRTSWT3001-GM"], {}))

    assert text.count(_NO_STOCK_PREFIX) == 1, text
    assert "SRTSWT3001" in text and "SRTSWT3001-GM" in text


def test_unresolved_token_keeps_its_own_sentence_only():
    text = _text(_envelope(["SRTSWT3001-GM"], {"SRTSWT3001-GM": 12}, unresolved=["wc2867"]))

    assert "I could not find wc2867." in text, text
    assert _NO_STOCK_PREFIX not in text, text


def test_several_missing_codes_share_one_line():
    text = _text(_envelope(["AAA1", "BBB2", "CCC3"], {"CCC3": 4}))

    assert "No stock found for AAA1 or BBB2." in text, text
    assert text.count(_NO_STOCK_PREFIX) == 1, text


def test_a_code_that_prefixes_another_requested_code_still_reaches_the_stock_tool():
    """Prod f0a2 log showed one product id on the stock call. Pinned here: from the
    resolver's rows down to the tool arguments, a requested code that is a prefix of
    another requested code is neither merged nor dropped (gate, then the argument
    builder), so the no-rows silence was the composer's, not the fetch's."""
    from app.services.chatbot.lanes.business.fetch import entity_ids_transformer
    from app.services.chatbot.lanes.business.gate import run_gate

    plain = "11111111-1111-4111-8111-111111111111"
    suffixed = "22222222-2222-4222-8222-222222222222"

    def match(uuid: str, code: str, tier: str) -> dict:
        return {
            "uuid": uuid,
            "entity_type": "product",
            "canonical_code": code,
            "match_tier": tier,
            "display": {"product_name": code},
        }

    resolver = {
        "tokens": ["srtswt3001", "srtswt3001-gm"],
        "resolutions": [
            {
                "token": "srtswt3001",
                "matches": [match(plain, "SRTSWT3001", "exact"), match(suffixed, "SRTSWT3001-GM", "prefix")],
            },
            {"token": "srtswt3001-gm", "matches": [match(suffixed, "SRTSWT3001-GM", "exact")]},
        ],
    }
    parser = {
        "domain_hint": "inventory",
        "entities": [
            {"hint": "product", "raw": "Srtswt3001"},
            {"hint": "product", "raw": "Srtswt3001-gm"},
        ],
    }

    gated = run_gate(dict(resolver), parser=parser, resolver=resolver)
    args = entity_ids_transformer(
        {"entities": gated["compatible_entities"], "tool": "crm_inventory_stock_balance"}
    )

    assert gated["gate_passed"] is True
    assert args["product_ids"] == [plain, suffixed]


def test_a_pinned_customer_is_never_named_as_a_stock_code():
    """Review round 2 C1: a customer subject carried into a stock ask is not a code."""
    text = _text(
        _envelope(["SRTSWT3001-GM"], {"SRTSWT3001-GM": 12}, customers=["CHIN CHUN HARDWARE"])
    )
    assert _NO_STOCK_PREFIX not in text, text


def test_a_counted_set_answer_adds_no_line():
    """Review round 2 C2: a described / counted set carries every member as a subject;
    naming each out-of-stock one would dump the set on one line."""
    env = _envelope(["AAA1", "BBB2", "CCC3"], {"CCC3": 4})
    env["header_override"] = "1 basin is in stock."
    assert _NO_STOCK_PREFIX not in _text(env)


def test_more_codes_than_the_header_shows_adds_no_line():
    from app.services.chatbot.turn.compose import HEADER_SUBJECT_MAX

    codes = [f"C{i:03d}" for i in range(HEADER_SUBJECT_MAX + 1)]
    assert _NO_STOCK_PREFIX not in _text(_envelope(codes, {codes[0]: 1}))


def test_the_line_sits_above_the_data_footer():
    text = _text(_envelope(["SRTSWT3001", "SRTSWT3001-GM"], {"SRTSWT3001-GM": 12}, footer=True))
    assert text.index("No stock found for SRTSWT3001.") < text.index("_Data last updated")


# --------------------------------------------------------------------------- #
# Crew browser pass on 9fba50f0 (TESTER-LOCAL): SRTSWT3001 dropped before compose
# --------------------------------------------------------------------------- #


def _resolution(token: str, *codes: str) -> dict:
    return {
        "token": token,
        "matches": [
            {"uuid": f"uuid-{c}", "entity_type": "product", "canonical_code": c, "match_tier": "prefix"}
            for c in codes
        ],
    }


def _typed(*raws: str) -> list[dict]:
    return [{"raw": r, "hint": "product", "current_message": True} for r in raws]


def test_a_token_answered_only_by_another_typed_code_is_reported_unplaced():
    """The crew copy's looked_up envelope: `entities: [SRTSWT3001-GM]`, `unresolved: []`.
    The resolver placed `srtswt3001` on SRTSWT3001-GM, so the gate deduped it away."""
    from app.services.chatbot.turn_runtime import absorbed_tokens

    resolved = {
        "resolutions": [
            _resolution("srtswt3001", "SRTSWT3001-GM"),
            _resolution("srtswt3001gm", "SRTSWT3001-GM"),
        ]
    }
    assert absorbed_tokens(_typed("Srtswt3001", "Srtswt3001-gm"), resolved) == {
        "srtswt3001": "Srtswt3001"
    }


def test_a_token_with_its_own_exact_match_is_not_absorbed():
    from app.services.chatbot.turn_runtime import absorbed_tokens

    resolved = {
        "resolutions": [
            _resolution("srtswt3001", "SRTSWT3001", "SRTSWT3001-GM"),
            _resolution("srtswt3001gm", "SRTSWT3001-GM"),
        ]
    }
    assert absorbed_tokens(_typed("Srtswt3001", "Srtswt3001-gm"), resolved) == {}


def test_a_family_prefix_matching_untyped_members_is_not_absorbed():
    """`srt5674` covering SRT5674-N AND SRT5674-NL is a real answer even when the customer
    also typed SRT5674-N: it matched something nobody else typed."""
    from app.services.chatbot.turn_runtime import absorbed_tokens

    resolved = {
        "resolutions": [
            _resolution("srt5674", "SRT5674-N", "SRT5674-NL"),
            _resolution("srt5674n", "SRT5674-N"),
        ]
    }
    assert absorbed_tokens(_typed("srt5674", "srt5674-n"), resolved) == {}


def test_a_single_token_is_never_absorbed():
    from app.services.chatbot.turn_runtime import absorbed_tokens

    resolved = {"resolutions": [_resolution("srtswt3001", "SRTSWT3001-GM")]}
    assert absorbed_tokens(_typed("Srtswt3001"), resolved) == {}


def test_the_absorbed_token_is_named_in_the_reply():
    """End of the chain: an unplaced token reaches compose as `unresolved`."""
    text = _text(_envelope(["SRTSWT3001-GM"], {"SRTSWT3001-GM": 20}, unresolved=["Srtswt3001"]))
    assert "I could not find Srtswt3001." in text, text
    assert "SRTSWT3001-GM" in text
