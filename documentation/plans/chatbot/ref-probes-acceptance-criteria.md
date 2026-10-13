# UAC: the kind of a reference number comes from the data (REF-PROBES)

Plan: `PLAN-ref-probes-11oct.md`. Card: `ref-probes-behaviour-card.md`. Assumes card
recommendations Q1 (a), Q2 (a), Q3 (a), Q4 (a), Q5 (a) until crew relays the owner's answers.
Tests in `sorento_crm_backend/tests/` (chatbot ones under `tests/chatbot/`).

## Resolver probes

AC-1  `resolve_references` with allowed `sales_order` resolves a typed SO number exactly,
      case and whitespace folded as the DO probe does: "so2610-0127" -> one `sales_order`
      match, canonical "SO2610-0127". Never prefix or substring.
AC-2  `customer_po_ref` matches `sales_orders.po_ref_key` with the same fold as
      `scm.po_ref.po_ref_key`; every SO carrying the ref is a match.
AC-3  `purchase_order` matches `purchase_orders.po_number` exactly; `purchase_request` matches
      `purchase_requests.request_number` exactly; a number held by two PR rows gives two matches.
AC-4  `sales_order` is its own resolver type (no longer an alias of the DO probe); `order`,
      `do`, `delivery_order` still mean the DO probe.
AC-5  SO and PO matches carry company attribution like other company-scoped kinds; a PR
      (no company column) does not.
AC-6  The resolver's all-types fallback never probes the four new kinds: a call that did
      not list them never returns them.

## Who sees which kind

AC-7  The kinds probed for a reference ask are the asker's visible kinds: always delivery
      order; sales order + customer PO ref with grant `sales_orders.outstanding`; purchase
      order + purchase request only for staff holding `purchase_orders.placed`. A dealer
      never gets a PO/PR match, whatever grants it holds.
AC-8  A dealer's sales order / PO ref match outside the dealer's linked customers is dropped
      before any picker or answer: the reply never names it, never says it exists elsewhere.

## Unnamed kind

AC-9  A number typed with no kind word, one hit across the visible kinds -> answered as that
      kind, the kind named in the reply ("Delivery order", "Sales order", "PO ref",
      "Purchase order", "Purchase request").
AC-10 Hits in two or more kinds -> one picker, each option "<number> (<kind words>)", e.g.
      "PO-2026/04-0011 (delivery order)" / "PO-2026/04-0011 (purchase order)".
AC-11 No hit -> today's did-you-mean / "I could not find X." unchanged.

## Named kind

AC-12 The parser reads DO / SO / PO / PR / PO ref as entity hints (semantic, prompt taught,
      no new regex or number-shape rule). A named kind with a hit in that kind answers that
      kind even when another kind also hits.
AC-13 "PO" names both our purchase order and a customer's PO ref; a hit in both -> picker.
AC-14 Named kind misses, another visible kind hits exactly -> reply starts
      "No <kind words> X." then answers the true kind.

## Answers

AC-15 Dashed SO numbers are found: "SO2610-0127" typed (any case/spacing) returns its card
      (today a miss: `so_status.lookup` compares the dash-stripped key to the stored dashed
      number).
AC-16 Staff PO ref asks return the SO cards (today staff see nothing).
AC-17 Purchase order card: number, supplier, status, issue date (d Mon yyyy), then each line
      code x qty, capped as the SO card caps lines. Purchase request card: number, customer,
      status, approval, then each line item x qty, same cap. Two rows -> two cards.
AC-18 A dealer without `sales_orders.outstanding` keeps today's not-enabled line.

## Purchase request read surface (security review)

AC-19 Read-only `GET` purchase request by number route, existing procurement router and auth;
      MCP read tool for it; listed in the chatbot read-only allow-list; the read-only parity
      test passes. No write path added.

## Regression

AC-20 Touched chatbot topics (DO ask, SO number ask, PO ref ask, kind pick) and the 30-turn
      smoke pass the crew eval gate.
