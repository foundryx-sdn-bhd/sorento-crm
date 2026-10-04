# Kill matrix (TDD evidence, ESCALATION-CONTROL, per-contact design)

`killmatrix.py` breaks the implementing line of each behaviour, runs the test that should
guard it, and restores the file. Run on 1 Oct 2026 against the per-contact redesign. Every
row is red. K13 and K15 stayed green on the first run; the direct tests named in their rows
were added and both re-ran red.

Red-first for the redesign: commit 52d51a82d holds 9 red tests (default not true, Q2 miss
wording, access-type column still present, migration backfill) before the implementing
commit 3432fe7ba; the frontend switch tests were 4 red against the previous component.

| Behaviour | Broken in | Result | Test outcome |
| --- | --- | --- | --- |
| K1 the contact flag is read | app/services/chatbot/turn_runtime.py | RED (guarded) | 1 failed, 13 warnings in 4.53s |
| K2 backfill: every contact allowed | alembic/versions/esc1_0001_escalation_allowed.py | RED (guarded) | 1 failed, 13 warnings in 0.47s |
| K3 duplicated contact: blocked only if every row is | app/services/chatbot/turn_runtime.py | RED (guarded) | 1 failed, 13 warnings in 4.67s |
| K4 offer gates read offers_escalation | app/services/chatbot/turn/state.py | RED (guarded) | 3 failed, 5 passed, 126 warnings in 22.09s |
| K5 stale offer closed on entry | app/services/chatbot/turn/apply.py | RED (guarded) | 1 failed, 13 warnings in 0.58s |
| K6 forced ask blocked (engine guard) | app/services/chatbot/engine.py | RED (guarded) | 1 failed, 126 warnings in 19.71s |
| K7 _lane marks the barred lane | app/services/chatbot/turn/apply.py | RED (guarded) | 3 failed, 13 warnings in 0.64s |
| K8 fallback team gated | app/services/chatbot/engine.py | RED (guarded) | 1 failed, 14 warnings in 5.89s |
| K9 casual lane strip | app/services/chatbot/engine.py | RED (guarded) | 1 failed, 14 warnings in 5.64s |
| K10 backstop strip | app/services/chatbot/escalation_control.py | RED (guarded) | 3 failed, 1 passed, 13 warnings in 0.61s |
| K11 trace wording | app/services/chatbot/trace.py | RED (guarded) | 1 failed, 13 warnings in 0.54s |
| K12 PUT sets the flag | app/api/v1/user_management/contacts.py | RED (guarded) | 1 failed, 131 warnings in 23.30s |
| K13 contact response field | app/services/contact_service.py | RED (guarded) | 1 failed (test_a_blocked_contact_reads_blocked, added after the first run left it green) |
| K14 Q2: miss offer becomes the salesman line | app/services/chatbot/lanes/business/answer.py | RED (guarded) | 3 failed, 126 warnings in 23.69s |
| K15 Q2: what-you-want reply refers | app/services/chatbot/lanes/business/answer.py | RED (guarded) | 2 failed (test_the_what_you_want_reply_ends_with_the_salesman_line_for_a_blocked_contact, added after the first run left it green) |
| K16 Q2: ladder block goes above the salesman line | app/services/chatbot/tail/compose.py | RED (guarded) | 1 failed, 126 warnings in 22.89s |
| K17 allowed contacts untouched | app/services/chatbot/turn/state.py | RED (guarded) | 3 failed, 126 warnings in 20.09s |
