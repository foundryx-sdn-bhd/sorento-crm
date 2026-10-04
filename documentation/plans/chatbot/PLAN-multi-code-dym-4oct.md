# PLAN: did-you-mean per missing code in multi-code replies (MULTI-CODE-DYM)

Status: BUILT on PR #1465 (track M, LEAD pattern); review + browser pass pending.
Behaviour card and owner rulings: `multi-code-dym-behaviour-card.md`.

## Rule

Owner, 4 Oct 2026: "treat each product code individually". Every code in a multi-code ask
gets what it gets asked alone.

## Slices (one branch, one PR)

1. **Candidates, once.** `lanes/business/answer.py::miss_token_candidates` (lifted out of
   `build_suggest_offer`, unchanged) and `did_you_mean_by_token` (per missed token: first
   three product candidates, the single-code D1 set).
2. **Carry.** `engine._unplaced_suggestions` reads them off the resolver payload ->
   `turn_runtime.make_tool_runner(unplaced_suggestions=)` -> `envelope_of(suggestions=)`
   -> `envelope["unresolved_suggestions"]`, keyed by the word the customer typed.
3. **Partial miss render + pick.** `turn/compose.py::_did_you_mean_per_code`: per-code
   paragraphs, numbers running on after numbered blocks, one closing line with the
   escalation offer, one `product_pick` (2+ options) or a team offer (1 option). No
   suggestion anywhere: `I could not find X.` + the escalation offer (Q4).
4. **All miss.** `build_suggest_offer` D1 multi arm prints the same paragraphs; a missed
   product code with no suggestion is named (Q5).
5. **Picks.** Several positions already work; a pick plus a typed code answers both
   (`engine._with_the_picked_axis`, `turn/apply.py::_focus_rules`,
   `engine._with_settled_picks`).

## Tests

`tests/chatbot/test_multi_code_dym.py` (compose, candidates, envelope, all-miss,
real-resolver engine turn), `tests/chatbot/test_multi_code_dym_console.py` (real engine,
real trigram: partial miss, one pick, two picks, pick + typed code).

## Not in scope

Dealer (availability-only) asks: their stock path strips the escalation clause
(`engine._dealer_refers_to_salesman` -> `dealer_stock.without_escalation`) and is otherwise unchanged. WA-CONCISE #1455 owns the
found blocks' format; the run-on numbering reads whatever numbers they carry.
