"""EVAL-BENCH round 35 (analyst spec r35_spec.md): grader rules (a) (c) (d) (e) (f) (h) (i) (j) (l) (n).

`test_red_*` fail on the round 34 grader for the reason the spec gives; `test_guard_*` pass before and after.
Synthetic rows only; TRAIN ids appear in comments only. New helper names are reached through `grade_turn` /
`made_up_facts`, never imported, so a red fails on its assertion.
"""
from __future__ import annotations

import json

from scripts.chatbot_eval.targets import grade_turn, made_up_facts
from tests.chatbot_eval.test_targets import _att, _file, item, GOLD_TS, STAFF
from tests.chatbot_eval.test_targets_v2 import (TOOL_A, TOOL_B, UA, UB, VARIANT_Q, YES_Q, _with_oq, prod, row2)

UC = "33333333-3333-4333-8333-333333333333"
TOOL_AB = {"name": "crm_inventory_list", "args": {"product_ids": [UA, UB]}}
DEALER = {"audience": "dealer", "linked_customer_ids": ["c1"], "linked_customer_names": ["Acme"], "hidden_fields": []}
REAL_NOTE = "payload omitted: 40000 bytes exceeds the 32768 byte cap"


def _note(r, note):
    for rec in r["trace"]:
        if rec.get("kind") == "apply":
            rec["note"] = note
    return r


def _memory(r):
    return next(rec for rec in r["trace"] if rec.get("kind") == "memory")


# ------------------------------------------------------------------ (a) staff stock asks on a code family
FAM = dict(fact="inventory", code_unresolved=True, family=["SRTF1-A", "SRTF1-B"])


def test_red_a_dead_end_on_a_staff_family_stock_ask_fails():
    r = row2(message="stock of srtf1", reply="Couldn't find SRTF1.", mem_after=[], tools=[])
    res = grade_turn(r, item("code_lookup", **FAM))
    assert res["passed"] is False, res["checks"]   # today True: nothing checks the family


def test_red_a_partial_family_answer_fails():
    r = row2(message="stock of srtf1", reply="SRTF1-A is in stock.", mem_after=[prod("SRTF1-A", UA)], tools=[TOOL_A])
    res = grade_turn(r, item("code_lookup", **FAM))
    assert res["passed"] is False, res["checks"]


def test_guard_a_full_family_stock_answer_passes():
    r = row2(message="stock of srtf1", reply="SRTF1-A is in stock. SRTF1-B is in stock.",
             mem_after=[prod("SRTF1-A", UA), prod("SRTF1-B", UB)], tools=[TOOL_AB])
    res = grade_turn(r, item("code_lookup", **FAM))
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_a_gold_without_family_is_unchanged():
    gold = {k: v for k, v in FAM.items() if k != "family"}
    r = row2(message="stock of srtf1", reply="Couldn't find SRTF1.", mem_after=[], tools=[])
    assert grade_turn(r, item("code_lookup", **gold))["passed"] is True   # no family: no new check


def test_guard_a_ambiguous_gold_still_wants_the_question():
    gold = dict(fact="inventory", ambiguous=True)
    asked = _with_oq(row2(message="stock of srtf1", reply="Which one?", mem_after=[]), VARIANT_Q, asking="product_pick")
    res = grade_turn(asked, item("code_lookup", asker=DEALER, **gold))
    assert res["checks"]["clarified"] is True, res["checks"]
    plain = row2(message="stock of srtf1", reply="SRTF1-A is in stock.", mem_after=[prod("SRTF1-A", UA)], tools=[TOOL_A])
    assert grade_turn(plain, item("code_lookup", asker=DEALER, **gold))["checks"]["clarified"] is False


# ------------------------------------------------------------------ (c) a set code is resolved and looked up
def _set_row(**kw):
    base = dict(message="srtset1-p", reply="*Set SRTSET1-P*\nSRTM1: in stock\nSRTM2: in stock", domains=("master_products",),
                mem_after=[prod("SRTM1", UA, set_code="SRTSET1-P"), prod("SRTM2", UB, set_code="SRTSET1-P")],
                tools=[{"name": "crm_master_products_list", "args": {"product_ids": [UA, UB]}}])
    base.update(kw)
    return row2(**base)


def test_red_c_set_answer_resolves_the_set_code():
    res = grade_turn(_set_row(), item("code_lookup", code="SRTSET1-P", fact="master_products"))
    assert res["checks"]["code"] is True and res["checks"]["tool_ran"] is True, res["checks"]
    assert res["passed"] is True, res["checks"]


def test_guard_c_another_sets_gold_code_fails():
    res = grade_turn(_set_row(), item("code_lookup", code="SRTSET9-P", fact="master_products"))
    assert res["checks"]["code"] is False and res["passed"] is False, res["checks"]


def test_guard_c_a_set_only_parked_in_extra_without_a_lookup_fails():
    parked = {"raw": "SRTSET1-P", "canonical_code": "SRTSET1-P"}
    r = _set_row(mem_after=[], mem_extra={"product_set": [parked]}, tools=[TOOL_B])
    res = grade_turn(r, item("code_lookup", code="SRTSET1-P", fact="master_products"))
    assert res["checks"]["code"] is False and res["passed"] is False, res["checks"]


def test_guard_c_a_member_gold_code_passes_as_today():
    res = grade_turn(_set_row(), item("code_lookup", code="SRTM1", fact="master_products"))
    assert res["checks"]["code"] is True and res["checks"]["tool_ran"] is True, res["checks"]


def test_guard_c_a_set_held_only_before_does_not_count():
    r = _set_row(mem_before=[prod("SRTM1", UA, set_code="SRTSET1-P")], mem_after=[], tools=[TOOL_A])
    res = grade_turn(r, item("code_lookup", code="SRTSET1-P", fact="master_products"))
    assert res["checks"]["code"] is False, res["checks"]


# ------------------------------------------------------------------ (h) a looked-up set is the new subject
def _switch_row(**kw):
    base = dict(message="srtset1", reply="*Set SRTSET1*", mem_before=[prod("SRTOLD1", UB)],
                mem_after=[prod("SRTM1", UA, set_code="SRTSET1"), prod("SRTM2", UC, set_code="SRTSET1")], tools=[TOOL_A])
    base.update(kw)
    return row2(**base)


def test_red_h_looked_up_set_is_the_new_subject():
    res = grade_turn(_switch_row(), item("followup", "switch", subject_after=["SRTSET1"]))
    assert res["checks"]["new_subject"] is True, res["checks"]   # old_dropped may still be False: members stay in memory


def test_guard_h_a_parked_set_nothing_looked_up_is_not_the_subject():
    parked = {"raw": "SRTSET1", "canonical_code": "SRTSET1"}
    r = row2(message="srtset1", reply="That would search every stock we have.", mem_before=[prod("SRTOLD1", UB)],
             mem_after=[prod("SRTOLD1", UB)], mem_extra={"product_set": [parked]}, tools=[TOOL_B])
    res = grade_turn(r, item("followup", "switch", subject_after=["SRTSET1"]))
    assert res["checks"]["new_subject"] is False, res["checks"]


# ------------------------------------------------------------------ (d) the capped payload note is a prefix
def test_red_d_made_up_waived_when_capped_and_no_results():
    r = _note(row2(apply_omitted=True, reply="SRTA100 has 99 pcs.", tools=[TOOL_A]), REAL_NOTE)
    assert made_up_facts(r) == [], made_up_facts(r)


def test_red_d_answer_cleared_question_counts():
    q = {"question": "which colour", "options": ["white", "black"]}
    r = _note(row2(apply_omitted=True, state_oq=q, oq={"before": q, "after": None, "writer": "engine"}), REAL_NOTE)
    res = grade_turn(r, item("followup", "answer", option="white"))
    assert res["checks"]["answered"] is True, res["checks"]


def test_red_d_refine_header_names_filter():
    r = _note(row2(apply_omitted=True, reply="Here's what you want: p trap water closets (1)\n1. SRTA100",
                   mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=[TOOL_A]), REAL_NOTE)
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTA100"], filter={"kind": "spec", "value": "p trap"}))
    assert res["checks"]["filter"] is True, res["checks"]


def test_guard_d_the_bare_note_is_still_omitted():
    r = row2(apply_omitted=True, reply="SRTA100 has 99 pcs.", tools=[TOOL_A])
    assert made_up_facts(r) == []


def test_guard_d_another_note_is_not_omitted():
    r = _note(row2(apply_omitted=True, reply="SRTA100 has 99 pcs.", tools=[TOOL_A]), "payload is not JSON-serialisable")
    assert made_up_facts(r) != []


def test_guard_d_full_results_with_the_real_note_still_check_numbers():
    r = _note(row2(apply_omitted=True, reply="SRTA100 has 4321 pcs.", tools=[]), REAL_NOTE)
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {}, "result": json.dumps({"qty": 5})}]
    assert any(f == "made up: 4321" for f in made_up_facts(r)), made_up_facts(r)


# ------------------------------------------------------------------ (e) held_family narrowing counts as applied
GOLD_E = dict(subject_after=["SRTA100-P"], filter={"kind": "spec", "value": "p trap"})


def _held_row(matched=("SRTA100-P",), specs=(("trap_type", "p_trap"),), event=True):
    r = row2(verdict={"entities": [], "reference_positions": []}, mem_before=[prod("SRTA100-P", UA)],
             mem_after=[prod("SRTA100-P", UA)], tools=[TOOL_A])
    _memory(r)["focus"]["before"]["extra"] = {"specification": [{"raw": "p trap", "spec_value": "p_trap"}]}
    if event:
        r["trace"].append({"kind": "held_family", "held": ["SRTA100-P"], "family": ["SRTA100-P"],
                           "matched": list(matched), "specs": [list(s) for s in specs]})
    return r


def test_red_e_held_family_narrowing_counts_as_applied():
    res = grade_turn(_held_row(), item("followup", "refine", **GOLD_E))
    assert res["checks"]["applied"] is True, res["checks"]


def test_guard_e_matched_code_not_looked_up_is_not_applied():
    res = grade_turn(_held_row(matched=("SRTA999",)), item("followup", "refine", **GOLD_E))
    assert res["checks"]["applied"] is False, res["checks"]


def test_guard_e_specs_naming_another_value_is_not_applied():
    res = grade_turn(_held_row(specs=(("trap_type", "s_trap"),)), item("followup", "refine", **GOLD_E))
    assert res["checks"]["applied"] is False, res["checks"]


def test_guard_e_no_event_and_an_id_lookup_is_not_applied():
    res = grade_turn(_held_row(event=False), item("followup", "refine", **GOLD_E))
    assert res["checks"]["applied"] is False, res["checks"]


# ------------------------------------------------------------------ (f) held, none match: an honest none (crew option A)
GOLD_F = dict(subject_after=["CB94SS-BL"], filter={"kind": "spec", "value": "chrome"}, none_on_record=True)
CHROME = [{"raw": "chrome", "hint": "specification", "spec_key": "finish", "spec_value": "chrome"}]


def _none_row(reply, mem_after=None, **kw):
    r = row2(verdict={"entities": CHROME, "reference_positions": []}, reply=reply,
             mem_before=[prod("CB94SS-BL", UA)], mem_after=mem_after or [prod("CB94SS-BL", UA)],
             mem_extra={"specification": [{"raw": "chrome", "spec_value": "chrome"}]}, tools=[TOOL_A], **kw)
    _memory(r)["focus"]["before"]["extra"] = {"specification": [{"raw": "chrome", "spec_value": "chrome"}]}
    return r


def test_red_f_honest_none_about_the_held_product_passes():
    res = grade_turn(_none_row("There is no chrome for CB94SS-BL."), item("followup", "refine", **GOLD_F))
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_f_a_catalogue_wide_list_fails_the_subject():
    r = _none_row("Chrome: CB10A, CB20B.", mem_after=[prod("CB10A", UB), prod("CB20B", UC)])
    res = grade_turn(r, item("followup", "refine", **GOLD_F))
    assert res["passed"] is False and res["checks"]["subject"] is False, res["checks"]


def test_guard_f_a_gold_without_none_on_record_is_judged_as_today():
    gold = {k: v for k, v in GOLD_F.items() if k != "none_on_record"}
    res = grade_turn(_none_row("There is no chrome for CB94SS-BL."), item("followup", "refine", **gold))
    assert res["passed"] is False, res["checks"]


def test_guard_f_an_honest_none_that_names_another_product_fails():
    r = _none_row("There is no chrome for CB94SS-BL, but CB10A is chrome.")
    assert grade_turn(r, item("followup", "refine", **GOLD_F))["passed"] is False


# ------------------------------------------------------------------ (i) a date is one line, never starts after a decimal point
def _results(r, text):
    r["tool_results"] = [{"name": "crm_inventory_list", "args": {}, "result": text}]
    return r


def test_red_i_amount_then_month_line_is_no_date():
    r = _results(row2(reply="*Total:* Qty 10, RM 2,000.15\nNov 2026: Qty 3", tools=[]),
                 "Qty 10; RM 2,000.15; Nov 2026; Qty 3")
    assert made_up_facts(r) == [], made_up_facts(r)


def test_red_i_a_day_never_starts_after_a_decimal_point():
    r = _results(row2(reply="Total RM 7,000.12 Oct 2026", tools=[]), "RM 7,000.12; Oct 2026")
    assert made_up_facts(r) == [], made_up_facts(r)


def test_red_i_a_day_across_a_line_break_is_two_findings():
    found = made_up_facts(_results(row2(reply="Due 12\nNov 2026", tools=[]), "Qty 5"))
    assert "made up: 12" in found and "made up: Nov 2026" in found, found


def test_guard_i_a_one_line_date_not_in_sources_is_made_up():
    assert "made up: 12 Nov 2026" in made_up_facts(_results(row2(reply="Delivered 12 Nov 2026", tools=[]), "Qty 5"))


def test_guard_i_month_first_date_is_still_a_date():
    assert "made up: Nov 12, 2026" in made_up_facts(_results(row2(reply="Due Nov 12, 2026", tools=[]), "Qty 5"))


def test_guard_i_an_amount_not_in_sources_is_made_up():
    assert "made up: 2,000.15" in made_up_facts(_results(row2(reply="Total RM 2,000.15", tools=[]), "Qty 5"))


# ------------------------------------------------------------------ (j) a catalogue ask with no catalogue type in the registry
GOLD_J = dict(none_on_record=True, asked_words=["catalogue", "catalog", "katalog"])


def test_red_j_honest_no_catalogue_passes():
    r = _att([], asked="catalogue", reply="We don't have a catalogue on record.")
    res = grade_turn(r, item("attachment", **GOLD_J))
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_red_j_silence_fails_on_said_none():
    r = _att([], asked="catalogue", reply="Which kind of file do you need?")
    res = grade_turn(r, item("attachment", **GOLD_J))
    assert res["checks"].get("said_none") is False, res["checks"]


def test_guard_j_silence_does_not_pass():
    r = _att([], asked="catalogue", reply="Which kind of file do you need?")
    assert grade_turn(r, item("attachment", **GOLD_J))["passed"] is False


def test_guard_j_a_file_sent_fails_none_on_record():
    r = _att([_file("Direct Access")], asked="catalogue", reply="Here it is.")
    res = grade_turn(r, item("attachment", **GOLD_J))
    assert res["checks"]["none_on_record"] is False and res["passed"] is False, res["checks"]


def test_guard_j_a_gold_with_a_registry_type_behaves_as_today():
    assert grade_turn(_att([_file("Technical Specifications")]), item("attachment", **GOLD_TS))["passed"] is True
    assert grade_turn(_att([]), item("attachment", **GOLD_TS))["passed"] is False


# ------------------------------------------------------------------ (l) refine judged by looked-up ids
GOLD_L = dict(subject_after=[], filter={"kind": "spec", "value": "intelligent toilet"},
              filter_products=["SRTA100", "SRTA200", "SRTA300"])
CAT = [{"raw": "Water Closet", "hint": "category", "canonical_code": None}]


def _ids_row(ids, codes):
    r = row2(verdict={"entities": CAT, "reference_positions": []}, mem_before=[], mem_after=[],
             tools=[{"name": "crm_inventory_list", "args": {"product_ids": ids}}])
    r["uuid_codes"] = codes
    return r


def test_red_l_listed_type_refine_judged_by_ids():
    res = grade_turn(_ids_row([UA, UB], {UA: "SRTA100", UB: "SRTA200"}), item("followup", "refine", **GOLD_L))
    assert res["checks"]["filter"] is True and res["checks"]["applied"] is True, res["checks"]


def test_red_l_unmapped_uuids_leave_filter_unknown():
    # spec says None; the round 34 grader answers False (words not read), so this is red too
    res = grade_turn(_ids_row([UA], {}), item("followup", "refine", **GOLD_L))
    assert res["checks"]["filter"] is None and res["checks"]["applied"] is None, res["checks"]


def test_guard_l_one_id_outside_the_set_fails():
    r = _ids_row([UA, UC], {UA: "SRTA100", UC: "SRTZ999"})
    res = grade_turn(r, item("followup", "refine", **GOLD_L))
    assert res["checks"]["filter"] is False and res["checks"]["applied"] is False, res["checks"]


def test_guard_l_gold_without_filter_products_reads_words_as_today():
    gold = {k: v for k, v in GOLD_L.items() if k != "filter_products"}
    res = grade_turn(_ids_row([UA, UB], {UA: "SRTA100", UB: "SRTA200"}), item("followup", "refine", **gold))
    assert res["checks"]["filter"] is False, res["checks"]


# ------------------------------------------------------------------ (n) a refine while the bot's own how-many is owed
GOLD_N = dict(subject_after=["SRTA100"], filter={"key": "size", "kind": "size", "value": "300mm"})   # _quantity_check reads flt["key"]
HOW_MANY = "How many units for each?\n1. SRTA100-300 -"


def _owed_row(tasks):
    r = row2(verdict={"entities": [], "reference_positions": []}, reply=HOW_MANY, mem_before=[prod("SRTA100", UA)],
             mem_after=[prod("SRTA100", UA)], mem_extra={"specification": [{"spec_key": "length", "spec_value": 300, "raw": "300"}]},
             tools=[TOOL_A])
    if tasks is not None:
        _memory(r)["focus"]["before"]["tasks"] = tasks
    return r


def test_red_n_refine_while_quantity_owed_may_ask_again():
    res = grade_turn(_owed_row([{"kind": "stock_qty", "status": "open"}]), item("followup", "refine", **GOLD_N))
    assert res["checks"].get("qty_ask_ok") is True and res["checks"].get("no_qty_ask") is not False, res["checks"]


def test_guard_n_an_answered_quantity_task_still_fails():
    res = grade_turn(_owed_row([{"kind": "stock_qty", "status": "answered"}]), item("followup", "refine", **GOLD_N))
    assert res["checks"]["no_qty_ask"] is False, res["checks"]


def test_guard_n_no_task_still_fails():
    res = grade_turn(_owed_row(None), item("followup", "refine", **GOLD_N))
    assert res["checks"]["no_qty_ask"] is False, res["checks"]


# ================================================================== review round (Opus review of PR #1774 at 0e2b69dc2b)
# ------------------------------------------------------------------ 1. (l) a word-only lookup cannot pass a gold id list
def test_red_l_word_only_lookup_listing_products_outside_the_gold_set_fails():
    r = row2(verdict={"entities": CAT, "reference_positions": []}, mem_before=[], mem_after=[prod("SRTZ999", UB)],
             reply="Water closets: SRTZ999",
             tools=[{"name": "crm_inventory_list", "args": {"scope_terms": ["water closet"]}}])
    res = grade_turn(r, item("followup", "refine", **GOLD_L))
    assert res["passed"] is False, res["checks"]   # today filter and applied are None, so it passes


def test_guard_l_all_unmapped_uuid_args_stay_unknown():
    res = grade_turn(_ids_row([UA, UB], {}), item("followup", "refine", **GOLD_L))
    assert res["checks"]["filter"] is None and res["checks"]["applied"] is None, res["checks"]


# ------------------------------------------------------------------ 2. (a) each family member must be named in the reply
def test_red_a_dead_end_reply_with_the_whole_family_held_fails():
    r = row2(message="stock of srtf1", reply="Couldn't find SRTF1.",
             mem_after=[prod("SRTF1-A", UA), prod("SRTF1-B", UB)], tools=[])
    assert grade_turn(r, item("code_lookup", **FAM))["passed"] is False


def test_red_a_reply_naming_one_member_while_memory_holds_the_family_fails():
    r = row2(message="stock of srtf1", reply="SRTF1-A is in stock.",
             mem_after=[prod("SRTF1-A", UA), prod("SRTF1-B", UB)], tools=[TOOL_AB])
    assert grade_turn(r, item("code_lookup", **FAM))["passed"] is False


def test_guard_a_the_full_family_named_in_the_reply_passes():
    r = row2(message="stock of srtf1", reply="SRTF1-A: in stock\nSRTF1-B: out of stock",
             mem_after=[prod("SRTF1-A", UA), prod("SRTF1-B", UB)], tools=[TOOL_AB])
    res = grade_turn(r, item("code_lookup", **FAM))
    assert res["passed"] is True, (res["reason"], res["checks"])


# ------------------------------------------------------------------ 3. (c)/(h) a looked-up set key is not an extra subject
def test_red_h_refine_keeping_one_member_of_a_set_stays_a_subset():
    r = row2(verdict={"entities": CHROME, "reference_positions": []}, mem_before=[prod("SRTM1", UA, set_code="SRTSET1"), prod("SRTM2", UB)],
             mem_after=[prod("SRTM1", UA, set_code="SRTSET1")], tools=[TOOL_A])
    res = grade_turn(r, item("followup", "refine", subject_after=["SRTM1", "SRTM2"],
                             filter={"kind": "spec", "value": "chrome"}))
    assert res["checks"]["subject"] is True, res["checks"]   # the set key must not join after_subject here


def _held_set_row(**kw):
    members = [prod("SRTM1", UA, set_code="SRTSET1"), prod("SRTM2", UC, set_code="SRTSET1")]
    base = dict(message="stock of srtx9", reply="Nothing found.", mem_before=members, mem_after=members, tools=[TOOL_A])
    base.update(kw)
    return row2(**base)


def test_red_h_re_held_set_members_are_not_a_change():
    res = grade_turn(_held_set_row(), item("followup", "switch", subject_after=["SRTX9"]))
    assert res["checks"]["changed"] is False, res["checks"]   # set keys not in want must not count as a change


def test_guard_h_a_real_change_still_counts():
    r = _held_set_row(mem_before=[prod("SRTOLD1", UB)])
    res = grade_turn(r, item("followup", "switch", subject_after=["SRTX9"]))
    assert res["checks"]["changed"] is True, res["checks"]


def test_guard_h_a_set_key_that_is_in_want_still_counts():
    r = _held_set_row(mem_before=[prod("SRTOLD1", UB)])
    res = grade_turn(r, item("followup", "switch", subject_after=["SRTSET1"]))
    assert res["checks"]["changed"] is True and res["checks"]["new_subject"] is True, res["checks"]


# ------------------------------------------------------------------ 4. (f) the lookup must be for the held product
def test_guard_f_an_honest_none_with_a_lookup_for_another_product_is_not_applied():
    # pins `want <= _tool_targets(row)`: delete it and this turns red
    r = _none_row("There is no chrome for CB94SS-BL.")
    r["trace"] = [rec for rec in r["trace"] if rec.get("kind") != "tool"]
    r["trace"].append({"kind": "tool", "name": "crm_inventory_list", "args": {"product_ids": [UB]}, "envelope": {}})
    r["trace"].append({"kind": "state", "turn_no": 2, "focus": {"products": [prod("CB10A", UB)]}, "open_question": None})
    res = grade_turn(r, item("followup", "refine", **GOLD_F))
    assert res["checks"]["applied"] is False, res["checks"]


def test_red_f_honest_none_worded_with_a_hyphenated_value_passes():
    gold = dict(subject_after=["CB94SS-BL"], filter={"kind": "spec", "value": "p_trap"}, none_on_record=True)
    spec = {"raw": "p trap", "spec_value": "p_trap"}
    r = row2(verdict={"entities": [dict(spec, hint="specification", spec_key="trap_type")], "reference_positions": []},
             reply="There is no P-trap for CB94SS-BL.", mem_before=[prod("CB94SS-BL", UA)],
             mem_after=[prod("CB94SS-BL", UA)], mem_extra={"specification": [spec]}, tools=[TOOL_A])
    _memory(r)["focus"]["before"]["extra"] = {"specification": [spec]}
    res = grade_turn(r, item("followup", "refine", **gold))
    assert res["passed"] is True, (res["reason"], res["checks"])


# ------------------------------------------------------------------ 5. (i) pins on the regex hunks
def test_guard_i_pins_the_date_slash_decimal_lookbehind():
    # DATE_SLASH `(?<![.\d])`: without it "12/10/2026" after a decimal point reads as a date
    r = _results(row2(reply="Ref 7,000.12/10/2026", tools=[]), "7,000.12; 10; 2026")
    assert made_up_facts(r) == [], made_up_facts(r)


def test_guard_i_pins_the_date_mdy_newline_change():
    # DATE_MDY `[^\S\n]+`: without it "Nov\n12, 2026" reads as one date
    r = _results(row2(reply="Due Nov\n12, 2026", tools=[]), "Nov; 12; 2026")
    assert made_up_facts(r) == [], made_up_facts(r)


def test_guard_i_pins_the_date_my_newline_change():
    # DATE_MY `[^\S\n]+`: without it "Nov\n2026" reads as one month-year
    r = _results(row2(reply="Due Nov\n2026", tools=[]), "Nov; 2026")
    assert made_up_facts(r) == [], made_up_facts(r)


# ------------------------------------------------------------------ a looked-up parked set keeps its members as the subject
PARKED = {"raw": "SRTSET1", "canonical_code": "SRTSET1"}


def _parked_row(term="SRTSET1", codes=("SRTM1", "SRTM2"), extra=None):
    return row2(message="srtset1", reply="Set found.", mem_before=[prod("SRTOLD1", UB)], mem_after=[],
                mem_extra=extra or {"product_set": [PARKED]},
                looked=[{"tool": "t", "entities": list(codes), "product_codes": list(codes)}],
                tools=[{"name": "crm_inventory_list", "args": {"scope_terms": [term]}}])


def test_red_h_parked_set_looked_up_keeps_its_lookup_members_as_the_subject():
    res = grade_turn(_parked_row(), item("followup", "switch", subject_after=["SRTM1", "SRTM2"]))
    assert res["checks"]["new_subject"] is True, res["checks"]   # since 0e2b69dc2b the set code takes the subject and is dropped


def test_red_h_parked_set_looked_up_keeps_a_held_order_as_the_subject():
    held = {"raw": "SO-1001", "hint": "customer_order", "uuid": None, "canonical_code": "SO-1001"}
    r = _parked_row(extra={"product_set": [PARKED], "order": [held]}, codes=())
    res = grade_turn(r, item("followup", "switch", subject_after=["SO-1001"]))
    assert res["checks"]["new_subject"] is True, res["checks"]


def test_guard_h_parked_set_with_a_lookup_for_another_product_still_fails():
    r = _parked_row(term="SRTOLD1", codes=("SRTOLD1",))
    res = grade_turn(r, item("followup", "switch", subject_after=["SRTM1", "SRTM2"]))
    assert res["checks"]["new_subject"] is False, res["checks"]
