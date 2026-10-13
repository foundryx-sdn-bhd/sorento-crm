# UAC: PV2-FIX (14 Oct 2026)

Plan: `PLAN-pv2-fix-14oct.md`. Cases are real dev-split cases (`crew/state/eval/dev`); recorded parser outputs in
`crew/state/eval/pv2-record/dev-20261014T012404`.

## U1 Product phrase that is not an exact code

- U1.1 "Hi, can help check WF12C when arrived" (cand-0043): PV2 asks a normal "which product" question whose
  options are the real SRTWF12C-* products v1's product search returns, labelled the way v1 lists products. No
  "couldn't find". Options capped at `CUSTOMER_CANDIDATE_CAP` (8).
- U1.2 "MKS9505-HC-SS / STOCK" (cand-0098): v1's search has ONE hit (MKS9505-SS-HC) -> PV2 resolves to it and the
  v1 stock answer for MKS9505-SS-HC runs (parity: same reply as v1 for that code + domain).
- U1.3 "7625有多少货" (cand-0075): a which-product question with the MWC7625-SH-* candidates.
- U1.4 A phrase v1's search finds nothing for -> v1's existing not-found wording for a product, not a new line.
- U1.5 Picking option N of U1.1 resolves that product and answers the held domain (incoming ETA) with v1's reply.
- U1.6 An exact code still resolves exactly as before (no candidate question for an exact hit).
- U1.7 No option label carries a UUID, an id or " · ".

## U2 Quantity

- U2.1 "check stock / SRTWC286-SH-200 5unit" (cand-0146): parser emits the product with quantity {number 5, unit
  "unit"}; no spec. The v1 verdict entity for SRTWC286-SH-200 carries quantity 5; the reply is v1's quantity-check
  answer ("SRTWC286-SH-200 x 5: ...").
- U2.2 "Check stock / Srt2367 2pc / Srt2565 2pc / SRT93-SS 2pc" (cand-0150): three products, each quantity 2.
- U2.3 "SRTBF11705 10 SETS ADA?" (cand-0153): quantity 10 on SRTBF11705, domain inventory.
- U2.4 A quantity rides on the held product: a later "stock?" refinement keeps it unless a new quantity is said.
- U2.5 Schema: `Entity.quantity` is `{number: number, unit: string|null} | null`, every property required in the
  strict schema; the prompt tells the model a count of units/pcs/sets/bijik/unit is a quantity, never a spec.

## U3 Document numbers and forms

- U3.1 "Delivery 202609-2012" (cand-0204): parser returns kind delivery_order (doc type) and doc number
  "202609-2012" separately; resolution uses the bare number through v1's DO lookup; reply is v1's delivery status
  answer for that DO.
- U3.2 "PS202609-0469 done deliver?" (cand-0219) and "202610-0838 STATUS?" (cand-0223): resolved by v1's doc
  lookups to the document v1 answers on (the DO dated 2026-09-29 / 2026-10-05).
- U3.3 "Mocha Application form" (cand-0007): a which-form question listing the real MOCHA application forms
  (DISPLAY STANDEE / CONCEPT RENOVATION), never "couldn't find any form".
- U3.4 Parity: PV2's reply for a resolved document equals v1's for the same document + domain.

## U4 Guards (every fix)

- No word list, regex or word-overlap check on user text added in PV2 code.
- System prompt <= ~8k o200k tokens; index <= 8,000 chars.
- Dev split: target >= 87/107 and multi-turn >= 28/40; zero wrong-data / permission cases; prompt tokens p95 and
  latency p95 within the bar (12k / 8 s).
