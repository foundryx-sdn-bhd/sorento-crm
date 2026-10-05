# CARD - "all products" after a delivery miss (ORDER-BROADEN)

Status: ANSWERED by owner 5 Oct 2026 via crew, all three as recommended: Q1 (a) after a MISS
"all products" re-runs the SAME window with the product filter dropped (owner: "I am thinking
to find all products"); Q2 (a) a window never carries across a domain change; Q3 (a) one
example set covering all products / all customers / any date after a MISS. The prompt change
ships as patch + test now; the migration only after crew confirms the base (#1429 combined
version). Plan: `PLAN-order-broaden-05oct.md`.

Owner (5 Oct, live, AVAILABILITY contact, prompt v42): "check delivery for last week" missed
(HANLIM, SRTWC286-SH, 28/09 to 04/10, "no order ... matched these. Reply with another month or
dates"). "all products" then answered stock ETA lines with "Order date: 28/09/2026 to
04/10/2026" between them. Expected: delivery orders for ALL products in that window, never
an ETA answer, no window on an ETA line.

## What exists today (origin/main, read this lane)

- Understanding lives in the parser prompt (owner rule). The engine never infers a domain
  from leftover focus: `turn/apply.py` sets `focus.domains` from the verdict's `asks` or
  `domain_hint`, else carries the previous domains; `_broaden` only drops axes. So the ETA
  answer is the PARSER's `domain_hint: incoming` on "all products".
- The BROADEN AXIS rule (prod text lines 459-512) already says "all products" drops the
  PRODUCT filter and "KEEP domain_hint ... 'all products' during an order enquiry is still
  an order enquiry". Its worked examples sit over a question that was ANSWERED (orders for
  SRTWC286-SH + HANLIM). None covers the turn right after a MISS reply that invites other
  dates, which is where v42 wandered.
- Presentation: the carried window rides every spec whose domain takes a date filter
  (`turn_runtime._spec_window`; incoming tools are in `DATE_PARAM_TOOLS`), `envelope_of`
  stamps `date_line`, and `turn/compose.py` prints it under the block's first line. The
  dealer incoming view has no header (one "<code>: ETA <dates>" line per product), so the
  window landed between two answers. `new_ask_drops_date_window` fires only when the
  message names an entity kind; "all products" names none.
- An order list with no product and no customer but a window runs (the empty-subject
  refusal, `_REFUSES_EMPTY_SUBJECT`, is inventory only, and a window counts as scope), so
  "all products" over a dated delivery ask is answerable as is.

## Rules

R1. A MISS reply never moves the domain. "all products" / "any product" / "all customers" /
    "any date" typed after "no order ... matched these. Reply with another month or dates"
    is the SAME order enquiry, widened on the one axis named: `domain_hint` and
    `intent_hint` as the previous turn, `broaden_axis` = that axis, `broaden_to` "all",
    `entity_op` "reuse", `entities` []. (Prompt: a worked example after the KEEP rule.)
R2. "all products" is never a stock or ETA question. Only ask-the-catalogue phrasing
    ("product list", "catalogue", "spec of X") moves the domain to master_products, and
    only when no business domain is in play (already in the prompt; restated beside R1).
R3. After a MISS, a widened axis re-runs the SAME window with that filter dropped (Q1).
    The miss reply's own invite ("another month or dates") is an offer, not a reset: the
    customer who answers it names new dates; the one who widens an axis keeps the dates.
R4. A date window never carries across a domain change (Q2). The engine clears the carried
    window when `focus.domains` changes this turn (`domain_change_drops_date_window`); a
    window THIS message names is set from the verdict as today. One standing exception,
    found by the drift run: a report drilled out of a top selling ranking carries the
    ranking's filters, period included (earlier owner ruling on that lane,
    `test_top_selling_round5.py::test_a_report_after_a_ranking_carries_its_filters`), so a
    carried focus whose status is the ranking keeps its window.
R5. A dealer-view block never prints the window line (presentation, `turn/compose.py`): the
    one-line ETA answers have no header for it to sit under.

## Examples (recorded turns in this repo plus the owner's live turn)

1. Owner, 5 Oct 2026 (the defect): "check delivery for last week" -> MISS for HANLIM +
   SRTWC286-SH, 28/09 to 04/10 -> "all products". Today: ETA lines + "Order date" line.
   After: `*orders* for HANLIM TRADING SDN BHD:` with `Order date: 28/09/2026 to 04/10/2026`
   under it, every product in that window (or the same MISS sentence without the product).
2. Order list -> "all products" (hand pass 5 owner console, 17 Sep 2026, contact 900000008
   turns 13-16, `tests/chatbot/journeys/handpass5-delivery-broaden.json`): "orders for
   srtwc286-sh" -> "for hanlim only" -> "okay nvm for all products" -> orders for HANLIM, no
   product code in the header. Works today; unchanged.
3. Stock ask -> "all products" (hand pass 9 owner console, 20 Sep 2026, turn 554cf9c4,
   `journeys/handpass9-all-then-domain-only-catalogue-leak.json`): a widen over the stock
   family with no product left is the WHOLE catalogue, refused as "That would search every
   stock we have" (`_REFUSES_EMPTY_SUBJECT` = inventory). Unchanged by this lane: R2 keeps
   the domain at inventory, and inventory with nothing to scope by still refuses.
4. "all customers" after a miss (the same owner shape, customer axis): "delivery for hanlim
   last week" MISS -> "all customers" -> the same window, every customer, product kept.
   R1 with `broaden_axis` "customer". The scope header the order list prints already says
   `Customer: all customers` for this (`journeys/parity-f1-order-hit-no-picker.json`).
5. Domain switch with a carried window (R4): "delivery for hanlim last week" -> "any
   incoming for srtwc286?" -> the incoming fetch runs with NO `eta_from`/`eta_to`, and the
   dealer reads the ETA lines alone. Before: the window rode into the incoming call and
   the reply.

## Edge cases

- "all" alone after a MISS (no axis named): `broaden_axis` "all", `entity_op` "clear",
  domain stays (already in the prompt). Over a dated order ask it drops every entity and
  the window ("broaden_all_clears_date"), then the DO-ASK date question fires if the list
  needs a period. Unchanged.
- "all products" with NO business domain in play: catalogue request, `master_products`
  (unchanged, the prompt's own last line of BROADEN AXIS).
- A dated domain switch ("incoming for srtwc286 in October"): the message names its own
  window, so R4's clear is overwritten by the verdict's dates at the tail of the focus
  rules (N2). Tested.
- A pick in the roster's domain (`domain_locked`): domains unchanged, window kept.
- The cross-domain ladder (inventory zero-stock -> incoming rung) answers in the dealer
  view too: R5 covers that block whatever stamped the window.

## Questions (max 3; crew-ask on PR #1495)

Q1. After a MISS, should "all products" re-run the SAME window with no product filter?
    (a) yes, same window, product dropped (recommended: the user answered the miss by
    widening the one axis, and the dates were theirs); (b) ask for dates again.
Q2. Should a date window ever carry across a domain change? (a) no, a different domain is
    a different question (recommended; the engine clears it, `domain_change_drops_date_
    window`); (b) yes, keep carrying it and only hide the line on dealer blocks.
Q3. Should the prompt example also name "all customers" / "any date" after a MISS (the same
    rule on the other axes), or only "all products"? (a) all three axes in one example set
    (recommended: one rule, three readings, no second lane later); (b) "all products" only.
