"""CP0 grader v2: the sources that exist in REAL traces (PLAN-eval-bench-cp0 "Grader v2"). Hand-built rows in the real
shapes; the v1 tests in test_targets.py stay and must still pass.

Shapes used (all from the plan section): product entity {hint: product, raw} with NO canonical_code; `memory` event
{focus: {before: {products}, after: {products, domains, brands, extra: {specification, attachment_type}}},
open_question: {before, after, writer}}; `state` event {focus: {products}, open_question}; `looked_up` stage
raw.envelopes[] with `entities` / `product_codes` as plain strings; tool events {name, args} where `name` is None for
domain markers and args.product_ids are uuids mapped to codes through the state + memory product lists; `engine_pick`;
`apply.verdict.open_question_answer {mode, items, picked}`; `prompt_text` {text}. A payload omitted at 32 KB is an apply
event `{kind: "apply", note: "payload omitted"}`. `row["prev_bot_message"]` carries the bot's previous message
(the loader sets it; made_up_facts reads it).
"""
from __future__ import annotations

import json

import pytest

from scripts.chatbot_eval.targets import grade_turn, made_up_facts
from tests.chatbot_eval.test_targets import check, item, ent, _att, _file, GOLD_TS  # noqa: F401  (helpers only)

UA = "11111111-1111-4111-8111-111111111111"
UB = "22222222-2222-4222-8222-222222222222"


def prod(code, uuid=None, **kw):
    return {"raw": code, "hint": "product", "uuid": uuid, "company_name": None, "canonical_code": code, **kw}


def row2(*, message="stock of srta100", reply="SRTA100 has 40 pcs.", domains=("inventory",), verdict=None,
         apply_omitted=False, state_products=(), state_oq=None, mem_before=(), mem_after=(), mem_extra=None,
         mem_brands=(), oq=None, looked=None, tools=(), pick=None, prompt_text=None, prev_bot="", truncated=False,
         files=0):
    trace = [
        {"stage": "routed", "status": "ok", "facts": {"lane": "business_query", "domains": list(domains)}},
        {"stage": "replied", "status": "ok", "facts": {"asking": None, "files": files}},
    ]
    if looked is not None:
        trace.append({"stage": "looked_up", "status": "ok", "facts": {}, "raw": {"envelopes": looked}})
    if apply_omitted:
        trace.append({"kind": "apply", "note": "payload omitted"})
    else:
        trace.append({"kind": "apply", "verdict": verdict if verdict is not None else
                      {"entities": [{"raw": "srta100", "hint": "product"}], "reference_positions": []},
                      "decision": {"kind": None, "why": None}})
    trace.append({"kind": "state", "turn_no": 2, "focus": {"products": list(state_products)}, "open_question": state_oq})
    trace.append({"kind": "memory", "episodes": [],
                  "focus": {"before": {"products": list(mem_before)},
                            "after": {"products": list(mem_after), "domains": list(domains), "brands": list(mem_brands),
                                      "extra": mem_extra or {}}},
                  "open_question": oq or {"before": None, "after": None, "writer": None}})
    for t in tools:
        trace.append({"kind": "tool", "name": t.get("name"), "args": t.get("args", {}),
                      "envelope": {"truncated": True, "bytes": 99999, "head": "{"} if truncated else t.get("envelope", {})})
    if pick is not None:
        trace.append({"kind": "engine_pick", "parser_positions": [], "positions": pick, "roster": []})
    if prompt_text is not None:
        trace.append({"kind": "prompt_text", "text": prompt_text})
    return {"id": "t1", "branch_kind": "business_query", "prev_bot_message": prev_bot,
            "envelope": {"message": {"message": {"message": {"text": message}}}},
            "trace": trace, "response": {"reply": {"text": reply}}}


GOLD_CL = dict(code="srta100", fact="inventory")
TOOL_A = {"name": "crm_inventory_list", "args": {"product_ids": [UA]}}
TOOL_B = {"name": "crm_inventory_list", "args": {"product_ids": [UB]}}


# ------------------------------------------------------------------ code, from memory.after (case/dash tolerant)
def test_code_from_memory_after_with_lowercase_dashless_gold():
    r = row2(mem_after=[prod("SRT-A100", UA)], tools=[TOOL_A])
    check(grade_turn(r, item("code_lookup", **GOLD_CL)), True)


def test_code_from_memory_after_wrong_code_fails():
    r = row2(mem_after=[prod("SRTB200", UB)], tools=[TOOL_B])
    check(grade_turn(r, item("code_lookup", **GOLD_CL)), False, "code")


# ------------------------------------------------------------------ code, from looked_up strings when memory is empty
def test_code_from_looked_up_strings_when_memory_is_empty():
    ok = row2(looked=[{"tool": "t", "entities": ["SRT-A100"], "product_codes": ["SRT-A100"]}],
              tools=[{"name": "crm_inventory_list", "args": {"scope_terms": ["SRTA100"]}}])
    check(grade_turn(ok, item("code_lookup", **GOLD_CL)), True)
    bad = row2(looked=[{"tool": "t", "entities": ["SRTB200"], "product_codes": ["SRTB200"]}],
               tools=[{"name": "crm_inventory_list", "args": {"scope_terms": ["SRTB200"]}}])
    check(grade_turn(bad, item("code_lookup", **GOLD_CL)), False, "code")


# ------------------------------------------------------------------ tool target via the uuid map; unnamed tools skipped
def test_tool_target_is_mapped_from_uuid_through_state_and_memory_products():
    ok = row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A])
    check(grade_turn(ok, item("code_lookup", **GOLD_CL)), True)
    wrong = row2(state_products=[prod("SRTA100", UA), prod("SRTB200", UB)], mem_after=[prod("SRTA100", UA)],
                 tools=[TOOL_B])
    check(grade_turn(wrong, item("code_lookup", **GOLD_CL)), False, "tool")


def test_tools_without_a_name_are_ignored():
    marker = {"name": None, "args": {"product_ids": [UB]}, "domain": "inventory"}
    ok = row2(mem_after=[prod("SRTA100", UA)], tools=[marker, TOOL_A])
    check(grade_turn(ok, item("code_lookup", **GOLD_CL)), True)
    only_marker = row2(mem_after=[prod("SRTA100", UA)], tools=[marker])
    check(grade_turn(only_marker, item("code_lookup", **GOLD_CL)), False, "tool")


# ------------------------------------------------------------------ follow-ups
def test_pick_via_reference_positions_when_there_is_no_engine_pick():
    v = {"entities": [], "reference_positions": [2], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    gold = dict(position=2, code="SRTA200")
    base = dict(mem_after=[prod("SRTA200", UA)], tools=[TOOL_A])
    check(grade_turn(row2(verdict=v, **base), item("followup", "pick", **gold)), True)
    v3 = dict(v, reference_positions=[3])
    check(grade_turn(row2(verdict=v3, **base), item("followup", "pick", **gold)), False, "position")


def test_answer_via_memory_open_question_change_without_a_mode():
    q = {"question": "which colour", "options": ["white", "black"]}
    v = {"entities": [], "reference_positions": [], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    # a cleared question reads as answered only when the apply payload was omitted (lead sign-off: narrowed)
    ok = row2(apply_omitted=True, state_oq=q, oq={"before": q, "after": None, "writer": "engine"})
    check(grade_turn(ok, item("followup", "answer", option="white")), True)
    bad = row2(verdict=v, state_oq=q, oq={"before": q, "after": q, "writer": "engine"})
    check(grade_turn(bad, item("followup", "answer", option="white")), False, "again")


def test_refine_with_a_brand_entity_and_memory_extra():
    v = {"entities": [{"raw": "sorento", "hint": "brand", "canonical_code": "Sorento"}], "reference_positions": []}
    gold = dict(subject_after=["SRTA100"], filter={"kind": "brand", "value": "Sorento"})
    ok = row2(verdict=v, mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], mem_brands=["Sorento"],
              tools=[TOOL_A])
    check(grade_turn(ok, item("followup", "refine", **gold)), True)
    bad = row2(verdict={"entities": [], "reference_positions": []}, mem_before=[prod("SRTA100", UA)],
               mem_after=[prod("SRTA100", UA)], mem_brands=[])
    check(grade_turn(bad, item("followup", "refine", **gold)), False, "filter")


def test_switch_reads_memory_before_and_after():
    ok = row2(mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTB200", UB)])
    check(grade_turn(ok, item("followup", "switch", subject_after=["SRTB200"])), True)
    same = row2(mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)])
    check(grade_turn(same, item("followup", "switch", subject_after=["SRTB200"])), False)


# ------------------------------------------------------------------ spec, attachment
def test_spec_from_memory_extra_with_an_empty_spec_key():
    spec_entries = [{"spec_key": "", "spec_value": "white", "raw": "white"}]
    gold = dict(spec=[{"key": "colour", "value": "white"}])
    ok = row2(verdict={"entities": [{"raw": "white", "hint": "specification", "spec_key": "", "spec_value": "white"}],
                       "reference_positions": []}, mem_extra={"specification": spec_entries})
    check(grade_turn(ok, item("spec", **gold)), True)
    bad_entries = [{"spec_key": "", "spec_value": "black", "raw": "black"}]
    bad = row2(verdict={"entities": [], "reference_positions": []}, mem_extra={"specification": bad_entries})
    check(grade_turn(bad, item("spec", **gold)), False, "spec")


def test_attachment_asked_type_from_memory_extra():
    gold = dict(attachment_type="technical_specifications", attachment_type_name="Technical Specifications",
                attachment_type_names=["technical_specifications", "Technical Specifications", "drawing"])
    files = [{"filename": "a.pdf", "url": "u", "mimeType": "application/pdf", "attachmentType": "Technical Specifications"}]
    tool = {"name": "crm_attachments_list", "args": {"attachment_type_ids": ["id-1"]}, "envelope": {"attachments": files}}
    ok = row2(verdict={"entities": [], "reference_positions": []}, files=1, tools=[tool],
              mem_extra={"attachment_type": "technical_specifications"})
    check(grade_turn(ok, item("attachment", **gold)), True)
    bad = row2(verdict={"entities": [], "reference_positions": []}, files=1, tools=[tool],
               mem_extra={"attachment_type": "product_photos"})
    check(grade_turn(bad, item("attachment", **gold)), False, "asked type")


# ------------------------------------------------------------------ made-up facts, widened sources
def test_codes_in_prompt_text_or_looked_up_entities_are_not_made_up():
    r = row2(reply="SRTA100 and SRTZ999 are in stock.", prompt_text="Products shown: SRTA100, SRTZ999")
    assert made_up_facts(r) == []
    r2 = row2(reply="SRTA100 and SRTZ999 are in stock.", looked=[{"tool": "t", "entities": ["SRTA100", "SRTZ999"]}])
    assert made_up_facts(r2) == []
    r3 = row2(reply="SRTA100 and SRTQ777 are in stock.", looked=[{"tool": "t", "entities": ["SRTA100"]}],
              message="stock of srta100")
    assert any("SRTQ777" in f for f in made_up_facts(r3))


def test_a_number_only_in_the_bots_previous_message_is_not_made_up_but_one_nowhere_is():
    ok = row2(reply="SRTA100 has 40 pcs.", prev_bot="Earlier I said 40 pcs were left.", tools=[TOOL_A])
    assert made_up_facts(ok) == []
    bad = row2(reply="SRTA100 has 77 pcs.", prev_bot="Earlier I said 40 pcs were left.", tools=[TOOL_A])
    assert any("77" in f for f in made_up_facts(bad))


def test_any_truncated_envelope_makes_unverifiable_findings():
    r = row2(reply="SRTA100 has 77 pcs.", tools=[TOOL_A], truncated=True)
    found = made_up_facts(r)
    assert found and all(f.startswith("unverifiable:") for f in found)


def test_a_missing_apply_payload_falls_back_without_crashing():
    r = row2(apply_omitted=True, mem_after=[prod("SRT-A100", UA)], tools=[TOOL_A])
    check(grade_turn(r, item("code_lookup", **GOLD_CL)), True)
    assert made_up_facts(r) == []


def test_a_number_only_in_tool_results_is_neither_made_up_nor_unverifiable():
    import json

    r = row2(reply="SRTA100 has 4321 pcs.", tools=[TOOL_A], truncated=True)
    assert made_up_facts(r) and all(f.startswith("unverifiable:") for f in made_up_facts(r))   # control: no full result
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {"product_ids": [UA]},
                          "result": json.dumps({"ok": True, "items": [{"code": "SRTA100", "qty": 4321}]})}]
    assert made_up_facts(r) == []


def test_a_number_missing_from_tool_results_is_still_made_up():
    import json

    r = row2(reply="SRTA100 has 9999 pcs.", tools=[TOOL_A], truncated=True)
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {}, "result": json.dumps({"qty": 4321})}]
    assert any("9999" in f for f in made_up_facts(r))


def test_pick_falls_back_to_the_subject_after_when_no_position_is_recorded():
    v = {"entities": [], "reference_positions": [], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    gold = dict(position=2, code="srt-a200")
    ok = row2(verdict=v, mem_after=[prod("SRTA200", UA)], tools=[TOOL_A])
    check(grade_turn(ok, item("followup", "pick", **gold)), True)
    bad = row2(verdict=v, mem_after=[prod("SRTB300", UB)], tools=[TOOL_B])
    check(grade_turn(bad, item("followup", "pick", **gold)), False, "position")


def test_spec_gold_values_may_be_a_subset_of_the_bots_values():
    bot = [{"spec_key": "mounting", "spec_value": "under_counter", "raw": "undermount"},
           {"spec_key": "bowls", "spec_value": "2", "raw": "double"}]
    v = {"entities": [], "reference_positions": []}
    ok = row2(verdict=v, mem_extra={"specification": bot})
    check(grade_turn(ok, item("spec", spec=[{"key": "mounting", "value": "under_counter"}])), True)
    missing = row2(verdict=v, mem_extra={"specification": bot})
    check(grade_turn(missing, item("spec", spec=[{"key": "mounting", "value": "under_counter"},
                                                  {"key": "colour", "value": "white"}])), False, "spec")


def test_pick_and_tool_target_resolve_uuids_through_row_uuid_codes():
    v = {"entities": [], "reference_positions": [], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    gold = dict(position=2, code="srtwc8066s200new")
    tool_x = {"name": "crm_inventory_list", "args": {"product_ids": [UA]}}
    ok = row2(verdict=v, mem_after=[], tools=[tool_x])
    ok["uuid_codes"] = {UA: "SRTWC8066-S-200-NEW"}
    r = grade_turn(ok, item("followup", "pick", **gold))
    check(r, True)
    assert r["checks"].get("tool_target", True) is True
    bad = row2(verdict=v, mem_after=[], tools=[{"name": "crm_inventory_list", "args": {"product_ids": [UB]}}])
    bad["uuid_codes"] = {UA: "SRTWC8066-S-200-NEW", UB: "SRTB300"}
    check(grade_turn(bad, item("followup", "pick", **gold)), False, "position")
    cl = row2(mem_after=[], tools=[tool_x])
    cl["uuid_codes"] = {UA: "SRTA100"}
    check(grade_turn(cl, item("code_lookup", **GOLD_CL)), True)


def test_spec_ignores_class_keys_and_compares_values_as_normalised_strings():
    v = {"entities": [], "reference_positions": []}
    bot = [{"spec_key": "mounting", "spec_value": "under_counter", "raw": "x"},
           {"spec_key": "length", "spec_value": 200, "raw": "200"}]
    gold = [{"key": "class", "value": "basin"}, {"key": "product_type", "value": "sink"},
            {"key": "furniture_type", "value": "vanity"},
            {"key": "mounting", "value": "Under Counter"}, {"key": "length", "value": "200"}]
    r = grade_turn(row2(verdict=v, mem_extra={"specification": bot}), item("spec", spec=gold))
    check(r, True)
    assert "class" in r["info"], "class keys are reported as their own info entry"
    wrong_class = grade_turn(row2(verdict=v, mem_extra={"specification": bot}),
                             item("spec", spec=[{"key": "class", "value": "tap"}, {"key": "mounting", "value": "under_counter"}]))
    assert wrong_class["passed"] is True and wrong_class["info"]["class"] is False
    check(grade_turn(row2(verdict=v, mem_extra={"specification": bot}), item("spec", spec=[{"key": "length", "value": "300"}])),
          False, "spec")


def _asking(r, value):
    for rec in r["trace"]:
        if rec.get("stage") == "replied":
            rec["facts"]["asking"] = value
    return r


def test_no_clarify_fails_only_when_the_bot_asked_instead_of_answering():
    answered = _asking(row2(mem_after=[prod("SRTA100", UA)], tools=[TOOL_A]), "product_pick")
    check(grade_turn(answered, item("code_lookup", **GOLD_CL)), True)
    asked = _asking(row2(mem_after=[prod("SRTA100", UA)], tools=[]), "product_pick")
    res = grade_turn(asked, item("code_lookup", **GOLD_CL))
    assert res["passed"] is False and res["checks"]["no_clarify"] is False


def test_team_pick_never_counts_as_a_clarify():
    r = _asking(row2(mem_after=[prod("SRTA100", UA)], tools=[]), "team_pick")
    assert grade_turn(r, item("code_lookup", **GOLD_CL))["checks"]["no_clarify"] is True


def test_absent_token_is_unverifiable_when_named_tools_ran_without_recorded_results():
    ran = row2(reply="SRTA100 has 4321 pcs.", tools=[TOOL_A])
    found = made_up_facts(ran)
    assert found and all(f.startswith("unverifiable:") and "4321" in f for f in found), found


def test_absent_token_is_made_up_when_no_named_tool_ran():
    none = row2(reply="SRTA100 has 4321 pcs.", tools=[{"name": None, "args": {}}])
    found = made_up_facts(none)
    assert found and any("4321" in f and not f.startswith("unverifiable:") for f in found), found


def test_with_recorded_tool_results_an_absent_token_is_still_made_up():
    import json

    r = row2(reply="SRTA100 has 4321 pcs.", tools=[TOOL_A])
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {}, "result": json.dumps({"qty": 5})}]
    found = made_up_facts(r)
    assert found and any("4321" in f and not f.startswith("unverifiable:") for f in found), found


def test_unresolved_code_lets_a_did_you_mean_reply_pass_no_clarify():
    r = _asking(row2(message="stock of chs2072", reply="Did you mean CSH2072?", tools=[]), "product_pick")
    typo_gold = item("code_lookup", code="CSH2072", fact="inventory", code_unresolved=True)
    assert grade_turn(r, typo_gold)["checks"]["no_clarify"] is True
    resolvable = item("code_lookup", code="CSH2072", fact="inventory", code_unresolved=False)
    assert grade_turn(r, resolvable)["checks"]["no_clarify"] is False
    plain = item("code_lookup", code="CSH2072", fact="inventory")
    assert grade_turn(r, plain)["checks"]["no_clarify"] is False


DO = "PS202610-0239"


def _do_row(codes, **kw):
    return row2(message=f"status of {DO}", reply=f"{DO} is delivered.", domains=("order",),
                looked=[{"tool": "crm_orders_list", "entities": codes, "product_codes": []}],
                tools=[{"name": "crm_orders_list", "args": {"scope_terms": [DO]}}], **kw)


def test_code_lookup_accepts_a_non_product_code_found_in_looked_up_entities():
    gold = dict(code=DO, fact="order")
    check(grade_turn(_do_row([DO], mem_after=[]), item("code_lookup", **gold)), True)
    check(grade_turn(_do_row(["PS202610-9999"], mem_after=[]), item("code_lookup", **gold)), False, "code")


def test_switch_accepts_a_non_product_subject_from_looked_up_entities():
    base = dict(mem_before=[prod("SRTA100", UA)], mem_after=[])
    check(grade_turn(_do_row([DO], **base), item("followup", "switch", subject_after=[DO])), True)
    check(grade_turn(_do_row(["PS202610-9999"], **base), item("followup", "switch", subject_after=[DO])), False)


def test_and_x_new_item_accepts_a_non_product_subject_from_looked_up_entities():
    gold = dict(variant="new_item", fact="order", subject_after=[DO])
    base = dict(mem_before=[prod("SRTA100", UA)], mem_after=[])
    ok = _do_row([DO], **base)
    check(grade_turn(ok, item("followup", "and_x", **gold)), True)
    bad = _do_row(["PS202610-9999"], **base)
    check(grade_turn(bad, item("followup", "and_x", **gold)), False)


def test_old_subject_still_in_memory_does_not_fail_and_x_new_item_or_switch():
    both = dict(mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA), prod("SRTB200", UB)],
                tools=[TOOL_B], reply="SRTB200 has 5 pcs.")
    r = row2(message="and srtb200?", **both)
    res = grade_turn(r, item("followup", "and_x", variant="new_item", fact="inventory", subject_after=["SRTB200"]))
    check(res, True)
    assert "old_dropped" in res["info"], "old_dropped stays reported"
    sw = grade_turn(row2(message="stock of srtb200", **both), item("followup", "switch", subject_after=["SRTB200"]))
    check(sw, True)
    assert "old_dropped" in sw["info"]


def test_empty_or_missing_gold_subject_skips_the_subject_check():
    r = row2(mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTB200", UB)])
    for gold in ({"subject_after": []}, {}):
        res = grade_turn(r, item("followup", "switch", **gold))
        assert res["passed"] is True, res
        assert "new_subject" in res["checks"] and res["checks"]["new_subject"] is None, res["checks"]  # unknown


def test_refine_needs_a_named_tool_after_the_filter():
    v = {"entities": [{"raw": "sorento", "hint": "brand", "canonical_code": "Sorento"}], "reference_positions": []}
    gold = dict(subject_after=["SRTA100"], filter={"kind": "brand", "value": "Sorento"})
    base = dict(verdict=v, mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], mem_brands=["Sorento"])
    asked = _asking(row2(tools=[], reply="Which brand do you want?", **base), "product_pick")
    res = grade_turn(asked, item("followup", "refine", **gold))
    assert res["passed"] is False and "lookup" in res["reason"].lower(), res["reason"]
    check(grade_turn(row2(tools=[TOOL_A], **base), item("followup", "refine", **gold)), True)


def test_spec_and_filter_values_normalise_units_and_spacing():
    v = {"entities": [], "reference_positions": []}
    bot = [{"spec_key": "length", "spec_value": 300, "raw": "300"}]
    for gold_value in ("300mm", "300 mm", "300", "300MM"):
        r = row2(verdict=v, mem_extra={"specification": bot})
        check(grade_turn(r, item("spec", spec=[{"key": "length", "value": gold_value}])), True)
    check(grade_turn(row2(verdict=v, mem_extra={"specification": bot}),
                     item("spec", spec=[{"key": "length", "value": "400mm"}])), False, "spec")
    refine = row2(verdict=v, mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)],
                  mem_extra={"specification": bot}, tools=[TOOL_A])
    gold = dict(subject_after=["SRTA100"], filter={"kind": "size", "value": "300mm"})
    check(grade_turn(refine, item("followup", "refine", **gold)), True)


def _lookup_row(env):
    return row2(message=f"status of {DO}", reply=f"{DO} is delivered.", domains=("order",), mem_after=[],
                looked=[env], tools=[{"name": "crm_orders_list", "args": {"scope_terms": [DO]}}])


def test_looked_up_envelopes_that_report_a_miss_do_not_count_as_the_subject():
    gold = dict(code=DO, fact="order")
    answered = {"tool": "crm_orders_list", "outcome": "answered", "entities": [DO], "product_codes": []}
    check(grade_turn(_lookup_row(answered), item("code_lookup", **gold)), True)
    for miss in ({"outcome": "not_found"}, {"miss": ["x"]}, {"unresolved": [DO]}):
        env = {"tool": "crm_orders_list", "entities": [DO], "product_codes": [], **miss}
        check(grade_turn(_lookup_row(env), item("code_lookup", **gold)), False, "code")


def _tool_only_row(uuid):
    r = row2(message="and eta of cb88ss-diy?", reply="CB88SS-DIY arrives soon.", domains=("incoming",),
             tools=[{"name": "crm_incoming_stock_list", "args": {"product_ids": [uuid]}}])
    r["trace"] = [e for e in r["trace"] if e.get("kind") != "memory"]       # no memory event at all
    r["uuid_codes"] = {UA: "CB88SS-DIY", UB: "SRTB200"}
    return r


def test_subject_after_includes_the_tool_targets_resolved_through_uuid_codes():
    gold = dict(variant="new_item", fact="incoming", subject_after=["cb88ss-diy"])
    check(grade_turn(_tool_only_row(UA), item("followup", "and_x", **gold)), True)
    check(grade_turn(_tool_only_row(UB), item("followup", "and_x", **gold)), False)
    sw = dict(subject_after=["cb88ss-diy"])
    check(grade_turn(_tool_only_row(UA), item("followup", "switch", **sw)), True)
    check(grade_turn(_tool_only_row(UB), item("followup", "switch", **sw)), False)


def test_pick_followed_by_a_question_passes_with_tool_target_unknown():
    gold = dict(position=2, code="SRTA200")
    v = {"entities": [], "reference_positions": [2], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    asked = _asking(row2(verdict=v, mem_after=[prod("SRTA200", UA)], tools=[], reply="Which period?"), "period_pick")
    res = grade_turn(asked, item("followup", "pick", **gold))
    check(res, True)
    assert "tool_target" in res["checks"] and res["checks"]["tool_target"] is None
    v3 = dict(v, reference_positions=[3])
    wrong_pos = _asking(row2(verdict=v3, mem_after=[prod("SRTA200", UA)], tools=[], reply="Which period?"), "period_pick")
    check(grade_turn(wrong_pos, item("followup", "pick", **gold)), False, "position")
    other_tool = row2(verdict=v, mem_after=[prod("SRTA200", UA)], tools=[TOOL_B], reply="SRTB200 has 5 pcs.")
    other_tool["uuid_codes"] = {UB: "SRTB200"}
    res2 = grade_turn(other_tool, item("followup", "pick", **gold))
    assert res2["passed"] is False and res2["checks"]["tool_target"] is False


# ------------------------------------------------------------------ review round 1 (grader)
def test_state_subject_is_used_only_when_memory_has_no_after_focus():
    # memory.after exists and is EMPTY (the bot dropped the subject); the subject lives only in the state event
    base = dict(state_products=[prod("SRTA100", UA)], mem_before=[prod("SRTA100", UA)], mem_after=[], tools=[TOOL_A])
    and_x = row2(message="and the price?", **base)
    check(grade_turn(and_x, item("followup", "and_x", variant="new_fact", fact="inventory", subject_after=["SRTA100"])),
          False, "subject")
    v = {"entities": [{"raw": "sorento", "hint": "brand", "canonical_code": "Sorento"}], "reference_positions": []}
    refine = row2(verdict=v, mem_brands=["Sorento"], **base)
    check(grade_turn(refine, item("followup", "refine", subject_after=["SRTA100"],
                                  filter={"kind": "brand", "value": "Sorento"})), False, "subject")
    pv = {"entities": [], "reference_positions": [], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    pick = _asking(row2(verdict=pv, state_products=[prod("SRTA200", UA)], mem_after=[], tools=[]), "period_pick")
    check(grade_turn(pick, item("followup", "pick", position=2, code="SRTA200")), False, "position")


def test_a_non_product_switch_still_passes_when_memory_after_is_empty():
    r = _do_row([DO], mem_before=[prod("SRTA100", UA)], mem_after=[], state_products=[prod("SRTA100", UA)])
    check(grade_turn(r, item("followup", "switch", subject_after=[DO])), True)


def test_answer_needs_a_real_answer_not_a_cancel_or_an_arm_that_clears_the_question():
    q = {"question": "which colour", "options": ["white", "black"]}
    cancel = {"entities": [], "reference_positions": [],
              "open_question_answer": {"mode": "cancel", "items": [], "picked": []}}
    r = row2(verdict=cancel, state_oq=q, oq={"before": q, "after": None, "writer": "engine"})
    res = grade_turn(r, item("followup", "answer", option="white"))
    assert res["passed"] is False and "answer" in res["reason"].lower()
    null = {"entities": [], "reference_positions": [],
            "open_question_answer": {"mode": None, "items": [], "picked": []}}
    smalltalk = row2(verdict=null, state_oq=q, oq={"before": q, "after": None, "writer": "casual"}, reply="Hello there!")
    res2 = grade_turn(smalltalk, item("followup", "answer", option="white"))
    assert res2["passed"] is False and "answer" in res2["reason"].lower()


def test_made_up_with_an_omitted_apply_payload_is_unverifiable_unless_results_were_recorded():
    import json

    r = row2(apply_omitted=True, reply="SRTA100 has 4321 pcs.", tools=[])
    found = made_up_facts(r)
    assert found and all(f.startswith("unverifiable:") for f in found), found
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {}, "result": json.dumps({"qty": 5})}]
    assert any("4321" in f and not f.startswith("unverifiable:") for f in made_up_facts(r))


def test_a_turn_whose_checks_are_all_unknown_is_passed_none():
    r = row2(verdict={"entities": [], "reference_positions": []})
    res = grade_turn(r, item("spec", spec=[{"key": "class", "value": "basin"}]))
    assert res["passed"] is None
    assert res["reason"] == "The bench could not confirm what the bot understood."
    assert all(v is None for v in res["checks"].values())


def test_summarise_counts_unknowns_apart_and_keeps_them_out_of_the_rate():
    from scripts.chatbot_eval.targets import summarise

    def res(passed):
        return {"item_id": "x", "slice": "spec", "kind": None, "passed": passed, "reason": "r", "checks": {},
                "leaks": [], "made_up": []}

    runs = [[res(True)] * 8 + [res(None)] * 2 for _ in range(3)]
    s = summarise(runs, {"spec": 0.9})
    assert s["slices"]["spec"]["rate"] == 1.0 and s["slices"]["spec"]["unknown"] == 2


def test_the_named_check_matches_codes_on_word_boundaries():
    base = dict(mem_after=[prod("SRT10", UA)], tools=[TOOL_A])
    gold = dict(code="srt10", fact="inventory")
    ok = grade_turn(row2(reply="SRT10 has 40 pcs.", **base), item("code_lookup", **gold))
    assert ok["checks"]["named"] is True
    longer = grade_turn(row2(reply="SRT105 has 40 pcs.", **base), item("code_lookup", **gold))
    assert longer["checks"]["named"] is False


# ------------------------------------------------------------------ review round 2 (grader)
_NULL_V = {"entities": [], "reference_positions": [], "open_question_answer": {"mode": None, "items": [], "picked": []}}


def _oq_row(before, after, picked=()):
    v = {"entities": [], "reference_positions": [],
         "open_question_answer": {"mode": None, "items": [], "picked": list(picked)}}
    return row2(verdict=v, state_oq=before, oq={"before": before, "after": after, "writer": "engine"})


def _colour_q(turn):
    return {"kind": "product_pick", "expects": "choice", "team": None, "asked_at_turn": turn, "payload": {},
            "options": [{"label": "white"}, {"label": "black"}]}


def test_a_question_differing_only_in_asked_at_turn_is_a_reask():
    r = _oq_row(_colour_q(1), _colour_q(2), picked=[1])
    res = grade_turn(r, item("followup", "answer", option="white"))
    assert res["checks"]["not_reasked"] is False and res["passed"] is False, res


def test_a_hand_over_offer_replacing_the_question_is_not_an_answer():
    offer = {"kind": "team_pick", "expects": "choice", "team": None, "asked_at_turn": 2, "payload": {},
             "options": [{"label": "Sales"}, {"label": "Support"}]}
    res = grade_turn(_oq_row(_colour_q(1), offer), item("followup", "answer", option="white"))
    assert res["checks"]["answered"] is False and res["passed"] is False, res


def test_a_genuinely_new_next_question_still_counts_as_answered():
    nxt = {"kind": "period_pick", "expects": "choice", "team": None, "asked_at_turn": 2, "payload": {},
           "options": [{"label": "this month"}, {"label": "last month"}]}
    res = grade_turn(_oq_row(_colour_q(1), nxt), item("followup", "answer", option="white"))
    assert res["checks"]["not_reasked"] is True and res["checks"]["answered"] is True, res


def test_made_up_reads_real_newlines_in_a_string_tool_result():
    r = row2(reply="Next arrival is 6 Sep 2026 with 1,200 pcs.", tools=[TOOL_A])
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {}, "result": "ETA\n6 Sep 2026\n1,200 pcs"}]
    assert made_up_facts(r) == []


def test_made_up_walks_dict_results_string_by_string():
    r = row2(reply="Next arrival is 6 Sep 2026 with 1,200 pcs.", tools=[TOOL_A])
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {},
                          "result": {"items": [{"note": "ETA\n6 Sep 2026\n1,200 pcs"}]}}]
    assert made_up_facts(r) == []


def _after_none(r):
    for rec in r["trace"]:
        if rec.get("kind") == "memory":
            rec["focus"]["after"] = None
    return r


def test_memory_focus_after_none_is_subject_dropped_and_a_refine_without_a_tool_fails():
    v = {"entities": [{"raw": "sorento", "hint": "brand", "canonical_code": "Sorento"}], "reference_positions": []}
    gold = dict(subject_after=["SRTA100"], filter={"kind": "brand", "value": "Sorento"})
    r = _after_none(row2(verdict=v, state_products=[prod("SRTA100", UA)], mem_before=[prod("SRTA100", UA)], tools=[]))
    res = grade_turn(r, item("followup", "refine", **gold))
    assert res["checks"].get("lookup") is False and res["passed"] is False, res
    assert res["checks"]["subject"] is False, "the pre-turn state stood in for a dropped subject"


def test_pick_tool_target_is_unknown_only_when_the_bot_asked_its_next_question():
    gold = dict(position=2, code="SRTA200")
    v = dict(_NULL_V, reference_positions=[2])
    err = row2(verdict=v, mem_after=[prod("SRTA200", UA)], tools=[], reply="Sorry, something went wrong.")
    res = grade_turn(err, item("followup", "pick", **gold))
    assert res["checks"]["tool_target"] is False and res["passed"] is False, res
    asked = _asking(row2(verdict=v, mem_after=[prod("SRTA200", UA)], tools=[], reply="Which period?"), "period_pick")
    assert grade_turn(asked, item("followup", "pick", **gold))["checks"]["tool_target"] is None


def test_tool_ran_matches_codes_on_word_boundaries():
    r = row2(reply="SRT10 has 40 pcs.", mem_after=[prod("SRT10", UA)],
             tools=[{"name": "crm_inventory_list", "args": {"scope_terms": ["SRT105"]}}])
    res = grade_turn(r, item("code_lookup", code="srt10", fact="inventory"))
    assert res["checks"]["tool_ran"] is False, res


def test_pick_tool_target_matches_codes_on_word_boundaries():
    v = dict(_NULL_V, reference_positions=[2])
    r = row2(verdict=v, mem_after=[prod("SRTA20", UA)],
             tools=[{"name": "crm_inventory_list", "args": {"scope_terms": ["SRTA200"]}}], reply="SRTA200 has 5 pcs.")
    res = grade_turn(r, item("followup", "pick", position=2, code="SRTA20"))
    assert res["checks"]["tool_target"] is False and res["passed"] is False, res


# ------------------------------------------------------------------ review round 5 (summarise)
def _r5(slice_, kind, passed, made_up=(), growth=False):
    x = {"item_id": "x", "slice": slice_, "kind": kind, "passed": passed, "reason": "r", "checks": {},
         "leaks": [], "made_up": list(made_up)}
    if growth:
        x["growth"] = True
    return x


def test_summarise_unverifiable_findings_are_counted_apart_and_never_block_meets():
    from scripts.chatbot_eval.targets import summarise
    runs = [[_r5("code_lookup", None, True, made_up=["unverifiable: truncated envelope"]) for _ in range(10)]
            for _ in range(3)]
    s = summarise(runs, {"code_lookup": 0.97})
    assert s["made_up"] == 0
    assert s["unverifiable"] == 30
    assert s["meets"] is True


def test_summarise_counts_only_the_made_up_entries_among_unverifiable_ones():
    from scripts.chatbot_eval.targets import summarise
    runs = [[_r5("code_lookup", None, True) for _ in range(10)] for _ in range(3)]
    runs[1][0] = _r5("code_lookup", None, True,
                     made_up=["made up: 4321", "unverifiable: a", "unverifiable: b"])
    s = summarise(runs, {"code_lookup": 0.97})
    assert s["made_up"] == 1
    assert s["unverifiable"] == 2
    assert s["meets"] is False


def test_summarise_slice_missing_from_one_run_is_left_out_of_rate_and_lowest():
    from scripts.chatbot_eval.targets import summarise
    run1 = [_r5("spec", None, True)] * 4 + [_r5("spec", None, False)] * 1   # 0.8
    run3 = [_r5("spec", None, True)] * 3 + [_r5("spec", None, False)] * 1   # 0.75
    run2 = [_r5("code_lookup", None, True)]                                 # no spec at all
    s = summarise([run1, run2, run3], {})
    assert s["slices"]["spec"]["rate"] == (0.8 + 0.75) / 2
    assert s["slices"]["spec"]["lowest"] == 0.75


def test_summarise_followup_kind_missing_from_one_run_is_left_out_of_rate_and_lowest():
    from scripts.chatbot_eval.targets import summarise
    run1 = [_r5("followup", "answer", True)] * 2 + [_r5("followup", "reject", True)]
    run2 = [_r5("followup", "reject", True)]                                # no "answer" kind
    run3 = [_r5("followup", "answer", True), _r5("followup", "answer", False), _r5("followup", "reject", True)]
    s = summarise([run1, run2, run3], {})
    k = s["slices"]["followup"]["kinds"]["answer"]
    assert k["rate"] == (1.0 + 0.5) / 2
    assert k["lowest"] == 0.5


def test_summarise_growth_kind_missing_from_one_run_is_left_out_of_rate_and_lowest():
    from scripts.chatbot_eval.targets import summarise
    g = lambda p: _r5("followup", "answer", p, growth=True)  # noqa: E731
    s = summarise([[g(True)], [_r5("spec", None, True)], [g(True), g(False)]], {})
    k = s["growth"]["followup"]["kinds"]["answer"]
    assert k["rate"] == (1.0 + 0.5) / 2
    assert k["lowest"] == 0.5


def test_summarise_run_with_only_unknown_results_is_left_out_of_rate_and_lowest():
    from scripts.chatbot_eval.targets import summarise
    run1 = [_r5("spec", None, True)] * 2
    run2 = [_r5("spec", None, None)] * 3
    run3 = [_r5("spec", None, True), _r5("spec", None, False)]
    s = summarise([run1, run2, run3], {})
    assert s["slices"]["spec"]["rate"] == (1.0 + 0.5) / 2
    assert s["slices"]["spec"]["lowest"] == 0.5


def test_summarise_slice_with_no_known_result_has_zero_rate_and_lowest_and_misses_its_bar():
    from scripts.chatbot_eval.targets import summarise
    runs = [[_r5("spec", None, None)] * 3 for _ in range(3)]
    s = summarise(runs, {"spec": 0.9})
    assert s["slices"]["spec"]["rate"] == 0.0
    assert s["slices"]["spec"]["lowest"] == 0.0
    assert s["meets"] is False


# ------------------------------------------------------------------ review round 6 (meets needs every run)
def test_summarise_one_known_run_of_three_does_not_meet_the_bar_and_reports_runs_1():
    from scripts.chatbot_eval.targets import summarise
    s = summarise([[_r5("spec", None, True)], [_r5("spec", None, None)], [_r5("spec", None, None)]], {"spec": 0.9})
    assert s["slices"]["spec"]["runs"] == 1
    assert s["meets"] is False


def test_summarise_every_run_known_reports_runs_3_and_meets_as_before():
    from scripts.chatbot_eval.targets import summarise
    s = summarise([[_r5("spec", None, True)] for _ in range(3)], {"spec": 0.9})
    assert s["slices"]["spec"]["runs"] == 3
    assert s["meets"] is True


# ------------------------------------------------------------------ review round 11 (picker option codes are sources)
def _picker(codes):
    return {"kind": "product_pick", "expects": "choice", "payload": {},
            "options": [{"code": c, "label": f"{c} synthetic label"} for c in codes]}


PICK_CODES = ["SRTPICKA1", "SRTPICKB2", "SRTPICKC3"]
PICK_REPLY = "Did you mean SRTPICKA1, SRTPICKB2 or SRTPICKC3?"


def test_option_codes_only_in_the_memory_open_question_after_are_not_made_up():
    q = _picker(PICK_CODES)
    r = row2(reply=PICK_REPLY, oq={"before": None, "after": q, "writer": "engine"})
    assert made_up_facts(r) == []


def test_option_codes_only_in_the_memory_open_question_before_are_not_made_up():
    q = _picker(PICK_CODES)
    r = row2(reply=PICK_REPLY, oq={"before": q, "after": None, "writer": "engine"})
    assert made_up_facts(r) == []


def test_option_codes_only_in_the_state_open_question_are_not_made_up():
    r = row2(reply=PICK_REPLY, state_oq=_picker(PICK_CODES))
    assert made_up_facts(r) == []


def test_a_code_in_no_source_and_no_option_is_still_made_up():
    r = row2(reply="Did you mean SRTPICKA1 or SRTNOWHERE9?", state_oq=_picker(["SRTPICKA1"]))
    found = made_up_facts(r)
    assert found == ["made up: SRTNOWHERE9"], found


# ------------------------------------------------------------------ review round 12 (grader defects from the TRAIN root cause)
# CHAT-STATE claims checked against the code: the `state` trace event is added at engine.py:7814 and :10200 from
# `session_payload(state, answer, ...)`, i.e. the question AFTER the turn (so the `_oq` fallback is real); the 32 KB
# cap writes {"note": "payload omitted: ..."} (trace.py:99); `option` compares gold text to labels (targets.py ~491).
def _picks(picked, mode=None):
    return {"entities": [], "reference_positions": [],
            "open_question_answer": {"mode": mode, "items": [], "picked": list(picked)}}


YES_Q = {"kind": "confirm", "expects": "choice", "payload": {}, "options": [{"label": "Yes"}]}
COLOUR_Q = {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 1,
            "options": [{"label": "white", "code": "SRTCOL1"}, {"label": "black", "code": "SRTCOL2"},
                        {"label": "grey", "code": "SRTCOL3"}]}


# 1. the question before the turn never comes from the post-turn state record
def test_an_unknown_before_question_makes_not_reasked_unknown_not_a_reask():
    r = row2(verdict=_picks([], mode="yes"), state_oq=YES_Q, oq={"before": None, "after": YES_Q, "writer": "engine"})
    res = grade_turn(r, item("followup", "answer", option="yes"))
    assert res["checks"]["not_reasked"] is None, res["checks"]
    assert res["passed"] is not False, res


def test_a_real_reask_with_a_known_before_question_still_fails():
    r = row2(verdict=_picks([1]), state_oq=COLOUR_Q, oq={"before": COLOUR_Q, "after": COLOUR_Q, "writer": "engine"})
    res = grade_turn(r, item("followup", "answer", option="white"))
    assert res["checks"]["not_reasked"] is False and res["passed"] is False, res


# 2. a numeric gold option is the 1-based position in the before question's options
def _numeric(gold_option, picked):
    r = row2(verdict=_picks(picked), state_oq=COLOUR_Q, oq={"before": COLOUR_Q, "after": None, "writer": "engine"})
    return grade_turn(r, item("followup", "answer", option=gold_option))


def test_a_numeric_gold_option_maps_to_the_option_at_that_position():
    assert _numeric("3", [3])["checks"]["option"] is True
    assert _numeric("3.", [3])["checks"]["option"] is True


def test_a_wrong_numeric_position_still_fails_and_a_label_gold_still_works():
    assert _numeric("2", [3])["checks"]["option"] is False
    assert _numeric("grey", [3])["checks"]["option"] is True


# 3a. a boolean spec_value is matched through the entity's raw words
def test_a_boolean_spec_value_matches_the_gold_filter_through_the_raw_phrase():
    v = {"entities": [{"raw": "intelligent toilet", "hint": "specification", "spec_key": "is_smart", "spec_value": True}],
         "reference_positions": []}
    r = row2(verdict=v, mem_after=[prod("SRTA100", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"],
                             filter={"kind": "spec", "value": "intelligent toilet"}))
    assert res["checks"]["filter"] is True, res["checks"]


def test_a_filter_phrase_in_no_entity_still_fails():
    v = {"entities": [{"raw": "round basin", "hint": "specification", "spec_key": "is_smart", "spec_value": True}],
         "reference_positions": []}
    r = row2(verdict=v, mem_after=[prod("SRTA100", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"],
                             filter={"kind": "spec", "value": "intelligent toilet"}))
    assert res["checks"]["filter"] is False


# 3b. omitted apply and memory payloads make the filter check unknown
def test_the_filter_check_is_unknown_when_the_apply_and_memory_payloads_were_omitted():
    r = row2(apply_omitted=True, tools=[TOOL_A])
    r["trace"] = [e for e in r["trace"] if e.get("kind") != "memory"]
    res = grade_turn(r, item("followup", "refine", filter={"kind": "spec", "value": "intelligent toilet"}))
    assert res["checks"]["filter"] is None, res["checks"]
    assert res["passed"] is not False, res


# 4. a typed code the resolver expanded satisfies the new subject
def _and_x(literal, after_codes, after_q=None, kind="and_x"):
    r = row2(mem_after=[prod(c, f"00000000-0000-4000-8000-{k:012d}") for k, c in enumerate(after_codes)],
             oq={"before": None, "after": after_q, "writer": "engine"}, tools=[TOOL_A])
    return grade_turn(r, item("followup", kind, fact="inventory", subject_after=[literal], code_unresolved=True))


def test_codes_that_start_with_the_typed_literal_satisfy_the_new_subject():
    res = _and_x("CBCH80029", ["CBCH80029B", "CBCH80029M"])
    assert res["checks"]["new_subject"] is True, res["checks"]
    assert _and_x("cbch-80029", ["CBCH80029B"])["checks"]["new_subject"] is True


def test_a_code_that_does_not_share_the_literal_still_fails_the_new_subject():
    res = _and_x("CBCH80029", ["XYZ999"])
    assert res["checks"]["new_subject"] is False, res["checks"]


def test_a_which_one_question_whose_options_all_start_with_the_literal_satisfies_the_new_subject():
    q = {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
         "options": [{"label": "SRTWC286A", "code": "SRTWC286A"}, {"label": "SRTWC286B", "code": "SRTWC286B"}]}
    assert _and_x("srtwc286", [], after_q=q)["checks"]["new_subject"] is True
    mixed = dict(q, options=q["options"] + [{"label": "OTHER1", "code": "OTHER1"}])
    assert _and_x("srtwc286", [], after_q=mixed)["checks"]["new_subject"] is False


def test_a_switch_to_an_unresolved_code_that_expands_to_the_held_family_is_a_new_subject_and_a_change():
    held = [prod("SRT6538-GM-DIY", UA)]
    r = row2(mem_before=held, mem_after=held, state_products=held, tools=[TOOL_A])
    res = grade_turn(r, item("followup", "switch", subject_after=["SRT6538-GM"], code_unresolved=True))
    assert res["checks"]["new_subject"] is True and res["checks"]["changed"] is True, res["checks"]


# ------------------------------------------------------------------ review round 12b (subject before)
# `before_subject` is computed in `_followup` but no check reads it today; the leak is in `_subject_before` itself.
def test_the_subject_before_never_comes_from_the_post_turn_state_record():
    from scripts.chatbot_eval.targets import _subject_before

    r = row2(mem_before=[], state_products=[prod("SRTPOST1", UA)])
    assert _subject_before(r) == set(), "the post-turn state record stood in for the unknown subject before"


def test_a_known_memory_before_still_gives_the_subject_before():
    from scripts.chatbot_eval.targets import _subject_before

    r = row2(mem_before=[prod("SRTBEFORE1", UA)], state_products=[prod("SRTPOST1", UB)])
    assert _subject_before(r) == {"SRTBEFORE1"}


# ------------------------------------------------------------------ review round 15 (attachment: honest none on record)
ATT_TOOL = "crm_master_product_attachments_list"


def _live_att(result_files, *, tool=ATT_TOOL, reply="No technical specifications are on record for this product.",
              sent=0):
    r = _att([], reply=reply, sent=sent)
    for rec in r["trace"]:
        if rec.get("kind") == "tool":
            rec["name"] = tool
    r["tool_results"] = [{"name": tool, "args": {"product_ids": [UA]}, "result": {"attachments": result_files}}]
    return r


def test_an_attachment_tool_result_with_no_file_of_the_type_and_nothing_sent_is_an_honest_none():
    r = _live_att([_file("Drawing")])
    r["uuid_codes"] = {UA: "SRTA100"}   # round 17: the searched product must be the item's product
    res = grade_turn(r, item("attachment", code="SRTA100", **GOLD_TS))
    assert res["checks"]["none_on_record"] is True and "files" not in res["checks"] and res["checks"].get("type") is None, res["checks"]
    check(res, True)


def test_a_result_that_has_the_type_but_nothing_sent_still_fails_files():
    res = grade_turn(_live_att([_file("Technical Specifications")]), item("attachment", **GOLD_TS))
    assert res["checks"]["files"] is False and res["passed"] is False, res["checks"]


def test_no_attachment_tool_run_still_fails_files():
    res = grade_turn(_live_att([_file("Drawing")], tool="crm_inventory_list"), item("attachment", **GOLD_TS))
    assert res["checks"]["files"] is False and res["passed"] is False, res["checks"]


def test_a_reply_claiming_attachment_with_nothing_sent_still_fails_files():
    res = grade_turn(_live_att([_file("Drawing")], reply="I have attached the file(s) below."), item("attachment", **GOLD_TS))
    assert res["checks"]["files"] is False and res["passed"] is False, res["checks"]


# ------------------------------------------------------------------ review round 16
def _switch(gold_code, held, **gold):
    r = row2(mem_before=[prod("SRTOLD1", UB)], mem_after=[prod(held, UA)], tools=[TOOL_A])
    return grade_turn(r, item("followup", "switch", subject_after=[gold_code], **gold))


def test_a_longer_held_code_does_not_satisfy_an_exact_gold_subject():
    res = _switch("SRTWC8605", "SRTWC8605-SC")
    assert res["checks"]["new_subject"] is False, res["checks"]


def test_the_same_longer_code_satisfies_a_gold_subject_flagged_unresolved():
    res = _switch("SRTWC8605", "SRTWC8605-SC", code_unresolved=True)
    assert res["checks"]["new_subject"] is True, res["checks"]


def test_picker_options_satisfy_the_subject_only_when_the_gold_code_is_unresolved():
    q = {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
         "options": [{"label": "SRTWC286A", "code": "SRTWC286A"}, {"label": "SRTWC286B", "code": "SRTWC286B"}]}
    r = row2(oq={"before": None, "after": q, "writer": "engine"}, tools=[TOOL_A])
    exact = grade_turn(r, item("followup", "and_x", fact="inventory", subject_after=["SRTWC286"]))
    assert exact["checks"]["new_subject"] is False, exact["checks"]


@pytest.mark.parametrize("spec_value", ["something else", 7, "no"])
def test_a_wrong_non_boolean_spec_value_does_not_match_the_gold_filter_through_raw(spec_value):
    v = {"entities": [{"raw": "intelligent toilet", "hint": "specification", "spec_key": "is_smart",
                       "spec_value": spec_value}], "reference_positions": []}
    r = row2(verdict=v, mem_after=[prod("SRTA100", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"],
                             filter={"kind": "spec", "value": "intelligent toilet"}))
    assert res["checks"]["filter"] is False, res["checks"]


@pytest.mark.parametrize("spec_value", [True, False, None])
def test_a_boolean_or_empty_spec_value_still_matches_through_raw(spec_value):
    v = {"entities": [{"raw": "intelligent toilet", "hint": "specification", "spec_key": "is_smart",
                       "spec_value": spec_value}], "reference_positions": []}
    r = row2(verdict=v, mem_after=[prod("SRTA100", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"],
                             filter={"kind": "spec", "value": "intelligent toilet"}))
    assert res["checks"]["filter"] is True, res["checks"]


def test_an_old_shape_memory_question_never_takes_the_post_turn_state_as_before():
    from scripts.chatbot_eval.targets import _oq

    after = {"kind": "product_pick", "expects": "choice", "payload": {}, "options": [{"label": "white"}]}
    post = {"kind": "confirm", "expects": "choice", "payload": {}, "options": [{"label": "AFTERONLY"}]}
    r = row2(state_oq=post, oq=after)   # the bare after value, no before/after keys: the older row shape
    assert _oq(r) == (None, after)


def test_a_gold_option_that_is_literally_a_label_wins_over_the_position_reading():
    q = {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 1,
         "options": [{"label": "1"}, {"label": "3"}, {"label": "5"}]}
    r = row2(verdict=_picks([2]), state_oq=q, oq={"before": q, "after": None, "writer": "engine"})
    res = grade_turn(r, item("followup", "answer", option="3"))
    assert res["checks"]["option"] is True, res["checks"]


# ------------------------------------------------------------------ review round 17: grade outcomes
# 1. attachment type names match by stem and plural ("Product Videos" satisfies a video gold)
GOLD_VIDEO = {"attachment_type": "video", "attachment_type_name": "Video"}


def test_product_videos_files_satisfy_a_gold_asking_for_video():
    res = grade_turn(_att([_file("Product Videos")], asked="videos"), item("attachment", **GOLD_VIDEO))
    assert res["checks"]["asked_type"] is True and res["checks"]["type"] is True, res["checks"]
    check(res, True)


def test_photo_and_photos_and_drawing_and_drawings_match_by_stem():
    gold = {"attachment_type": "photos", "attachment_type_name": "Photos"}
    assert grade_turn(_att([_file("Photo")], asked="photo"), item("attachment", **gold))["checks"]["type"] is True
    gold = {"attachment_type": "drawing", "attachment_type_name": "Drawing"}
    assert grade_turn(_att([_file("Drawings")], asked="drawings"), item("attachment", **gold))["checks"]["type"] is True


def test_a_different_type_does_not_match_by_stem():
    res = grade_turn(_att([_file("Drawing")], asked="videos"), item("attachment", **GOLD_VIDEO))
    assert res["checks"]["type"] is False and res["passed"] is False


# 2. spec: the reply must show at least one product
SPEC_V = {"entities": [{"raw": "white", "hint": "specification", "spec_key": "colour", "spec_value": "white"}],
          "reference_positions": []}
SPEC_ITEM = dict(spec=[{"key": "colour", "value": "white"}])


def test_a_spec_turn_that_says_it_could_not_find_anything_fails_even_if_the_words_were_read():
    r = row2(verdict=SPEC_V, reply='Couldn\'t find: "kitchan tap" (product). Would you like me to escalate?')
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["passed"] is False, res["checks"]


def test_a_spec_turn_that_lists_a_product_still_passes():
    r = row2(verdict=SPEC_V, reply="Here are white taps: SRTKT100 and SRTKT200.")
    check(grade_turn(r, item("spec", **SPEC_ITEM)), True)


# 3. refine: the filter must be applied (lookup carries it) and the answer must change
REFINE_V = {"entities": [{"raw": "chrome", "hint": "specification", "spec_key": "finish", "spec_value": "chrome"}],
            "reference_positions": []}
LIST_REPLY = "Here are SRTA100 and SRTA200."


def test_a_refine_answered_with_the_same_list_fails_even_when_the_filter_was_read():
    r = row2(verdict=REFINE_V, mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], prev_bot=LIST_REPLY, reply=LIST_REPLY)
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "chrome"}))
    assert res["passed"] is False, res["checks"]


def test_a_refine_whose_lookup_carries_the_filter_and_whose_answer_differs_passes():
    tool = {"name": "crm_inventory_list", "args": {"product_ids": [UA], "scope_terms": ["chrome"]}}
    r = row2(verdict=REFINE_V, mem_after=[prod("SRTA100", UA)], tools=[tool], prev_bot=LIST_REPLY,
             reply="Chrome ones: SRTA100 in chrome.")
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "chrome"}))
    assert res["passed"] is True, res["checks"]


# 4. a needless clarify menu fails a code lookup or spec item (clarify only product vs customer)
def _menu(entity_types):
    """The engine's real kind menu (turn/reconcile.py): kind `kind_pick`, labels "Raw (word)", an entity_type each."""
    return {"kind": "kind_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
            "options": [{"position": i + 1, "label": f"Sorento ({t})", "raw": "Sorento", "code": "Sorento", "uuid": None,
                         "uuids": [], "entity_type": t, "payload": {"kind": t}} for i, t in enumerate(entity_types)]}


def test_a_product_vs_attachment_menu_on_a_spec_item_fails():
    r = row2(verdict=SPEC_V, reply="Here are white taps: SRTKT100.", oq={"before": None, "after": _menu(["product", "attachment"]),
                                                                       "writer": "engine"})
    r["branch_kind"] = "clarify_menu"
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["passed"] is False, res["checks"]


def test_a_product_vs_attachment_menu_on_a_code_lookup_item_fails():
    r = row2(mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], oq={"before": None, "after": _menu(["product", "attachment"]),
                                                                 "writer": "engine"})
    r["branch_kind"] = "clarify_menu"
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["passed"] is False, res["checks"]


def test_a_product_vs_customer_kind_pick_menu_is_allowed():
    r = row2(verdict=SPEC_V, reply="Here are white taps: SRTKT100.", oq={"before": None, "after": _menu(["product", "customer"]),
                                                                       "writer": "engine"})
    r["branch_kind"] = "clarify_menu"
    check(grade_turn(r, item("spec", **SPEC_ITEM)), True)


# 5. honest none needs the attachment tool to have been asked about the item's product
def test_an_honest_none_with_no_product_in_the_tool_args_fails():
    r = _live_att([_file("Drawing")])
    r["tool_results"][0]["args"] = {}
    res = grade_turn(r, item("attachment", code="SRTA100", **GOLD_TS))
    assert res["checks"]["files"] is False and res["passed"] is False, res["checks"]


def test_an_honest_none_for_a_product_that_is_not_the_items_fails():
    r = _live_att([_file("Drawing")])
    r["uuid_codes"] = {UA: "SRTOTHER9"}
    res = grade_turn(r, item("attachment", code="SRTA100", **GOLD_TS))
    assert res["checks"]["files"] is False and res["passed"] is False, res["checks"]


# 6. an asked attribute needs a value in the reply (gold carries `fact_label`, e.g. "weight")
def test_a_code_lookup_for_an_attribute_fails_when_the_reply_has_no_value_for_it():
    r = row2(mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], reply="SRTA100 has 40 pcs.")
    res = grade_turn(r, item("code_lookup", code="srta100", fact="inventory", fact_label="weight"))
    assert res["passed"] is False, res["checks"]


def test_a_code_lookup_for_an_attribute_passes_when_the_reply_states_it():
    r = row2(mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], reply="SRTA100 weight: 12.5 kg.")
    check(grade_turn(r, item("code_lookup", code="srta100", fact="inventory", fact_label="weight")), True)


# 7. an unlinked non-staff contact must not see another company's delivery order fields
DO_RESULT = {"deliveryOrders": [{"doNumber": "DO-SYN-1", "customer": "Synthetic Trading Sdn Bhd", "driver": "Ali Synthetic",
                                 "lorry_plate": "PLATE-1"}]}
DO_REPLY = "DO-SYN-1 for Synthetic Trading Sdn Bhd, driver Ali Synthetic, lorry PLATE-1."


def _do_leak_row():
    r = row2(reply=DO_REPLY)
    r["tool_results"] = [{"name": "crm_delivery_orders_list", "args": {}, "result": DO_RESULT}]
    return r


def test_an_unlinked_dealer_shown_another_companys_delivery_order_fields_leaks():
    asker = {"audience": "unlinked", "linked_customer_ids": [], "linked_customer_names": [], "hidden_fields": []}
    leaks = grade_turn(_do_leak_row(), dict(item("scope"), asker=asker))["leaks"]
    assert leaks, "customer, driver and plate of another company were shown to an unlinked contact"


def test_staff_shown_the_same_delivery_order_has_no_leak():
    asker = {"audience": "staff", "linked_customer_ids": None, "linked_customer_names": [], "hidden_fields": []}
    assert grade_turn(_do_leak_row(), dict(item("scope"), asker=asker))["leaks"] == []


def test_a_dealer_linked_to_that_company_has_no_leak_on_the_company_name():
    asker = {"audience": "dealer", "linked_customer_ids": ["c1"],
             "linked_customer_names": ["Synthetic Trading Sdn Bhd"], "hidden_fields": []}
    leaks = grade_turn(_do_leak_row(), dict(item("scope"), asker=asker))["leaks"]
    assert not any("Synthetic Trading" in x for x in leaks), leaks


# 8. escalation kind: the turn must route to a person
def test_an_escalation_item_passes_when_the_turn_routes_to_a_person():
    r = row2(reply="I will connect you to our sales team.")
    r["branch_kind"] = "escalation"
    for rec in r["trace"]:
        if rec.get("stage") == "routed":
            rec["facts"]["lane"] = "escalation"
    check(grade_turn(r, item("followup", "escalation", route=True)), True)


def test_an_escalation_item_fails_when_the_bot_sends_files_or_answers_instead():
    r = row2(reply="Here are the files.", files=2)
    res = grade_turn(r, item("followup", "escalation", route=True))
    assert res["passed"] is False


# ------------------------------------------------------------------ round 18
# 2. a hand-over offered as a team-pick question after the turn counts as routed
def test_an_escalation_item_passes_when_the_handover_is_offered_as_a_team_pick_question():
    offer = {"kind": "team_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
             "options": [{"label": "Sales"}, {"label": "Support"}]}
    r = row2(reply="Which team would you like?", oq={"before": None, "after": offer, "writer": "engine"})
    check(grade_turn(r, item("followup", "escalation", route=True)), True)


# 3. only unlinked askers are checked for delivery order fields; company names compare without suffixes
@pytest.mark.parametrize("audience", ["linked_dealer", "staff"])
def test_askers_that_are_not_unlinked_do_not_leak_on_delivery_order_fields(audience):
    asker = {"audience": audience, "linked_customer_ids": None, "linked_customer_names": [], "hidden_fields": []}
    assert grade_turn(_do_leak_row(), dict(item("scope"), asker=asker))["leaks"] == []


def test_the_askers_own_company_spelled_with_another_suffix_or_case_is_not_a_leak():
    asker = {"audience": "linked_dealer", "linked_customer_ids": ["c1"],
             "linked_customer_names": ["SYNTHETIC TRADING SDN. BHD."], "hidden_fields": []}
    res = grade_turn(_do_leak_row(), dict(item("scope", watch_customer_names=["Synthetic Trading Sdn Bhd"]), asker=asker))
    assert res["leaks"] == [], res["leaks"]


def test_another_company_is_still_a_leak_for_a_linked_asker_with_a_watch_list():
    asker = {"audience": "linked_dealer", "linked_customer_ids": ["c1"],
             "linked_customer_names": ["Other Company Sdn Bhd"], "hidden_fields": []}
    res = grade_turn(_do_leak_row(), dict(item("scope", watch_customer_names=["Synthetic Trading Sdn Bhd"]), asker=asker))
    assert res["leaks"], "a customer outside the links was shown"


# 4. honest none: the expected product is what was held BEFORE the turn plus codes typed in the message
def _none_row(held_before=(), held_after=(), message="send the drawing"):
    r = _live_att([_file("Photo")])
    r["envelope"] = {"message": {"message": {"message": {"text": message}}}}
    r["trace"].append({"kind": "memory", "focus": {
        "before": {"products": [{"canonical_code": c, "raw": c, "uuid": u} for c, u in held_before]},
        "after": {"products": [{"canonical_code": c, "raw": c, "uuid": u} for c, u in held_after]}}})
    return r


def test_an_honest_none_for_a_product_held_before_the_turn_passes():
    res = grade_turn(_none_row(held_before=[("SRTA100", UA)]), item("attachment", **GOLD_TS))
    assert res["checks"]["none_on_record"] is True and "files" not in res["checks"], res["checks"]


def test_an_honest_none_for_a_product_held_only_after_the_turn_fails_as_circular():
    res = grade_turn(_none_row(held_after=[("SRTA100", UA)]), item("attachment", **GOLD_TS))
    assert res["checks"]["files"] is False and res["passed"] is False, res["checks"]


def test_an_honest_none_for_a_code_typed_in_the_message_passes():
    r = _none_row(held_after=[("SRTA100", UA)], message="send the drawing of SRTA100")
    res = grade_turn(r, item("attachment", **GOLD_TS))
    assert res["checks"]["none_on_record"] is True and "files" not in res["checks"], res["checks"]


# ------------------------------------------------------------------ review round 19 (trading and enterprise are part of the name)
def _watch(reply, linked, watch):
    asker = {"audience": "linked_dealer", "linked_customer_ids": ["c1"], "linked_customer_names": linked,
             "hidden_fields": []}
    return grade_turn(row2(reply=reply), dict(item("scope", watch_customer_names=watch), asker=asker))["leaks"]


def test_alpha_enterprise_linked_does_not_cover_alpha_trading_shown():
    assert _watch("Alpha Trading has 40 pcs.", ["Alpha Enterprise"], ["Alpha Trading"]), "different company missed"


def test_alpha_trading_linked_does_not_cover_alpha_enterprise_shown():
    assert _watch("Alpha Enterprise has 40 pcs.", ["Alpha Trading"], ["Alpha Enterprise"]), "different company missed"


@pytest.mark.parametrize("linked,watch,shown", [
    ("ALPHA TRADING SDN. BHD.", "Alpha Trading Sdn Bhd", "Alpha Trading Sdn Bhd"),
    ("Alpha Trading Berhad", "Alpha Trading Bhd", "Alpha Trading Bhd"),
    ("ALPHA TRADING PTE. LTD.", "Alpha Trading Pte Ltd", "Alpha Trading Pte Ltd")])
def test_only_legal_form_suffixes_and_punctuation_are_ignored_when_matching_the_own_company(linked, watch, shown):
    assert _watch(f"{shown} has 40 pcs.", [linked], [watch]) == []


# ------------------------------------------------------------------ review round 20
UNLINKED = {"audience": "unlinked", "linked_customer_ids": [], "linked_customer_names": [], "hidden_fields": []}
STAFF_ASKER = {"audience": "staff", "linked_customer_ids": None, "linked_customer_names": [], "hidden_fields": []}


# 1. a leak fails an item of any slice (targets.py ~842 applies it to the scope slice only)
def _lookup_leaking_row():
    r = row2(mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], reply="SRTA100 has 40 pcs. " + DO_REPLY)
    r["tool_results"] = [{"name": "crm_delivery_orders_list", "args": {}, "result": DO_RESULT}]
    return r


def test_a_code_lookup_that_shows_an_unlinked_contact_another_companys_delivery_order_fails():
    res = grade_turn(_lookup_leaking_row(), dict(item("code_lookup", **GOLD_CL), asker=UNLINKED))
    assert res["leaks"] and res["passed"] is False, res


def test_the_same_code_lookup_for_staff_still_passes():
    res = grade_turn(_lookup_leaking_row(), dict(item("code_lookup", **GOLD_CL), asker=STAFF_ASKER))
    assert res["leaks"] == [] and res["passed"] is True, res


# 2. honest none has its own check name; "files" only means files were sent
def test_an_honest_none_is_reported_as_none_on_record_not_as_files_sent():
    r = _live_att([_file("Drawing")])
    r["uuid_codes"] = {UA: "SRTA100"}
    res = grade_turn(r, item("attachment", code="SRTA100", **GOLD_TS))
    assert res["checks"].get("none_on_record") is True and "files" not in res["checks"], res["checks"]


# 3. a refine's subject may be a non-empty subset of the gold list or of the products held before
def _narrow(gold_codes, after, held_before=()):
    r = row2(verdict=REFINE_V, mem_before=[prod(c, f"00000000-0000-4000-8000-{k:012d}") for k, c in enumerate(held_before)],
             mem_after=[prod(c, f"00000000-0000-4000-9000-{k:012d}") for k, c in enumerate(after)], tools=[TOOL_A])
    return grade_turn(r, item("followup", "refine", subject_after=gold_codes, filter={"kind": "spec", "value": "chrome"}))


def test_a_narrowed_answer_that_is_a_subset_of_the_gold_subject_passes_the_subject_check():
    assert _narrow(["SRTA1", "SRTA2", "SRTA3", "SRTB9"], ["SRTA1", "SRTA2"])["checks"]["subject"] is True


def test_a_narrowed_answer_that_is_a_subset_of_the_products_held_before_passes_the_subject_check():
    assert _narrow(["SRTZ1"], ["SRTA1", "SRTA2"], held_before=["SRTA1", "SRTA2", "SRTA3"])["checks"]["subject"] is True


def test_an_empty_or_outside_the_set_subject_still_fails():
    assert _narrow(["SRTA1", "SRTA2"], [])["checks"]["subject"] is False
    assert _narrow(["SRTA1", "SRTA2"], ["SRTA1", "SRTQ7"])["checks"]["subject"] is False


# 4a. the product-vs-customer menu rule also covers attachment, and_x and switch items, unless the gold is ambiguous
def _kind_menu(types=("product", "attachment")):
    return {"kind": "kind_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
            "options": [{"position": i + 1, "label": f"Sorento ({t})", "raw": "Sorento", "code": "Sorento", "uuid": None,
                         "uuids": [], "entity_type": t, "payload": {"kind": t}} for i, t in enumerate(types)]}


def test_a_needless_menu_fails_an_attachment_item():
    r = _live_att([_file("Drawing")])
    r["trace"].append({"kind": "memory", "open_question": {"before": None, "after": _kind_menu(), "writer": "engine"}})
    res = grade_turn(r, item("attachment", **GOLD_TS))
    assert res["checks"].get("no_menu") is False and res["passed"] is False, res["checks"]


@pytest.mark.parametrize("kind", ["and_x", "switch"])
def test_a_needless_menu_fails_an_and_x_or_switch_item(kind):
    r = row2(mem_before=[prod("SRTOLD1", UB)], mem_after=[prod("SRTNEW1", UA)], tools=[TOOL_A],
             oq={"before": None, "after": _kind_menu(), "writer": "engine"})
    res = grade_turn(r, item("followup", kind, fact="inventory", subject_after=["SRTNEW1"]))
    assert res["checks"].get("no_menu") is False and res["passed"] is False, res["checks"]


def test_a_which_one_menu_is_right_when_the_gold_code_is_ambiguous():
    r = row2(mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], oq={"before": None, "after": _kind_menu(), "writer": "engine"})
    res = grade_turn(r, item("code_lookup", ambiguous=True, **GOLD_CL))
    assert res["checks"].get("no_menu") is not False, res["checks"]


# 4b. a trailing quantity question after the answer is needless on a lookup, spec, attachment or promo answer
QTY_OQ = {"kind": "product_pick", "expects": "choice", "payload": {"stock_pick": True}, "asked_at_turn": 2,
          "options": [], "owed": ["quantity"]}


def _qty_row(base):
    base["trace"].append({"kind": "memory", "open_question": {"before": None, "after": QTY_OQ, "writer": "engine"}})
    return base


def test_a_trailing_quantity_question_fails_a_code_lookup():
    r = _qty_row(row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A],
                      reply="SRTA100 has 40 pcs. How many units?"))
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"]["tool_ran"] is True and res["checks"]["code"] is True, res["checks"]   # only the question is wrong
    assert res["passed"] is False, res["checks"]


def test_a_trailing_quantity_question_fails_a_spec_item():
    r = _qty_row(row2(message="white taps with soft close", verdict=SPEC_V, reply="Here are white taps: SRTKT100."))
    assert grade_turn(r, item("spec", **SPEC_ITEM))["passed"] is False


def test_a_trailing_quantity_question_fails_an_attachment_item():
    r = _qty_row(_att([_file("Technical Specifications")], reply="Here are the files."))
    assert grade_turn(r, item("attachment", **GOLD_TS))["checks"]["type"] is True   # the attachment itself is right
    assert grade_turn(r, item("attachment", **GOLD_TS))["passed"] is False


# 5. reason texts say narrowing down, not refine
def test_a_refine_failure_reason_never_says_refine_or_refining():
    r = row2(verdict=REFINE_V, mem_after=[prod("SRTB200", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "chrome"}))
    assert res["passed"] is False and "refin" not in res["reason"].lower(), res["reason"]
    assert "narrowing down" in res["reason"].lower(), res["reason"]


# 6. checks that do not decide `passed` live in a separate `info` dict
def test_class_is_reported_in_info_and_not_in_checks():
    v = {"entities": [{"raw": "white", "hint": "specification", "spec_key": "colour", "spec_value": "white"},
                      {"raw": "basin", "hint": "category", "canonical_code": "basin"}], "reference_positions": []}
    r = row2(verdict=v, reply="Here are white basins: SRTWB100.")
    res = grade_turn(r, item("spec", spec=[{"key": "class", "value": "basin"}, {"key": "colour", "value": "white"}]))
    assert "class" in res["info"] and "class" not in res["checks"], (res.get("info"), res["checks"])


def test_old_dropped_is_reported_in_info_and_not_in_checks_on_a_real_trace():
    r = row2(mem_before=[prod("SRTOLD1", UB)], mem_after=[prod("SRTNEW1", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "switch", fact="inventory", subject_after=["SRTNEW1"]))
    assert "old_dropped" in res["info"] and "old_dropped" not in res["checks"], (res.get("info"), res["checks"])


# ------------------------------------------------------------------ review round 21 (narrowing by warehouse)
# Real shapes: entity hint `warehouse` (contracts.ENTITY_HINTS) and the memory focus slot `warehouse` (session_state.py).
def _warehouse_row(entity_raw, memory_raw):
    v = {"entities": [{"raw": entity_raw, "hint": "warehouse", "canonical_code": entity_raw}], "reference_positions": []}
    tool = {"name": "crm_inventory_list", "args": {"product_ids": [UA], "warehouse_codes": ["KL"]}}
    r = row2(verdict=v, mem_after=[prod("SRTA100", UA)], tools=[tool], prev_bot="Stock in all warehouses.",
             reply="Stock in KL: SRTA100 40 pcs.")
    for rec in r["trace"]:
        if rec.get("kind") == "memory":
            rec["focus"]["after"]["warehouse"] = [{"raw": memory_raw, "hint": "warehouse", "canonical_code": memory_raw}]
    return r


WAREHOUSE_ITEM = dict(subject_after=["SRTA100"], filter={"kind": "warehouse", "value": "KL"})


def test_a_warehouse_entity_held_in_memory_counts_as_the_applied_filter():
    res = grade_turn(_warehouse_row("KL", "KL"), item("followup", "refine", **WAREHOUSE_ITEM))
    assert res["checks"]["filter"] is True, res["checks"]
    check(res, True)


def test_a_different_warehouse_does_not_match_the_gold_filter():
    res = grade_turn(_warehouse_row("JB", "JB"), item("followup", "refine", **WAREHOUSE_ITEM))
    assert res["checks"]["filter"] is False, res["checks"]


PRICE_ASK = "Promo price for SRTA100 is RM 90. How many units?"
NO_ANSWER_ASK = "How many units of SRTA100?"


def _price_given_or_blocked(res, given):
    """Owner round 29: a how-many beside the price is fine; one that replaces the answer fails with its own reason."""
    if given:
        assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]
    else:
        assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]
        assert "instead of giving the answer" in res["reason"], res["reason"]


# ------------------------------------------------------------------ review round 21 part 2 (needless questions, crew policy)
# Where the resolved Stock Visibility mode lives in the trace: the stock tool envelope carries
# `stock_visibility: {"mode": ...}` (lanes/business/miss_suggest.py:898 reads it the same way; the mode itself is
# `stock_visibility.resolve_policy(...).mode`, `turn_runtime._stock_availability_only` is the engine's own read).
def _stock_tool(mode):
    return {"name": "crm_inventory_list", "args": {"product_ids": [UA]},
            "envelope": {"stock_visibility": {"mode": mode}, "items": [{"code": "SRTA100", "qty": 40}]}}


def _stock_lookup(mode, reply):
    return row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[_stock_tool(mode)], reply=reply)


@pytest.mark.parametrize("ask", ["How many units do you need?", "Berapa unit yang anda perlukan?"])
def test_a_quantity_question_is_right_on_a_stock_ask_for_an_availability_only_contact(ask):
    res = grade_turn(_stock_lookup("availability", f"SRTA100 is available. {ask}"), item("code_lookup", **GOLD_CL))
    assert res["checks"]["code"] is True and res["checks"]["tool_ran"] is True, res["checks"]
    check(res, True)


@pytest.mark.parametrize("mode", ["detailed", "compact"])
def test_the_same_quantity_question_is_fine_for_a_detailed_or_compact_stock_contact(mode):
    res = grade_turn(_stock_lookup(mode, "SRTA100 has 40 pcs. How many units do you need?"), item("code_lookup", **GOLD_CL))
    assert res["checks"]["code"] is True and res["checks"]["tool_ran"] is True, res["checks"]
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]
    assert res["passed"] is True, res["checks"]


@pytest.mark.parametrize("reply,given", [(PRICE_ASK, True), (NO_ANSWER_ASK, False)])
def test_a_quantity_question_after_a_promo_answer_fails_an_and_x_item_only_when_no_price_is_given(reply, given):
    tool = {"name": "crm_promotions_list", "args": {"product_ids": [UA]}, "envelope": {"items": [{"code": "SRTA100"}]}}
    r = row2(domains=("promotion",), state_products=[prod("SRTA100", UA)], mem_before=[prod("SRTOLD1", UB)],
             mem_after=[prod("SRTA100", UA)], tools=[tool], reply=reply)
    res = grade_turn(r, item("followup", "and_x", fact="promotion", subject_after=["SRTA100"]))
    assert res["checks"]["new_subject"] is True, res["checks"]
    _price_given_or_blocked(res, given)


# Q2a: product-variant pickers fail unless the gold code is ambiguous
VARIANT_Q = {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
             "options": [{"label": "SRTA100-W", "code": "SRTA100-W"}, {"label": "SRTA100-B", "code": "SRTA100-B"}]}
TIER_Q = {"kind": "tier_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
          "options": [{"label": "Dealer"}, {"label": "End user"}]}


def _with_oq(r, q, asking=None):
    r["trace"].append({"kind": "memory", "open_question": {"before": None, "after": q, "writer": "engine"}})
    if asking:
        for rec in r["trace"]:
            if rec.get("stage") == "replied":
                rec["facts"]["asking"] = asking
    return r


# owner ruling 17 Oct (answer.py:4846-4848): a sibling picker after a full answer is right, not needless
def test_a_sibling_picker_after_a_full_code_lookup_answer_is_not_needless():
    r = _with_oq(row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A]), VARIANT_Q)
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"]["code"] is True and res["checks"]["tool_ran"] is True, res["checks"]
    assert res["checks"]["no_picker"] is not False, res["checks"]
    assert res["passed"] is True, res["checks"]


def test_a_variant_picker_fails_an_attachment_item():
    r = _with_oq(_att([_file("Technical Specifications")]), VARIANT_Q)
    assert grade_turn(r, item("attachment", **GOLD_TS))["checks"]["type"] is True
    assert grade_turn(r, item("attachment", **GOLD_TS))["passed"] is False


# owner ruling 17 Oct (answer.py:4846-4848): a sibling picker after a full answer is right, not needless
def test_a_sibling_picker_after_a_full_and_x_answer_is_not_needless():
    r = _with_oq(row2(mem_before=[prod("SRTOLD1", UB)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A]), VARIANT_Q)
    res = grade_turn(r, item("followup", "and_x", fact="inventory", subject_after=["SRTA100"]))
    assert res["checks"]["new_subject"] is True, res["checks"]
    assert res["checks"]["no_picker"] is not False, res["checks"]
    assert res["passed"] is True, res["checks"]


def test_a_variant_picker_is_right_when_the_gold_code_is_ambiguous():
    r = _with_oq(row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A]),
                 VARIANT_Q, asking="product_pick")
    res = grade_turn(r, item("code_lookup", ambiguous=True, **GOLD_CL))
    check(res, True)


# Q2b: the tier menu is an access ask: the item is unknown
def test_a_tier_menu_after_a_spec_ask_is_unknown_not_failed_and_not_passed():
    r = _with_oq(row2(verdict=SPEC_V, reply="Which price tier are you?"), TIER_Q, asking="tier_pick")
    assert grade_turn(r, item("spec", **SPEC_ITEM))["passed"] is None


def test_a_tier_menu_after_a_promo_ask_is_unknown():
    tool = {"name": "crm_promotions_list", "args": {"product_ids": [UA]}, "envelope": {"items": []}}
    r = _with_oq(row2(domains=("promotion",), mem_before=[prod("SRTOLD1", UB)], mem_after=[prod("SRTA100", UA)],
                      tools=[tool], reply="Which price tier are you?"), TIER_Q, asking="tier_pick")
    assert grade_turn(r, item("followup", "and_x", fact="promotion", subject_after=["SRTA100"]))["passed"] is None


# ------------------------------------------------------------------ review round 22
# 1. the quantity policy runs on every slice and follow-up kind, and a stock ask comes from the gold only
def _promo_widened_to_inventory(reply=PRICE_ASK, **kw):
    return row2(domains=("promotion", "inventory"), tools=[_stock_tool("availability")], reply=reply, **kw)


@pytest.mark.parametrize("reply,given", [(PRICE_ASK, True), (NO_ANSWER_ASK, False)])
def test_a_promo_answer_followup_asking_how_many_fails_only_without_the_price_even_if_the_plan_widened_to_inventory(reply, given):
    q = COLOUR_Q
    r = _promo_widened_to_inventory(reply, state_oq=q, oq={"before": q, "after": None, "writer": "engine"})
    r["trace"] = [dict(e, verdict=_picks([1])) if e.get("kind") == "apply" else e for e in r["trace"]]
    res = grade_turn(r, item("followup", "answer", option="white", fact="promotion"))
    assert res["checks"]["answered"] is True and res["checks"]["option"] is True, res["checks"]
    _price_given_or_blocked(res, given)


def test_a_pick_followup_on_a_non_stock_gold_whose_reply_gives_the_price_may_ask_how_many():
    v = {"entities": [], "reference_positions": [1], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    r = row2(verdict=v, state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)],
             tools=[_stock_tool("availability")], reply="SRTA100 is RM 90. How many units?")
    res = grade_turn(r, item("followup", "pick", position=1, code="SRTA100", fact="promotion"))
    assert res["checks"]["position"] is True, res["checks"]
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_a_pick_followup_on_a_stock_gold_in_availability_mode_may_ask_how_many():
    v = {"entities": [], "reference_positions": [1], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    r = row2(verdict=v, state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)],
             tools=[_stock_tool("availability")], reply="SRTA100 is available. How many units?")
    check(grade_turn(r, item("followup", "pick", position=1, code="SRTA100", fact="inventory")), True)


@pytest.mark.parametrize("reply,given", [(PRICE_ASK, True), (NO_ANSWER_ASK, False)])
def test_a_code_lookup_whose_gold_is_not_stock_fails_a_how_many_only_without_the_price_even_when_the_plan_includes_inventory(reply, given):
    r = _promo_widened_to_inventory(reply, state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)])
    res = grade_turn(r, item("code_lookup", code="srta100", fact="promotion"))
    _price_given_or_blocked(res, given)


# 2. a spec answer that showed products and leaves a product list open is not a needless picker
def test_a_spec_answer_that_showed_products_and_leaves_the_list_open_is_not_a_needless_picker():
    r = _with_oq(row2(verdict=SPEC_V, reply="Here are white taps: SRTKT100 and SRTKT200."), VARIANT_Q)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("no_picker") is not False, res["checks"]
    check(res, True)


def test_a_spec_picker_with_no_products_shown_still_fails_as_a_picker():
    r = _with_oq(row2(verdict=SPEC_V, reply="Which one do you mean?"), VARIANT_Q)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("no_picker") is False and res["passed"] is False, res["checks"]


# 3. neutral picker reason wording
def test_the_picker_reason_for_an_unresolved_code_never_says_the_code_was_clear():
    r = _with_oq(row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A]), VARIANT_Q)
    res = grade_turn(r, item("code_lookup", code="srta100", fact="inventory", code_unresolved=True))
    assert res["passed"] is False
    assert "code was clear" not in res["reason"].lower(), res["reason"]
    assert "list of products" in res["reason"].lower(), res["reason"]


# ------------------------------------------------------------------ review round 23 (stock ask from the user's words)
def _how_many_row(message, verdict=None, mode="availability"):
    kw = {"verdict": verdict} if verdict else {}
    return row2(message=message, tools=[_stock_tool(mode)], reply="Here are SRTKT100 and SRTKT200. How many units?", **kw)


def test_a_spec_item_without_a_fact_is_a_stock_ask_when_the_message_says_available():
    r = _how_many_row("Any WC with s trap 300mm available?", SPEC_V)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_a_scope_item_without_a_fact_is_a_stock_ask_when_the_message_says_ada_stock():
    res = grade_turn(_how_many_row("ada stock?"), item("scope"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


@pytest.mark.parametrize("message", ["is it in stock", "ready stock?", "how many left", "berapa ada"])
def test_the_other_stock_words_count_when_the_gold_has_no_fact(message):
    res = grade_turn(_how_many_row(message, SPEC_V), item("spec", **SPEC_ITEM))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_a_spec_item_without_a_fact_and_without_a_stock_word_still_fails_a_how_many():
    res = grade_turn(_how_many_row("white tap with soft close", SPEC_V), item("spec", **SPEC_ITEM))
    assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]


def test_the_stock_word_makes_no_difference_when_the_bots_plan_is_the_only_stock_evidence():
    r = row2(message="white tap with soft close", verdict=SPEC_V, domains=("inventory",), tools=[_stock_tool("availability")],
             reply="Here are SRTKT100 and SRTKT200. How many units?")
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("no_qty_ask") is False, res["checks"]


@pytest.mark.parametrize("reply,given", [(PRICE_ASK, True), (NO_ANSWER_ASK, False)])
def test_a_non_stock_gold_fact_stays_not_a_stock_ask_whatever_the_message_says(reply, given):
    r = row2(message="promo price, is it in stock?", state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)],
             tools=[_stock_tool("availability")], reply=reply)
    res = grade_turn(r, item("code_lookup", code="srta100", fact="promotion"))
    _price_given_or_blocked(res, given)


# ------------------------------------------------------------------ review round 24
STOCK_PICK_Q = {"kind": "product_pick", "expects": "choice", "payload": {"stock_pick": True, "stock_qty": None},
                "asked_at_turn": 1, "options": [{"label": "SRTA100", "code": "SRTA100"}, {"label": "SRTA200", "code": "SRTA200"}]}
PLAIN_PICK_Q = dict(STOCK_PICK_Q, payload={})
TWO_CODES_REPLY = "Here are SRTA100 and SRTA200."


def _pick_two(before_q):
    v = {"entities": [], "reference_positions": [2], "open_question_answer": {"mode": None, "items": [], "picked": []}}
    return row2(message="2", verdict=v, state_oq=before_q, oq={"before": before_q, "after": None, "writer": "engine"},
                state_products=[prod("SRTA200", UA)], mem_after=[prod("SRTA200", UA)], tools=[_stock_tool("availability")],
                reply="SRTA200 is available. How many units?")


# 1. a pick on a stock picker is a stock ask
def test_a_pick_on_a_stock_picker_may_ask_how_many_in_availability_mode():
    res = grade_turn(_pick_two(STOCK_PICK_Q), item("followup", "pick", position=2, code="SRTA200"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]
    check(res, True)


def test_a_how_many_after_a_pick_of_unknown_type_is_fine():
    res = grade_turn(_pick_two(PLAIN_PICK_Q), item("followup", "pick", position=2, code="SRTA200"))
    assert res["checks"]["position"] is True, res["checks"]
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


# 2. a reply that shows 2+ product codes and leaves a list open is not a needless picker, on every slice and kind
def test_a_shown_list_left_open_is_a_picker_on_an_answer_followup():
    r = row2(verdict=_picks([1]), state_oq=COLOUR_Q, oq={"before": COLOUR_Q, "after": VARIANT_Q, "writer": "engine"},
             reply=TWO_CODES_REPLY)
    res = grade_turn(r, item("followup", "answer", option="white"))
    assert res["checks"].get("no_picker") is False, res["checks"]


def test_a_shown_list_left_open_is_a_picker_on_a_scope_item():
    r = _with_oq(row2(reply=TWO_CODES_REPLY), VARIANT_Q)
    res = grade_turn(r, dict(item("scope"), asker=STAFF_ASKER))
    assert res["checks"].get("no_picker") is False and res["passed"] is False, res["checks"]


def test_a_product_pick_with_no_sections_listing_three_codes_is_a_picker_on_a_code_lookup():
    r = _with_oq(row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A],
                      reply="Did you mean SRTA100, SRTA101 or SRTA102?"), VARIANT_Q, asking="product_pick")
    for rec in r["trace"]:
        if rec.get("stage") == "replied":
            rec["facts"]["sections"] = 0
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"].get("no_picker") is False and res["passed"] is False, res["checks"]


def test_a_spec_answer_showing_products_with_a_list_open_still_passes_the_picker_check():
    r = _with_oq(row2(verdict=SPEC_V, reply="Here are white taps: SRTKT100 and SRTKT200."), VARIANT_Q)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("no_picker") is not False, res["checks"]


def test_a_picker_with_nothing_shown_still_fails_on_a_scope_item():
    r = _with_oq(row2(reply="Which one do you mean?"), VARIANT_Q)
    res = grade_turn(r, dict(item("scope"), asker=STAFF_ASKER))
    assert res["checks"].get("no_picker") is False and res["passed"] is False, res["checks"]


# 3. the same list left open as the question before is not a new question
def test_the_same_product_list_left_open_as_the_question_before_is_not_needless():
    after = dict(VARIANT_Q, asked_at_turn=3)
    r = row2(reply="Thanks.", oq={"before": VARIANT_Q, "after": after, "writer": "engine"})
    res = grade_turn(r, dict(item("scope"), asker=STAFF_ASKER))
    assert res["checks"].get("no_picker") is not False, res["checks"]


def test_a_different_product_list_left_open_with_nothing_shown_still_fails():
    other = dict(VARIANT_Q, options=[{"label": "SRTZ900", "code": "SRTZ900"}, {"label": "SRTZ901", "code": "SRTZ901"}])
    r = row2(reply="Thanks.", oq={"before": VARIANT_Q, "after": other, "writer": "engine"})
    res = grade_turn(r, dict(item("scope"), asker=STAFF_ASKER))
    assert res["checks"].get("no_picker") is False, res["checks"]


# ------------------------------------------------------------------ review round 26 (a follow-up on a stock answer is a stock ask)
# The domain before the turn lives at memory `focus.before.domains` (the session focus slot `domains`, session_state.py).
def _refine_how_many(before_domain, message="black only", prev_bot="SRTA100: Got stock.\nSRTA200: Got stock."):
    tool = {"name": "crm_inventory_list", "args": {"product_ids": [UA], "scope_terms": ["black"]},
            "envelope": {"stock_visibility": {"mode": "availability"}, "items": [{"code": "SRTA100", "qty": 40}]}}
    v = {"entities": [{"raw": "black", "hint": "specification", "spec_key": "colour", "spec_value": "black"}],
         "reference_positions": []}
    r = row2(message=message, verdict=v, mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[tool],
             prev_bot=prev_bot, reply="Black SRTA100 is available. How many units?")
    for rec in r["trace"]:
        if rec.get("kind") == "memory":
            rec["focus"]["before"]["domains"] = [before_domain]
    return grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "black"}))


def test_a_refine_of_a_stock_answer_may_ask_how_many_in_availability_mode():
    res = _refine_how_many("inventory")
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_a_refine_of_a_promo_answer_may_ask_how_many_when_the_ask_type_is_unknown():
    res = _refine_how_many("promotion")
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


# ------------------------------------------------------------------ review round 27 (stock carry only after a stock answer)
# Stock reply markers: label_catalog.py "Got stock." (~77), "No stock ...", "in stock" lines.
def test_a_dealer_answer_to_a_tier_question_followed_by_how_many_is_not_a_stock_carry():
    tool = {"name": "crm_inventory_list", "args": {"product_ids": [UA]},
            "envelope": {"stock_visibility": {"mode": "availability"}, "items": [{"code": "SRTA100", "qty": 40}]}}
    q = {"kind": "tier_pick", "expects": "choice", "payload": {}, "asked_at_turn": 1,
         "options": [{"label": "Dealer"}, {"label": "End user"}]}
    r = row2(message="Dealer", verdict=_picks([1]), state_oq=q, oq={"before": q, "after": None, "writer": "engine"},
             mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[tool],
             prev_bot="Which price tier are you?", reply="Promo for SRTA100 attached. How many units do you need?")
    for rec in r["trace"]:
        if rec.get("kind") == "memory":
            rec["focus"]["before"]["domains"] = ["inventory"]
    res = grade_turn(r, item("followup", "answer", option="Dealer"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_a_refine_after_a_stock_answer_in_availability_mode_may_ask_how_many_and_says_so():
    res = _refine_how_many("inventory", prev_bot="SRTA100: Got stock.")
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_a_refine_after_a_non_stock_answer_with_an_inventory_domain_may_ask_how_many_when_the_ask_type_is_unknown():
    res = _refine_how_many("inventory", prev_bot="Here is the promotion price list for SRTA100.")
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_no_quantity_ask_is_reported_as_no_qty_ask_true():
    r = row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A], reply="SRTA100 has 40 pcs.")
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"].get("no_qty_ask") is True and "qty_ask_ok" not in res["checks"], res["checks"]


# round 28: owner CP0 rulings 3, 5, 7
# ---- rule 3: an attribute ask answered "none on record" passes only when the record really holds none
R28_CODE = "SRTKT1636SS-GM"
R28_BASE_FIELDS = [
    {"label": "Product Code", "value": R28_CODE},
    {"label": "Description", "value": "SORENTO WALL MOUNTED KITCHEN TAP  SRTKT1636SS-GM"},
    {"label": "List Price", "value": "MYR 285.00"},
    {"label": "Dimensions", "value": "Not defined"},
    {"key": "spec:class", "label": "Product class", "value": "Tap"},
    {"key": "spec:mounting", "label": "Mounting", "value": "Wall hung"},
]
R28_SILENT = ("*Product Code:* SRTKT1636SS-GM\n*Description:* SORENTO WALL MOUNTED KITCHEN TAP SRTKT1636SS-GM\n"
              "*List Price:* MYR 285.00\n*Dimensions:* Not defined\n*Specs:* Tap, Wall hung")


def _r28_result(extra_fields=(), code=R28_CODE, as_json=False):
    res = {"result_type": "products", "intro": "Here are the matching products.",
           "items": [{"title": code, "fields": [dict(f, value=code) if f.get("label") == "Product Code" else f
                                                for f in R28_BASE_FIELDS] + list(extra_fields)}],
           "attachments": []}
    return json.dumps(res) if as_json else res


def _r28_attr_row(reply, result="default"):
    tool = {"name": "crm_master_products_list", "args": {"product_ids": [UA]}}
    r = row2(message=f"{R28_CODE} check weight ?", reply=reply, mem_after=[prod(R28_CODE, UA)], tools=[tool])
    if result == "default":
        result = _r28_result()
    if result is not None:
        r["tool_results"] = [{"name": "crm_master_products_list", "args": {"product_ids": [UA]}, "result": result}]
    return r


R28_GOLD = dict(code=R28_CODE, fact="inventory", fact_label="weight")


@pytest.mark.parametrize("reply", [
    "SRTKT1636SS-GM has no weight on record.",
    "SRTKT1636SS-GM is a wall hung tap. There is no weight for this product.",
    "SRTKT1636SS-GM\nWeight: not defined",
    "For SRTKT1636SS-GM, we don't have the weight on record.",
])
@pytest.mark.parametrize("as_json", [False, True])
def test_r28_a_human_none_on_record_reply_passes_when_the_record_has_no_such_field(reply, as_json):
    res = grade_turn(_r28_attr_row(reply, _r28_result(as_json=as_json)), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is True, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r28_a_silent_product_block_still_has_no_value_for_the_attribute():
    res = grade_turn(_r28_attr_row(R28_SILENT), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]
    assert res["passed"] is False and res["reason"] == "The reply has no value for the attribute asked.", res["reason"]


def test_r28_a_none_on_record_reply_while_the_record_has_a_weight_field_is_not_honest():
    result = _r28_result([{"key": "spec:weight", "label": "Weight", "value": "12 kg"}])
    res = grade_turn(_r28_attr_row("SRTKT1636SS-GM has no weight on record.", result), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]
    assert res["passed"] is False


def test_r28_a_none_on_record_reply_with_no_tool_results_for_the_code_is_not_honest():
    for result in (None, _r28_result(code="SRTOTHER9")):
        res = grade_turn(_r28_attr_row("SRTKT1636SS-GM has no weight on record.", result), item("code_lookup", **R28_GOLD))
        assert res["checks"]["attribute_value"] is False, res["checks"]
        assert res["passed"] is False


# ---- rule 5: a set reply must show products, and a cut list says how to see more
R28_BRAND_SUMMARY = ("Here's what you want: wash basins with stock (550)\n• Sorento wash basins: 282\n"
                     "• Bravat wash basins: 81\n• Cabana wash basins: 59")


def _r28_entries(n):
    return "\n".join(f"{i}. *Product Code:* BRBC{22100 + i}W" for i in range(1, n + 1))


def test_r28_a_brand_summary_set_reply_shows_no_product_and_fails_a_spec_item():
    r = row2(verdict=SPEC_V, reply=R28_BRAND_SUMMARY)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("set_list") is False, res["checks"]
    assert res["passed"] is False and res["reason"] == "The set reply showed no product.", res["reason"]


def test_r28_a_brand_summary_set_reply_fails_a_followup_item_too():
    tool = {"name": "crm_inventory_list", "args": {"product_ids": [UA], "scope_terms": ["chrome"]}}
    r = row2(verdict=REFINE_V, mem_after=[prod("SRTA100", UA)], tools=[tool], prev_bot=LIST_REPLY,
             reply=R28_BRAND_SUMMARY)
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "chrome"}))
    assert res["passed"] is False and "The set reply showed no product." in res["reason"], (res["reason"], res["checks"])


def test_r28_a_cut_list_with_no_see_more_cue_fails():
    reply = "Here's what you want: kitchen sinks with length 860 mm (38)\n" + _r28_entries(33)
    res = grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))
    assert res["checks"].get("set_list") is False, res["checks"]
    assert res["passed"] is False and res["reason"] == "The list is cut short without saying how to see more.", res["reason"]


def test_r28_a_header_that_says_showing_a_to_b_passes_the_set_list_check():
    reply = "Here's what you want: kitchen sinks with length 860 mm (38, showing 1 to 33)\n" + _r28_entries(33)
    res = grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))
    assert res["checks"].get("set_list") is True, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r28_a_cut_list_with_a_see_more_cue_passes_the_set_list_check():
    reply = "Here's what you want: kitchen sinks (38)\n" + _r28_entries(33) + "\nReply more to see more."
    res = grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))
    assert res["checks"].get("set_list") is True, res["checks"]


def test_r28_a_full_list_of_n_entries_passes_the_set_list_check():
    reply = "Here's what you want: under counter wash basins with stock (23)\n" + _r28_entries(23)
    res = grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))
    assert res["checks"].get("set_list") is True, res["checks"]


def test_r28_a_reply_without_a_set_header_has_no_set_list_check():
    reply = "Stock summary for the requested products.\n\n1. *Product Code:* SRT8303\n2. *Product Code:* SRT8304"
    res = grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))
    assert "set_list" not in res["checks"], res["checks"]


# ---- rule 7: a price-tier ask on a spec item is fine only when the products found match the spec
R28_SPEC_CODES = ["MWB8051", "MWB8052", "MWB8053", "MWB8054"]
R28_UUIDS = [f"{i}{i}{i}{i}{i}{i}{i}{i}-aaaa-4aaa-8aaa-aaaaaaaaaaaa" for i in "3456"]
R28_TIER_ASK = ("Which access level do you need for basin, undermount?\n1. Office - no promotion\n"
                "2. Dealer - has promotion\n3. End user - no promotion")


def _r28_tier_row(uuids, codes, reply=R28_TIER_ASK, tools=True):
    tool = {"name": "crm_promotion_products_list", "args": {"product_ids": list(uuids)}}
    r = row2(message="any promo for undermount basin", verdict=SPEC_V, reply=reply, tools=[tool] if tools else [])
    r["uuid_codes"] = dict(zip(uuids, codes))
    return _with_oq(r, TIER_Q, asking="tier_pick")


def test_r28_a_tier_ask_after_finding_only_spec_products_stays_unjudged():
    r = _r28_tier_row(R28_UUIDS, R28_SPEC_CODES)
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is None and res["checks"].get("tier_spec") is True, (res["reason"], res["checks"])
    assert res["reason"] == "The bot asked an access question (price tier); the bench cannot judge it."


def test_r28_a_tier_ask_after_finding_a_product_outside_the_spec_fails():
    r = _r28_tier_row(R28_UUIDS, R28_SPEC_CODES[:3] + ["SRTWC8152"])
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is False and res["checks"].get("tier_spec") is False, (res["reason"], res["checks"])
    assert res["reason"] == "The products found do not match the spec asked.", res["reason"]


def test_r28_a_tier_ask_before_finding_any_product_fails():
    r = _r28_tier_row([], [], reply="Which one do you mean?\n1. dealer\n2. office\n3. end user", tools=False)
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is False and res["checks"].get("tier_spec") is False, (res["reason"], res["checks"])
    assert res["reason"] == "The bot asked which price tier before finding any product.", res["reason"]


def test_r28_a_tier_ask_on_a_spec_item_without_spec_products_is_unchanged():
    r = _r28_tier_row(R28_UUIDS, R28_SPEC_CODES)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["passed"] is None and "tier_spec" not in res["checks"], (res["reason"], res["checks"])


def test_r28_a_tier_ask_on_a_followup_item_is_unchanged():
    tool = {"name": "crm_promotions_list", "args": {"product_ids": [UA]}, "envelope": {"items": []}}
    r = _with_oq(row2(domains=("promotion",), mem_before=[prod("SRTOLD1", UB)], mem_after=[prod("SRTA100", UA)],
                      tools=[tool], reply="Which price tier are you?"), TIER_Q, asking="tier_pick")
    res = grade_turn(r, item("followup", "and_x", fact="promotion", subject_after=["SRTA100"]))
    assert res["passed"] is None and "tier_spec" not in res["checks"], (res["reason"], res["checks"])


@pytest.mark.parametrize("reply", [
    "There is no weight for SRTKT1636SS-GM.",
    "Weight of SRTKT1636SS-GM: not on record.",
])
def test_r28_a_digit_inside_a_product_code_is_not_an_attribute_value(reply):
    result = _r28_result([{"key": "spec:weight", "label": "Weight", "value": "12 kg"}])
    res = grade_turn(_r28_attr_row(reply, result), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]


def test_r28_a_weight_value_after_the_word_still_passes():
    res = grade_turn(_r28_attr_row("SRTKT1636SS-GM weight: 2.5 kg"), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is True, res["checks"]


# round 28b: review findings on the round 28 fix
def _r28b_spec(reply):
    return grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))


R28B_AVAIL = ("Here's what you want: Sorento wash basins with stock (3)\n\nBRBC22101W x 2: Available\n\n"
              "BRBC22102W x 2: Available\n\nBRBC22103W x 2: Available")
R28B_MEMBERS = ("Here's what you want: gunmetal wash basins (3)\n• BRBC22101W\n• BRBC22102W\n• BRBC22103W\n\n"
                "But no incoming matched these.")


@pytest.mark.parametrize("reply", [R28B_AVAIL, R28B_MEMBERS])
def test_r28b_plain_code_rows_and_code_bullets_are_set_entries(reply):
    res = _r28b_spec(reply)
    assert res["checks"].get("set_list") is True, res["checks"]


def test_r28b_a_bullet_without_a_code_is_still_not_an_entry():
    res = _r28b_spec(R28_BRAND_SUMMARY)
    assert res["checks"].get("set_list") is False and res["reason"] == "The set reply showed no product.", res["reason"]


def test_r28b_warehouse_lines_are_not_counted_as_entries():
    block = "{i}. *Product Code:* BRBC{n}W\n*DC1-SMC:* 0\n*WH3-IB:* 0"
    reply = "Here's what you want: basins (38)\n" + "\n".join(block.format(i=i, n=22100 + i) for i in range(1, 34))
    res = _r28b_spec(reply)
    assert res["checks"].get("set_list") is False, res["checks"]
    assert res["reason"] == "The list is cut short without saying how to see more.", res["reason"]


def test_r28b_a_none_statement_takes_the_none_branch_only():
    result = _r28_result([{"key": "spec:weight", "label": "Weight", "value": "12 kg"}])
    reply = "SRTKT1636SS-GM has no weight on record, list price MYR 285.00."
    res = grade_turn(_r28_attr_row(reply, result), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]


def _r28b_tier(extra_tool=None, result_codes=(), uuids=R28_UUIDS, codes=R28_SPEC_CODES, reply=R28_TIER_ASK):
    r = _r28_tier_row(uuids, codes, reply=reply)
    if extra_tool:
        r["trace"].append({"kind": "tool", "name": "crm_promotions_list", "args": extra_tool, "envelope": {}})
    if result_codes:
        items = [{"title": c, "fields": [{"label": "Product Code", "value": c}]} for c in result_codes]
        r["tool_results"] = [{"name": "crm_promotion_products_list", "args": {}, "result": {"items": items}}]
    return r


def test_r28b_scope_terms_are_not_found_product_codes():
    r = _r28b_tier(extra_tool={"scope_terms": ["600mm undermount basin"]})
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is None and res["checks"].get("tier_spec") is True, (res["reason"], res["checks"])


def test_r28b_an_off_spec_code_only_in_a_tool_result_fails_the_tier_ask():
    r = _r28b_tier(result_codes=["SRTWC8152"])
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is False and res["reason"] == "The products found do not match the spec asked.", (res["reason"], res["checks"])


def test_r28b_a_field_whose_value_is_a_none_word_is_absent():
    gold = dict(code=R28_CODE, fact="inventory", fact_label="dimensions")
    res = grade_turn(_r28_attr_row("SRTKT1636SS-GM has no dimensions on record."), item("code_lookup", **gold))
    assert res["checks"]["attribute_value"] is True, res["checks"]


@pytest.mark.parametrize("reply", [
    "About the weight, nonetheless SRTKT1636SS-GM is a wall hung tap.",
    "Weight of SRTKT1636SS-GM? Promotion: none.",
])
def test_r28b_a_none_word_unrelated_to_the_attribute_is_not_an_honest_none(reply):
    res = grade_turn(_r28_attr_row(reply), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]


def test_r28b_a_thousands_comma_header_with_a_showing_range_is_a_complete_cut():
    reply = "Here's what you want: basins (1,204, showing 1 to 10)\n" + _r28_entries(10)
    assert _r28b_spec(reply)["checks"].get("set_list") is True


def test_r28b_a_thousands_comma_count_is_read_whole_so_ten_entries_are_a_cut_list():
    reply = "Here's what you want: basins (1,204)\n" + _r28_entries(10)
    res = _r28b_spec(reply)
    assert res["checks"].get("set_list") is False, res["checks"]


def test_r28b_a_header_with_a_moved_count_note_after_it_is_still_a_set_header():
    reply = "Here's what you want: basins (40, showing 11 to 20). It was 42 when you asked.\n" + _r28_entries(10)
    assert _r28b_spec(reply)["checks"].get("set_list") is True


def test_r28b_a_failed_tier_check_keeps_an_earlier_failure_reason():
    leaky = f"SRTWC8152 options. Which one?\n1. dealer\n2. office {UA}"
    r = _r28_tier_row([], [], reply=leaky, tools=False)
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is False
    assert res["reason"] == "The reply shows an internal id.", res["reason"]


@pytest.mark.parametrize("reply", [
    "Weight: nonetheless SRTKT1636SS-GM is a wall hung tap.",
    "Weight: none/other",
])
def test_r28b_a_none_word_glued_to_a_longer_word_after_the_attribute_is_not_a_none(reply):
    res = grade_turn(_r28_attr_row(reply), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]


# round 28c: re-review of the round 28b fix (bot's header builder: answer.py build_set_header)
def _r28c_refine(reply):
    tool = {"name": "crm_inventory_list", "args": {"product_ids": [UA], "scope_terms": ["chrome"]}}
    r = row2(verdict=REFINE_V, mem_after=[prod("SRTA100", UA)], tools=[tool], prev_bot=LIST_REPLY, reply=reply)
    return grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "chrome"}))


@pytest.mark.parametrize("header", [
    "Here's what you want: Sorento wash basins with stock (276). That is all 276.",
    "Here's what you want: Sorento wash basins with stock (276). It was 280 when you asked. That is all 276.",
])
def test_r28c_a_last_page_header_with_no_rows_is_not_a_set_header(header):
    res = _r28c_refine(header)
    assert "set_list" not in res["checks"], res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r28c_a_first_line_with_other_text_after_the_count_is_not_a_set_header():
    res = _r28b_spec("Here's what you want: basins (40). Reply 1 to pick.\n" + _r28_entries(10))
    assert "set_list" not in res["checks"], res["checks"]


def test_r28c_the_bots_own_moved_count_trailer_stays_a_set_header():
    reply = "Here's what you want: basins (40, showing 11 to 20). It was 42 when you asked.\n" + _r28_entries(10)
    assert _r28b_spec(reply)["checks"].get("set_list") is True


@pytest.mark.parametrize("reply", [
    "The weight of this product is not available.",
    "Weight information is not available for SRTKT1636SS-GM.",
])
def test_r28c_a_none_phrase_with_a_few_words_after_the_attribute_is_an_honest_none(reply):
    res = grade_turn(_r28_attr_row("SRTKT1636SS-GM is a wall hung tap. " + reply), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is True, res["checks"]


def test_r28c_an_unrelated_none_after_a_question_stays_not_a_none():
    res = grade_turn(_r28_attr_row("Weight of SRTKT1636SS-GM? Promotion: none."), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is False, res["checks"]


def test_r28c_numbered_blocks_that_each_carry_a_code_led_row_count_once():
    block = "{i}. *Product Code:* BRBC{n}W\nBRBC{n}W x 2: Available"
    reply = "Here's what you want: basins (38)\n" + "\n".join(block.format(i=i, n=22100 + i) for i in range(1, 34))
    res = _r28b_spec(reply)
    assert res["checks"].get("set_list") is False, res["checks"]
    assert res["reason"] == "The list is cut short without saying how to see more.", res["reason"]


def test_r28c_a_leak_fails_a_spec_tier_ask_whose_found_products_are_on_spec():
    r = _r28_tier_row(R28_UUIDS, R28_SPEC_CODES, reply=f"{R28_TIER_ASK}\nref {UA}")
    res = grade_turn(r, item("spec", spec_products=R28_SPEC_CODES, **SPEC_ITEM))
    assert res["passed"] is False and res["reason"] == "The reply shows an internal id.", (res["passed"], res["reason"])


# round 29: owner ruling, a how-many ask fails only when it replaces a files, promo or spec-info answer
QTY_BLOCKED = "The bot asked how many instead of giving the answer."
TIER3_Q = {"kind": "tier_pick", "expects": "choice", "payload": {}, "asked_at_turn": 1,
           "options": [{"label": "Office"}, {"label": "Dealer"}, {"label": "End user"}]}


def test_r29_a_how_many_ask_of_unknown_type_passes_on_a_scope_item():
    r = row2(message="u help me check", reply="How many units of SRTWF12C-NEW?",
             prev_bot="Sorry, that isn't under your account. I can only check on CUSTOMER A.")
    res = grade_turn(r, item("scope"))
    assert res["checks"].get("qty_ask_ok") is True, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r29_a_how_many_ask_with_an_answered_promo_file_passes_a_tier_answer():
    reply = ("How many units do you need?\n\nI have attached the file(s) below.\n\n"
             "1. *Promotion:* CABANA COMBINE PROMO DEALER_08072026.pdf")
    r = row2(message="Dealer", verdict=_picks([2]), state_oq=TIER3_Q, oq={"before": TIER3_Q, "after": None, "writer": "engine"},
             prev_bot="Which access level do you need?\n1. Office\n2. Dealer\n3. End user", reply=reply, files=1)
    res = grade_turn(r, item("followup", "answer", option="dealer"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r29_a_stock_ask_in_detailed_mode_may_ask_how_many():
    res = grade_turn(_stock_lookup("detailed", "SRTA100 has 40 pcs. How many units?"), item("code_lookup", **GOLD_CL))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29_a_stock_ask_with_no_stock_lookup_may_ask_how_many():
    r = row2(message="stock of srta100", reply="How many units of SRTA100?")
    res = grade_turn(r, item("scope"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29_a_files_ask_answered_only_by_how_many_fails_with_the_new_reason():
    r = row2(message="send me the brochure of SRTMCB21616", reply="How many units of SRTMCB21616?")
    res = grade_turn(r, item("scope", fact="product_attachment", fact_label="files"))
    assert res["checks"].get("no_qty_ask") is False, res["checks"]
    assert res["passed"] is False and res["reason"] == QTY_BLOCKED, res["reason"]


def test_r29_a_spec_item_that_lists_products_with_a_how_many_ask_passes():
    reply = "How many units for each?\n1. SPSRTSS1217 - \n2. SRTWT2634-BL - "
    r = row2(message="white tap with soft close", verdict=SPEC_V, reply=reply)
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29_a_spec_item_with_no_stock_word_answered_only_by_how_many_fails():
    r = row2(message="white tap with soft close", verdict=SPEC_V, reply="How many units of SRTWC8152-SH?")
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]


def test_r29_a_promotion_ask_answered_only_by_how_many_fails():
    r = row2(message="any promo for SRTA100", reply="How many units do you need?")
    res = grade_turn(r, item("scope", fact="promotion"))
    assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]


def test_r29_an_attribute_ask_answered_only_by_how_many_fails():
    r = row2(message="SRTKT1636SS-GM check weight ?", reply="How many units of SRTKT1636SS-GM?")
    res = grade_turn(r, item("scope", fact_label="weight"))
    assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]


# the set header may sit below a prefix line the bot writes (turn_runtime.py:4105)
def test_r29_a_set_header_below_a_prefix_line_is_found_and_a_summary_under_it_fails():
    reply = ("pink does not match gunmetal - dropped, showing gunmetal on its own:\n\n"
             "Here's what you want: gunmetal wash basins with stock (550)\n• Sorento gunmetal wash basins: 282")
    res = _r28c_refine(reply)
    assert res["checks"].get("set_list") is False, res["checks"]
    assert res["passed"] is False and res["reason"] == "The set reply showed no product.", res["reason"]


def test_r29_a_what_you_want_line_below_a_prefix_with_no_count_is_not_a_set_header():
    reply = "Showing gunmetal on its own:\n\nHere's what you want: gunmetal wash basins\n1. *Product Code:* BRBC22101W"
    res = _r28b_spec(reply)
    assert "set_list" not in res["checks"], res["checks"]


# a number in the words after the attribute is a value, not a none statement
def test_r29_a_number_after_the_attribute_word_is_a_value_even_beside_a_none_phrase():
    result = _r28_result([{"key": "spec:weight", "label": "Weight", "value": "25 kg"}])
    res = grade_turn(_r28_attr_row("SRTKT1636SS-GM weight 25 kg not available in stock.", result), item("code_lookup", **R28_GOLD))
    assert res["checks"]["attribute_value"] is True, res["checks"]


# "no answer" = once the how-many sentence is removed, no number outside a product code, no listed entry, no file
def test_r29_a_price_answer_beside_a_how_many_is_an_answer():
    r = row2(message="any promo for SRTA100", reply="The promo price is RM 90. How many units?")
    res = grade_turn(r, item("scope", fact="promotion"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29_a_product_name_alone_beside_a_how_many_is_not_an_answer():
    r = row2(message="send me the brochure of SRTMCB21616", reply="Product: SRTMCB21616\nHow many units?")
    res = grade_turn(r, item("scope", fact="product_attachment", fact_label="files"))
    assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]


def test_r29_a_code_led_entry_beside_a_how_many_is_an_answer():
    r = row2(message="white tap with soft close", verdict=SPEC_V, reply="Here are white taps:\nSRTKT100\nSRTKT200\nHow many units?")
    res = grade_turn(r, item("spec", **SPEC_ITEM))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29_a_file_sent_beside_a_how_many_is_an_answer():
    r = _att([_file("Technical Specifications")], reply="How many units?")
    res = grade_turn(r, item("scope", fact="product_attachment", fact_label="files"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


# round 29b: a spec item whose catalogue holds nothing is graded on an honest none
R29B_ITEM = dict(SPEC_ITEM, none_on_record=True, near=["SRTWT2234", "SRTWT2235"])
R29B_NONE_REASON = "The bot did not say that no product matches the spec."


def _r29b(reply, gold=R29B_ITEM):
    return grade_turn(row2(message="any double flexible kitchen tap in chrome for mocha or sorento lite", verdict=SPEC_V,
                           reply=reply), item("spec", **gold))


@pytest.mark.parametrize("reply", [
    "Here's what you want: chrome double flexible kitchen taps\n\nBut none matched. Please refer to your salesman.",
    "Here's what you want: chrome double flexible kitchen taps\n\nBut no chrome double flexible kitchen taps matched these.\n"
    "Closest: SRTWT2234, SRTWT2235",
])
def test_r29b_an_honest_none_passes_a_none_on_record_spec_item(reply):
    res = _r29b(reply)
    assert res["checks"].get("none_on_record") is True and "shown" not in res["checks"], res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r29b_an_unknown_word_dead_end_is_not_a_none():
    reply = "Here's what you want: tap\n\nCouldn't find: \"kitchan\". Would you like me to escalate to purchasing team?"
    res = _r29b(reply)
    assert res["checks"].get("none_on_record") is False, res["checks"]
    assert res["passed"] is False and res["reason"] == R29B_NONE_REASON, res["reason"]


def test_r29b_without_none_on_record_a_none_reply_still_fails_on_shown():
    res = _r29b("Here's what you want: chrome taps\n\nBut none matched. Please refer to your salesman.", SPEC_ITEM)
    assert res["checks"].get("shown") is False and "none_on_record" not in res["checks"], res["checks"]
    assert res["passed"] is False and res["reason"] == "The reply showed no product.", res["reason"]


# round 29c: the bot's own quantity slots and menus are not an answer
QTY_BLOCKED_29C = "The bot asked how many instead of giving the answer."


def _r29c(reply, slice_="scope", message="any promo for SRTA100 and SRTA200", **gold):
    return grade_turn(row2(message=message, reply=reply), item(slice_, **gold))


def _r29c_blocked(res):
    assert res["checks"].get("no_qty_ask") is False and res["passed"] is False, res["checks"]
    assert res["reason"] == QTY_BLOCKED_29C, res["reason"]


def test_r29c_quantity_slot_lines_are_not_a_promotion_answer():
    _r29c_blocked(_r29c("How many units for each?\n1. SRTA100 - \n2. SRTA200 - ", fact="promotion"))


def test_r29c_a_filled_slot_number_is_not_a_files_answer():
    _r29c_blocked(_r29c("How many units for each?\n1. SRTA100 - 10\n2. SRTA200 - ",
                        message="send the brochures of SRTA100 and SRTA200", fact="product_attachment", fact_label="files"))


def test_r29c_menu_option_lines_are_not_a_promotion_answer():
    _r29c_blocked(_r29c("How many units do you need?\nWhich access level?\n1. Office\n2. Dealer", fact="promotion"))


def test_r29c_a_chinese_how_many_is_a_how_many():
    _r29c_blocked(_r29c("SRTA100 需要多少件？", fact="promotion"))


def test_r29c_a_code_and_the_question_on_one_line_is_not_an_answer():
    _r29c_blocked(_r29c("SRTMCB21616: how many units do you need?", message="send the brochure of SRTMCB21616",
                        fact="product_attachment", fact_label="files"))


def test_r29c_an_attachment_item_with_no_fact_answered_only_by_how_many_fails():
    res = grade_turn(row2(message="send the brochure of SRTMCB21616", reply="How many units of SRTMCB21616?"),
                     item("attachment"))
    assert res["checks"].get("no_qty_ask") is False, res["checks"]


def test_r29c_a_promotion_answer_block_beside_a_how_many_is_an_answer():
    reply = "How many units do you need?\n\n1. *Promotion:* CABANA COMBINE PROMO DEALER_08072026.pdf"
    res = _r29c(reply, message="any promo for SRTA100", fact="promotion")
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


# round 29d: a spec refine answered by a how-many, and a filter held before the turn, do not pass
R29D_PREV = "CB94SS-BL x 20: ✅ Only 1 got stock."
R29D_SPEC = {"entities": [{"raw": "chrome", "hint": "specification", "spec_key": "finish", "spec_value": "chrome"}],
             "reference_positions": []}
R29D_BRAND = {"entities": [{"raw": "sorento", "hint": "brand", "canonical_code": "sorento"}], "reference_positions": []}


def _r29d(reply, *, held_before, args=None, verdict=R29D_SPEC, extra_value="chrome", flt=None, brands=()):
    tool = {"name": "crm_inventory_list", "args": args if args is not None else {"product_ids": [UA]},
            "envelope": {"items": [{"code": "CB94SS-BL", "qty": 1}]}}
    spec = [{"spec_value": extra_value, "raw": extra_value}] if extra_value else []
    r = row2(message="in chrome", verdict=verdict, mem_before=[prod("CB94SS-BL", UA)], mem_after=[prod("CB94SS-BL", UA)],
             mem_extra={"specification": spec}, mem_brands=list(brands), tools=[tool], prev_bot=R29D_PREV, reply=reply)
    for rec in r["trace"]:
        if rec.get("kind") == "memory":
            rec["focus"]["before"]["extra"] = {"specification": spec if held_before else []}
    flt = flt or {"key": "spec", "kind": "spec", "value": "chrome"}
    return grade_turn(r, item("followup", "refine", subject_after=["CB94SS-BL"], filter=flt))


def test_r29d_a_spec_refine_answered_by_a_how_many_alone_fails():
    res = _r29d("How many units of CB94SS-BL?", held_before=True)
    assert res["checks"].get("no_qty_ask") is False, res["checks"]
    assert res["passed"] is False and res["reason"] == "The bot asked how many instead of giving the answer.", res["reason"]


def test_r29d_a_filter_held_before_the_turn_is_applied_only_when_a_lookup_carries_it():
    res = _r29d("CB94SS-BL x 20: ❌ No stock.", held_before=True)
    assert res["checks"].get("applied") is False, res["checks"]
    assert res["passed"] is False


def test_r29d_a_filter_new_this_turn_and_held_after_is_applied_from_memory():
    res = _r29d("CB94SS-BL x 20: ❌ No stock.", held_before=False)
    assert res["checks"].get("applied") is True, res["checks"]


def test_r29d_a_filter_held_before_and_carried_in_the_lookup_args_is_applied():
    res = _r29d("CB94SS-BL x 20: ❌ No stock.", held_before=True, args={"product_ids": [UA], "scope_terms": ["chrome"]})
    assert res["checks"].get("applied") is True, res["checks"]


def test_r29d_a_brand_refine_answered_by_a_how_many_alone_is_unchanged():
    res = _r29d("How many units of CB94SS-BL?", held_before=False, verdict=R29D_BRAND, extra_value=None, brands=["sorento"],
                flt={"key": "brand", "kind": "brand", "value": "sorento"})
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


# round 29e: the bot's own value-miss reply is an honest none; loose none phrases are not
@pytest.mark.parametrize("reply", [
    "Here's what you want: double flexible kitchen taps with chrome\n• Sorento double flexible kitchen taps: 2\n\n"
    "Couldn't find: chrome (finish or colour).",
    "Here's what you want: double flexible kitchen taps with chrome\n• Sorento double flexible kitchen taps: 2\n\n"
    "Couldn't find: double flexible (spout type).",
])
def test_r29e_a_value_miss_reply_with_a_label_is_an_honest_none(reply):
    res = _r29b(reply)
    assert res["checks"].get("none_on_record") is True, res["checks"]


@pytest.mark.parametrize("reply", [
    "Here's what you want: chrome kitchen taps (2)\n1. *Product Code:* SRTWT2234\n2. *Product Code:* SRTWT2235\n\n"
    "Sorry, we don't have stock for SRTWT2235.",
    "No worries, these matched your filters.",
])
def test_r29e_a_stock_line_or_a_loose_phrase_is_not_a_none_for_the_spec(reply):
    res = _r29b(reply)
    assert res["checks"].get("none_on_record") is False, res["checks"]


def test_r29e_the_quoted_unknown_word_line_stays_a_dead_end():
    res = _r29b("Here's what you want: tap\n\nCouldn't find: \"kitchan\". Would you like me to escalate to purchasing team?")
    assert res["checks"].get("none_on_record") is False, res["checks"]


def test_r29e_a_code_led_value_row_beside_a_how_many_is_an_answer_on_a_files_item():
    r = row2(message="send the brochure of SRTA100", reply="How many units?\n1. SRTA100 x 2: Available")
    res = grade_turn(r, item("scope", fact="product_attachment", fact_label="files"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29e_a_code_led_price_row_beside_a_how_many_is_an_answer_on_a_promo_item():
    r = row2(message="any promo for SRTA100", reply="How many units?\n1. SRTA100: RM 90")
    res = grade_turn(r, item("scope", fact="promotion"))
    assert res["checks"].get("qty_ask_ok") is True and "no_qty_ask" not in res["checks"], res["checks"]


def test_r29e_a_chinese_price_question_is_not_a_how_many():
    r = row2(message="any promo for SRTA100", reply="SRTA100 多少钱？RM 90")
    res = grade_turn(r, item("scope", fact="promotion"))
    assert res["checks"].get("no_qty_ask") is True and "qty_ask_ok" not in res["checks"], res["checks"]


# round 30: rootcause G-CODE and G-BLIND (a switch on the held subject, a filter phrase across entities)
R30_HELD = ["SRTWCFT150-OP", "SRTWCFT151-OP"]


def _r30_switch(gold_codes, held, **gold):
    mem = [prod(c, f"{i}{i}{i}{i}{i}{i}{i}{i}-aaaa-4aaa-8aaa-aaaaaaaaaaaa") for i, c in enumerate(held, 1)]
    r = row2(mem_before=mem, mem_after=mem, state_products=mem, tools=[TOOL_A])
    return grade_turn(r, item("followup", "switch", subject_after=list(gold_codes), **gold))


def test_r30_a_fact_switch_on_the_products_already_held_is_a_change():
    res = _r30_switch(R30_HELD, R30_HELD)
    assert res["checks"]["changed"] is True, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_r30_a_new_code_that_memory_never_took_up_is_still_no_change():
    res = _r30_switch(["SRTNEW1"], ["SRTOLD1"])
    assert res["checks"]["changed"] is False, res["checks"]


R30_REFINE_GOLD = dict(subject_after=["SRTWC100"])
R30_REPLY_SMART = "Here's what you want: intelligent / smart toilet water closets (47)\n\n1. *Product Code:* SRTWC100"


def _r30_refine(reply, flt, entities, *, extra=None, omitted=False):
    tool = {"name": "crm_master_products_list", "args": {"product_ids": [UA]}, "envelope": {"items": [{"code": "SRTWC100"}]}}
    verdict = {"entities": entities, "reference_positions": []}
    r = row2(message="Check intelligent toilet models", verdict=verdict, apply_omitted=omitted, mem_before=[prod("SRTWC100", UA)],
             mem_after=[prod("SRTWC100", UA)], mem_extra=extra or {}, tools=[tool], prev_bot="Older list: SRTWC100.", reply=reply)
    return grade_turn(r, item("followup", "refine", filter=flt, **R30_REFINE_GOLD))


SMART_ENTITIES = [{"raw": "toilet", "hint": "category"},
                  {"raw": "intelligent", "hint": "specification", "spec_key": "is_smart", "spec_value": True}]
SMART_EXTRA = {"specification": [{"spec_key": "is_smart", "spec_value": True, "raw": "intelligent", "spec_label": "Intelligent / smart toilet"}],
               "category": [{"raw": "toilet", "hint": "category"}]}   # the real 75e516bd40 memory-after extra


def test_r30_a_filter_phrase_split_across_a_category_and_a_specification_entity_is_applied():
    res = _r30_refine(R30_REPLY_SMART, {"key": "spec", "value": "intelligent toilet"}, SMART_ENTITIES, extra=SMART_EXTRA)
    assert res["checks"].get("filter") is True and res["checks"].get("applied") is True, res["checks"]


def test_r30_a_filter_phrase_with_only_one_of_its_words_read_is_not_credited():
    res = _r30_refine(R30_REPLY_SMART, {"key": "spec", "value": "intelligent toilet"}, [{"raw": "toilet", "hint": "category"}])
    assert res["checks"].get("filter") is False, res["checks"]


def test_r30_an_omitted_apply_payload_takes_the_filter_words_from_the_sets_own_header():
    res = _r30_refine("Here's what you want: P trap water closets (217)\n1. *Product Code:* SRTWC100",
                      {"key": "spec", "value": "p_trap"}, [], omitted=True)
    assert res["checks"].get("filter") is True and res["checks"].get("applied") is True, res["checks"]


def test_r30_a_header_that_does_not_name_the_filter_gives_no_credit():
    res = _r30_refine("Here's what you want: water closets (217)\n1. *Product Code:* SRTWC100",
                      {"key": "spec", "value": "p_trap"}, [], omitted=True)
    assert res["checks"].get("filter") is not True and res["checks"].get("applied") is not True, res["checks"]


def test_r30_an_ambiguous_gold_code_allows_a_picker_after_an_and_x_turn():
    q = {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
         "options": [{"label": "SRTWC286A", "code": "SRTWC286A"}, {"label": "SRTWC286B", "code": "SRTWC286B"}]}
    r = row2(oq={"before": None, "after": q, "writer": "engine"}, mem_before=[prod("SRTOLD1", UB)], mem_after=[prod("SRTWC286A", UA)],
             tools=[TOOL_A])
    res = grade_turn(r, item("followup", "and_x", fact="inventory", subject_after=["SRTWC286"], ambiguous=True))
    assert "no_picker" not in res["checks"], res["checks"]


# round 30b: a filter word must be carried, not just read; header credit only for an omitted payload
BLACK_SPEC = {"raw": "black", "hint": "specification", "spec_key": "finish", "spec_value": "black"}
R30B_LIST = "1. *Product Code:* SRTWC100"


def _r30b(flt, entities, tools, *, reply=R30B_LIST, extra=None, extra_before=None, omitted=False):
    tl = [{"name": n, "args": a, "envelope": {"items": []}} for n, a in tools]
    r = row2(message="refine", verdict={"entities": entities, "reference_positions": []}, apply_omitted=omitted,
             mem_before=[prod("SRTWC100", UA)], mem_after=[prod("SRTWC100", UA)], mem_extra=extra or {}, tools=tl,
             prev_bot="Older list: SRTWC100.", reply=reply)
    if extra_before is not None:
        for rec in r["trace"]:
            if rec.get("kind") == "memory" and isinstance(rec.get("focus"), dict) and "before" in rec["focus"]:
                rec["focus"]["before"]["extra"] = extra_before
    return grade_turn(r, item("followup", "refine", filter=flt, subject_after=["SRTWC100"]))


def test_r30b_a_word_read_only_inside_a_category_phrase_is_not_applied():
    res = _r30b({"key": "spec", "value": "black matt"}, [BLACK_SPEC, {"raw": "matt basin", "hint": "category"}],
                [("crm_master_products_list", {"product_ids": [UA], "scope_terms": ["black"]})],
                extra={"specification": [{"spec_value": "black", "raw": "black"}]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30b_a_filter_read_only_in_a_category_entity_with_nothing_carried_is_not_applied():
    res = _r30b({"key": "spec", "value": "chrome"}, [{"raw": "chrome tap", "hint": "category"}],
                [("crm_master_products_list", {"product_ids": [UA]})])
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30b_header_credit_needs_an_omitted_payload_and_a_value_new_this_turn():
    res = _r30b({"key": "spec", "value": "chrome"}, [], [("crm_master_products_list", {"product_ids": [UA]})],
                reply="Here's what you want: chrome taps (3)\n" + R30B_LIST,
                extra_before={"specification": [{"spec_value": "chrome", "raw": "chrome"}]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30b_words_pooled_across_different_tools_do_not_make_a_carried_phrase():
    res = _r30b({"key": "spec", "value": "black matt"}, [BLACK_SPEC],
                [("crm_master_products_list", {"product_ids": [UA], "scope_terms": ["black"]}),
                 ("crm_inventory_list", {"product_ids": [UA], "query": "matt"})],
                extra={"specification": [{"spec_value": "black", "raw": "black"}]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30b_the_whole_filter_phrase_in_one_argument_string_is_carried():
    res = _r30b({"key": "spec", "value": "black matt"}, [BLACK_SPEC],
                [("crm_master_products_list", {"product_ids": [UA], "scope_terms": ["black matt basin"]})])
    assert res["checks"].get("applied") is True, res["checks"]


# round 30c: one lookup argument carries a phrase; pins for header credit
def test_r30c_header_credit_needs_the_payload_omitted_even_when_the_value_is_new():
    res = _r30b({"key": "spec", "value": "chrome"}, [], [("crm_master_products_list", {"product_ids": [UA]})],
                reply="Here's what you want: chrome taps (3)\n" + R30B_LIST)
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30c_header_credit_never_covers_a_value_held_before_the_turn():
    res = _r30b({"key": "spec", "value": "chrome"}, [], [("crm_master_products_list", {"product_ids": [UA]})],
                reply="Here's what you want: chrome taps (3)\n" + R30B_LIST, omitted=True,
                extra_before={"specification": [{"spec_value": "chrome", "raw": "chrome"}]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30c_every_filter_word_in_one_argument_list_of_one_call_is_carried():
    res = _r30b({"key": "spec", "value": "black matt"}, [BLACK_SPEC],
                [("crm_master_products_list", {"product_ids": [UA], "scope_terms": ["black", "matt"]})])
    assert res["checks"].get("applied") is True, res["checks"]


def test_r30c_words_in_different_keys_of_the_same_call_are_not_pooled():
    res = _r30b({"key": "spec", "value": "black matt"}, [BLACK_SPEC],
                [("crm_master_products_list", {"product_ids": [UA], "scope_terms": ["black"], "query": "matt"})])
    assert res["checks"].get("applied") is False, res["checks"]


# round 30d: a carried filter word is a whole word
def _r30d(value, args):
    return _r30b({"key": "spec", "value": value}, [], [("crm_master_products_list", dict(args))])


def test_r30d_a_filter_word_inside_a_longer_word_is_not_carried():
    res = _r30d("p_trap", {"scope_terms": ["s trap water closet"]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30d_digits_inside_a_product_uuid_are_not_a_carried_size():
    res = _r30d("300mm", {"product_ids": ["4d3006aa-1b2c-4d5e-8f90-123456789abc"]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30d_digits_inside_a_product_code_are_not_a_carried_size():
    res = _r30d("300mm", {"product_codes": ["SRTWB3001"]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30d_a_whole_word_size_in_a_search_term_is_carried():
    res = _r30d("300mm", {"scope_terms": ["300 mm basin"]})
    assert res["checks"].get("applied") is True, res["checks"]


def test_r30d_two_filter_words_in_one_argument_list_are_carried():
    res = _r30d("black matt", {"scope_terms": ["black", "matt"]})
    assert res["checks"].get("applied") is True, res["checks"]


def test_r30d_the_whole_phrase_in_one_string_is_carried():
    res = _r30d("black matt", {"scope_terms": ["black matt basin"]})
    assert res["checks"].get("applied") is True, res["checks"]


# round 30e: product codes and uuids are not filter words
def test_r30e_a_size_number_inside_a_product_code_is_not_carried():
    res = _r30d("600mm", {"product_codes": ["SRTWB600-W"]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30e_a_size_number_inside_a_product_uuid_is_not_carried():
    res = _r30d("600mm", {"product_ids": ["4d3a600b-1b2c-4d5e-8f90-123456789abc"]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30e_a_letter_inside_a_product_code_does_not_make_a_filter_word():
    res = _r30d("p_trap", {"scope_terms": ["SRTWC8354-SH-UF-P", "s trap"]})
    assert res["checks"].get("applied") is False, res["checks"]


def test_r30e_a_size_without_a_space_in_a_search_term_is_carried():
    res = _r30d("300mm", {"scope_terms": ["300mm basin"]})
    assert res["checks"].get("applied") is True, res["checks"]


def test_r30e_a_uuid_segment_is_not_a_carried_filter_word():
    res = _r30d("1234", {"product_ids": ["4d3a600b-1234-4d5e-8f90-123456789abc"]})
    assert res["checks"].get("applied") is False, res["checks"]


@pytest.mark.parametrize("term", ["ptrap water closet", "p trap water closet"])
def test_r30e_a_phrase_spelled_with_or_without_a_space_is_carried(term):
    res = _r30d("p_trap", {"scope_terms": [term]})
    assert res["checks"].get("applied") is True, res["checks"]


# round 30f: a decimal size stays one number
def test_r30f_a_decimal_size_with_its_unit_in_a_search_term_is_carried():
    res = _r30d("1.5m", {"scope_terms": ["1.5m bathtub"]})
    assert res["checks"].get("applied") is True, res["checks"]


def test_r30f_a_decimal_size_with_a_spaced_unit_is_carried():
    res = _r30d("1.2m", {"scope_terms": ["1.2 m basin"]})
    assert res["checks"].get("applied") is True, res["checks"]


def test_r30f_a_different_number_is_not_the_decimal():
    res = _r30d("1.5m", {"scope_terms": ["15 m"]})
    assert res["checks"].get("applied") is False, res["checks"]
