"""Kill matrix for ESCALATION-CONTROL (PR #1406).

Runs in a THROWAWAY git worktree of the current HEAD (never the shared checkout: another
agent or a running server may be reading those files; LESSONS-LEARNT 104). For each row it
breaks the implementing line, runs the test that should guard it, restores the file, and
prints RED (guarded) or GREEN (NOT guarded). Needs `sorento_crm_backend/venv` and
`.env.ci-tests` in the main checkout; pass row ids (K1 K2 ...) to run a subset.

    python documentation/plans/chatbot/evidence/escalation-control/killmatrix.py [K1 ...]
"""
import atexit
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
WT = pathlib.Path(tempfile.mkdtemp(prefix="killmatrix-")) / "wt"
subprocess.run(["git", "-C", str(REPO), "worktree", "add", "-q", "--detach", str(WT), "HEAD"], check=True)
atexit.register(lambda: subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(WT)]))
B = WT / "sorento_crm_backend"
os.symlink(REPO / "sorento_crm_backend" / "venv", B / "venv")
shutil.copy(REPO / "sorento_crm_backend" / ".env.ci-tests", B / ".env.ci-tests")
T = "tests/chatbot/test_escalation_control.py"
A = "tests/chatbot/test_escalation_control_api.py"
M = "tests/test_migration_esc1_0001_escalation_allowed.py"
KILLS = [
 ("K1 the contact flag is read", "app/services/chatbot/turn_runtime.py", "    return (row[4] if len(row) > 4 else None) is not False", "    return True", T+"::TestTheContactFlagDecides::test_the_flag_unticked_blocks"),
 ("K2 backfill: every contact allowed", "alembic/versions/esc1_0001_escalation_allowed.py", '"BOOLEAN NOT NULL DEFAULT true"', '"BOOLEAN NOT NULL DEFAULT false"', M+"::test_every_existing_contact_is_backfilled_allowed_and_access_types_get_nothing"),
 ("K3 duplicated contact: any unticked row blocks (all rows read)", "app/services/chatbot/turn_runtime.py", "        if any(_escalation_allowed(row) is False for row in every_row):", "        if all(_escalation_allowed(row) is False for row in every_row):", T+"::TestAmbiguousContact"),
 ("K4 offer gates read offers_escalation", "app/services/chatbot/turn/state.py", "    return not is_staff_profile(profile) and not escalation_barred(profile)", "    return not is_staff_profile(profile)", T+"::test_every_offer_builder_is_silent_for_a_barred_dealer"),
 ("K5 stale offer closed on entry", "app/services/chatbot/turn/apply.py", "    open_question, _dropped = without_escalation(state.pending)", "    open_question, _dropped = state.pending, None", T+"::test_apply_closes_an_open_escalation_offer_on_entry_for_a_barred_contact"),
 ("K6 forced ask blocked (engine guard)", "app/services/chatbot/engine.py", '        and turn_state.escalation_barred(getattr(state_out, "profile", None))\n    ):', '        and False\n    ):', T+"::TestDealerIsNeverOfferedAndCannotForce::test_asking_for_a_person_gets_the_salesman_line_and_no_hand_off"),
 ("K7 _lane marks the barred lane", "app/services/chatbot/turn/apply.py", '    if barred and lane == "escalation":\n        return "escalation_barred"', '    if False:\n        return "escalation_barred"', T+"::test_lane_blocks_every_forced_door_for_a_barred_contact"),
 ("K8 fallback team gated", "app/services/chatbot/engine.py", "        if turn_state.escalation_barred(profile)\n        else", "        if False\n        else", T+"::TestFallbackLane::test_a_barred_contact_is_offered_no_team"),
 ("K9 casual lane strip", "app/services/chatbot/engine.py", "        stripped, _question, offered = escalation_control.strip_text(text, None, state.profile)\n        if offered:", "        stripped, _question, offered = escalation_control.strip_text(text, None, state.profile)\n        if False:", T+"::TestFallbackLane::test_a_clarifier_that_writes_an_escalate_sentence_is_stripped_before_sending"),
 ("K10 backstop strip", "app/services/chatbot/escalation_control.py", "    if not offered:\n        return text, question, False", "    return text, question, False", T+"::TestStripOffers"),
 ("K11 trace wording", "app/services/chatbot/trace.py", '    if branch_kind == "out_of_scope" and lane == "escalation_barred":', '    if False:', T+"::TestNamedTeamAndTrace::test_the_trace_says_the_escalation_was_withheld"),
 ("K12 PUT sets the flag", "app/api/v1/user_management/contacts.py", "        if body.escalation_allowed is not None:\n            contact.escalation_allowed", "        if False:\n            contact.escalation_allowed", A+"::test_put_unticks_and_ticks_the_flag_and_absent_leaves_it_alone"),
 ("K13 contact response field", "app/services/contact_service.py", '            "escalation_allowed": getattr(contact, "escalation_allowed", True) is not False,\n', "", A+"::test_a_blocked_contact_reads_blocked"),
 ("K14 Q2: miss offer becomes the salesman line", "app/services/chatbot/lanes/business/answer.py", "        if barred:\n            return REFER_TO_SALESMAN", "        if False:\n            return REFER_TO_SALESMAN", T+"::test_a_blocked_miss_names_what_was_asked_then_refers_to_the_salesman"),
 ("K15 Q2: what-you-want reply refers", "app/services/chatbot/lanes/business/answer.py", "    if team == REFER_TO_SALESMAN:", "    if False:", T+"::test_the_what_you_want_reply_ends_with_the_salesman_line_for_a_blocked_contact"),
 ("K16 Q2: ladder block goes above the salesman line", "app/services/chatbot/tail/compose.py", "    REFER_TO_SALESMAN,\n)", ")", T+"::TestDealerIsNeverOfferedAndCannotForce::test_a_miss_offers_no_escalation_and_arms_nothing"),
 ("K18 Q2: composer miss ends with the salesman line", "app/services/chatbot/turn/compose.py", "            elif escalation_barred(getattr(state, \"profile\", None)) and not clarifying_open:", "            elif False:", T+"::test_the_composer_miss_ends_with_the_salesman_line_for_a_blocked_contact"),
 ("K19 silent-company offer withheld", "app/services/chatbot/answer_bridge.py", "        if escalation_barred(profile):\n            # ESCALATION-CONTROL: a barred contact is offered no team, silent or not.\n            return answer", "        if False:\n            return answer", T+"::test_the_silent_company_offer_is_not_made_to_a_blocked_contact"),
 ("K20 /complete reads the contact's profile", "app/services/chatbot/engine.py", "                    else turn_runtime.load_profile(db, str(contact_respond_id or \"\"))[0]", "                    else None", "tests/chatbot/test_escalation_control_complete.py"),
 ("K17 allowed contacts untouched", "app/services/chatbot/turn/state.py", '    return getattr(profile, "escalation_allowed", True) is False', "    return True", T+"::TestUnbarredContactsAreUnchanged"),
]
only = sys.argv[1:]
for name, f, a, b, test in KILLS:
    if only and name.split()[0] not in only: continue
    p = B / f; orig = p.read_text()
    assert orig.count(a) == 1, (name, orig.count(a))
    p.write_text(orig.replace(a, b))
    try:
        r = subprocess.run(f"SORENTO_ENV_FILE=.env.ci-tests timeout 900 venv/bin/pytest -q -p no:cacheprovider '{test}' 2>&1 | tail -1", shell=True, cwd=B, capture_output=True, text=True)
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
        verdict = "RED (guarded)" if ("failed" in line or "error" in line) else "GREEN (NOT guarded)"
        print(f"{name} | {f} | {verdict} | {line}", flush=True)
    finally:
        p.write_text(orig)
