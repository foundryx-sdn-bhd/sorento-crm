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

from scripts.chatbot_eval.targets import grade_turn, made_up_facts
from tests.chatbot_eval.test_targets import check, item, ent  # noqa: F401  (helpers only)

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
    assert "class" in r["checks"], "class keys are reported as their own check"
    wrong_class = grade_turn(row2(verdict=v, mem_extra={"specification": bot}),
                             item("spec", spec=[{"key": "class", "value": "tap"}, {"key": "mounting", "value": "under_counter"}]))
    assert wrong_class["passed"] is True and wrong_class["checks"]["class"] is False
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
    assert "old_dropped" in res["checks"], "old_dropped stays reported"
    sw = grade_turn(row2(message="stock of srtb200", **both), item("followup", "switch", subject_after=["SRTB200"]))
    check(sw, True)
    assert "old_dropped" in sw["checks"]


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
