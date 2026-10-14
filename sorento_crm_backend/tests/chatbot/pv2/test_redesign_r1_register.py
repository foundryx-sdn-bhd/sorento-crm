"""PV2 redesign R1 (UAC R1-1..R1-12): the answer register writer.

Envelopes are REAL shapes: the MCP body goes through the real presenter, the real
`fetch.output_structurer` and the real `turn_runtime.envelope_of` (`_register_envelopes.py`). The writer is
`pv2.register.entries_from_envelopes(envelopes, turn)`; state is `RegisterEntry` + `ConversationState.register`.

Pinned by this file (the plan names the first three; the rest are the red tests' own choices, stated in the
tester's report): depth-first order (an entry, then its children, then the next entry); `register.roll(previous,
fresh)` for the last-2-replies / cap-20 rule; an envelope's `printed_rows` (indices into `figures`) gates which
rows are recorded; normalized `rows` [{kind, key, label}] on the envelope for sources that are not figures
(so_status cards, report breakdowns); an unprinted `lines` [{product_code, qty}] on a figure.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.chatbot.pv2 import _register_envelopes as E
from tests.chatbot.pv2.carry_fakes import NOW, held as mk_held, mk_state

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _writer():
    from app.services.chatbot.pv2 import register

    return register


def build(*envelopes, turn=3):
    return _writer().entries_from_envelopes(list(envelopes), turn)


def _dump(entries):
    return [e.model_dump() for e in entries]


def _with_printed(env, indices=None):
    """The structurer's `printed_rows` (the coder adds it); for a hand-edited envelope, set it explicitly."""
    env = copy.deepcopy(env)
    env["printed_rows"] = list(range(len(env["figures"]))) if indices is None else list(indices)
    return env


def _orders(access=None, with_lines=True):
    env = E.envelope(E.ORDERS_TOOL, E.ORDERS_RAW, domain="order", access=access)
    for fig, raw in zip(env["figures"], E.ORDERS_RAW["data"]):
        fig.pop("lines", None)  # the presenter may already carry it; the test sets exactly what it means to
        if with_lines:
            fig["lines"] = [{"product_code": ln["product"]["product_code"], "qty": ln["quantity"]}
                            for ln in raw["lines"]]
    return _with_printed(env)


# ------------------------------------------------------------------ state shape (R1-9, R1-10)


def test_register_entry_has_the_pinned_fields():
    from app.services.chatbot.pv2.state import RegisterEntry

    e = RegisterEntry(id="R1", kind="delivery_order", key="202609-2012", label="202609-2012", parent=None,
                      subject="S1", turn=3)
    assert e.model_dump() == {"id": "R1", "kind": "delivery_order", "key": "202609-2012",
                              "label": "202609-2012", "parent": None, "subject": "S1", "turn": 3}


def test_a_fresh_state_has_an_empty_register():
    assert mk_state().register == []


def test_r1_9_a_state_stored_before_r1_loads_unchanged():
    from app.services.chatbot.pv2.state import dump_state, load_state

    raw = dump_state(mk_state(mk_held("inventory", {"product": ["SRTWC286"]})))
    raw.pop("register", None)
    state = load_state(raw, NOW)
    assert state.held.domain == "inventory" and state.register == []
    assert [r.key for r in state.held.refs["product"]] == ["SRTWC286"]


def test_the_register_round_trips_through_dump_and_load():
    from app.services.chatbot.pv2.state import RegisterEntry, dump_state, load_state

    state = mk_state()
    state.register = [RegisterEntry(id="R1", kind="product", key="A", label="A", parent=None, subject="S1", turn=2)]
    assert load_state(dump_state(state), NOW).register == state.register


def test_r1_10_memory_is_byte_identical_with_and_without_a_register():
    from app.services.chatbot.pv2.history import MemoryProfile, render_memory
    from app.services.chatbot.pv2.state import RegisterEntry

    plain = mk_state(mk_held("inventory", {"product": ["SRTWC286"]}))
    with_register = plain.model_copy(deep=True)
    with_register.register = [RegisterEntry(id="R1", kind="delivery_order", key="X", label="X", parent=None,
                                            subject="S1", turn=2)]
    profile = MemoryProfile(audience="staff")
    assert render_memory(with_register, profile, NOW) == render_memory(plain, profile, NOW)


# ------------------------------------------------------------------ R1-1 / R1-2 orders and their lines


def test_r1_1_an_order_list_writes_one_entry_per_printed_order_in_printed_order():
    entries = build(_orders())
    orders = [e for e in entries if e.kind == "delivery_order"]
    assert [e.label for e in orders] == ["202609-2012", "202609-2013"]
    assert [e.key for e in orders] == ["202609-2012", "202609-2013"]
    assert all(e.parent is None and e.subject is None and e.turn == 3 for e in orders)


def test_r1_1_ids_run_R1_to_Rn_depth_first():
    entries = build(_orders())
    assert [e.id for e in entries] == [f"R{n}" for n in range(1, len(entries) + 1)]
    assert [e.label for e in entries] == ["202609-2012", "SRTWB1096", "SRTWT7203", "202609-2013", "SRTW5000"]


def test_r1_2_each_order_has_product_children_from_its_lines():
    entries = build(_orders())
    by_id = {e.id: e for e in entries}
    first = next(e for e in entries if e.label == "202609-2012")
    kids = [e for e in entries if e.parent == first.id]
    assert [(k.kind, k.label) for k in kids] == [("product", "SRTWB1096"), ("product", "SRTWT7203")]
    assert all(by_id[k.parent].kind == "delivery_order" for k in kids)


def test_r1_2_children_come_from_lines_never_from_the_printed_text():
    env = _orders()
    products_field = next(f for f in env["figures"][0]["fields"] if f["label"] == "Products")
    products_field["value"] = "FAKETEXT (9), OTHERTEXT (1)"
    labels = [e.label for e in build(env)]
    assert "FAKETEXT" not in labels and "OTHERTEXT" not in labels
    assert "SRTWB1096" in labels


def test_r1_2_an_order_without_lines_has_no_children_and_the_text_is_not_parsed():
    entries = build(_orders(with_lines=False))
    assert [e.label for e in entries] == ["202609-2012", "202609-2013"]


def test_r1_7_children_are_not_recorded_when_the_products_field_was_hidden_by_the_reveal_filter():
    access = [a for a in E.ALL_REVEALS if a != "delivery_orders.products"]
    entries = build(_orders(access=access))
    assert [e.label for e in entries] == ["202609-2012", "202609-2013"]


def test_r1_7_an_order_whose_number_was_hidden_is_not_recorded_and_its_title_never_is():
    access = [a for a in E.ALL_REVEALS if a != "delivery_orders.order_number"]
    env = _orders(access=access)
    assert env["figures"][0]["title"] == "202609-2012", "the presenter title still carries the number"
    labels = [e.label for e in build(env)]
    assert "202609-2012" not in labels and "202609-2013" not in labels


def test_r1_7_a_hidden_customer_name_never_appears_in_any_entry():
    access = [a for a in E.ALL_REVEALS if a != "delivery_orders.customer"]
    dumped = json.dumps(_dump(build(_orders(access=access))))
    assert "DELUXE HOME" not in dumped and "HANLIM" not in dumped


# ------------------------------------------------------------------ R1-3 purchase orders


def test_r1_3_a_po_list_writes_purchase_orders_with_their_product_as_a_child():
    env = _with_printed(E.envelope(E.PO_TOOL, E.PO_RAW, domain="purchase_order"))
    entries = build(env)
    pos = [e for e in entries if e.kind == "purchase_order"]
    assert [e.label for e in pos] == ["PO-1001", "PO-1002"]
    child = next(e for e in entries if e.parent == pos[0].id)
    assert (child.kind, child.label) == ("product", "SRTWC8517")


def test_r1_7_the_restricted_supplier_is_never_recorded():
    env = _with_printed(E.envelope(E.PO_TOOL, E.PO_RAW, domain="purchase_order", access=[]))
    assert "Supplies" not in json.dumps(_dump(build(env)))


# ------------------------------------------------------------------ R1-4 product lists


@pytest.mark.parametrize("tool,raw,domain,expected", [
    (E.STOCK_TOOL, E.STOCK_RAW, "inventory", ["SRTBF11201-NEW", "SRTWB7109"]),
    (E.INCOMING_TOOL, E.INCOMING_RAW, "incoming", ["SRTWC286-SH-NEW", "SRTWC286-SH-NEW-P"]),
    (E.PRODUCTS_TOOL, E.PRODUCTS_RAW, "master_products", ["SRTWC8517", "SRTWC8518"]),
], ids=["stock", "incoming", "products"])
def test_r1_4_product_rows_write_product_entries_in_printed_order(tool, raw, domain, expected):
    entries = build(_with_printed(E.envelope(tool, raw, domain=domain)))
    assert [(e.kind, e.label) for e in entries] == [("product", c) for c in expected]
    assert all(e.parent is None for e in entries)


def test_r1_4_attachment_rows_write_product_entries():
    from app.services.chatbot.lanes.business import fetch

    rows = [{"fields": [{"label": "Product Code", "value": "SRTWC286-SH"},
                        {"label": "Attachment Type", "value": "Product Photos"},
                        {"label": "File Name", "value": "SRTWC286-SH.jpg"}], "flags": {}, "file_urls": []}]
    figures = fetch._file_rows({"result_type": "product_attachments", "items": rows}, links=False)["items"]
    env = {"domain": "product_attachment", "figures": figures, "files": [], "printed_rows": [0]}
    assert [(e.kind, e.label) for e in build(env)] == [("product", "SRTWC286-SH")]


def test_r1_4_a_set_page_writes_its_listed_products():
    env = {"domain": "master_products", "figures": [], "printed_rows": [], "listed_products": [
        {"raw": "SRTWC8517", "hint": "product", "canonical_code": "SRTWC8517",
         "uuid": "11111111-1111-4111-8111-111111111111", "current_message": False},
        {"raw": "SRTWC8518", "hint": "product", "canonical_code": "SRTWC8518",
         "uuid": "22222222-2222-4222-8222-222222222222", "current_message": False}]}
    entries = build(env)
    assert [(e.kind, e.label) for e in entries] == [("product", "SRTWC8517"), ("product", "SRTWC8518")]
    assert not any(UUID_RE.search(e.label) for e in entries)


def test_r1_4_top_selling_rows_write_their_entity_type():
    rows = [{"idx": 1, "label": "SRTWT7445", "code": "SRTWT7445", "entity_type": "product"},
            {"idx": 2, "label": "SRTKT39SS", "code": "SRTKT39SS", "entity_type": "product"}]
    env = {"domain": "sales", "figures": [], "printed_rows": [],
           "lane_ask": {"kind": "top_selling_pick", "last_result_set": rows, "filters": {}}}
    assert [(e.kind, e.label) for e in build(env)] == [("product", "SRTWT7445"), ("product", "SRTKT39SS")]
    cats = [dict(r, entity_type="category") for r in rows]
    env["lane_ask"]["last_result_set"] = cats
    assert {e.kind for e in build(env)} == {"category"}


def test_r1_4_a_drill_down_offer_is_not_a_printed_row():
    env = {"domain": "sales", "figures": [], "printed_rows": [],
           "lane_ask": {"kind": "sales_report_detail", "filters": {}, "last_result_set": [
               {"idx": 1, "label": "By customer", "value": "customer", "aliases": []}]}}
    assert build(env) == []


# ------------------------------------------------------------------ R1-5 sales / purchase order cards


def test_r1_5_rows_from_so_status_write_sales_order_entries():
    env = {"domain": "order", "figures": [], "printed_rows": [], "rows": [
        {"kind": "sales_order", "key": "11111111-1111-4111-8111-111111111111", "label": "SO422057"},
        {"kind": "sales_order", "key": "22222222-2222-4222-8222-222222222222", "label": "SO422056"}]}
    entries = build(env)
    assert [(e.kind, e.label) for e in entries] == [("sales_order", "SO422057"), ("sales_order", "SO422056")]
    assert not any(UUID_RE.search(e.label) for e in entries)


def test_r1_5_po_and_pr_ref_cards_likewise():
    env = {"domain": "order", "figures": [], "printed_rows": [], "rows": [
        {"kind": "purchase_order", "key": "p1", "label": "PO-PV2-1"},
        {"kind": "purchase_request", "key": "r1", "label": "PR-77"}]}
    assert [(e.kind, e.label) for e in build(env)] == [("purchase_order", "PO-PV2-1"), ("purchase_request", "PR-77")]


# ------------------------------------------------------------------ R1-6 report rows


def test_r1_6_report_ask_drill_rows_write_entries_of_the_group_by_kind():
    from app.services.chatbot.lanes.business import fetch

    out = fetch._report_ask_output(E.REPORT_ASK_ENVELOPE, {"semantic_input": {"report_ask_args": {"group_by": "customer"}}})
    env = {"domain": "sales", "figures": [], "printed_rows": [], "sales_ranking_frame": out["sales_ranking_frame"]}
    entries = build(env)
    assert [(e.kind, e.label) for e in entries] == [("customer", "ABC SDN BHD"), ("customer", "XYZ")]


def test_r1_6_the_delivered_breakdown_rows_write_entries_of_the_group_by_kind():
    out = E.structured("crm_sales_report", E.SALES_DRILL_RAW)
    assert out["rows"] == [{"kind": "product", "key": "PRD-A101", "label": "PRD-A101"},
                           {"kind": "product", "key": "PRD-A102", "label": "PRD-A102"}]
    env = {"domain": "sales", "figures": [], "printed_rows": [], "rows": out["rows"]}
    assert [(e.kind, e.label) for e in build(env)] == [("product", "PRD-A101"), ("product", "PRD-A102")]


def test_r1_6_a_plain_delivered_total_writes_no_rows():
    raw = {**E.SALES_DRILL_RAW, "group_by": None, "rows": [],
           "periods": [{"period": "2026-09", "qty": 10, "amount": 100.0}]}
    assert E.structured("crm_sales_report", raw).get("rows", []) == []


# ------------------------------------------------------------------ R1-7 no uuid, no title, no hidden data


def test_r1_7_no_label_is_a_uuid_and_the_stock_product_id_is_never_recorded():
    entries = build(_with_printed(E.envelope(E.STOCK_TOOL, E.STOCK_RAW, domain="inventory")))
    assert not any(UUID_RE.search(e.label) for e in entries)
    assert "11111111-1111" not in json.dumps([e.label for e in entries])


def test_r1_7_an_item_title_with_no_keyed_field_is_not_recorded():
    env = {"domain": "master_products", "printed_rows": [0], "figures": [
        {"title": "SECRET TITLE", "fields": [{"label": "List Price", "value": "10"}], "flags": {}}]}
    assert build(env) == []


# ------------------------------------------------------------------ R1-7b only printed rows are recorded


def test_r1_7b_rows_not_in_printed_rows_are_not_recorded():
    env = _orders()
    env["printed_rows"] = [1]
    assert [e.label for e in build(env) if e.kind == "delivery_order"] == ["202609-2013"]


def test_r1_7b_no_printed_rows_key_records_nothing_from_the_answers():
    env = _orders()
    env.pop("printed_rows")
    assert build(env) == []


def test_r1_7b_an_empty_printed_rows_records_nothing():
    env = _orders()
    env["printed_rows"] = []
    assert build(env) == []


# ------------------------------------------------------------------ R1-8 cap, last two replies, direct reply, new session


def _entry(n, turn, kind="product"):
    from app.services.chatbot.pv2.state import RegisterEntry

    return RegisterEntry(id=f"R{n}", kind=kind, key=f"K{n}", label=f"L{n}", parent=None, subject="S1", turn=turn)


def test_r1_8_the_register_keeps_at_most_20_entries():
    fresh = [_entry(n, 5) for n in range(1, 26)]
    kept = _writer().roll([], fresh)
    assert len(kept) == 20
    assert [e.label for e in kept] == [f"L{n}" for n in range(1, 21)], "newest reply first, printed order"


def test_r1_8_only_the_last_two_replies_are_kept():
    older = [_entry(1, 1)]
    prev = [_entry(2, 2)]
    fresh = [_entry(3, 3)]
    kept = _writer().roll(prev + older, fresh)
    assert {e.turn for e in kept} == {3, 2}


def test_r1_8_a_direct_reply_adds_nothing_and_keeps_the_previous_entries():
    prev = [_entry(1, 2), _entry(2, 2)]
    assert _writer().roll(prev, []) == prev


def test_r1_8_entry_ids_stay_unique_after_a_roll():
    kept = _writer().roll([_entry(1, 2), _entry(2, 2)], [_entry(1, 3), _entry(2, 3)])
    assert len({e.id for e in kept}) == len(kept)


def test_r1_8_a_new_session_clears_the_register():
    from app.services.chatbot.pv2.state import roll_session

    state = mk_state(mk_held("inventory", {"product": ["A"]}), last_turn_at=NOW - timedelta(hours=5))
    state.register = [_entry(1, 2)]
    assert roll_session(state, NOW).register == []


def test_r1_8_within_a_session_the_register_survives_roll_session():
    from app.services.chatbot.pv2.state import roll_session

    state = mk_state(mk_held("inventory", {"product": ["A"]}), last_turn_at=NOW - timedelta(minutes=5))
    state.register = [_entry(1, 2)]
    assert roll_session(state, NOW).register == state.register


# ------------------------------------------------------------------ R1-11 PO gate for a non-staff contact


def _po_turn(audience, granted):
    from app.services.chatbot.pv2 import turn
    from app.services.chatbot.pv2.history import MemoryProfile
    from app.services.chatbot.pv2.state import dump_state
    from tests.chatbot.pv2.carry_fakes import FakeResolver, ask as _base_ask, out
    from app.services.chatbot.pv2.schema import Ask

    base = _base_ask().model_dump()
    ent = {"kind": "product", "key": None, "phrase": "SRTWC286", "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": None}
    base.update(domain="purchase_order", entities=[ent])
    parsed = out(Ask.model_validate(base))
    resolver = FakeResolver({"entities": {"product|SRTWC286": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}})
    ctx = turn.TurnV2Context(
        text="any PO for SRTWC286?", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience=audience),
        parse=lambda m, u: parsed, resolver=resolver, load_exchanges=lambda s: [], granted_reveals=frozenset(granted))
    return turn._run(ctx)


@pytest.mark.parametrize("audience", ["dealer", "end_user", None])
def test_r1_11_a_non_staff_po_list_is_not_refused_for_lacking_the_placed_grant(audience):
    res = _po_turn(audience, [])
    assert res.verdict is not None, res.direct_reply
    assert res.verdict["domain_hint"] == "purchase_order"


def test_r1_11_a_non_staff_multi_part_turn_with_a_po_part_is_not_refused_either():
    from app.services.chatbot.pv2 import turn
    from app.services.chatbot.pv2.history import MemoryProfile
    from app.services.chatbot.pv2.schema import Ask
    from app.services.chatbot.pv2.state import dump_state
    from tests.chatbot.pv2.carry_fakes import FakeResolver, ask as _base_ask, out

    def ask(domain):
        base = _base_ask().model_dump()
        base.update(domain=domain, entities=[{"kind": "product", "key": None, "phrase": "SRTWC286", "op": "set",
                                              "refers_to_held": False, "quantity": None, "doc_number": None}])
        return Ask.model_validate(base)

    resolver = FakeResolver({"entities": {"product|SRTWC286": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}})
    parsed = out(ask("purchase_order"), ask("inventory"))
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=resolver, load_exchanges=lambda s: [], granted_reveals=frozenset())
    res = turn._run(ctx)
    assert res.verdict is not None, res.direct_reply
    assert {a["domain"] for a in res.verdict.get("asks") or []} >= {"purchase_order", "inventory"}


def test_r1_11_staff_without_the_grant_are_still_refused():
    res = _po_turn("staff", [])
    assert res.verdict is None and res.direct_reply


def test_r1_11_staff_with_the_grant_reach_v1():
    res = _po_turn("staff", ["purchase_orders.placed"])
    assert res.verdict is not None and res.verdict["domain_hint"] == "purchase_order"
