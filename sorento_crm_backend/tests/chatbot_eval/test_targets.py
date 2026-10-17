"""EVAL-BENCH CP0 grader (`scripts/chatbot_eval/targets.py`): one pass and one fail per slice on hand-built rows.

Row shapes below mirror a real persisted turn (checked against recorded rows by the captain):
  - grounded entities: `apply.verdict.entities`; the parser's pick read: `apply.verdict.reference_positions` and
    `apply.verdict.open_question_answer`; `apply.decision = {kind, why}`; optional `engine_pick` event
    `{parser_positions, positions, roster}` when the engine resolved a pick (picked position = its `positions`)
  - subject after the turn: `state.focus.products[*].canonical_code`
  - fact asked: `routed.facts.domains`; a clarify = `replied.facts.asking` truthy
  - open question: before = `state.open_question`, after = `memory.open_question`; re-asked = both equal and set
  - attachments: tool args `attachment_type_ids`; envelope `attachments[*].attachmentType` (gold matches the label)
  - scope: `asker.linked_customer_names`, `item.watch_customer_names`; leak = a watched name not linked, a hidden
    field, or a uuid in the reply. `scope_leaks(row, asker, watch_customer_names=())`
  - reply text `response.reply.text`; user message `envelope.message.message.message.text`;
    truncated envelope `{"truncated": True, "head": "..."}`
"""
from __future__ import annotations

import re

from scripts.chatbot_eval.targets import grade_turn, made_up_facts, scope_leaks, summarise

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
INTERNAL = ("canonical_code", "spec_key", "spec_value", "decision.kind", "linked_customer_ids", "envelope", "trace",
            "branch_kind", "attachment_type", "reference_positions")
STAFF = {"audience": "staff", "linked_customer_ids": None, "hidden_fields": []}


def row(*, message="stock of SRTA100", reply="SRTA100 has 40 pcs.", entities=None, domains=("inventory",),
        asking=None, decision=None, positions=None, tools=None, open_question=None, focus_codes=None, answer=None, engine_pick=None):
    trace = [
        {"stage": "understood", "status": "ok", "facts": {"entities": len(entities or [])}, "raw": {}},
        {"stage": "routed", "status": "ok", "facts": {"lane": "business_query", "domains": list(domains)}},
        {"stage": "replied", "status": "ok", "facts": {"asking": asking, "files": 0}},
        {"kind": "apply", "verdict": {"entities": entities or [], "reference_positions": positions or [],
                                      "open_question_answer": answer},
         "decision": {"kind": decision, "why": "synthetic", "own_domain": None} if decision else None},
    ]
    if engine_pick:
        trace.append({"kind": "engine_pick", "parser_positions": [], "positions": engine_pick, "roster": []})
    for t in tools or []:
        trace.append({"kind": "tool", "name": t["name"], "args": t.get("args", {}), "envelope": t.get("envelope", {})})
    trace.append({"kind": "state", "turn_no": 2,
                  "focus": {"products": [{"canonical_code": c} for c in (focus_codes or [])]},
                  "open_question": (open_question or {}).get("before")})
    trace.append({"kind": "memory", "focus": {}, "episodes": [], "open_question": (open_question or {}).get("after")})
    return {"id": "t1", "branch_kind": "business_query",
            "envelope": {"message": {"message": {"message": {"text": message}}}},
            "trace": trace, "response": {"reply": {"text": reply}}}


def ent(code, **extra):
    return {"raw": code, "hint": "product", "canonical_code": code, "confident": True, **extra}


def tool_for(code, items=None, name="crm_inventory_list"):
    return {"name": name, "args": {"code": code}, "envelope": {"items": items if items is not None else [{"code": code, "qty": 40}]}}


def item(slice_, kind=None, message="synthetic ask", asker=STAFF, watch_customer_names=(), **gold):
    return {"item_id": "it1", "slice": slice_, "kind": kind, "message": message, "gold": gold, "asker": asker,
            "watch_customer_names": list(watch_customer_names)}


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
    # asked INSTEAD of answering: asking set and no named tool call targeted the code
    r = row(entities=[ent("SRTA100")], reply="Which SRTA100 do you mean?", asking="product_pick")
    res = grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory"))
    assert res["passed"] is False and res["checks"]["no_clarify"] is False


def test_code_lookup_clarify_passes_when_gold_is_ambiguous():
    r = row(entities=[ent("SRTA100")], reply="Which SRTA100 do you mean?", asking="which_product")
    check(grade_turn(r, item("code_lookup", code="SRTA100", fact="inventory", ambiguous=True)), True)


# ------------------------------------------------------------------ follow-ups
def test_followup_pick_pass_and_wrong_position_fails():
    ok = row(decision="answer", positions=[2], engine_pick=[2], entities=[ent("SRTA200")], reply="SRTA200 has 7 pcs.",
             tools=[tool_for("SRTA200")])
    check(grade_turn(ok, item("followup", "pick", position=2, code="SRTA200")), True)
    bad = row(decision="answer", positions=[3], engine_pick=[3], entities=[ent("SRTA300")], reply="SRTA300 has 7 pcs.",
              tools=[tool_for("SRTA300")])
    check(grade_turn(bad, item("followup", "pick", position=2, code="SRTA200")), False, "position")


def test_followup_answer_pass_and_reask_fails():
    q = {"question": "which colour", "options": ["white", "black"]}
    ok = row(decision="answer", open_question={"before": q, "after": None}, answer={"mode": "pick", "picked": [1]})
    check(grade_turn(ok, item("followup", "answer", option="white")), True)
    bad = row(decision="answer", open_question={"before": q, "after": q})
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


def _att(files, asked="technical_specifications", reply="Here are the files.", sent=None):
    r = row(reply=reply, entities=[{"hint": "attachment_type", "canonical_code": asked, "raw": "drawing", "confident": True}],
            tools=[{"name": "crm_attachments_list", "args": {"attachment_type_ids": ["id-1"]},
                    "envelope": {"items": [], "attachments": files}}])
    for rec in r["trace"]:
        if rec.get("stage") == "replied":
            rec["facts"]["files"] = len(files) if sent is None else sent
    return r


def _file(label, name="a.pdf"):
    return {"filename": name, "url": "u", "mimeType": "application/pdf", "attachmentType": label}


GOLD_TS = {"attachment_type": "technical_specifications", "attachment_type_name": "Technical Specifications"}


def test_attachment_pass_when_sent_label_matches_gold_name_or_code():
    check(grade_turn(_att([_file("Technical Specifications")]), item("attachment", **GOLD_TS)), True)
    check(grade_turn(_att([_file("technical_specifications")]), item("attachment", **GOLD_TS)), True)


def test_attachment_wrong_files_sent_fails():
    r = _att([_file("Product Photos", "a.jpg")])
    check(grade_turn(r, item("attachment", **GOLD_TS)), False, "type")


def test_attachment_requested_type_must_equal_gold_even_when_files_look_right():
    r = _att([_file("Technical Specifications")], asked="product_photos")
    check(grade_turn(r, item("attachment", **GOLD_TS)), False, "asked type")


def test_attachment_none_on_record_is_decided_from_evidence_not_english_words():
    gold = dict(GOLD_TS, none_on_record=True)
    check(grade_turn(_att([], reply="There is none on record for this product."), item("attachment", **gold)), True)
    check(grade_turn(_att([], reply="Maaf, tiada rekod untuk produk ini."), item("attachment", **gold)), True)


def test_attachment_none_on_record_fails_when_the_reply_sends_files_or_files_exist():
    gold = dict(GOLD_TS, none_on_record=True)
    claims = _att([], reply="Here are the files you asked for.", sent=2)
    check(grade_turn(claims, item("attachment", **gold)), False)
    exists = _att([_file("Technical Specifications")], reply="Nothing here.")
    check(grade_turn(exists, item("attachment", **gold)), False)


def test_attachment_files_sent_when_gold_says_files_exist_but_none_sent_fails():
    check(grade_turn(_att([], reply="There is none on record."), item("attachment", **GOLD_TS)), False)


# ------------------------------------------------------------------ scope
DEALER = {"audience": "dealer", "linked_customer_ids": ["c-own"], "linked_customer_names": ["Acme Sdn Bhd"],
          "hidden_fields": ["cost_price"]}
WATCH = ["Rival Trading"]


def test_scope_customer_outside_links_is_a_leak_and_clean_passes():
    leaky = row(reply="Orders for Acme Sdn Bhd and Rival Trading.")
    leaks = scope_leaks(leaky, DEALER, watch_customer_names=WATCH)
    assert leaks and any("Rival Trading" in x for x in leaks) and not any("Acme" in x for x in leaks)
    clean = row(reply="Orders for Acme Sdn Bhd.")
    assert scope_leaks(clean, DEALER, watch_customer_names=WATCH) == []
    check(grade_turn(clean, item("scope", asker=DEALER, watch_customer_names=WATCH)), True)
    check(grade_turn(leaky, item("scope", asker=DEALER, watch_customer_names=WATCH)), False, "customer")


def test_scope_uuid_in_reply_is_a_leak():
    assert scope_leaks(row(reply="Ref 123e4567-e89b-42d3-a456-426614174000."), DEALER)


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


# ------------------------------------------------------------------ ideas follow-up (G1) and growth items
def idea_row(*, before, patch, nested=False, reply="Noted, anything else on that?"):
    inner = {"variables": {"ideation": before}} if nested else {"ideation": before}
    sv = {"respond_io_id": "9000", "session_vars": inner}  # the real shape (engine.py received stage)
    r = row(reply=reply, decision="answer", domains=("ideate",))
    r["branch_kind"] = "ideate"
    r["trace"].insert(0, {"stage": "received", "status": "ok", "facts": {}, "raw": {"session_vars": sv}})
    r["trace"].append({"stage": "remembered", "status": "ok", "facts": {}, "raw": {"session_patch": patch}})
    return r


HELD = {"id": "IDEA-SYN-1", "title": "synthetic towel rail idea"}


def test_ideas_followup_pass_when_the_held_idea_is_untouched_or_reset_to_itself():
    it = item("followup", "answer", idea_ref="IDEA-SYN-1")
    check(grade_turn(idea_row(before=HELD, patch={}), it), True)
    check(grade_turn(idea_row(before=HELD, patch={"ideation": HELD}), it), True)
    check(grade_turn(idea_row(before=HELD, patch={}, nested=True), it), True)


def test_ideas_followup_fails_when_the_patch_clears_the_held_idea():
    it = item("followup", "answer", idea_ref="IDEA-SYN-1")
    check(grade_turn(idea_row(before=HELD, patch={"ideation": None}), it), False, "idea")
    r = grade_turn(idea_row(before=HELD, patch={"ideation": None}), it)
    assert "drop" in r["reason"].lower()


def test_ideas_followup_fails_when_the_patch_replaces_the_idea():
    other = {"id": "IDEA-SYN-2", "title": "synthetic other idea"}
    r = grade_turn(idea_row(before=HELD, patch={"ideation": other}), item("followup", "answer", idea_ref="IDEA-SYN-1"))
    check(r, False)
    assert "drop" in r["reason"].lower()


def test_growth_items_are_reported_apart_and_never_count_toward_meets():
    bars = {"followup": 0.9}
    runs = []
    for _ in range(3):
        run = [_res("followup", "answer", True) for _ in range(10)]
        growth = _res("followup", "answer", False)
        growth["growth"] = True
        runs.append(run + [growth])
    s = summarise(runs, bars)
    assert s["meets"] is True
    assert s["slices"]["followup"]["n"] == 10 and s["slices"]["followup"]["rate"] == 1.0
    g = s["growth"]["followup"]
    assert g["n"] == 1 and g["rate"] == 0.0


def test_and_x_new_fact_passes_when_subject_kept_and_fact_replaced():
    r = row(decision="new_ask", domains=("incoming",), focus_codes=["SRTA100"])
    check(grade_turn(r, item("followup", "and_x", variant="new_fact", fact="incoming", subject_after=["SRTA100"])), True)


def test_and_x_new_fact_fails_when_subject_dropped():
    r = row(decision="new_ask", domains=("incoming",), focus_codes=[])
    check(grade_turn(r, item("followup", "and_x", variant="new_fact", fact="incoming", subject_after=["SRTA100"])),
          False, "subject")


def test_and_x_new_fact_fails_when_fact_is_wrong():
    r = row(decision="new_ask", domains=("inventory",), focus_codes=["SRTA100"])
    check(grade_turn(r, item("followup", "and_x", variant="new_fact", fact="incoming", subject_after=["SRTA100"])),
          False, "asked")


def test_dates_match_across_formats_and_partial_dates():
    iso_dt = _fact_row("SRTA100 due 6 Sep 2026.", [{"code": "SRTA100", "eta": "2026-09-06T00:00:00"}])
    assert made_up_facts(iso_dt) == []
    slash = _fact_row("SRTA100 due 06/09/2026.", [{"code": "SRTA100", "eta": "2026-09-06"}])
    assert made_up_facts(slash) == []
    month = _fact_row("SRTA100 arrives in Sep 2026.", [{"code": "SRTA100", "eta": "2026-09-06"}])
    assert made_up_facts(month) == []
    wrong = _fact_row("SRTA100 due 07/09/2026.", [{"code": "SRTA100", "eta": "2026-09-06"}])
    assert any("07/09/2026" in f for f in made_up_facts(wrong))


def test_summarise_made_up_fact_in_one_of_three_runs_means_not_met():
    bars = {"code_lookup": 0.97}
    runs = [[_res("code_lookup", None, True) for _ in range(100)] for _ in range(3)]
    runs[2][5] = _res("code_lookup", None, True, made_up=["999"])
    s = summarise(runs, bars)
    assert s["meets"] is False and s["made_up"] == 1


def test_summarise_refuses_with_fewer_than_three_runs():
    runs = [[_res("code_lookup", None, True) for _ in range(100)] for _ in range(2)]
    s = summarise(runs, {"code_lookup": 0.97})
    assert s["meets"] is False
    assert "3" in str(s.get("reason", "")) and "run" in str(s.get("reason", ""))


def test_asked_attachment_type_accepts_the_parser_taxonomy_words_in_gold_names():
    gold = dict(GOLD_TS, attachment_type_names=["technical_specifications", "Technical Specifications",
                                                "technical drawing", "drawing"])
    ok = _att([_file("Technical Specifications")], asked="technical drawing")
    check(grade_turn(ok, item("attachment", **gold)), True)
    check(grade_turn(_att([_file("Technical Specifications")], asked="Drawing"), item("attachment", **gold)), True)
    bad = _att([_file("Technical Specifications")], asked="product photos")
    check(grade_turn(bad, item("attachment", **gold)), False, "asked type")


def test_a_word_starting_with_a_month_prefix_is_not_a_date():
    r = _fact_row("SRTA100 Marble 1200 pcs", [{"code": "SRTA100", "qty": 1200}])
    assert made_up_facts(r) == []
    r2 = _fact_row("SRTA100 Mayfair 1200 pcs, Mandarin 1200 pcs", [{"code": "SRTA100", "qty": 1200}])
    assert made_up_facts(r2) == []


def test_month_year_still_parses_as_a_date():
    ok = _fact_row("SRTA100 arrives in Mar 2026.", [{"code": "SRTA100", "eta": "2026-03-12"}])
    assert made_up_facts(ok) == []
    bad = _fact_row("SRTA100 arrives in Mar 2026.", [{"code": "SRTA100", "eta": "2026-04-01"}])
    assert any("Mar 2026" in f for f in made_up_facts(bad))
