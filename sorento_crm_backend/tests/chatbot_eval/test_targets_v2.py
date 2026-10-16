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
    ok = row2(verdict=v, state_oq=q, oq={"before": q, "after": None, "writer": "engine"})
    check(grade_turn(ok, item("followup", "answer", option="white")), True)
    bad = row2(verdict=v, state_oq=q, oq={"before": q, "after": q, "writer": "engine"})
    check(grade_turn(bad, item("followup", "answer", option="white")), False, "again")


def test_refine_with_a_brand_entity_and_memory_extra():
    v = {"entities": [{"raw": "sorento", "hint": "brand", "canonical_code": "Sorento"}], "reference_positions": []}
    gold = dict(subject_after=["SRTA100"], filter={"kind": "brand", "value": "Sorento"})
    ok = row2(verdict=v, mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], mem_brands=["Sorento"])
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
