# UAC: PV2 redesign (14 Oct 2026)

Plan: `PLAN-pv2-redesign-14oct.md`. Each round adds its section before its red tests.

## R1 Answer register writer
R1-1 After a PV2 turn whose v1 lane printed a delivery-order list, the saved PV2 state's register holds one entry per
     printed order (kind delivery_order, label = the printed order number) in printed order, ids R1..Rn.
R1-2 Each order entry has child entries for its product lines (kind product, label = printed product code,
     parent = the order's R id), from the MCP item's unprinted `lines` key, never by parsing the reply text.
R1-3 A PO placed list writes purchase_order entries with their product as a child entry.
R1-4 Stock, incoming, matching-product, attachment, set-page and top-selling lists write product (or their
     entity_type) entries, one per printed row, in printed order.
R1-5 A sales-order card or SO list writes sales_order entries (label = SO number) from so_status's structured rows;
     PO/PR ref cards likewise.
R1-6 Report rows (report_ask drill rows; delivered by-customer/by-product breakdown) write entries of the group_by kind.
R1-7 A field removed by the reveal filter, and an item's unprinted title, are never recorded; keys (codes, uuids,
     numbers) are stored but no entry label shows a uuid.
R1-7b Only rows the reply printed are recorded: rows kept in `answers` but hidden from the text (summary mode,
     needs-quantity stock mode, duplicates skipped on a status-only ask) are not recorded.
R1-8 The register keeps at most 20 entries and only the last 2 replies; a PV2 direct reply (no lanes) adds nothing and
     keeps the previous reply's entries; a new session clears it.
R1-9 Stored state from before R1 (no register field) loads unchanged.
R1-10 MEMORY is byte-identical to today for the same state (the register is not rendered in R1).
R1-11 A non-staff contact asking for a product's PO list is not refused for lacking purchase_orders.placed (owner
      ruling 14 Oct); the answer matches v1's for the same contact. Staff behaviour is unchanged.
R1-12 v1 turns (parser mode v1) are unaffected: no register write, same replies.

## R2 Subject stack + PendingQuestion.origin
R2-1 Every subject gets a stable `sid` when it starts; S1 = held, S2..S5 = topics newest first (cap 5 as today). A subject
     keeps its sid when pushed to topics and when switched back to.
R2-2 Register entries carry the sid of the subject they were printed under; after a new topic pushes that subject,
     `subject_label(state, entry.subject)` is "S2".
R2-3 `subject_at(state, "S<n>")` returns that subject; an unknown or out-of-range S id returns None.
R2-4 MEMORY is byte-identical to today for the same state (no S<n>/R<n> rendering in R2).
R2-5 Whenever a question interrupts an ask (entity clarify, did-you-mean, flagged clarify, a missed ask's question, the v1
     picker after a PV2 Lookup), `PendingQuestion.origin` holds the full original ask.
R2-6 A pick completes the ORIGINAL ask with the picked entity: "any eta mou?" -> pick "1" answers ETA (incoming) for the
     picked product; an ask with a period ("stock for SRTWC286 last month") keeps its period after the pick; attachment
     kinds, quantity, specs and status likewise.
R2-7 State saved before R2 (with `deferred`, no `origin`, no `sid`) loads and a pick on it behaves as today.
R2-8 Every PV2 answer path that prints rows writes the register (each path named in the test); the stock-ask render
     records the rows it prints.
R2-9 output_structurer drops order `lines` whenever the Products field is hidden (test).
R2-10 `printed_rows` stays aligned with the envelope's `figures` when an answer is not a dict.
R2-11 No LLM calls; v1 turns unchanged.

## R-GAPS Frozen v1 lanes (owner rule 14 Oct; source crew/state/reports/PV2-FROZEN-LANES-coverage-14oct.md)
All from handwritten parses (no LLM). [cust] = a customer named in the earlier turn.
RG-1 (#1570) Staff: "[cust] 7/10/2026 有送货吗？" -> "SRT391-GM几时会安排送货？": the carried customer plus the new product is a
     clarify ("Delivery for SRT391-GM: only [cust] (from earlier) or all customers?"), nothing fetched. PV2 table case-072
     expects this. The 3rd identical clarify to staff refers them to the salesman instead of asking again.
RG-2 (#1582) "250mm srtwc286 got stock?" answers only the variants with 250 on any axis, with no Length/Width question;
     "srtwc286 300mm" with no 300 on any axis -> "No 300 mm for SRTWC286. Sizes on file: ..." (localized).
RG-3 (#1603) A v1 escalate offer answered "yes" -> "Routed to your PIC, they will reply shortly." and the hand-over saves
     the asked text, never "".
RG-4 (#1617) "SRTWT5880-CR picture" -> "HAVE 10??" (no open question) -> the stock answer for 10, no photo re-send, no
     type question.
RG-5 (#1443) A set code -> its member stock, never "I need at least one filter"; "SRTWC8608 stock" offers its sets.
RG-6 (#1517) An ask with no product ("product") -> a which-product question and no tool call; a product list never shows
     an unsearchable or inactive row (e.g. "FOR ACCOUNT DEPARTMENT ONLY").
RG-7 (#1572) "Srtkt71-ss outstanding" -> "Dealer outstanding only" keeps the product and adds the channel; "CBDR4003
     stock" -> "Top sales Sorento and Mocha" -> "Sorento and mocha" never carries CBDR4003 or BK007A.
RG-8 No LLM calls; v1 turns unchanged.
