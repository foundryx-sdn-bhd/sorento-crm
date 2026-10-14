# Behaviour card: park reserve requests and reserve notices, send in bulk (OI-STAGED-NOTIFY)

Base: origin/main 760ed152b. Size L (additive migration, two sides, new list filter + bulk send + preview).

## Today (code)

- "Request CS to reserve" (OI gear, `OrderInquiryDetail.tsx:775-784`) opens `ReserveRequestDialog.tsx`;
  Send request = `create_request` (`order_inquiry_reserve_service.py:422`) queues ONE email per
  request at commit (`:558`, drained `:1182`).
- CS "Reserve (N)" (header button, `OrderInquiryDetail.tsx:846-855`, no lightbox) = `commit_request`
  (`:620`) queues ONE "Reserved" email per request touched (`:966`). An amend to the same figure
  already sends nothing (`:819-822`).
- Recipients (seed `oirs_0001_reserve_requests.py:380-405`, dev DB identical): request mail =
  fixed CS users (EMPTY on dev; seed note says owner adds Eling on prod, UNVERIFIED) + requester
  + OI raiser; reserved mail = requester + OI raiser. One email, the person who acted moved to Cc
  (`automation_service.py:641-660`). So on dev: request mail To = OI raiser, Cc requester;
  reserved mail To = requester, Cc raiser.

## Rules (proposed)

1. Request lightbox gets a tickbox "Park this first", ticked every time it opens.
   Ticked: the request is saved (CS can see and answer it on the OI page) but NO email.
   Unticked: sent at once, exactly as today.
2. Documents list (default OI screen) gets a "Parked" filter and a toolbar button
   "Send parked requests to CS (N)". N = parked requests with at least one line CS has not
   answered yet. Click = preview dialog: one card per email (To, Cc, subject, OI count, full
   rendered body), then "Send N emails".
3. One email per To person. Every parked request whose single email would go To that person
   rides in it; Cc = everyone those single emails would have Cc'd (deduped). Built from the data
   at send time: still-open lines only, today's remaining/qty.
4. CS side, same idea: a "Park notice" tickbox (ticked by default) beside "Reserve (N)". Ticked:
   reserve saved, purchasing notice parked. Unticked: notice sent now, as today.
   CS gets "Notify purchasing (N)" on the Documents list, same preview, one email per To person
   (the requester), listing only lines whose reserved figure differs from what purchasing was
   last told. A line amended back to the figure already sent drops out; nothing actionable = no email.
5. A line changed after its notice was sent is parked again ("saved = sent", as #1631): the next
   Notify purchasing carries only that line, with the new figure.
6. No automatic or daily send. Forgotten items stay in the count until someone presses the button.

## Real dev examples (sorento_cagent_stack, read-only)

| # | OI | SO | Customer / project | OI raiser | Open ORDER rows |
|---|---|---|---|---|---|
| 1 | OI-2609-0612 | SO314593 | PTL SUPPLIES SDN BHD / SG DUA | Eling | 9 (B2154-NL 220 @BRW-IR, CSA150 220 ...) |
| 2 | OI-2609-0614 | SO314595 | PTL SUPPLIES SDN BHD / SG DUA | Eling | 8 |
| 3 | OI-2609-0592 | SO314592 | PTL SUPPLIES SDN BHD / SG DUA | Eling | 5 |
| 4 | OI-2609-0718 | SO421824 | MENTARI BERSERI SDN BHD (PROJECT) / CARE RESIDENCE | Niki | 17 |
| 5 | OI-2609-0727 | SO416694 | SETIA-WOOD INDUSTRIES SDN BHD (PROJECT) / 07-001 | Niki | 6 |

- Joey (Purchasing Manager Role) parks a request on each of the 5. Filter "Parked" shows 5;
  button reads "Send parked requests to CS (5)". Preview on dev config: 2 emails. Email 1 To Eling,
  Cc Joey, 3 OIs (0612, 0614, 0592). Email 2 To Niki, Cc Joey, 2 OIs (0718, 0727). With a fixed CS
  user (e.g. Eling) on the prod automation: 1 email To Eling, Cc Joey + Niki, all 5 OIs.
- Before Joey sends, Eling answers OI-2609-0592 in full: it drops out, button reads (4).
- Eling reserves on OI-2609-0612 with Park notice ticked: B2154-NL 220 @BRW, CSA150 0 "no stock".
  She amends B2154-NL to 200 the same day. Notify purchasing (1) preview: 1 email To Joey, Cc Eling,
  OI-2609-0612 with B2154-NL 200 and CSA150 0 (latest figures, one email).
- After sending, she amends B2154-NL to 150: Notify purchasing (1) again, only that line. If she
  amends it back to 200 before sending, it drops out (purchasing already has 200).

## Edge cases

| Case | Behaviour |
|---|---|
| Parked request cancelled before sending | Drops out, no email (cancel never emails today, AC-RS-19). |
| CS answers some lines of a parked request first | Email lists only the lines still open; all answered = drops out. |
| Two clicks, two tabs, two people | Rows locked, then stamped in the same transaction as the outbox rows; the second press finds nothing and says "Nothing left to send". |
| Nobody to send to (no fixed CS user, raiser has no email) | Preview shows that card as "No recipient, stays parked"; it is not stamped and stays in the count. |
| A request answered while parked, then its notice parked | Independent: the request side and notice side each have their own parked flag. |
| Old requests and reserves (before this ships) | Count as already sent. No backfill: new nullable columns, NULL = not parked. |

Schema (additive, no backfill): `order_inquiry_reserve_requests.parked_at`;
`order_inquiry_reserve_request_rows.notice_parked_at` + `notified_qty` (the figure last sent).

## Questions (each with a recommendation)

- Q1 Who presses: "Send parked requests (N)" sends (a) only the requests YOU parked, or (b) everyone's.
  Rec (a): you send what you checked; a colleague's half-done batch stays theirs. The filter still
  shows everyone's with a "Parked by" column. Same rule for CS's "Notify purchasing (N)".
- Q2 Grouping: (a) one email per To person, Cc merged across the OIs in it; (b) one email per exact
  To + Cc set (more emails, Cc exact per OI). Rec (a): that is "one email per CS recipient".
- Q3 CS tickbox: CS has no lightbox today. (a) "Park notice" tickbox beside "Reserve (N)", ticked
  by default, no extra click; (b) Reserve (N) opens a small lightbox with the tickbox (one extra
  click per OI). Rec (a): CS works ~100 OIs, one click each.
- Q4 Where: (a) Documents list only (one row per OI, default screen; filter in its Filters popover,
  button in its toolbar); (b) Documents and Lines views. Rec (a): one place to look.
- Q5 Subject of a bulk email: (a) "Reserve requests: 3 order inquiries (Joey)" /
  "Reserved: 2 order inquiries (Eling)", body one section per OI; (b) keep today's per-OI subject
  even for bulk (first OI named, "+2 more"). Rec (a). A single parked item keeps today's subject.

## Owner notes on mock v1 (14 Oct, via crew) - supersede the rules above where they differ

1. Default SEND, not park: "Park this first" opens unticked; the CS "Park notice" too (my call A,
   same rule, filed with mock v2).
2. ONE summary preview: a table To | Cc | Order inquiries | Lines, one line per email, no bodies.
3. A multi-OI email is short: heading with the count, intro, first 5 OIs (OI NO, S/O NO,
   CUSTOMER, LINES), "and N more", ONE button that opens the Documents list filtered to exactly
   that email's OIs (send id in the URL, chip "From email: ..."). An email with one OI keeps
   today's detailed email (my call B, filed with mock v2).
