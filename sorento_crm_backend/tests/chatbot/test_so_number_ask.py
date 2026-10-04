"""SO-NUMBER-ASK: an ask about one SO number answers about that SO only.

Tester note on PR #1433 ("E3 SO number"): a linked dealer asked `status of SO422056`. The
resolver has no probe for `sales_orders.so_number` (it reads `orders.order_number`, the DO
book), so the token came back unresolved. The engine then swapped the resolver's
not-found exit for the dealer's linked customers (`engine._scoped_compatible` +
`engine._pass_scope_gate`), the orders list ran on those customer ids alone, and the
reply was a 20-row DO dump over every linked customer closed by "I could not find
SO422056." `turn_runtime._answered_unfiltered` did not catch it because the scope rows
carry real uuids, so the fetch did not read as unfiltered.

Rules under test (PLAN-so-number-ask.md, UAC so-number-ask-acceptance-criteria.md):

* A word nobody could place is a miss, whatever customer scope the contact carries: the
  scope rows are not subjects, so the links' DO list never reaches the reply.
* An SO-shaped word is answered from `sales_orders` (`app/services/chatbot/so_status.py`):
  one card per SO in scope, the scope refusal for an SO outside the links, one
  "I could not find" line for the rest, all behind the `sales_orders.outstanding` key.

Same harness as `test_customer_scope_lane.py` (one real `engine.run_turn`, parser,
access, resolver and MCP faked). Postgres only.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_customer_scope_lane import ORDERS, _calls, _link_customers
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import CONTACT_ID, _run_turn, _seed_contact

SO = "SO422056"
DUMP_DO = "DOZZTDUMP1"

#: What the orders list would have answered on the scope alone: a DO the SO ask never named.
DO_DUMP = {
    "has_result": True,
    "response": f"*Orders*\n1. {DUMP_DO} - ZZT OWN A - Delivered\n2. DOZZTDUMP2 - ZZT OWN B - Processing",
    "items": [{"order_number": DUMP_DO}, {"order_number": "DOZZTDUMP2"}],
}


def _so_ask(hint: str, raw: str = SO) -> dict[str, Any]:
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
    )


def _turn(session_factory, monkeypatch, qf: dict[str, Any], body: str, attributes=("sales_orders.outstanding",)):
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body=body,
        msg_id=f"ZZT-so-ask-{uuid.uuid4().hex[:10]}",
        attributes=list(attributes),
        matches={},  # the resolver places nothing: it has no SO probe
        mcp_response=DO_DUMP,
    )
    return ((result.reply or {}).get("text") or ""), captured


class TestUnresolvedSoNumberIsOneMissLine:
    @pytest.mark.parametrize("hint", ["order", "order_number", "customer_order"])
    def test_linked_dealer_gets_one_miss_line(self, session_factory, monkeypatch, hint) -> None:
        """AC-SO-01: no such SO -> the one line, no DO rows, no customer names. Answered by
        the SO lookup now; the `_answered_unfiltered` scope fix this lane started with is
        guarded by `test_unplaced_non_so_word_is_a_miss_not_the_links_list` below."""
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A", "ZZT OWN B")

        reply, captured = _turn(session_factory, monkeypatch, _so_ask(hint), f"status of {SO}")

        assert reply.strip() == f"I could not find {SO}.", reply
        assert _calls(captured, ORDERS) == [], captured

    def test_unplaced_non_so_word_is_a_miss_not_the_links_list(self, session_factory, monkeypatch) -> None:
        """The same hole for any word nothing placed: the scope rows are not subjects, so the
        DO list the links alone produced never reaches the reply, and no scope header either."""
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A", "ZZT OWN B")

        reply, _captured = _turn(session_factory, monkeypatch, _so_ask("order", "DOZZT999"), "status of DOZZT999")

        assert DUMP_DO not in reply, reply
        assert "ZZT OWN" not in reply, reply
        assert "Customer:" not in reply, reply
        assert reply.startswith('Couldn\'t find: "DOZZT999"'), reply

    def test_bare_order_ask_still_runs_on_the_links(self, session_factory, monkeypatch) -> None:
        """Guard: the fix is about a typed number that did not resolve. An order ask naming
        nothing at all still runs on the links (AC-CS-30)."""
        _seed_contact(session_factory, variables={})
        own = _link_customers(session_factory, "ZZT OWN A", "ZZT OWN B")

        # Dated: a DO list ask with no range asks which period first (DO-ASK-SIMPLIFY, owner
        # 4 Oct 2026); this guard is about the links, not the range.
        _reply, captured = _turn(
            session_factory,
            monkeypatch,
            _parser_output(domain_hint="order", intent_hint="check_order", order_status=None, entities=[], date_filter_start="2026-10-01", date_filter_end="2026-10-31"),
            "my orders",
        )

        (args,) = _calls(captured, ORDERS)
        assert sorted(args["customer_ids"]) == sorted(own), args


# --------------------------------------------------------------------------- #
# The SO lookup (owner chose option (b) on PR #1435; behaviour card v2 rulings)
# --------------------------------------------------------------------------- #

OUTSTANDING_KEY = "sales_orders.outstanding"
REFUSAL_PREFIX = "Sorry, that isn't under your account."
SO_NOT_ENABLED = "Sales order figures are not enabled for your account."


def _seed_so(
    session_factory,
    so_number: str,
    *,
    customer_id: str | None,
    lines: list[tuple[int, int]] | None = None,
    status: str = "open",
    debtor_name: str | None = "ZZT HANLIM TRADING [A/C I]",
    debtor_code: str | None = None,
    company_id: str | None = None,
    order_date: str = "2026-09-15",
    cancelled_lines: list[tuple[int, int]] | None = None,
) -> None:
    """One `sales_orders` row and its lines, by raw SQL so another company's SO can be
    written from a Sorento-scoped test session. `lines` are (ordered, delivered)."""
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from sqlalchemy import text
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    company = company_id or DEFAULT_COMPANY_ID
    so_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO sales_orders (id, company_id, so_number, customer_id, debtor_code, debtor_name, "
            "order_date, status, created_at, updated_at) "
            "VALUES (:id, :co, :num, :cust, :dcode, :dname, :odate, :status, now(), now())"
        ),
        {
            "id": so_id, "co": company, "num": so_number, "cust": customer_id, "dcode": debtor_code,
            "dname": debtor_name, "odate": order_date, "status": status,
        },
    )
    rows = [(o, d, "open") for o, d in (lines or [])] + [(o, d, "cancelled") for o, d in (cancelled_lines or [])]
    if rows:
        from app.services.company_scope import DEFAULT_COMPANY_ID as _SRT

        prod = seed_product(db, company_id=_SRT)
        for ordered, delivered, line_status in rows:
            db.execute(
                text(
                    "INSERT INTO sales_order_lines (id, company_id, sales_order_id, product_id, qty_ordered, "
                    "qty_delivered, line_status, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :co, :so, :prod, :o, :d, :ls, now(), now())"
                ),
                {"co": company, "so": so_id, "prod": str(prod.id), "o": ordered, "d": delivered, "ls": line_status},
            )
    db.commit()


def _so_turn(session_factory, monkeypatch, *numbers: str, attributes=(OUTSTANDING_KEY,)):
    qf = _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[
            {"raw": n, "hint": "order_number", "canonical_code": None, "current_message": True, "confident": True}
            for n in numbers
        ],
    )
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=qf,
        text_body="status of " + " ".join(numbers),
        msg_id=f"ZZT-so-card-{uuid.uuid4().hex[:10]}",
        attributes=list(attributes),
        matches={},
        mcp_response=DO_DUMP,
    )
    return ((result.reply or {}).get("text") or ""), captured


def _card(number: str, status_words: str, delivery: str | None, *, name="ZZT HANLIM TRADING [A/C I]", date="15 Sep 2026") -> str:
    lines = [f"*{number}* - {name}", f"Ordered {date} - {status_words}"]
    if delivery is not None:
        lines.append(f"Delivery: {delivery}")
    return "\n".join(lines)


class TestSoCardInScope:
    def _own(self, session_factory) -> str:
        _seed_contact(session_factory, variables={})
        own, _other = _link_customers(session_factory, "ZZT OWN A", "ZZT OWN B")
        return own

    def test_open_partly_delivered(self, session_factory, monkeypatch) -> None:
        """AC-SO-03, AC-SO-08, AC-SO-09."""
        own = self._own(session_factory)
        _seed_so(session_factory, "SO421624", customer_id=own, lines=[(10, 10), (10, 1), (6, 0)])
        reply, captured = _so_turn(session_factory, monkeypatch, "SO421624")
        assert reply.strip() == _card("SO421624", "Open", "partly delivered"), reply
        assert _calls(captured, ORDERS) == [], captured

    def test_open_nothing_delivered(self, session_factory, monkeypatch) -> None:
        """AC-SO-04."""
        own = self._own(session_factory)
        _seed_so(session_factory, "SO422057", customer_id=own, lines=[(2, 0)], order_date="2026-09-21")
        reply, _ = _so_turn(session_factory, monkeypatch, "SO422057")
        assert reply.strip() == _card("SO422057", "Open", "not delivered yet", date="21 Sep 2026"), reply

    def test_closed_fully_delivered(self, session_factory, monkeypatch) -> None:
        """AC-SO-05. A cancelled line does not count against "fully"."""
        own = self._own(session_factory)
        _seed_so(
            session_factory, "SO422095", customer_id=own, status="closed",
            lines=[(3, 3)], cancelled_lines=[(5, 0)], order_date="2026-09-21",
        )
        reply, _ = _so_turn(session_factory, monkeypatch, "SO422095")
        assert reply.strip() == _card("SO422095", "Closed", "fully delivered", date="21 Sep 2026"), reply

    def test_closed_short_reads_partly(self, session_factory, monkeypatch) -> None:
        """AC-SO-06."""
        own = self._own(session_factory)
        _seed_so(session_factory, "SO422056", customer_id=own, status="closed", lines=[(5, 3)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO422056")
        assert reply.strip() == _card("SO422056", "Closed", "partly delivered"), reply

    def test_cancelled_has_marker_and_no_delivery_line(self, session_factory, monkeypatch) -> None:
        """AC-SO-07."""
        own = self._own(session_factory)
        _seed_so(session_factory, "SO421900", customer_id=own, status="cancelled", lines=[(4, 0)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421900")
        assert reply.strip() == _card("SO421900", "❗ Cancelled", None), reply

    def test_null_customer_matched_by_linked_debtor_code(self, session_factory, monkeypatch) -> None:
        """An SO whose debtor code names a linked customer but carries no customer id is in scope."""
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A", codes={"ZZT OWN A": "ZZT-300-H001"})
        _seed_so(session_factory, "SO422200", customer_id=None, debtor_code="ZZT-300-H001", lines=[(1, 0)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO422200")
        assert reply.strip() == _card("SO422200", "Open", "not delivered yet"), reply

    def test_several_numbers_one_card_each_and_one_miss_line(self, session_factory, monkeypatch) -> None:
        """AC-SO-12."""
        own = self._own(session_factory)
        _seed_so(session_factory, "SO421624", customer_id=own, lines=[(10, 1)])
        _seed_so(session_factory, "SO422057", customer_id=own, lines=[(2, 0)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421624", "SO999998", "SO422057")
        assert reply.strip() == "\n\n".join(
            [
                _card("SO421624", "Open", "partly delivered"),
                _card("SO422057", "Open", "not delivered yet"),
                "I could not find SO999998.",
            ]
        ), reply


class TestSoOutsideTheAccount:
    def test_other_customers_so_gets_the_scope_refusal(self, session_factory, monkeypatch) -> None:
        """AC-SO-10: the refusal, and nothing about the SO itself."""
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A")
        from tests.chatbot.test_customer_scope_lane import _other_customer

        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_so(session_factory, "SO421777", customer_id=foreign, debtor_name="ZZT FOREIGN SDN BHD", lines=[(1, 1)])
        reply, captured = _so_turn(session_factory, monkeypatch, "SO421777")
        assert reply.strip() == f"{REFUSAL_PREFIX} I can only check on ZZT OWN A.", reply
        assert "FOREIGN" not in reply and "delivered" not in reply, reply
        assert _calls(captured, ORDERS) == [], captured

    def test_another_companys_so_is_not_found(self, session_factory, monkeypatch) -> None:
        """AC-SO-11: company scope applies; the SO is simply not there."""
        from tests._mc_lookup_seed import MOCHA_ID, seed_mocha

        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        db = session_factory()
        seed_mocha(db)
        db.commit()
        _seed_so(session_factory, "SO421888", customer_id=own, company_id=MOCHA_ID, lines=[(1, 0)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421888")
        assert "SO421888" in reply and "Ordered" not in reply and "delivered" not in reply, reply
        assert REFUSAL_PREFIX not in reply, reply

    def test_found_and_refused_and_missing_in_one_message(self, session_factory, monkeypatch) -> None:
        """Cards first, then the refusal, then the miss line."""
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        from tests.chatbot.test_customer_scope_lane import _other_customer

        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_so(session_factory, "SO421624", customer_id=own, lines=[(1, 0)])
        _seed_so(session_factory, "SO421777", customer_id=foreign, lines=[(1, 1)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421624", "SO421777", "SO999998")
        assert reply.strip() == "\n\n".join(
            [
                _card("SO421624", "Open", "not delivered yet"),
                f"{REFUSAL_PREFIX} I can only check on ZZT OWN A.",
                "I could not find SO999998.",
            ]
        ), reply


class TestSoRevealKeyAndStaff:
    def test_no_key_gets_the_not_enabled_line(self, session_factory, monkeypatch) -> None:
        """AC-SO-13."""
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_so(session_factory, "SO421624", customer_id=own, lines=[(10, 1)])
        reply, captured = _so_turn(session_factory, monkeypatch, "SO421624", attributes=())
        assert reply.strip() == SO_NOT_ENABLED, reply
        assert _calls(captured, ORDERS) == [], captured

    def test_unscoped_contact_sees_any_so_of_its_company(self, session_factory, monkeypatch) -> None:
        """AC-SO-14: no links, so no customer scope is enforced."""
        from tests.chatbot.test_customer_scope_lane import _other_customer

        _seed_contact(session_factory, variables={})
        cust = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_so(session_factory, "SO421624", customer_id=cust, debtor_name="ZZT ANY SDN BHD", lines=[(10, 10)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421624")
        assert reply.strip() == _card("SO421624", "Open", "fully delivered", name="ZZT ANY SDN BHD"), reply


class TestReviewRound1:
    def test_spaced_so_number_still_gets_its_card(self, session_factory, monkeypatch) -> None:
        """Reviewer S1: "SO 421624" folds to SO421624, exactly as the resolver's token key does."""
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_so(session_factory, "SO421624", customer_id=own, lines=[(10, 1)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO 421624")
        assert reply.strip() == _card("SO421624", "Open", "partly delivered"), reply

    def test_debtor_code_of_a_link_in_another_company_is_not_in_scope(self, session_factory, monkeypatch) -> None:
        """Security S1: one debtor code can name different debtors in two companies. An SO with
        no customer id is in scope only on a link's code IN THE SAME COMPANY."""
        from sqlalchemy import text

        from tests._mc_lookup_seed import MOCHA_ID, seed_mocha

        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A", codes={"ZZT OWN A": "ZZT-300-H001"})
        db = session_factory()
        seed_mocha(db)
        # The contact's scope covers both companies, so the Mocha SO is visible at all.
        db.execute(
            text(
                "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"cid": str(CONTACT_ID), "company_id": MOCHA_ID},
        )
        db.commit()
        _seed_so(
            session_factory, "SO421999", customer_id=None, debtor_code="ZZT-300-H001",
            debtor_name="ZZT OTHER DEBTOR", company_id=MOCHA_ID, lines=[(1, 0)],
        )
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421999")
        assert "OTHER DEBTOR" not in reply and "Ordered" not in reply, reply
        assert reply.strip().startswith(REFUSAL_PREFIX), reply


class TestRefusalGroupsTheLinksByFamily:
    def test_refusal_names_the_group_name_only(self, session_factory, monkeypatch) -> None:
        """Owner rulings (PR #1435, 2 Oct 2026): the "isn't under your account" sentence names
        the linked customers by group (`ledger_family.family_words`), never one name per
        ledger, and by the GROUP NAME ONLY: no "(3 accounts)" count."""
        _seed_contact(session_factory, variables={})
        _link_customers(
            session_factory,
            "ZZT HANLIM TRADING SDN BHD [A/C I]",
            "ZZT HANLIM TRADING SDN BHD [A/C II]",
            "ZZT HANLIM TRADING SDN BHD [A/C III]",
        )
        from tests.chatbot.test_customer_scope_lane import _other_customer

        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_so(session_factory, "SO421777", customer_id=foreign, lines=[(1, 1)])
        reply, _ = _so_turn(session_factory, monkeypatch, "SO421777")
        assert reply.strip() == (
            f"{REFUSAL_PREFIX} I can only check on ZZT HANLIM TRADING SDN BHD."
        ), reply


class TestAllMySalesOrdersAfterAnSoCard:
    """Owner hand test on #1435: "status of SO422056" (card), then "okay how about all my
    sales order?" replied `Couldn't find: "SO422056" (order).` The SO lane answered the word
    itself, but it stayed on the focus carry (`focus.extra["order"]`, no uuid), so the next
    order turn handed it to the resolver again and named it as a miss. "my" is the
    CHATBOT-SELFREF-SCOPE `self_reference` path: the second turn is answered from the
    contact's own links through that path, and the old SO number plays no part in it."""

    @pytest.mark.parametrize("order_status", [None, "so_outstanding"])
    def test_all_my_sales_orders_drops_the_answered_so(self, session_factory, monkeypatch, order_status) -> None:
        from tests.chatbot.test_outstanding_lane import _session_of

        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT HANLIM TRADING SDN BHD [A/C III]")
        _seed_so(session_factory, "SO422056", customer_id=own, status="closed", lines=[(3, 3)])

        first, _ = _so_turn(session_factory, monkeypatch, "SO422056")
        assert first.startswith("*SO422056*"), first
        carried = ((_session_of(session_factory).get("focus") or {}).get("extra") or {}).get("order") or []
        assert not any(isinstance(r, dict) and r.get("raw") == "SO422056" for r in carried), carried

        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status=order_status,
                entities=[], self_reference=True,
            ),
            text_body="okay how about all my sales order?",
            msg_id=f"ZZT-so-all-{uuid.uuid4().hex[:10]}",
            attributes=[OUTSTANDING_KEY],
            matches={},
            mcp_response={"has_result": True, "response": "ZZT ROWS"},
        )
        reply = (result.reply or {}).get("text") or ""
        assert "SO422056" not in reply, reply
        # Owner option (2), 2 Oct 2026: "all my sales order" is the SO list over the links,
        # whatever `order_status` the parser read ("outstanding" in the WORDS keeps the
        # report): with no period it asks which one (`tests/chatbot/test_so_list.py`).
        assert reply.startswith("Which period for ZZT HANLIM TRADING SDN BHD?"), reply
        assert captured == [], captured


class TestCloudPassParserCopiesThePreviousSo:
    """Cloud pass on PR #1435, 3 Oct 2026, live parser: "status of SO422056" (card), then
    "status of SO421624" replied with SO422056's card again in 2 of 4 runs. The parser read
    the second message as `{"raw": "SO422056", "current_message": true}`, copying the number
    off "Previous response". The numbers the message TYPES decide the cards, whatever the
    parser copied from the conversation."""

    def _seeded(self, session_factory) -> None:
        _seed_contact(session_factory, variables={})
        own, _other = _link_customers(session_factory, "ZZT OWN A", "ZZT OWN B")
        _seed_so(session_factory, "SO422056", customer_id=own, status="closed", lines=[(3, 3)], order_date="2026-09-21")
        _seed_so(session_factory, "SO421624", customer_id=own, lines=[(10, 10), (10, 1)])

    @pytest.mark.parametrize(
        "parsed",
        [["SO422056"], ["SO422056", "SO421624"], ["SO421624", "SO422056"]],
        ids=["copied-only", "copied-first", "copied-last"],
    )
    def test_typed_number_wins_over_a_copied_one(self, session_factory, monkeypatch, parsed) -> None:
        self._seeded(session_factory)
        qf = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            order_status=None,
            entities=[
                {"raw": n, "hint": "order", "canonical_code": None, "current_message": True, "confident": True}
                for n in parsed
            ],
        )
        reply, captured = _turn(session_factory, monkeypatch, qf, "status of SO421624")
        assert reply.strip() == _card("SO421624", "Open", "partly delivered"), reply
        assert _calls(captured, ORDERS) == [], captured
