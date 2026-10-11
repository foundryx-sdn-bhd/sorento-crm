# PLAN - CLARIFY-POLICY

Status: building on the card's recommendations (Q1-Q5 (a)) while owner rulings are pending (crew
instruction 11 Oct). Track: full (M), LEAD pattern. UAC: `clarify-policy-acceptance-criteria.md`.
Card: `CARD-clarify-policy-11oct.md` (+ `.html`).

## Slices (one branch, one PR)

S1 Loop breaker (AC-1..7). New module `app/services/chatbot/clarify_policy.py`:
`ask_fingerprint(kind, param, subject)` and a check over the bot's last two stored fingerprints.
The fingerprint is stored with the bot's own reply (the turn's stored response / trace, read back
the same way `turn_runtime.previous_reply_text` reads the last reply, `turn_runtime.py:304-350`),
so no session schema change. Applied once at the single point where a turn's reply is finalised
(coder locates it in `engine.py` / `tail/outcome.py` and cites it), so every ask site is covered,
including those that store no pending. Breaking = best-effort answer + handover line (AC-6).

S2 Promotion level (AC-8..12): `lanes/business/tier_gate.py:127-148`, `answer.py:257-361`,
`turn/narrow.py:410-429`.

S3 Attachment type (AC-13..16): `lanes/business/gate.py:495-530`, `answer.py:3134-3139,
3215-3224`, `turn/narrow.py:500-506`.

S4 DO / SO period (AC-17..23): `do_ask.py:104-153` (`range_reply`) gains the named-subject
default; `lanes/business/__init__.py:2202-2212` and `:1520-1548`; `so_status.py:295-350`.
Existing tests that pin the old ask for a named subject (`tests/chatbot/test_do_ask_range*.py`,
`test_do_ask_tester_pass1.py`) are updated by the TESTER, not the coder.

S5 Collapse + outstanding default (AC-24..26): `gate.py:569-974`, `turn/task.py:871-895`,
`lanes/business/__init__.py:1947-1955`.

S6 Generic menus (AC-27..28): `turn/apply.py:2094-2113`, `turn/compose.py:1170-1214`.

## Order

Tester writes red tests for all slices from the UAC (no implementation view), commit
`test(red): ...` alone, red-proof posted. Coder (one instance, Opus: tangled 10k-line engine, per
CLAUDE.md escalation "complex architecture") makes them green slice by slice S1..S6. Kill test per
slice. Reviewer (Opus). Eval gate: touched topics (promotion, product_attachment, order,
inventory, general) + 30 smoke. Hand test script.

## Test seams

- Engine-level: `tests/chatbot/test_dealer_eta_stock_routing.py` `Console` (real engine, parser
  verdict stubbed; used by `test_do_ask_range_engine.py`), and `tests/chatbot/test_outstanding_lane.py`
  `_run_turn` (used by `test_promo_single_level.py`).
- Unit: `clarify_policy.py` pure functions.

## Out of scope

- The "has promotion" probe disagreeing with the file for a dealer (cand-0274 data defect).
- Ranking / top-selling / low-stock asks (owner rule: no default).
