"""PV2-REPORTS: answer_report (one tool call, fake mcp) and the hand-test B chain (pure, no DB, no LLM)."""
from tests.chatbot.pv2.reports_schema_shim import install

schema = install()
ReportSpec, Filter, Period = schema.ReportSpec, schema.Filter, schema.Period

from datetime import date

import pytest

from app.services.chatbot.pv2 import reports as r

TODAY = date(2026, 10, 13)
CONTACT, SPACE = "contact-1", "space-1"


def spec(**kw):
    base = dict(
        dataset=None, measures=[], group_by=[], top_n_per_group=None, filters=[], period=None,
        compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=None, basis=None,
    )
    base.update(kw)
    return ReportSpec(**base)


def period(**kw):
    base = dict(preset=None, n=None, date_from=None, date_to=None)
    base.update(kw)
    return Period(**base)


def flt(dimension, keys=(), op="in", values=()):
    return Filter(dimension=dimension, op=op, keys=list(keys), values=list(values))


def ranking(**kw):
    base = dict(
        dataset="sales", measures=["amount"], basis="delivered", group_by=["customer"],
        period=period(preset="last_month"), top_n=10,
    )
    base.update(kw)
    return spec(**base)


class FakeMcp:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return self.result


ENVELOPE = {
    "result_type": "report_ask",
    "response": (
        "Top 2 customers by delivered sales\nPeriod: 2026-09-01 to 2026-09-30\n"
        "1. ABC SDN BHD RM 10.00, 2 pcs\n2. XYZ RM 5.00, 1 pc\nTotal RM 15.00, 3 pcs"
    ),
    "has_result": True,
    "drill_rows": {
        "group_by": "customer",
        "rows": [{"name": "ABC SDN BHD", "keys": ["c1"]}, {"name": "XYZ", "keys": ["c2", "c3"]}],
    },
}


def answer(s, mcp, refs=None, dealer=False):
    return r.answer_report(
        s, refs=refs or {}, today=TODAY, mcp_call=mcp, contact_id=CONTACT, space_id=SPACE, dealer=dealer
    )


# ---------------------------------------------------------------- no tool call for non-run plans


@pytest.mark.parametrize(
    "s,refs,status",
    [
        (ranking(measures=[], basis=None), {}, "ask"),
        (ranking(period=None), {}, "ask"),
        (ranking(group_by=["region"]), {}, "say"),
        (ranking(filters=[flt("customer", ["ABC"])]), {}, "unresolved"),
    ],
)
def test_non_run_plans_make_no_tool_call(s, refs, status):
    mcp = FakeMcp(ENVELOPE)
    reply = answer(s, mcp, refs)
    assert mcp.calls == []
    assert reply.plan.status == status
    assert reply.rows == []


def test_ask_reply_carries_pending_measure():
    mcp = FakeMcp()
    reply = answer(ranking(measures=[], basis=None), mcp)
    plan = reply.plan
    assert reply.text == plan.reply
    assert reply.pending["kind"] == "clarify_report_measure"
    assert reply.pending["asked"] == plan.reply
    assert reply.pending["options"] == [{"key": o.key, "label": o.label, "payload": o.payload} for o in plan.options]
    assert len(reply.pending["options"]) == 6


def test_ask_reply_carries_pending_period():
    reply = answer(ranking(period=None), FakeMcp())
    assert reply.pending["kind"] == "clarify_report_period"
    assert reply.pending["asked"] == reply.plan.reply == reply.text
    assert reply.pending["options"] == []


@pytest.mark.parametrize("s", [ranking(group_by=["region"]), ranking(filters=[flt("customer", ["ABC"])])])
def test_say_and_unresolved_have_no_pending(s):
    reply = answer(s, FakeMcp())
    assert reply.pending is None


# ---------------------------------------------------------------- run


def test_run_calls_tool_once_with_route_args():
    mcp = FakeMcp(ENVELOPE)
    reply = answer(ranking(), mcp)
    assert len(mcp.calls) == 1
    name, args = mcp.calls[0]
    assert name == "crm_report_ask"
    assert args == {"view": "render", **reply.plan.route_args, "contact_id": CONTACT, "space_id": SPACE}
    assert args["group_by"] == "customer" and args["date_from"] == "2026-09-01"


def test_run_text_is_header_then_presenter_response():
    reply = answer(ranking(), FakeMcp(ENVELOPE))
    assert reply.text == reply.plan.header + "\n" + ENVELOPE["response"]
    assert reply.text.startswith("*Read as:* sales amount, delivered sales, by customer, last month, top 10\n")


def test_run_notes_sit_between_header_and_response():
    reply = answer(ranking(compare_to="last_year"), FakeMcp(ENVELOPE))
    assert reply.plan.notes == ["Comparison with last year is not available yet."]
    assert reply.text.split("\n") == [
        reply.plan.header, "Comparison with last year is not available yet.", *ENVELOPE["response"].split("\n"),
    ]


def test_run_rows_are_display_name_options():
    reply = answer(ranking(), FakeMcp(ENVELOPE))
    assert [o.label for o in reply.rows] == ["ABC SDN BHD", "XYZ"]
    assert [o.payload for o in reply.rows] == [
        {"report_row": {"dimension": "customer", "keys": ["c1"], "label": "ABC SDN BHD"}},
        {"report_row": {"dimension": "customer", "keys": ["c2", "c3"], "label": "XYZ"}},
    ]
    for o in reply.rows:
        assert not any(k in o.key for k in ("c1", "c2", "c3"))
    assert len({o.key for o in reply.rows}) == 2


def test_run_frame_carries_group_by_and_shown():
    reply = answer(ranking(), FakeMcp(ENVELOPE))
    assert reply.frame["group_by"] == "customer"
    assert reply.frame["shown"] == ENVELOPE["drill_rows"]


def test_run_rows_feed_drill_row():
    s = ranking()
    reply = answer(s, FakeMcp(ENVELOPE))
    new, refs = r.drill_row(s, reply.rows[1])
    assert refs["customer"] == [
        {"kind": "customer", "key": "c2", "label": "XYZ"},
        {"kind": "customer", "key": "c3", "label": "XYZ"},
    ]
    assert [f.keys for f in new.filters if f.dimension == "customer"] == [["XYZ"]]


@pytest.mark.parametrize(
    "envelope,text",
    [
        (
            {"error": "forbidden", "status_code": 403,
             "detail": {"message": "That breakdown is not available for your account."}},
            "That breakdown is not available for your account.",
        ),
        (
            {"error": "not_found", "status_code": 404, "detail": {"message": "Brand not found"}},
            "I couldn't find that brand.",
        ),
        ({"error": "boom", "status_code": 500, "detail": {"message": "x"}}, None),
        ({"error": "boom"}, None),
    ],
)
def test_run_error_envelopes(envelope, text):
    from app.services.chatbot.lanes.business import fetch

    reply = answer(ranking(), FakeMcp(envelope))
    assert reply.text == (text or fetch._REPORT_ASK_ERROR_TEXT)
    assert "*Read as:*" not in reply.text
    assert reply.rows == []


# ---------------------------------------------------------------- hand test B, chained


def pick(plan, index):
    """The user picks option `index` of a measure question: its payload is the incoming report."""
    return spec(**plan.options[index].payload["report"])


def test_hand_test_b_chain():
    refs = {}

    # Turn 1: "top 10 customers by sales last month" names neither measure nor basis.
    inc1 = spec(dataset="sales", group_by=["customer"], period=period(preset="last_month"), top_n=10)
    held = r.edit_report(None, inc1)
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "ask" and plan.missing == ["measure", "basis"]
    assert plan.about == "report_measure" and len(plan.options) == 6
    held = plan.spec  # the spec as it is held next turn

    # Turn 1b: the user answers through the option payload (amount, delivered).
    held = r.edit_report(held, pick(plan, 0))
    assert held.measures == ["amount"] and held.basis == "delivered"
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args == {
        "basis": "delivered", "measure": "amount", "group_by": "customer", "sort": "desc", "top_n": 10,
        "date_from": "2026-09-01", "date_to": "2026-09-30",
    }

    # Turn 2: "by qty".
    held = r.edit_report(held, spec(measures=["qty"]))
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["measure"] == "qty" and plan.route_args["basis"] == "delivered"
    assert plan.route_args["group_by"] == "customer" and plan.route_args["top_n"] == 10

    # Turn 3: "SORENTO brand" adds a brand filter.
    held = r.edit_report(held, spec(filters=[flt("brand", ["SORENTO"])]))
    refs = {"brand": [{"key": "b-uuid-1", "label": "SORENTO"}]}
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["brand_ids"] == ["b-uuid-1"] and plan.route_args["measure"] == "qty"
    assert "b-uuid-1" not in plan.header and "brand SORENTO" in plan.header

    # Turn 4: "by salesman instead" swaps group_by, keeps the brand filter.
    held = r.edit_report(held, spec(group_by=["sales_agent"]))
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.route_args["group_by"] == "sales_agent" and plan.route_args["brand_ids"] == ["b-uuid-1"]
    assert "by sales agent" in plan.header

    # Turn 5: "the 2nd one" drills into that row.
    row = r.ReportOption(
        key="ali",
        label="ALI",
        payload={"report_row": {"dimension": "sales_agent", "keys": ["u-uuid-3"], "label": "ALI"}},
    )
    held, drill_refs = r.drill_row(held, row)
    assert drill_refs == {"sales_agent": [{"kind": "sales_agent", "key": "u-uuid-3", "label": "ALI"}]}
    refs = {**refs, **drill_refs}
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "run"
    assert "group_by" not in plan.route_args
    assert plan.route_args["sales_agent_ids"] == ["u-uuid-3"] and plan.route_args["brand_ids"] == ["b-uuid-1"]
    assert plan.route_args["measure"] == "qty" and plan.route_args["date_from"] == "2026-09-01"
    assert "sales agent ALI" in plan.header and "u-uuid-3" not in plan.header

    # Turn 6: "compare with last year" runs the same report plus the truthful line.
    before = plan.route_args
    held = r.edit_report(held, spec(compare_to="last_year"))
    plan = r.plan_report(held, refs=refs, today=TODAY)
    assert plan.status == "run"
    assert plan.notes == ["Comparison with last year is not available yet."]
    assert plan.route_args == before
