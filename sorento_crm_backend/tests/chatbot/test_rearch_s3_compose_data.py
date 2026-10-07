"""S3 - composers return data first (AC-1531, PLAN-chatbot-turn-rearch.md "Fetch and
compose": `{sections: [{domain, entities, figures, files, miss}], question, offer,
canned}`; text rendered from that data by the #930 grammar, contract 102 to 105).

`turn/compose.py::compose(envelopes, state, policy, ctx) -> Answer` does not exist yet,
so EVERY test below is RED at collection with `ModuleNotFoundError: No module named
'app.services.chatbot.turn.compose'` - the right reason.

Golden bodies (coordinator instruction, mid-task): the #930 lane is parked LOCAL-ONLY,
worktree `chatbot-answer-feedback-15sep` (branch `feat/chatbot-answer-feedback-15sep`,
code head 128976f47), read read-only, never imported across worktrees. The literal
strings below (`_STOCK_ROWS_TEXT`, `_REASON_1`, `_INCOMING_ROW_TEXT`, `_CLOSING`,
`_ESCALATE`) are copied byte-for-byte from
`sorento_crm_backend/tests/chatbot/test_answer_feedback_golden_bodies.py::
test_golden_msk11c_stock`'s `stock_body` / `reason_1` / `incoming_body` / `closing` /
`escalate` locals in that worktree (bubble 1, "msk11c Stock" - the only golden bubble
whose reason line, escalate line and file-fold sentence all fall inside one bubble).
The retired-sentence list is copied from the same worktree's
`test_answer_feedback_retired_sentences.py::_RETIRED_STRINGS`.

**Ambiguity flagged to the captain**: the Section `figures` shape is not specified
anywhere in the PLAN/UAC beyond the name. This file assumes `figures` is a list of
`{"fields": [{"label": ..., "value": ...}], "flags": {...}}` dicts - the SAME per-row
shape `lanes/business/fetch.py::output_structurer` already renders from today (so the
#930 grammar's row rendering, which is explicitly "kept, tightened" per the PLAN, has
somewhere to read from without inventing a new row shape). If the coder picks a
different `figures` shape, `_stock_figures`/`_incoming_figures` below need updating to
match - the STRUCTURAL assertions (one section per envelope, ordering, dedup, offer,
file-fold, retired sentences) do not depend on this choice, only the one golden-body
comparison test does.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.chatbot.turn.state import Profile, State, Focus

from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

# --------------------------------------------------------------------------- #
# Golden literals, copied from the #930 worktree (see module docstring).
# --------------------------------------------------------------------------- #

_TS = "15/09/2026 11:53:07"
_PENDING = "\U0001f6a9  *(PENDING ALLOCATION)*"

_STOCK_ROWS_TEXT = (
    "1. *Company:* Mocha\n*Product Code:* MSK11C\n*Warehouse:* MERU\n"
    "*System Location:* MOCHA-WH\n*Quantity On Hand:* 0\n\n"
    "2. *Company:* Mocha\n*Product Code:* MSK11C-BL-DIY\n*Warehouse:* MERU\n"
    "*System Location:* MOCHA-WH\n*Quantity On Hand:* 0\n\n"
    "3. *Company:* Mocha\n*Product Code:* MSK11C-DIY\n*Warehouse:* MERU\n"
    "*System Location:* MOCHA-WH\n*Quantity On Hand:* 0\n\n"
    "4. *Company:* Mocha\n*Product Code:* MSK11C-GM-DIY\n*Warehouse:* MERU\n"
    "*System Location:* MOCHA-WH\n*Quantity On Hand:* 50"
)
_REASON_1 = "MSK11C, MSK11C-BL-DIY, MSK11C-DIY have 0 on hand, so I checked what is coming in."
_INCOMING_ROW_TEXT = (
    "1. *Company:* Mocha\n*Product Code:* MSK11C-BL-DIY\n*Container:* SEGU4140083\n"
    f"*ETA:* 2026-09-18\n*Incoming Quantity:* 200\n{_PENDING}"
)
_CLOSING = "No stock, no incoming and no outstanding purchase order for MSK11C, MSK11C-DIY."
_ESCALATE = "Would you like me to escalate to purchasing team?"
_ATTACHED_SENTENCE = "I have attached the file(s) below."

_RETIRED_SENTENCES = (
    "But there is INCOMING stock (ETA) for the requested products",
    "But here are the stock details for the requested products",
    "Stock is 0 at every location and no incoming for",
    "No incoming and stock is 0 at every location for",
    "but PO is placed",
    "but stock is on order from the supplier",
)


def _stock_domain_row() -> dict[str, Any]:
    return _domain_row("inventory", narrowing={"product": "list_all"}, tools=("crm_inventory_stock_balance_list",))


def _incoming_domain_row() -> dict[str, Any]:
    row = _domain_row("incoming", narrowing={"product": "narrow_to_code"}, tools=("crm_incoming_stock_list",))
    row["escalation_team_code"] = "purchasing"
    return row


def _po_domain_row() -> dict[str, Any]:
    row = _domain_row("purchase_order", narrowing={"product": "narrow_to_code"}, tools=("crm_procurement_po_placed_list",))
    row["escalation_team_code"] = "purchasing"
    return row


def _policy(*rows: dict[str, Any]):
    from app.services.chatbot.turn.policy import Policy

    labeled = []
    for row in rows:
        labeled.append(row)
    return Policy.from_rows(domains=labeled, kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state() -> State:
    return State(focus=Focus(), pending=None, profile=Profile(), turn_no=1)


def _compose(envelopes: list[dict[str, Any]], *, policy=None, ctx=None):
    from app.services.chatbot.turn.compose import compose

    return compose(envelopes, _state(), policy or _policy(_stock_domain_row(), _incoming_domain_row()), ctx or SimpleNamespace())


def _envelope(domain: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "domain": domain,
        "denied": False,
        "entities": [],
        "figures": [],
        "files": [],
        "miss": [],
    }
    base.update(overrides)
    return base


class TestOneSectionPerEnvelopeInOrder:
    def test_sections_match_envelope_order(self) -> None:
        envelopes = [
            _envelope("inventory", entities=["MSK11C"]),
            _envelope("incoming", entities=["MSK11C-BL-DIY"]),
        ]
        answer = _compose(envelopes)

        assert [s.domain for s in answer.sections] == ["inventory", "incoming"]

    def test_section_carries_entity_ids_and_figures(self) -> None:
        figures = [{"fields": [{"label": "Product Code", "value": "MSK11C"}]}]
        envelopes = [_envelope("inventory", entities=["MSK11C"], figures=figures)]
        answer = _compose(envelopes)

        section = answer.sections[0]
        assert section.entities == ["MSK11C"]
        assert section.figures == figures


class TestSectionHeaderGrammar930:
    """Header `*<Label>* for <codes>:` (Label from the domain row), rows numbered
    from 1 per section."""

    def test_header_uses_domain_label_and_codes(self) -> None:
        row = _domain_row("inventory", narrowing={"product": "list_all"})
        row["label"] = "Stock"
        policy = _policy(row)
        # STOCK-FILL-PREDICATE (never-stuck): a header prints over a body, never over
        # nothing - so the grammar is read off a section that has a row.
        envelopes = [
            _envelope(
                "inventory",
                entities=["MSK11C", "MSK11C-BL-DIY"],
                figures=[{"fields": [{"label": "Product Code", "value": "MSK11C"}]}],
            )
        ]

        answer = _compose(envelopes, policy=policy)

        assert answer.text.startswith("*Stock* for MSK11C, MSK11C-BL-DIY:"), answer.text

    def test_rows_numbered_from_one_per_section(self) -> None:
        row_a = _domain_row("inventory", narrowing={"product": "list_all"})
        row_a["label"] = "Stock"
        row_b = _incoming_domain_row()
        row_b["label"] = "Incoming (ETA)"
        policy = _policy(row_a, row_b)
        figures_a = [{"fields": [{"label": "Product Code", "value": "A"}]}, {"fields": [{"label": "Product Code", "value": "B"}]}]
        figures_b = [{"fields": [{"label": "Product Code", "value": "C"}]}]
        envelopes = [
            _envelope("inventory", entities=["A", "B"], figures=figures_a),
            _envelope("incoming", entities=["C"], figures=figures_b),
        ]

        answer = _compose(envelopes, policy=policy)

        assert answer.text.count("1.") == 2, (
            f"each section restarts numbering at 1 - expected two '1.' rows, got: {answer.text!r}"
        )


class TestFactDedupOneSectionOnly:
    def test_fact_for_entity_and_domain_appears_in_exactly_one_section(self) -> None:
        row_a = _domain_row("inventory", narrowing={"product": "list_all"})
        row_a["label"] = "Stock"
        row_b = _incoming_domain_row()
        row_b["label"] = "Incoming (ETA)"
        policy = _policy(row_a, row_b)
        figures = [{"fields": [{"label": "Product Code", "value": "MSK11C"}]}]
        envelopes = [
            _envelope("inventory", entities=["MSK11C"], figures=figures),
            _envelope("incoming", entities=["MSK11C"], figures=figures),
        ]

        answer = _compose(envelopes, policy=policy)

        occurrences = answer.text.count("*Product Code:* MSK11C")
        assert occurrences == 1, (
            f"the same fact for (MSK11C, one domain) must not repeat across sections "
            f"once deduped, saw it {occurrences} times: {answer.text!r}"
        )


class TestOfferOnTotalMissVsPartialMiss:
    def test_total_miss_yields_offer_with_missed_domain_teams_in_section_order_and_one_escalate_line(self) -> None:
        row_a = _domain_row("inventory", narrowing={"product": "list_all"})
        row_a["label"] = "Stock"
        row_a["escalation_team_code"] = "warehouse"
        row_b = _incoming_domain_row()
        row_b["label"] = "Incoming (ETA)"
        row_b["escalation_team_code"] = "purchasing"
        policy = _policy(row_a, row_b)
        envelopes = [
            _envelope("inventory", entities=["MSK11C"], miss=["MSK11C"]),
            _envelope("incoming", entities=["MSK11C"], miss=["MSK11C"]),
        ]

        answer = _compose(envelopes, policy=policy)

        assert answer.offer is not None
        assert answer.offer.teams == ["warehouse", "purchasing"]
        assert answer.text.count("Would you like me to escalate") == 1, answer.text

    def test_partial_miss_yields_no_offer(self) -> None:
        row = _domain_row("inventory", narrowing={"product": "list_all"})
        row["label"] = "Stock"
        row["escalation_team_code"] = "warehouse"
        policy = _policy(row)
        envelopes = [_envelope("inventory", entities=["MSK11C", "MSK11C-DIY"], miss=["MSK11C-DIY"])]

        answer = _compose(envelopes, policy=policy)

        assert answer.offer is None


class TestFilesFoldOnceDeduped:
    def test_files_from_two_sections_fold_into_one_actions_send_deduped_by_id_or_url(self) -> None:
        row_a = _domain_row("inventory", narrowing={"product": "list_all"})
        row_a["label"] = "Stock"
        row_b = _incoming_domain_row()
        row_b["label"] = "Incoming (ETA)"
        policy = _policy(row_a, row_b)
        file_a = {"url": "https://cdn.example/a.pdf", "filename": "a.pdf"}
        file_b = {"url": "https://cdn.example/a.pdf", "filename": "a.pdf"}  # same url - a dup
        file_c = {"url": "https://cdn.example/b.pdf", "filename": "b.pdf"}
        envelopes = [
            _envelope("inventory", entities=["MSK11C"], files=[file_a]),
            _envelope("incoming", entities=["MSK11C"], files=[file_b, file_c]),
        ]

        answer = _compose(envelopes, policy=policy)

        file_actions = [a for a in answer.actions if a.get("url") or a.get("kind") == "send_file"]
        urls = [a.get("url") for a in file_actions]
        assert sorted(set(urls)) == sorted(urls), f"file actions must be deduped: {urls!r}"
        assert set(urls) == {"https://cdn.example/a.pdf", "https://cdn.example/b.pdf"}
        assert answer.text.count(_ATTACHED_SENTENCE) == 1, answer.text


class TestRetiredSentencesNeverAppear:
    """Five stub envelope shapes exercising a miss, a fallback ladder, an SPO row and a
    fully-answered section - none of the six retired sentences ever appears in the
    rendered text."""

    STUB_SHAPES = [
        # 1: total miss on one domain.
        [_envelope("inventory", entities=["A"], miss=["A"])],
        # 2: fallback ladder inventory -> incoming both zero.
        [
            _envelope("inventory", entities=["A"], miss=["A"]),
            _envelope("incoming", entities=["A"], miss=["A"]),
        ],
        # 3: fallback ladder inventory -> incoming -> purchase_order, PO answers.
        [
            _envelope("inventory", entities=["A"], miss=["A"]),
            _envelope("incoming", entities=["A"], miss=["A"]),
            _envelope("purchase_order", entities=["A"], figures=[{"fields": [{"label": "Product Code", "value": "A"}]}]),
        ],
        # 4: an SPO row unioned into the purchase-order section (contract: AC-27 port).
        [
            _envelope(
                "purchase_order",
                entities=["A"],
                figures=[{"fields": [{"label": "Product Code", "value": "A"}, {"label": "SPO", "value": "SPO-1"}]}],
            )
        ],
        # 5: fully answered, nothing missed anywhere.
        [_envelope("inventory", entities=["A"], figures=[{"fields": [{"label": "Product Code", "value": "A"}]}])],
    ]

    @pytest.mark.parametrize("shape_index", range(len(STUB_SHAPES)))
    @pytest.mark.parametrize("retired", _RETIRED_SENTENCES)
    def test_retired_sentence_absent(self, shape_index: int, retired: str) -> None:
        row_a = _domain_row("inventory", narrowing={"product": "list_all"})
        row_a["label"] = "Stock"
        row_a["escalation_team_code"] = "warehouse"
        row_b = _incoming_domain_row()
        row_b["label"] = "Incoming (ETA)"
        row_c = _po_domain_row()
        row_c["label"] = "Purchase orders placed"
        policy = _policy(row_a, row_b, row_c)

        answer = _compose(self.STUB_SHAPES[shape_index], policy=policy)

        assert retired not in answer.text, (
            f"retired sentence {retired!r} must never appear, shape {shape_index}: {answer.text!r}"
        )


class TestGoldenBodyStockSection:
    """Bubble 1 (MSK11C) from the #930 golden bodies file, lifted as the expected
    `Answer.text` for a stub envelope built with this file's own `figures` shape
    assumption (see module docstring)."""

    def _msk11c_figures(self) -> list[dict[str, Any]]:
        def row(company: str, code: str, qty: int) -> dict[str, Any]:
            return {
                "fields": [
                    {"label": "Company", "value": company},
                    {"label": "Product Code", "value": code},
                    {"label": "Warehouse", "value": "MERU"},
                    {"label": "System Location", "value": "MOCHA-WH"},
                    {"label": "Quantity On Hand", "value": qty},
                ]
            }

        return [
            row("Mocha", "MSK11C", 0),
            row("Mocha", "MSK11C-BL-DIY", 0),
            row("Mocha", "MSK11C-DIY", 0),
            row("Mocha", "MSK11C-GM-DIY", 50),
        ]

    def test_stock_section_text_matches_the_golden_row_block(self) -> None:
        row = _domain_row("inventory", narrowing={"product": "list_all"})
        row["label"] = "Stock"
        policy = _policy(row)
        envelopes = [
            _envelope(
                "inventory",
                entities=["MSK11C", "MSK11C-BL-DIY", "MSK11C-DIY", "MSK11C-GM-DIY"],
                figures=self._msk11c_figures(),
            )
        ]

        answer = _compose(envelopes, policy=policy)

        assert _STOCK_ROWS_TEXT in answer.text, (
            f"the rendered stock rows must match the golden bubble byte-for-byte:\n"
            f"expected substring:\n{_STOCK_ROWS_TEXT!r}\ngot:\n{answer.text!r}"
        )
