"""AGENT-LOOP-SPIKE unit tests: blocks, tools, memory, loop. No DB, no live LLM, no live MCP.

UAC: documentation/plans/chatbot/agent-loop-spike-acceptance-criteria.md. RED until
`app/services/chatbot/agent/` exists.
"""
from __future__ import annotations

import json

from app.services.chatbot.llm_call import RATE_LIMITED_REPLY, RateLimited, TimedOut

from tests.chatbot.agent._fakes import (
    ALL_KEYS,
    CATALOG,
    DEALER_KEYS,
    MARKER,
    FakeChat,
    McpRecorder,
    Recorder,
    result,
    tool_call,
)


def _ctx(**kw):
    from app.services.chatbot.agent.contact import ContactCtx

    base = dict(
        contact_id="900000008",
        space_id="sp1",
        staff=False,
        scope_customer_ids=None,
        linked=(),
        reveal_keys=ALL_KEYS,
        stock_allowed=True,
    )
    base.update(kw)
    return ContactCtx(**base)


def _dealer(**kw):
    return _ctx(scope_customer_ids=["c1"], linked=("c1",), reveal_keys=DEALER_KEYS, **kw)


def _toolbox(ctx=None, *, mcp=None, resolve=None, so=None, dry_run=True):
    from app.services.chatbot.agent.blocks import BlockStore
    from app.services.chatbot.agent.tools import ToolBox

    mcp = mcp or McpRecorder()
    resolve = resolve or Recorder()
    so = so or Recorder({"orders": []})
    box = ToolBox(
        ctx or _ctx(staff=True),
        blocks=BlockStore(),
        mcp_call=mcp,
        resolve_call=resolve,
        so_lookup=so,
        catalog=CATALOG,
        dry_run=dry_run,
    )
    return box, mcp, resolve, so


def _schema_props(box, name):
    for s in box.schemas():
        if s["function"]["name"] == name:
            return s["function"]["parameters"].get("properties", {})
    raise AssertionError(f"{name} not in schemas")


def _run(chat, box, *, messages=None, max_rounds=4):
    from app.services.chatbot.agent.loop import run_loop

    return run_loop(
        chat=chat,
        toolbox=box,
        system="You are a test assistant.",
        messages=messages or [{"role": "user", "content": "stock for SRT-TEST-1"}],
        max_rounds=max_rounds,
    )


# ----------------------------- Loop: AC-5 .. AC-12 -----------------------------


def test_ac5_framing_text_and_block_inserted_verbatim():
    box, _mcp, _r, _s = _toolbox()
    chat = FakeChat([
        result(calls=[tool_call("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})]),
        result("Here you go\n{{block:1}}"),
    ])
    out = _run(chat, box)
    assert out.text.startswith("Here you go")
    assert "SRT-TEST-1" in out.text
    assert "{{" not in out.text
    assert out.error is None


def test_ac6_prompt_holds_summary_never_the_block_body():
    box, _mcp, _r, _s = _toolbox()
    chat = FakeChat([
        result(calls=[tool_call("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})]),
        result("{{block:1}}"),
    ])
    out = _run(chat, box)
    second_prompt = chat.prompt_text(1)
    assert "block 1:" in second_prompt
    assert MARKER not in second_prompt
    assert MARKER in out.text  # the body still reaches the customer, inserted by the engine


def test_ac6_block_store_summary_line_only():
    from app.services.chatbot.agent.blocks import BlockStore

    store = BlockStore()
    n = store.add("crm_x", "Line A: 1\nLine B: 2\nSECRET-BODY: 3", "two facts")
    assert n == 1
    lines = store.summaries()
    assert lines == [lines[0]] and lines[0].startswith("block 1: crm_x, 3 lines")
    assert "two facts" in lines[0]
    assert "SECRET-BODY" not in lines[0]
    assert "SECRET-BODY" in store.assemble("{{block:1}}")


def test_ac7_unknown_block_ref_dropped_and_unused_blocks_appended():
    from app.services.chatbot.agent.blocks import BlockStore

    store = BlockStore()
    store.add("crm_x", "Code: SRT-TEST-1", "one product")
    out = store.assemble("Sure\n{{block:9}}")
    assert "{{" not in out and "}}" not in out
    assert out.startswith("Sure")
    assert "Code: SRT-TEST-1" in out


def test_ac8_empty_final_text_gives_blocks_in_order():
    from app.services.chatbot.agent.blocks import BlockStore

    store = BlockStore()
    store.add("crm_a", "First: 1", "a")
    store.add("crm_b", "Second: 2", "b")
    out = store.assemble("")
    assert out.index("First: 1") < out.index("Second: 2")


def test_ac8_loop_empty_final_text_after_tool_calls():
    box, _m, _r, _s = _toolbox()
    chat = FakeChat([
        result(calls=[tool_call("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})]),
        result(""),
    ])
    out = _run(chat, box)
    assert "SRT-TEST-1" in out.text


def test_ac9_model_keeps_asking_for_tools_final_call_has_no_tools():
    box, _m, _r, _s = _toolbox()
    counter = {"n": 0}

    def always_tool(messages, tools):
        if not tools:
            return result("Done {{block:1}}")
        counter["n"] += 1
        return result(calls=[tool_call("crm_master_products_list", {"query": "x"}, f"call_{counter['n']}")])

    chat = FakeChat(default=always_tool)
    out = _run(chat, box, max_rounds=4)
    assert len(chat.calls) <= 5
    assert not chat.calls[-1]["tools"]
    assert out.text.strip()
    assert out.rounds <= 5


def test_ac10_rate_limit_gives_rate_limited_reply():
    box, _m, _r, _s = _toolbox()
    out = _run(FakeChat([RateLimited("429 slow down")]), box)
    assert out.text == RATE_LIMITED_REPLY
    assert out.error


def test_ac10_timeout_gives_rate_limited_reply():
    box, _m, _r, _s = _toolbox()
    out = _run(FakeChat([TimedOut("deadline")]), box)
    assert out.text == RATE_LIMITED_REPLY
    assert out.error


def test_ac11_tool_raises_model_gets_error_result_and_trace_records_it():
    mcp = McpRecorder(raises={"crm_master_products_list": RuntimeError("boom-upstream")})
    box, _m, _r, _s = _toolbox(mcp=mcp)
    chat = FakeChat([
        result(calls=[tool_call("crm_master_products_list", {"query": "x"})]),
        result("I could not look that up right now."),
    ])
    out = _run(chat, box)
    assert "boom-upstream" in chat.prompt_text(1) or "error" in chat.prompt_text(1).lower()
    assert out.text.strip()
    assert "Traceback" not in out.text
    assert box.events[0]["tool"] == "crm_master_products_list"
    assert box.events[0]["error"]


def test_ac11_provider_failure_still_gives_a_plain_reply():
    box, _m, _r, _s = _toolbox()
    out = _run(FakeChat([RuntimeError("provider exploded")]), box)
    assert out.text.strip()
    assert "Traceback" not in out.text and "exploded" not in out.text
    assert out.error


def test_ac11_execute_never_raises():
    mcp = McpRecorder(raises={"crm_master_products_list": RuntimeError("boom")})
    box, _m, _r, _s = _toolbox(mcp=mcp)
    outcome = box.execute("crm_master_products_list", {"query": "x"})
    assert outcome.error
    assert outcome.block is None


def test_ac12_tool_off_allow_list_refused_and_never_reaches_mcp():
    box, mcp, _r, _s = _toolbox()
    outcome = box.execute("crm_order_cancel", {"order_id": "o1"})
    assert outcome.error
    assert mcp.calls == []
    assert "crm_order_cancel" not in [s["function"]["name"] for s in box.schemas()]


def test_ac12_unknown_tool_name_refused():
    box, mcp, _r, _s = _toolbox()
    assert box.execute("totally_made_up", {}).error
    assert mcp.calls == []


# ----------------------------- Scope: AC-13 .. AC-19 -----------------------------


def test_ac13_dealer_schemas_have_no_customer_params_on_scoped_tools():
    box, *_ = _toolbox(_dealer())
    props = _schema_props(box, "crm_order_management_orders_list")
    assert "customer_ids" not in props and "customer_query" not in props
    assert "order_ids" in props


def test_ac13_staff_schemas_keep_customer_params():
    box, *_ = _toolbox(_ctx(staff=True))
    props = _schema_props(box, "crm_order_management_orders_list")
    assert "customer_ids" in props


def test_ac13_guard_params_never_shown_to_the_model():
    box, *_ = _toolbox(_dealer())
    props = _schema_props(box, "crm_inventory_stock_balance_list")
    assert "contact_id" not in props and "space_id" not in props


def test_ac14_dealer_naming_another_customer_is_refused():
    box, mcp, *_ = _toolbox(_dealer())
    outcome = box.execute("crm_order_management_orders_list", {"customer_ids": ["c2"]})
    assert outcome.error
    assert mcp.calls == []


def test_ac14_dealer_naming_none_gets_linked_ids_injected():
    box, mcp, *_ = _toolbox(_dealer())
    outcome = box.execute("crm_order_management_orders_list", {"order_ids": ["SO-TEST-1"]})
    assert outcome.error is None
    (_name, args), = mcp.calls
    assert args["customer_ids"] == ["c1"]
    assert "customer_query" not in args


def test_ac14_dealer_customer_query_is_dropped():
    box, mcp, *_ = _toolbox(_dealer())
    box.execute("crm_order_management_orders_list", {"customer_query": "Other Trading"})
    assert "customer_query" not in mcp.calls[0][1]


def test_ac15_staff_customer_ids_pass_unchanged():
    box, mcp, *_ = _toolbox(_ctx(staff=True))
    outcome = box.execute("crm_order_management_orders_list", {"customer_ids": ["c2", "c9"]})
    assert outcome.error is None
    assert mcp.calls[0][1]["customer_ids"] == ["c2", "c9"]


def test_ac16_every_mcp_call_carries_contact_and_space_whatever_the_model_sent():
    box, mcp, *_ = _toolbox(_ctx(staff=True, contact_id="900000008", space_id="sp1"))
    box.execute("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"], "contact_id": "evil", "space_id": "evil"})
    box.execute("crm_master_products_list", {"query": "x"})
    assert len(mcp.calls) == 2
    for _name, args in mcp.calls:
        assert str(args["contact_id"]) == "900000008"
        assert args["space_id"] == "sp1"


def test_ac17_stock_not_allowed_refuses_stock_tools():
    box, mcp, *_ = _toolbox(_dealer(stock_allowed=False))
    outcome = box.execute("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})
    assert outcome.error
    assert mcp.calls == []
    # a non-stock tool is unaffected
    assert box.execute("crm_master_products_list", {"query": "x"}).error is None


def test_ac17_refusal_reaches_the_model_as_a_tool_result_not_a_crash():
    box, _m, _r, _s = _toolbox(_dealer(stock_allowed=False))
    chat = FakeChat([
        result(calls=[tool_call("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})]),
        result("Sorry, I cannot share stock with you."),
    ])
    out = _run(chat, box)
    tool_msgs = [m for m in chat.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["content"].strip()
    assert out.text.startswith("Sorry")


def test_ac18_missing_reveal_key_refuses_domain_tools():
    box, mcp, *_ = _toolbox(_dealer())  # DEALER_KEYS lacks purchase_orders.cost
    outcome = box.execute("crm_procurement_po_last_cost_list", {"product_ids": ["SRT-TEST-1"]})
    assert outcome.error
    assert mcp.calls == []


def test_ac18_with_the_reveal_key_the_tool_runs():
    box, mcp, *_ = _toolbox(_ctx(staff=True))
    outcome = box.execute("crm_procurement_po_last_cost_list", {"product_ids": ["SRT-TEST-1"]})
    assert outcome.error is None
    assert [c[0] for c in mcp.calls] == ["crm_procurement_po_last_cost_list"]


def test_ac19_so_lookup_receives_the_contacts_linked_customers():
    so = Recorder({"orders": []})
    box, *_ = _toolbox(_dealer(), so=so)
    box.execute("so_lookup", {"numbers": ["SO-TEST-1"]})
    assert len(so.calls) == 1
    assert so.seen("c1") and so.seen("SO-TEST-1")


# ----------------------------- Memory: AC-20 / AC-21 (unit half) -----------------------------


def test_ac20_memory_patch_holds_last_messages_records_and_offered():
    from app.services.chatbot.agent.memory import Memory

    mem = Memory.from_state(None)
    for i in range(5):
        mem.remember_turn(
            f"user {i}",
            f"bot {i}",
            [{"kind": "product", "code": "SRT-TEST-1"}],
            [{"n": 1, "label": "SRT-TEST-1"}, {"n": 2, "label": "SRT-TEST-2"}],
        )
    patch = mem.to_patch()
    agent = patch["agent"]
    assert len(agent["messages"]) == 6
    assert agent["messages"][-1]["content"] == "bot 4"
    assert agent["messages"][-2]["content"] == "user 4"
    assert any(r.get("code") == "SRT-TEST-1" for r in agent["records"])
    assert [o["n"] for o in agent["offered"]] == [1, 2]


def test_ac20_memory_round_trips_through_state():
    from app.services.chatbot.agent.memory import Memory

    mem = Memory.from_state(None)
    mem.remember_turn("hello", "hi", [{"kind": "product", "code": "SRT-TEST-9"}], [])
    again = Memory.from_state(mem.to_patch())
    assert again.messages == mem.messages
    assert again.records == mem.records


def test_ac20_memory_tolerates_missing_or_foreign_state():
    from app.services.chatbot.agent.memory import Memory

    assert Memory.from_state(None).messages == []
    assert Memory.from_state({}).messages == []
    assert Memory.from_state({"agent": None}).messages == []
    assert Memory.from_state({"something": "else"}).records == []


# ----------------------------- Portal link: AC-23 -----------------------------


def test_ac23_portal_link_dry_run_returns_link_shape_without_calling_the_token_route():
    box, mcp, *_ = _toolbox(_ctx(staff=True), dry_run=True)
    outcome = box.execute("portal_link", {"submission_type": "order"})
    assert outcome.error is None
    assert outcome.block is not None
    assert "crm_portal_link_get" not in [c[0] for c in mcp.calls]
    assert "http" in box.blocks.assemble("{{block:%d}}" % outcome.block).lower()


# ----------------------------- Style: AC-25 -----------------------------


def test_ac25_rendered_blocks_put_one_fact_per_line_and_never_join_with_dots():
    box, *_ = _toolbox(_ctx(staff=True))
    outcome = box.execute("crm_inventory_stock_balance_list", {"product_ids": ["SRT-TEST-1"]})
    text = box.blocks.assemble("{{block:%d}}" % outcome.block)
    assert " · " not in text
    assert "\n" in text


def test_ac24_loop_sums_tokens_across_rounds():
    box, *_ = _toolbox()
    chat = FakeChat([
        result(calls=[tool_call("crm_master_products_list", {"query": "x"})], prompt=100, cached=40, out=10),
        result("ok {{block:1}}", prompt=150, cached=60, out=20),
    ])
    out = _run(chat, box)
    assert out.tokens == {"prompt": 250, "cached": 100, "output": 30}
    assert out.rounds == 2
    json.dumps(out.tokens)
