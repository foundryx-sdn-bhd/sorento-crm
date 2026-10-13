# PLAN: PICKER-ONE, one customer picker for every entry path

Status: ready for review, PR #1619 (small fix track: chatbot only, no migration, no auth/RBAC change); reviewer READY at aebbb948

## Owner ask (13 Oct)

Two "Which one do you mean?" customer pickers look different: "1. HANLIM 2. HANLIM TRADING" (no
stamp) vs "1. HANLIM - no DO 2. HANLIM TRADING - no DO" (stamped), and the gate's "Which customer
do you mean? Please choose: 1. X (MCH, SRT) - has DO". Rule: reuse, never parallel paths.

## What is on main today (fe6c08d9)

| Entry path | Options built by | Stamp | Rendered by |
|---|---|---|---|
| Resolver ambiguity (narrow roster) | `turn/narrow.py::_options` over `turn_runtime.candidates_by_kind` | `candidates_by_kind` when `stamp_customer` (this turn's verdict names a customer, not outstanding) | `turn/compose.py::compose_question` |
| Carried-subject clarify (customer rows from the focus) | `narrow._options` over focus rows | none | `compose_question` |
| Typed-names roster (several customer words) | `narrow._options` over the typed words | none | `compose_question` |
| Gate picker (`offer` exit) | `gate.py` `rep_labels` (`_rep_label` + `_company_suffix`) | `pickers.annotate_customer` rewrites the rendered text | `answer_bridge._offer_answer` -> production text ladder ("Which customer do you mean? Please choose:") |

## Build

1. **One option builder**: `narrow._options` (customer kind, `ledger_family` grouping). The
   bridge's customer `offer` arm builds its options through `turn_runtime.candidates_by_kind`
   + `narrow._options`, the same pair the narrow roster uses, and prints them through
   `compose_question`. The gate's "Which customer do you mean? Please choose:" text no longer
   reaches a contact.
2. **One stamp helper**: `pickers.customer_stamp(name, bases_with_do)` -> `"has DO"` /
   `"no DO"` / `None` (not measured), built on `customer_bases_with_do` + `customer_base`.
   Every customer option is stamped by it: `candidates_by_kind`, and the engine's ask point for
   a customer roster that reached it unstamped (carried-subject, typed names).
3. **One probe per turn**: the gate's customer-picker arm records the measured family bases as
   data (`customer_bases_with_do`) instead of rewriting text; `resolve_kinds` reuses that rather
   than probing a second time.
4. **Company tag**: the option label carries "(MCH, SRT)" only when the roster's options span
   different company sets (2+ distinct); one company set across all options means no tag.
5. **Header + stamp format**: the one existing format, `compose_question`'s "Which one do you
   mean?" + `N. <label> - <stamp>`. No new wording; the gate's trailing "None of these have a
   recent DO." goes with `annotate_customer`.
6. **Deleted as dead**: the gate's own "Which customer do you mean? Please choose:" text
   (`gate.py` customer arm writes no `gate_clarification` now; its "Couldn't find" line rides as
   `dropped_filter_line`), `pickers.annotate_customer` (text rewriting) and its node replay runner.
   `answer_bridge._stamps_by_position` stays: no customer roster reaches it (the customer arm
   always exits `offer`, never the miss arm); it serves the product miss rosters, whose
   has/no-incoming stamps are written into text by `annotate_incoming` / `dym-annotate`.

## R20 (outstanding asks unstamped): kept

A real owner rule: owner round 7, 13 Sep 2026, quoted in `resolve_gate.py` ("it is still kinda
strange for me though, to say no DO, then later when i get the summary, there is DO"). The probe
measures DELIVERED orders, the opposite population from the outstanding report's DO block.

## Tests (red first)

- Parity: the same two customers produce byte-identical picker text from the gate `offer` arm,
  the narrow resolver roster, the carried-subject roster and the typed-names roster.
- Company tag kept when the two options' companies differ, dropped when they are the same.
- R20: an outstanding ask's roster is unstamped on every path.
