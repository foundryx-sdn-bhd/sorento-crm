# PLAN: kind_pick ages, and a stale uuid-less class word stays off a product-scoped fetch (STALE-EXTRA-CARRY)

Status: in progress; track: small fix (backend only, under 300 changed lines, no migration, no auth/RBAC change, no new ingest surface)

UAC: `stale-extra-carry-acceptance-criteria.md` (alongside).

## Report

Prod, 6-7 Oct 2026, contact 550337684, prompt v47. A photo of seat covers minted a
`kind_pick` open question ("seat cover (promotion / attachment type / form)") with
`asked_at_turn=None`. Sixteen hours later it was still the open question. Beside it,
`focus.extra.category` and `focus.extra.specification` both held "seat cover" with no
uuid (the specification with `spec_key ""`), and those two rows rode into every reuse
fetch: after a correct "SRT320-CR x 30" stock answer, "Got ETA ?" reached the resolver
with "seat cover" as a scope term, the described set qualified nothing, and the reply
printed the seat-cover miss instead of the product's ETA line.

## Root cause (crew trace, re-verified on origin/main e4158ab52)

1. `turn/apply.py:1779-1781` mints the kind_pick with `asked_at_turn=None`. Only the
   escalation offers tick (`turn/pending.py:306-338`). The stamp-aware arm
   `apply.py:2604-2606` (`_stock_task_is_the_current_question`) needs an `int` stamp, so
   a None stamp reads as "still the current question" on every later turn.
   `turn/compose.py:775` and `:844` compare the stamp with `state.turn_no` too, so a
   None-stamped kind_pick never counts as "asked this turn" either.
2. uuid-less `focus.extra` rows survive a refine turn (`decide.py:115-130`; eviction at
   `apply.py:1105-1160` runs only under `starts_fresh`), and
   `turn_runtime.with_carried_entities(..., unsettled_only=True)` (`:3718-3798`) hands
   every uuid-less extra row to the resolver. `resolve_gate.py:687-695` turns a
   `category` / `product_type` entity into `scope_terms`, which skips the typed-code
   guard at `:716`.
3. #1502 (merged) drops the set predicate on a product-scoped fetch; #1509 (open) stops
   the menu being minted from a class hint. Residual after both: the stale extras are
   still handed over on every reuse turn, and the kind_pick never ages.

## Fix (code only; the parser prompt is not touched)

1. **Stamp the kind_pick.** `_reconcile_step` takes `turn_no` from `apply()` (which has
   `state.turn_no`) and mints the kind_pick with `asked_at_turn=turn_no`; the queued
   sibling pick (`_answer_pending`'s `kind_pick_queue_advanced` arm) is stamped with the
   turn that asks it. No new ageing rule: the existing stamp-aware arm and the
   compose-time "asked this turn" checks now see an int.
2. **Keep uuid-less class words off a product-scoped fetch.** In
   `with_carried_entities(unsettled_only=True)`: when the focus holds a product with a
   uuid that is NOT a described set's member (`described_set` marker, #1502), a carried
   `category` / `product_type` row with no uuid, or a `specification` row with no uuid
   and no `spec_key`, is not handed to the resolver. It can only ever become a
   `scope_terms` entry, never a `*_ids` param, and the fetch is already scoped to the
   product. A described set's members (rows marked `described_set`) keep their class
   word, so "cert?" after "which basins have incoming?" still re-describes the set, and a
   grounded specification (`spec_key` set) is untouched. No regex, no keyword, no reading
   of user text: parser fields and focus rows only.

Guard: the whole `tests/chatbot` suite stays green, the described-set refinement cases in
particular. If (2) breaks one of them, (1) ships alone and the failing cases are posted as
a crew-note with file:line.

Overlap with #1509 (CLASS-HINT-KIND-PICK): `resolve_gate.py` and `turn_runtime.py`. This
lane touches `turn_runtime.with_carried_entities` only (one hunk) and does not touch
`resolve_gate.py`.

## Tests

- `tests/chatbot/test_stale_extra_carry.py` (red before code):
  - a kind_pick minted by `apply()` at turn N carries `asked_at_turn == N`;
  - the queued sibling pick is stamped with the turn that asks it;
  - a kind_pick minted at turn N, with a stock task touched at N+1, is not the current
    question at N+2: a bare number is the task's quantity
    (`bare_number_is_the_quantity`), the existing rule;
  - `with_carried_entities(unsettled_only=True)` on the incident focus hands no
    `category` / `specification` row to the resolver; a described set's class word and a
    grounded specification are still handed over; a `starts_fresh`-less refine with no
    resolved product is untouched;
  - the incident shape through the real engine (AvailConsole): "Got ETA ?" over products
    `[SRT320-CR uuid]`, extra `category` + `specification` "seat cover", parser entities
    `[]`, `entity_op reuse`, domain `incoming`: the reply is the product's ETA line and
    the resolver body carries no `scope_terms`.
- Console pass with the real parser (incident mirror): "seat cover" -> "srt320-cr x30"
  -> "got ETA ?" expecting the ETA line (hand test: `laneboard/scripts/<PR>.md`).
