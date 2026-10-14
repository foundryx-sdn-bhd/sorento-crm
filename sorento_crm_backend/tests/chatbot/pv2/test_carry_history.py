"""MEMORY block + history (CONTRACT-PV2-13oct.md section 2). DB-free inputs only; the
Postgres `load_exchanges` path is not covered here (its fixtures are the heavy ones)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.services.chatbot.pv2.history import (
    MemoryProfile,
    history_messages,
    load_exchanges,
    render_memory,
    render_user_message,
)
from app.services.chatbot.pv2.schema import Period, ReportSpec
from app.services.chatbot.pv2.state import Held, ResolvedRef, ResolvedSpec
from tests.chatbot.pv2.carry_fakes import NOW, held, mk_state, option, pending

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PROFILE = MemoryProfile(audience="staff", role="salesperson", company="Sorento Group", language="zh",
                        brands=["SORENTO", "MOCHA"])
KEYS = ["TODAY:", "CONTACT:", "HELD:", "PENDING QUESTION:", "TOPICS (switch_back keys, newest first):",
        "LAST ANSWER ROWS:", "EARLIER SESSIONS:"]


def _report(**kw):
    base = dict(dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
                period=Period(preset="last_month", n=None, date_from=None, date_to=None), compare_to=None,
                compare_period=None, grain=None, threshold=None, sort=None, top_n=None, basis=None)
    base.update(kw)
    return ReportSpec(**base)


def _contract_state():
    h = Held(
        domain="product_attachment",
        refs={"product": [ResolvedRef(kind="product", key="SRTWC286", label="SRTWC286 (family, 10 variants)")]},
        attachment_type="technical_specifications",
        specs=[ResolvedSpec(attribute="trap_type", comparator="eq", value="p_trap"),
               ResolvedSpec(attribute="dim_length", comparator="eq", value=600, unit="mm")],
    )
    return mk_state(
        h,
        pending_=pending("clarify_spec_attribute",
                         [option("dim_length", "Length"), option("dim_height", "Height")],
                         asked="Which 600 mm do you mean?"),
        topics=[held("inventory", {"product": ["SRTWC6003"]}), Held(domain="sales", report=_report())],
        last_rows=[option("1", "SRTWC6003"), option("2", "SRTWC8066")],
    )


def test_contract_example_line_by_line():
    expected = [
        "<memory>",
        "TODAY: 2026-10-13 Tue (Asia/Kuala_Lumpur)",
        "CONTACT: audience=staff; role=salesperson; company=Sorento Group; language=zh; brands=SORENTO,MOCHA",
        "HELD: domain=product_attachment",
        "  product: SRTWC286 (family, 10 variants)",
        "  attachment_type: technical_specifications",
        "  spec: trap_type=p_trap; dim_length=600 mm",
        'PENDING QUESTION: kind=clarify_spec_attribute; asked="Which 600 mm do you mean?"',
        "  options: 1=dim_length (Length) | 2=dim_height (Height)",
        "TOPICS (switch_back keys, newest first):",
        "  T1: inventory | product: SRTWC6003",
        "  T2: sales | report: amount by customer, last_month",
        "LAST ANSWER ROWS: 1=SRTWC6003 | 2=SRTWC8066",
        "EARLIER SESSIONS: none",
        "</memory>",
    ]
    got = render_memory(_contract_state(), PROFILE, NOW).split("\n")
    # R4a: LAST ANSWER ROWS / HELD codes / LAST REPLY. The one new line (the open question's kind, no text) is
    # asserted here and the rest of the contract example is compared as it was.
    assert [g for g in got if g.startswith("LAST REPLY:")] == ["LAST REPLY: asked clarify_spec_attribute"]
    got = [g for g in got if not g.startswith("LAST REPLY:")]
    bad = [(i, e, g) for i, (e, g) in enumerate(zip(expected, got)) if e != g]
    assert not bad and len(got) == len(expected), bad or got


def test_every_key_printed_in_fixed_order_and_none_when_empty():
    text = render_memory(mk_state(), MemoryProfile(), NOW)
    lines = text.split("\n")
    idx = [next(i for i, l in enumerate(lines) if l.startswith(k)) for k in KEYS]
    assert idx == sorted(idx)
    assert "HELD: none" in lines
    assert "PENDING QUESTION: none" in lines
    assert "TOPICS (switch_back keys, newest first): none" in lines
    assert "LAST ANSWER ROWS: none" in lines
    assert "EARLIER SESSIONS: none" in lines
    assert "CONTACT: audience=none; role=none; company=none; language=none; brands=none" in lines
    assert lines[0] == "<memory>" and lines[-1] == "</memory>"


def test_today_is_the_kuala_lumpur_date():
    late = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)  # 01:00 next day in KL
    assert "TODAY: 2026-10-14 Wed (Asia/Kuala_Lumpur)" in render_memory(mk_state(), PROFILE, late)


def test_refs_print_label_never_key_or_uuid():
    uid = "123e4567-e89b-12d3-a456-426614174000"
    h = Held(domain="sales", refs={"customer": [ResolvedRef(kind="customer", key=uid, label="Ahmad Trading")]})
    text = render_memory(mk_state(h, topics=[h]), PROFILE, NOW)
    assert "Ahmad Trading" in text and uid not in text and not UUID_RE.search(text)


def test_report_line_and_held_period():
    h = Held(domain="sales", report=_report(basis="delivered", top_n=10))
    lines = render_memory(mk_state(h), PROFILE, NOW).split("\n")
    assert ("  report: dataset=sales; measures=amount; basis=delivered; group_by=customer; "
            "period=last_month; top_n=10") in lines


def test_earlier_sessions_capped_at_three_lines():
    p = MemoryProfile(earlier_sessions=["s1 line", "s2 line", "s3 line", "s4 line"])
    text = render_memory(mk_state(), p, NOW)
    assert text.index("s1 line") < text.index("s2 line") < text.index("s3 line")
    assert "s4 line" not in text and "EARLIER SESSIONS: none" not in text


def test_no_dot_separator_anywhere():
    assert " · " not in render_memory(_contract_state(), PROFILE, NOW)


def test_user_message_is_memory_then_verbatim_text():
    text = 'stock of <b>SRTWC286</b>?\nline "two" & more'
    msg = render_user_message(_contract_state(), PROFILE, NOW, text)
    mem = render_memory(_contract_state(), PROFILE, NOW)
    assert msg == f"{mem}\n<message>{text}</message>"


def test_history_is_last_three_exchanges_oldest_first():
    ex = [(f"u{i}", f"b{i}") for i in range(5)]
    msgs = history_messages(ex)
    assert [m["content"] for m in msgs] == ["u2", "b2", "u3", "b3", "u4", "b4"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] * 3
    assert history_messages([]) == []
    assert len(history_messages(ex[:1])) == 2


def test_load_exchanges_failure_is_no_history():
    class Boom:
        def query(self, *a, **k):
            raise RuntimeError("db down")

    assert load_exchanges(Boom(), contact_respond_id="1", ingress=None, is_test=False,
                          since=NOW - timedelta(hours=1)) == []
