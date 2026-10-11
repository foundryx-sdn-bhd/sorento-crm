# Behaviour card: resolver probes for SO, customer PO ref, our PO, purchase request (REF-PROBES)

Filed to crew as --ask on 11 Oct 2026.

Owner answers (11 Oct 2026, relayed by crew): Q2 (a) PR read = same people as our POs (`purchase_orders.placed`); Q3 (a) unqualified "PO X" probes our PO and customer PO ref, picker on a double hit; Q4 (a) narrowed miss + other-kind hit answers the true kind, named; Q5 (a) header + lines, capped. Q1 (a) this lane does reference numbers only; "which PRs / SOs contain product X" (cases 0216, 0235) goes to a follow-up lane. The build already matches every answer.

```
BEHAVIOUR CARD REF-PROBES (data: sorento_eval_base, read-only; code at origin/main fd3eb5a90)

RULES
R1 Four new exact-match resolver probes beside DO/SPO/GRN (probe table entity_resolver.py:4763): sales_order (sales_orders.so_number), customer_po_ref (sales_orders.po_ref_key, same fold as so_status.lookup_po_refs so_status.py:218), purchase_order (purchase_orders.po_number), purchase_request (purchase_requests.request_number). Whitespace/case fold as _probe_customer_order :997. Never prefix/fuzzy.
R2 Kind not named: one lookup across ALL kinds the asker may see. 1 hit -> answered as that kind, kind named in reply ("Delivery order 202610-0838: ..."); several -> the existing cross-kind picker (turn/reconcile.py:93-113, label "X (delivery order)") stamped per kind; none -> existing did-you-mean / "I could not find X." (compose.py:388/459).
R3 Kind named: parser emits a new semantic entity hint (contracts.py:266 gets sales_order / customer_po_ref / purchase_order / purchase_request; prompt teaches DO/SO/PO/PR words). No regex/shape rule added; SO arm's SO_NUMBER_RE gate (so_status.py:33, turn_runtime.py:4691) stays only as today's fallback. Named kind narrows the probe set. Narrowed miss + exact hit in another visible kind -> "No sales order X. Delivery order X: <card>".
R4 Cards reuse what exists: DO card (today), SO card so_status._card, PO-ref card po_ref_reply_text (so_status.py:262). New: PO card (number, supplier, status, issue date, lines code+qty), PR card (number, customer, status, approval, lines item+qty).
R5 Scope unchanged: dealer/customer contacts probe DO + SO + own PO refs only, scoped as today (so_status._scope_check :127, fetch.py:1256); a hit outside their links = silent miss, never a "exists elsewhere" hint. purchase_order / purchase_request probed only for staff holding the grant (Q2). SO needs sales_orders.outstanding as today (lanes/business/__init__.py:69).
R6 Fix alias: entity_resolver.py:77 maps "sales_order" -> customer_order (DO probe). Becomes its own type; "order"/"do"/"delivery_order" stay DO.
R7 PR read: new GET route by number (read-only, no writes) + MCP read tool + chatbot allow-list (fetch.py:1400 / contracts.py:65) + parity test test_tool_pool_is_read_only.py. Security reviewer runs.

REAL EXAMPLES (masked)
E1 0223 "202610-0838 STATUS?" -> only DO hit ([Customer B] [A/C I]); not SO/PO/PR. Reply: "Delivery order 202610-0838: ..." (today same answer, now kind-named).
E2 0235 "C22802W-MY got in PR request?" -> C22802W-MY is a PRODUCT code. purchase_request_lines.item_code is free text "BRAVAT C22802W-MY UNDER COUNTER BASIN" (no product FK, models/procurement.py:1180) on PR26-0338 and PR26-0339 (both processed_by_cs/approved, [Customer C] (PROJECT), 10 units). Not a reference-number probe (Q1).
E3 0216 "Does [Customer A] order MBF97521" -> MBF97521 is a PRODUCT (products.product_code), on SO lines (SO2610-0127, SO2610-0049 ...), not a DO/SO number (Q1).
E4 0298 "CHECK PO 2609-176" -> no DO/SO/PO/po_ref hit in either dev DB. Honest miss "I could not find PO 2609-176." stays.
E5 SO2610-0127 typed bare (staff) -> SO hit only -> "Sales order SO2610-0127: ..." (today only reached via SO_NUMBER_RE arm).

EDGE CASES
C1 DO=PO collision: PO-2026/04-0011 is a DO ([Customer D]) AND our PO (closed). Staff bare -> picker "PO-2026/04-0011 (delivery order)" / "(purchase order)"; dealer -> DO only (PO invisible).
C2 DO=SO collision: only junk row "TEST" in both tables. Picker if typed.
C3 po_ref empty in eval_base (0 rows); 4 in cagent_stack, "HT-7788 / A" on 2 SOs -> both cards (existing grouped reply).
C4 Our PO "202610-S0023" vs DO "202610-1227": same prefix shape, so no shape guess anywhere; data decides.
C5 so_number / po_number unique per company only; same number in 2 companies -> existing company suffix in picker (narrow.py:164).
C6 PR has no company_id (not CompanyScopedMixin) -> no company scope; request_number not unique (procurement.py:1091) -> several rows -> in-kind picker.

QUESTIONS
Q1 0235 and 0216 name a PRODUCT, not a reference. (a) this lane only does reference numbers; product-in-PR-lines and product-in-SO-lines become a follow-up lane (b) also add "which PRs carry product X" (item_code word match) here, 0216 stays out (c) both here. REC (a): brief's goal is reference-kind resolution; item_code is free text needing its own match rule; mixing grows to L. If owner wants E2 answered now, (b).
Q2 PR read grant: PR list/get API has NO permission slug (procurement/purchase_requests.py:54,87, user-or-api-key only); only write slugs exist (:363-579). (a) reuse purchase_orders.placed (answer.py:1190, the purchasing audience) for PO and PR chat reads (b) new procurement.purchase_requests.view slug (seed migration, permission change) (c) any staff. REC (a): no migration, same people read POs and PRs.
Q3 Staff typing "PO X" unqualified: our purchase_order or a customer's po_ref? (a) parser reads "PO" as both kinds -> probe both, picker on a double hit (b) staff "PO" = our PO only. REC (a): CS staff check customers' POs daily (SO-PO-REF arm exists), data decides.
Q4 Narrowed miss with other-kind hit: (a) answer the other kind ("No sales order X. Delivery order X: ...") (b) only name it ("X is a delivery order, not a sales order") and wait. REC (a) per brief.
Q5 New PO/PR card lines: (a) header + all lines (b) header only + line count. REC (a) capped at the existing card line cap.

Continuing on recommendations: UAC + plan next, then tester red tests.
```
