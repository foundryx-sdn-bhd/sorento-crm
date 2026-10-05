# PLAN: "all products" after a delivery MISS stays an order enquiry (ORDER-BROADEN)

Status: IN PROGRESS (5 Oct 2026). Lane `ORDER-BROADEN`, small fix track (prompt rule +
presentation fix, no migration, no auth change, no UI). Cloud sandbox, LEAD pattern.
UAC: `order-broaden-acceptance-criteria.md`.
Behaviour card: `CARD-order-broaden-05oct.md`.

## Defect

Owner, 5 Oct 2026, live, AVAILABILITY contact, prompt v42: "check delivery for last week"
missed (customer HANLIM, product SRTWC286-SH, 28/09 to 04/10, "no order ... matched these.
Reply with another month or dates"). Then "all products" answered STOCK ETA lines:

```
SRTWC286-SH-NEW-200: ETA 29/10/2026
Order date: 28/09/2026 to 04/10/2026

SRTWC286-SH-NEW-P: ETA 29/10/2026

Please refer to your salesman.
```

Expected: delivery orders for ALL products in that window (product filter dropped), never a
stock/ETA answer, and never an "Order date" window on an ETA line.

## Cause (origin/main, cited by the crew scout and re-read here)

1. Understanding: the engine never infers a domain from leftover focus
   (`turn/apply.py:1184-1187` sets `focus.domains = [verdict.domain_hint]`, `_broaden`
   only drops axes), so the jump to the dealer incoming view is the PARSER's
   `domain_hint`. The BROADEN AXIS rule (prod file lines 459-512) already says "all
   products" during an order enquiry keeps the domain, but no worked example covers the
   case right after a MISS reply that invites other dates.
2. Presentation: incoming tools take a date window (`turn/policy_rows.py:DATE_PARAM_TOOLS`),
   the carried window rides the spec (`turn_runtime._spec_window`), `envelope_of` stamps
   `date_line`, and `turn/compose.py` prints it under the block's first line. The dealer
   view answers with one line per product, so the window lands between two ETA lines.

## Fix

1. PROMPT (patch file + tests, no migration; sequenced onto #1429's combined version by
   crew): a worked example after the BROADEN AXIS "KEEP domain_hint" rule: previous turn a
   delivery/order MISS, message "all products" -> domain_hint "order", intent unchanged,
   broaden_axis "product", broaden_to "all", entity_op "reuse". Rule text: a miss reply
   inviting other dates never moves the domain; "all products" is never a stock/ETA
   question.
2. CODE (presentation only): a dealer-view block never prints `date_line`
   (`turn/compose.py`), and a turn whose domain changes clears the carried window
   (`turn/apply.py`). Red-first, whichever the tests prove safest; both documented here.

## Evidence

- Live parser reproduction: this sandbox's egress proxy refuses api.openai.com (403), so
  the live journey test is written `live_external` (opt-in, `CHATBOT_LIVE_LLM=1`) and runs
  on the owner's machine; the verdict trace is recorded in the PR when crew runs it.
- Prompt regression: the committed prompt tests (`tests/test_ai_semantic_parser_*`,
  `tests/chatbot/test_*parser*`) pass on the patched text.
