"""EVAL-BENCH CP0 grader (`scripts/chatbot_eval/targets.py`): one pass and one fail per slice on hand-built rows.

Row shapes below mirror a real persisted turn (probed from a synthetic engine turn). Where the plan did not pin a
location, the choice made here is the contract the coder is held to:
  - grounded entities: the `apply` event's `verdict.entities` (the real `understood.facts.entities` is only a COUNT)
  - `apply.decision = {kind, why, positions}`; `apply.verdict.reference_positions` is the parser's own read
  - subject after the turn: `state` event `focus.products[*].canonical_code`
  - fact asked: the `routed` stage `facts.domains`; a clarify = `replied` stage `facts.asking` truthy
  - open question: `open_question` event `{before, answer, after, handler, outcome}`; re-asked = `after` equals `before`
  - attachment: tool event `crm_attachments_list` args `attachment_type`; envelope `items[*].attachment_type`
  - customers in an envelope: `items[*].{customer_id, customer_name}`; reply text at `response.reply.text`
  - user message: `envelope.message.message.message.text`
  - truncated envelope: `{"truncated": True, "head": "..."}` (trace.cap_envelope)
"""
from __future__ import annotations

import re

from scripts.chatbot_eval.targets import grade_turn, made_up_facts, scope_leaks, summarise

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
INTERNAL = ("canonical_code", "spec_key", "spec_value", "decision.kind", "linked_customer_ids", "envelope", "trace",
            "branch_kind", "attachment_type", "reference_positions")
STAFF = {"audience": "staff", "linked_customer_ids": None, "hidden_fields": []}


def row(*, message="stock of SRTA100", reply="SRTA100 has 40 pcs.", entities=None, domains=("inventory",),
        asking=None, decision=None, positions=None, tools=None, open_question=None, focus_codes=None):
    trace = [
        {"stage": "understood", "status": "ok", "facts": {"entities": len(entities or [])}, "raw": {}},
        {"stage": "routed", "status": "ok", "facts": {"lane": "business_query", "domains": list(domains)}},
        {"stage": "replied", "status": "ok", "facts": {"asking": asking, "files": 0}},
        {"kind": "apply", "verdict": {"entities": entities or [], "reference_positions": positions or []},
         "decision": {"kind": decision, "why": "synthetic", "positions": positions or []} if decision else None},
    ]
    for t in tools or []:
        trace.append({"kind": "tool", "name": t["name"], "args": t.get("args", {}), "envelope": t.get("envelope", {})})
    if open_question is not None:
        trace.append({"kind": "open_question", **open_question})
    if focus_codes is not None:
        trace.append({"kind": "state", "focus": {"products": [{"canonical_code": c} for c in focus_codes]}})
    return {"id": "t1", "branch_kind": "business_query",
            "envelope": {"message": {"message": {"message": {"text": message}}}},
            "trace": trace, "response": {"reply": {"text": reply}}}


def ent(code, **extra):
    return {"raw": code, "hint": "product", "canonical_code": code, "confident": True, **extra}


def tool_for(code, items=None, name="crm_inventory_list"):
    return {"name": name, "args": {"code": code}, "envelope": {"items": items if items is not None else [{"code": code, "qty": 40}]}}


def item(slice_, kind=None, message="synthetic ask", asker=STAFF, **gold):
    return {"item_id": "it1", "slice": slice_, "kind": kind, "message": message, "gold": gold, "asker": asker}


def check(result, passed, gist=None):
    assert result["passed"] is passed, result
    assert set(result) >= {"item_id", "slice", "kind", "passed", "reason", "checks", "leaks", "made_up"}
    assert result["reason"] and not UUID.search(result["reason"])
    for word in INTERNAL:
        assert word not in result["reason"], word
    if gist:
        assert gist in result["reason"].lower(), result["reason"]


# ------------------------------------------------------------------ code lookup
def test_code_lookup_pass():
    r = row(entities=[ent("SRTA100")], tools=[tool_for("SRTA100")])
    check(grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory")), True)


def test_code_lookup_wrong_code_fails():
    r = row(entities=[ent("SRTB200")], reply="SRTB200 has 40 pcs.", tools=[tool_for("SRTB200")])
    check(grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory")), False, "code")


def test_code_lookup_clarify_on_unambiguous_code_fails():
    r = row(entities=[ent("SRTA100")], tools=[tool_for("SRTA100")], asking="which_product")
    check(grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory")), False, "clarif")


def test_code_lookup_clarify_passes_when_gold_is_ambiguous():
    r = row(entities=[ent("SRTA100")], reply="Which SRTA100 do you mean?", asking="which_product")
    check(grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory", ambiguous=True)), True)


# ------------------------------------------------------------------ follow-ups
def test_followup_pick_pass_and_wrong_position_fails():
    ok = row(decision="answer", positions=[2], entities=[ent("SRTA200")], reply="SRTA200 has 7 pcs.",
             tools=[tool_for("SRTA200")])
    check(grade_turn(ok, item("followup", "pick", position=2, code="SRTA200")), True)
    bad = row(decision="answer", positions=[3], entities=[ent("SRTA300")], reply="SRTA300 has 7 pcs.",
              tools=[tool_for("SRTA300")])
    check(grade_turn(bad, item("followup", "pick", position=2, code="SRTA200")), False, "position")


def test_followup_answer_pass_and_reask_fails():
    q = {"question": "which colour", "options": ["white", "black"]}
    ok = row(decision="answer", open_question={"before": q, "answer": "white", "after": None, "outcome": "resolved"})
    check(grade_turn(ok, item("followup", "answer", option="white")), True)
    bad = row(decision="answer", open_question={"before": q, "answer": None, "after": q, "outcome": "unresolved"})
    check(grade_turn(bad, item("followup", "answer", option="white")), False, "again")


def test_followup_refine_pass_and_dropped_subject_fails():
    spec = {"hint": "specification", "spec_key": "colour", "spec_value": "white", "canonical_code": "white"}
    ok = row(decision="refine", entities=[spec], focus_codes=["SRTA100"])
    check(grade_turn(ok, item("followup", "refine", subject_after=["SRTA100"], filter={"key": "colour", "value": "white"})), True)
    bad = row(decision="refine", entities=[spec], focus_codes=[])
    check(grade_turn(bad, item("followup", "refine", subject_after=["SRTA100"], filter={"key": "colour", "value": "white"})), False, "subject")


def test_followup_switch_pass_and_carried_old_subject_fails():
    ok = row(decision="new_ask", entities=[ent("SRTB200")], focus_codes=["SRTB200"])
    check(grade_turn(ok, item("followup", "switch", subject_after=["SRTB200"])), True)
    bad = row(decision="new_ask", entities=[ent("SRTB200")], focus_codes=["SRTA100", "SRTB200"])
    check(grade_turn(bad, item("followup", "switch", subject_after=["SRTB200"])), False, "old")


def test_followup_and_x_pass_and_dropped_fact_fails():
    ok = row(decision="new_ask", entities=[ent("SRTB200")], domains=("inventory",), focus_codes=["SRTB200"])
    check(grade_turn(ok, item("followup", "and_x", fact="inventory", subject_after=["SRTB200"])), True)
    bad = row(decision="new_ask", entities=[ent("SRTB200")], domains=("master_products",), focus_codes=["SRTB200"])
    check(grade_turn(bad, item("followup", "and_x", fact="inventory", subject_after=["SRTB200"])), False, "fact")


# ------------------------------------------------------------------ spec, attachment
def test_spec_pass_and_missing_value_fails():
    good = [{"hint": "specification", "spec_key": "colour", "spec_value": "white", "canonical_code": "white"}]
    check(grade_turn(row(entities=good), item("spec", spec=[{"key": "colour", "value": "white"}])), True)
    bad = [{"hint": "specification", "spec_key": "colour", "spec_value": None, "canonical_code": None}]
    check(grade_turn(row(entities=bad), item("spec", spec=[{"key": "colour", "value": "white"}])), False, "spec")


def _att(files, type_="technical_specifications", reply="Here are the files."):
    return row(reply=reply, tools=[{"name": "crm_attachments_list", "args": {"attachment_type": type_},
                                    "envelope": {"items": files}}])


def test_attachment_pass_and_wrong_type_fails():
    files = [{"name": "a.pdf", "attachment_type": "technical_specifications"}]
    check(grade_turn(_att(files), item("attachment", attachment_type="technical_specifications")), True)
    wrong = [{"name": "a.jpg", "attachment_type": "product_photos"}]
    check(grade_turn(_att(wrong, type_="product_photos"), item("attachment", attachment_type="technical_specifications")), False, "type")


def test_attachment_none_on_record_passes_only_when_gold_says_none():
    r = _att([], reply="There is none on record for this product.")
    check(grade_turn(r, item("attachment", attachment_type="technical_specifications", none_on_record=True)), True)
    check(grade_turn(r, item("attachment", attachment_type="technical_specifications", none_on_record=False)), False, "none on record")


# ------------------------------------------------------------------ scope
DEALER = {"audience": "dealer", "linked_customer_ids": ["c-own"], "hidden_fields": ["cost_price"]}


def test_scope_customer_outside_links_is_a_leak_and_clean_passes():
    leaky = row(reply="Orders for Acme Sdn Bhd and Rival Trading.",
                tools=[{"name": "crm_orders_list", "envelope": {"items": [
                    {"customer_id": "c-own", "customer_name": "Acme Sdn Bhd"},
                    {"customer_id": "c-other", "customer_name": "Rival Trading"}]}}])
    leaks = scope_leaks(leaky, DEALER)
    assert leaks and any("Rival Trading" in x for x in leaks) and not any("Acme" in x for x in leaks)
    clean = row(reply="Orders for Acme Sdn Bhd.",
                tools=[{"name": "crm_orders_list", "envelope": {"items": [{"customer_id": "c-own", "customer_name": "Acme Sdn Bhd"}]}}])
    assert scope_leaks(clean, DEALER) == []
    check(grade_turn(clean, item("scope", asker=DEALER)), True)
    check(grade_turn(leaky, item("scope", asker=DEALER)), False, "customer")


def test_scope_hidden_field_in_reply_is_a_leak():
    r = row(reply="The cost price is RM 12.00.")
    assert scope_leaks(r, DEALER)
    assert scope_leaks(row(reply="The list price is RM 20.00."), DEALER) == []


# ------------------------------------------------------------------ made-up facts
def _fact_row(reply, items, message="stock of SRTA100", envelope_override=None):
    t = {"name": "crm_inventory_list", "envelope": envelope_override or {"items": items}}
    return row(message=message, reply=reply, tools=[t])


def test_made_up_quantity_not_in_any_envelope_is_a_finding():
    r = _fact_row("SRTA100 has 999 pcs.", [{"code": "SRTA100", "qty": 40}])
    assert any("999" in f for f in made_up_facts(r))
    assert made_up_facts(_fact_row("SRTA100 has 40 pcs.", [{"code": "SRTA100", "qty": 40}])) == []


def test_list_ordinals_are_ignored():
    r = _fact_row("1. SRTA100\n2. SRTA101", [{"code": "SRTA100"}, {"code": "SRTA101"}])
    assert made_up_facts(r) == []


def test_thousands_separator_and_date_formats_are_normalised():
    r = _fact_row("SRTA100 has 1,200 pcs, due 6 Sep 2026.", [{"code": "SRTA100", "qty": 1200, "eta": "2026-09-06"}])
    assert made_up_facts(r) == []


def test_truncated_envelope_makes_an_unverifiable_finding():
    r = _fact_row("SRTA100 has 77 pcs.", None, envelope_override={"truncated": True, "bytes": 99999, "head": "{\"items\": ["})
    assert any(f.startswith("unverifiable:") and "77" in f for f in made_up_facts(r))


def test_made_up_fact_fails_the_graded_turn_and_lists_it():
    r = row(entities=[ent("SRTA100")], reply="SRTA100 has 999 pcs.", tools=[tool_for("SRTA100")])
    res = grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory"))
    assert res["made_up"]


# ------------------------------------------------------------------ summarise
def _res(slice_, kind, passed, leaks=(), made_up=()):
    return {"item_id": "x", "slice": slice_, "kind": kind, "passed": passed, "reason": "r", "checks": {},
            "leaks": list(leaks), "made_up": list(made_up)}


def test_summarise_meets_only_when_every_run_is_clean():
    bars = {"code_lookup": 0.97}
    clean = [[_res("code_lookup", None, True) for _ in range(100)] for _ in range(3)]
    s = summarise(clean, bars)
    assert s["meets"] is True
    assert s["slices"]["code_lookup"]["n"] == 100 and s["slices"]["code_lookup"]["rate"] == 1.0
    dirty = [list(run) for run in clean]
    dirty[1][0] = _res("code_lookup", None, True, leaks=["a customer outside the links"])
    s2 = summarise(dirty, bars)
    assert s2["meets"] is False
    assert s2["leaks"] == 1
