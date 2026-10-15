# Behaviour card - OI-RESERVE-RECEIVED (v1, 15 Oct 2026)

Request CS to reserve on an OI line whose PO/SPO link points at a line already received into the
warehouse (its stock went to other sales orders). Base origin/main 504ce9252. Data: shared dev DB
`sorento_cagent_stack`, read-only. Customer names left out.

## Rules in plain words

1. **Who can be requested.** An OI line can be sent to CS when part of it is still unlinked
   (today), OR when some of its links point at PO/SPO lines that are FULLY received. A link to a
   line that is still on order (incoming, or only partly received) blocks that part as today:
   purchasing re-links it.
2. **How much can be requested.** Line qty minus links to lines still on order, minus what CS
   already reserved, minus bundle. Links to fully received lines do not reduce it. Example:
   SRT6638, 1260 linked to a received SPO, so 1260 can be requested (today 0).
3. **CS accepts some or all.** The whole received link is released, the reserve covers the line,
   and anything CS could not reserve shows as Remaining (To buy) for purchasing.
4. **CS declines (0).** Nothing changes: the received link stays, the line stays On PO/SPO, and
   the pill reads Not reserved (as today).
5. **A released SPO/PO line is used up for good.** It is never offered again, to this line or to
   any other sales order, by Link now, Auto link, Confirm, PO confirm, or a PO/SPO import.
6. **No stock is held in the CRM.** CS reserves in AutoCount. Stock Debt and the board do not
   change. The stock-transfer push is a later lane.
7. **What the line shows after accept.** The PO/SPO columns lose the released document. The pill
   reads Reserved N. Taken = reserved. Remaining = shortfall. History (Why) carries the note in
   section 8.
8. **Texts.**
   - Row note (History "Why"): `Released SPO-2026/01-0005 (received, stock used by other orders) for CS reserve 1260 @ BRW`
   - Disabled tooltip when every link is to a line still on order: `{item}: on a PO/SPO not received yet, nothing left to request`.
     The other tooltips are unchanged.
   - Reserved email (to the requester, Cc the raiser, as today): the line's REASON cell starts with
     `Released SPO-2026/01-0005 (received).` and then carries CS's own reason. The LOCATION column
     already names the reserve location (BRW). No template change (see Q4).

## Real dev examples (sorento_cagent_stack)

| # | Line | Today | Free stock | Accept | After |
|---|------|-------|-----------|--------|-------|
| 1 | OI-2609-0284, SO313699 L19, SRT6638, qty 1260 | 1260 on SPO-2026/01-0005 (1260/1260 received, BRW-IR), SO delivered 0, On PO/SPO, Request disabled | BRW 1523, BRW-BB 160, BRW-NTC 140 | CS reserves 1260 @ BRW | SPO link gone, Reserved 1260, Taken 1260, Remaining 0 |
| 2 (partial) | OI-2609-0284, SO313699 L3, B2154-NL, qty 1092 | 1092 on SPO-2026/01-0143 (received) | BRW 435, BRW-IR 537, BRW-BB 1379 | CS reserves 435 @ BRW, reason "only 435 free at BRW" | SPO link gone, Reserved 435, Remaining 657 (To buy) |
| 3 (partial) | OI-2609-0214, SO341788 L43, SRT53-CR, qty 788 | 788 on SPO-2026/06-0033 (received, BRW-BB) | BRW 28, BRW-BB 788 | CS reserves 28 @ BRW (or 788 @ BRW-BB) | Remaining 760 (or 0) |
| 4 (mixed) | OI-2609-0612, SO314593 L1, B2154-NL, qty 182 | 158 on SPO-2026/01-0143 (received) + 24 on SPO-2026/09-0036 (0 of 746 received) | as #2 | requestable 158; CS reserves 158 | 158 link released, 24 open link stays, Remaining 0 |
| 5 (bundle) | OI-2609-0284, SO313699 L30, SRTWC8608-SC, qty 420, bundled 266 | 154 on SPO-2026/01-0085 (received) | BRW 26 | requestable 154; CS reserves 26 | link released, Reserved 26; bundle is re-derived, so it may cover part of the 128 left |

Partly received case (not eligible): OI-2609-0020, SO391687 L52, TPE-9201-300, 3 on
PO-2026/04-0018 (1796 of 3000 received): the line is still on order, so the request stays
disabled.

Scale on dev: 1116 lines are On PO/SPO, fully linked only to received lines, with the SO line not
delivered. Another 101 Partly-on-PO/SPO lines have only received links. 79 of the 1116 hold two or
more received links (e.g. OI-2609-0168, SO390918 L16: SPO-2026/05-0037 + SPO-2026/04-0046); all
of those links are released together. Existing reserves on dev: 8 reserved, 3 cancelled. One was
a partial accept (88 asked, 80 reserved) and one a zero accept (44 asked, 0).

## Edge cases

- **Mixed received + open links:** only the received links are released; the open links stay
  (example 4).
- **Partly received line:** treated as on order and not requestable (rule 1).
- **Bundle:** requestable excludes bundle. After release the bundle is re-derived, as after any
  unlink (`derive_bundles`, project_order_inquiry_service.py:8043), so the bundle host may absorb
  part of the shortfall.
- **CS declines:** nothing changes (rule 4). The email goes out as today with RESERVED 0.
- **CS enters more than allowed:** refused by the existing cap, "reserved quantity must be
  between 0 and {cap}" (order_inquiry_reserve_service.py:239-244). The cap is now
  min(requested, requestable at commit).
- **Links change between request and commit** (e.g. purchasing links 500 to a new open SPO): the
  cap shrinks to 760, as today (cap re-read at commit, :806).
- **CS later amends the reserve down, even to 0:** the released link stays released and the
  shortfall goes to Remaining. No re-link, because the release record outlives the reserve.
- **Unlink selected** (today's workaround): unchanged, and out of scope. It has its own re-link
  gap, flagged in Q1.

## Facts (base 504ce9252)

- Request gate: state raised/partly_linked only, `_REQUESTABLE_STATES` (order_inquiry_reserve_service.py:66, 409 at :482-487).
  `_remaining` subtracts every link (:122-130). Commit cap is min(requested, remaining) (:806).
  Commit writes the reserve link and event (:877-894). The FE gate mirrors this (OrderInquiryDetail.tsx:102-109, :119-134).
- A line fully linked to a received SPO is `placed` ("On PO/SPO"). Receipt never changes the state
  (`_coverage_state`, project_order_inquiry_service.py:8029-8039).
- The links table has no released/history flag (models/project_so.py:1137-1263). Target capacity
  is tallied in one place, `_linked_by_target` (project_order_inquiry_service.py:8370-8398). The
  book step reads it through `_claimed_capacity` (project_order_inquiry_import_service.py:2124-2134, free = capacity - used :2281).
- The book step re-links only when the SPO/PO line's `from_so_line_ref` names the SO line
  (`_refs_named_by_book` :3678). On dev, SPO-2026/01-0005's allocation has NO book ref, so the
  SO313699 case would not re-link today. But 21 of the 1316 received targets on the 1116 lines do
  carry a book ref. UNVERIFIED whether prod has more.
- Other links onto received lines: `relink_to_matching_lines` (:11543-11691, closed PO lines
  allowed, capacity net of claimed) and `planning_change_service.py:4080-4125`. Both UNVERIFIED as
  real re-link paths. Auto link, Link now candidates and place-on-PO only offer open lines (:8734-8835, :10303).
- Reusing #973's `redirected_to_pool` on this line would hide its shortfall from purchasing
  (demand NOT_REDIRECTED_SQL, scm/demand.py:368), so it is not an option.

## Questions (crew / owner)

**Q1 - Re-link guard (rule 5).**
- (a) **One small additive table of released links** (line, released target, document, qty,
  who/when). `_linked_by_target` adds released qty to the target's tally, so that SPO/PO line
  stays used up for EVERY sales order on the book step and its D3 displacement.
  `relink_to_matching_lines` keeps its own tally (:11633-11636) and needs the same read. The
  line's own sums never read it. One migration (SQL for the crew test copy) and about five touch
  points: commit, the two tallies, History, tests.
- (b) No migration: the book step refuses every received target for lines that have had a CS
  reserve. It protects only this line. Another sales order whose book names the same SPO (21
  targets on dev) can still take the freed qty, so it breaks rule 5 for "other SOs".
- (c) No migration: copy #973 and move the received link onto a hidden redirected history line.
  It touches line identity across the importer, replan and revisions, so the risk is high.

**Recommendation: (a)**, because the rule says never, for any SO, and (a) is the only option that
holds for every path from one read point. Size impact: one additive migration, so this M lane
gains the L migration checks.

**Q2 - On a line that is partly unlinked AND has a received link, when does accept release?**
- (a) Only when the request reaches into the received part (requested > the unlinked remaining).
  A smaller request is today's plain reserve, and the received link stays.
- (b) Any accept releases.

**Recommendation: (a)**. Example: 200 qty, 150 received link, 50 unlinked. A request for 50 is
today's flow and should not silently drop 150 of cover.

**Q3 - Mixed received + open links:** is this line eligible, with only the received links
released and the open links kept? **Recommendation: yes** (example 4; rule 1 says open links are
not touched).

**Q4 - Where the email names the released SPO.**
- (a) Prefix the REASON cell in the existing reserved email: no template change, no migration.
- (b) A new RELEASED column, which needs a template UPDATE held for the owner.

**Recommendation: (a)**.

**Q5 - Wording.** Is the row note and tooltip text in rule 8 OK? **Recommendation: as written.**
