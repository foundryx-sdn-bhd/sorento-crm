# Owner hand pass 2 (AC-1593), 17 Sep 2026 15:40 to 15:49 MYT, phone via tunnel, stack :3081/:8081 on 2453e64d0, clone `sorento_ai_automation_rearch`, contact 900000008

27 console turns, all stored on the clone (`chatbot.turns`, ingress console). Turn ids are the first 8 hex chars. Each finding becomes a replay case (expected written per the ruling) BEFORE the fix.

| # | Turns | What happened | Ruling (owner, 17 Sep) |
|---|-------|---------------|------------------------|
| 1 | 7c60b2e6 "Delivery for hanlim" | Six-line customer roster, one per ledger (A/C I, II, III ...). | Ledgers of one trading name are ONE customer family (D: ledger family = one customer by default): no picker for hanlim; the family's uuids are all fetched. |
| 2 | 7c60b2e6, d369447b | Customer roster lines carry no stamps. | Customer rosters carry has DO / no DO stamps like product rosters carry incoming. |
| 3 | 70be252c "Last purchase cost for srtwc286" | Product picker before the answer. | Purchase cost lists all variants: seed `purchase_cost` narrowing product = list_all; stays configurable on the Domains page. |
| 4 | c45e2929, d405a92b "2", 09b894b0 "Delivery orders", a0b1baac "1" | Outstanding report asked "which document" then ignored every answer, re-ran the same report. | Answering the scope / detail question re-runs the report with that scope (contracts 38/39). Coder 12 in flight. |
| 5 | c45e2929 "Outstsnding DO for 7445" | Report scoped by HANLIM [A/C III] carried from six turns earlier. Parser said entity_op replace. | A new ask that names its own entities and says replace drops the carried customer (old engine: Customer: all). Recall is off; this is focus carry, not episodes. |
| 6 | c45e2929 | Product 7445 dropped silently, "Product: all". | A product token on an order ask resolves (roster when ambiguous) and filters the report; never dropped silently. Seed `order` narrowing product = optional_filter (resolve and filter). |
| 7 | c45e2929, 1d194952 | DO list shows no product lines under each order. | The report lists the products under each DO as the old engine did (measure tool output vs compose first). |
| 8 | 29605e65 miss, then 586746d3 "5", a253e14f "3" | The escalate offer replaced the sticky customer roster; "5" and "3" re-printed the offer. | A miss after a roster pick keeps the roster as the open question; the offer sentence is appended; a later number picks from the roster, "yes" escalates. |
| 9 | 64f7b2f2 "Promo for srtwc286", 142dd695 "1", 1cf36d59 "2" | Tier question re-asked after each pick; parser resolved the pick. | A tier pick sets the tier and the promotion answer follows. |
| 10 | 70be252c roster, 0a181cfd "All" | Roster re-printed; parser said picks all. | "All" over a roster answers for every option, whatever the narrowing policy. |
| 11 | 3d6f8424 "Last purchase cost and stock", 78f34206 "1"; 29284863 "Incoming and stock for 7445", cac3f42e "1" | Only the first domain answered after the pick. | A pick answers every domain the ask named (fan-out kept through the roster). |
| 12 | c45e2929 "Outstanding DO ..." | Asked "which document" although the message said DO. | The document named in the message sets the scope; the question arms only when no document was named. |

Also seen, working: stock list, incoming roster with stamps, picks by code, sticky re-pick on the hanlim rpacc roster (1d194952 then a180b3a1), last cost detail, Golden Win roster, incoming for 7445 variant with attachment.
Ruled 17 Sep: the cross-domain ladder appending the stock section under a no-incoming answer (d5eb6174) is fine, keep.
