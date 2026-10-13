"""S5 (PLAN-chatbot-media-into-turn.md): the rearch engine's "a token nobody could
place is named, never dropped" rule (`turn/compose.py:355-371`) did not fire for the
owner's 14:34 turn (image + caption, two of ten codes silently absent, no "Couldn't
find" line - PLAN Evidence 3).

AC-1850 to AC-1853 (UAC section F). Reproduces the SHAPE of that miss - several placed
codes answered normally, several genuinely unplaceable codes (zero fuzzy neighbours) -
through the real engine + real resolver (`test_outstanding_lane.py`'s `real_resolver=True`
harness), rather than a hand-built `compose.py` call, so the test exercises the actual
seam chain the plan names (`resolve_kinds` -> `unplaced_tokens` -> `envelope_of.unresolved`
-> `compose`).

**Simplification flagged to the captain**: the recorded 14:34 turn (image + 10 codes,
2 unplaced) is not available as a fixture on this branch (no replay JSON exists for it -
`grep` across `tests/chatbot/` and `documentation/` found nothing naming `M496-GM` /
`MBF 9902` / the exec ids). This file reproduces the STRUCTURE (N placed + M unplaced,
zero-neighbour tokens) with its own seeded ZZT product codes instead of the exact prod
codes - the plan's own kill test (AC-1853) is satisfied the same way regardless of which
codes are used, and a prod-code-accurate replay can be added later if the recorded
verdict is found.

Current (pre-fix) wording is "I could not find {codes}." (`compose.py:371`) - already
close to but not the same as the plan's target "Couldn't find: {codes}." (colon, verb
tense), so every test here is red against the PLAN's wording even on a turn where
today's existing bare-miss line already fires.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot._turn_helpers import make_office_access_type
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_outstanding_lane import (
    REPORT_HIT,
    _qf,
    _seed_contact,
    _run_turn,
)


def _seed_products(session_factory, codes: list[str]) -> None:
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    for code in codes:
        seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.commit()


PLACED_CODE = "ZZTPARTIALA"
UNPLACED_1 = "ZZQNOMATCH1"
UNPLACED_2 = "ZZQNOMATCH2"


def _entity(raw: str) -> dict[str, Any]:
    return {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}


class TestTwoUnplacedTokensAreBothNamed:
    """AC-1850/AC-1852: the kill test's shape - a real answer for the placed code, PLUS
    a single line naming every unplaceable token, verbatim as typed.

    HELD, coordinator ruling 22 Sep 2026: the rearch engine's existing miss line
    (`turn/compose.py:371`, "I could not find X or Y.") ALREADY names both
    zero-neighbour tokens through this exact seam chain - measured, not assumed,
    by running this test against the unmodified engine. That contradicts the
    plan's own Evidence 3 (the owner's 14:34 turn silently dropped two codes), so
    the real cause of that drop is not this branch and the owner is pasting the
    turn's Technical details to find it. Per the ruling: do NOT rewrite the
    existing "I could not find X or Y." wording to the plan's target "Couldn't
    find: X, Y." just to turn this test green - the assertion below is loosened
    to the CURRENT sentence shape (still naming both tokens, still the kill
    test's real guard) until the trace arrives and the actual seam is found.
    """

    def test_couldnt_find_line_names_every_unplaced_token(self, session_factory, monkeypatch) -> None:
        _seed_products(session_factory, [PLACED_CODE])
        _seed_contact(session_factory, variables={})
        # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
        make_office_access_type(session_factory, CONTACT_ID)

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                entities=[_entity(PLACED_CODE), _entity(UNPLACED_1), _entity(UNPLACED_2)],
            ),
            text_body=f"{PLACED_CODE} {UNPLACED_1} {UNPLACED_2} sales order outstanding",
            msg_id="ZZT-partial-hit-1",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": PLACED_CODE},
            real_resolver=True,
        )

        assert captured, "the placed code must still be fetched and answered"
        reply = (result.reply or {}).get("text") or ""
        assert f"Product: {PLACED_CODE}" in reply, (
            f"expected a real answer for the placed code before the miss line: {reply!r}"
        )
        # HELD (see class docstring): the CURRENT wording, not the plan's target -
        # still a hard requirement that BOTH unplaced tokens are named, verbatim.
        assert f"I could not find {UNPLACED_1} or {UNPLACED_2}." in reply, (
            f"expected the existing miss-line wording naming both unplaced tokens: {reply!r}"
        )

    @pytest.mark.skip(reason="S5 deferred: awaiting the 14:34 prod trace")
    def test_kill_test_mutation_documented(self) -> None:
        """AC-1853. Documented rather than executed against a second code path (there is
        none to mutate yet): once S5 lands, REMOVING the new "name every unplaced token"
        branch (`turn/compose.py`'s rewritten miss-line block, or whichever seam the
        coder's fix lands in - `resolve_kinds`'s `unplaced_tokens` plumbing,
        `envelope_of.unresolved`, or the compose join itself) must make
        `test_couldnt_find_line_names_every_unplaced_token` above fail. The tester
        records the mutation site here rather than asserting on it blind, since the fix
        location is the coder's to choose (plan: "diagnose along the seam", not
        prescribed)."""
        assert True


class TestOneUnplacedTokenWithADidYouMeanNeighbourIsUnchanged:
    """AC-1851: a genuinely near-miss token still gets the EXISTING suggest/roster
    behaviour, not a duplicate "Couldn't find" mention on top of it.

    NOTE FOR THE CAPTAIN: measured PASSING today (not independently red) - the existing
    did-you-mean path already avoids a bare miss line for a real trigram neighbour. Kept
    as the regression guard AC-1851 asks for ("unchanged"), proving that guarantee holds
    both before and after S5's wording fix."""

    def test_near_miss_token_is_suggested_not_flatly_missed(self, session_factory, monkeypatch) -> None:
        near_code = "ZZTNEARMISS1"
        typo = "ZZTNEARMISS2"  # one character different - a real trigram neighbour
        _seed_products(session_factory, [near_code])
        _seed_contact(session_factory, variables={})

        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding", entities=[_entity(typo)]),
            text_body=f"{typo} sales order outstanding",
            msg_id="ZZT-partial-hit-neighbour-1",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "product_code": near_code},
            real_resolver=True,
        )

        reply = (result.reply or {}).get("text") or ""
        assert f"Couldn't find: {typo}." not in reply, (
            f"a token with a real trigram neighbour must not be flatly named as unplaced: {reply!r}"
        )
        assert near_code in reply, (
            f"expected the near-miss neighbour {near_code!r} to be surfaced (did-you-mean or "
            f"silent resolve), got: {reply!r}"
        )
