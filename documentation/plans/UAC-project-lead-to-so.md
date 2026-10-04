# UAC - Lead to Sales Order (module `projects`, phase 2)

> **Table names in this document predate the schema move.** On 2026-08-15 the projects
> module's 47 tables moved into a dedicated `projects` Postgres schema and the 34 that
> carried a `project_` prefix dropped it: `project_leads` is now `projects.leads`,
> `project_quotation_lines` is `projects.quotation_lines`, and so on. The 13 unprefixed
> ones only changed schema. Nothing else in this document changes. See
> [ADR-0011](../adr/0011-project-sales-tables-live-in-the-projects-schema.md) and
> `documentation/plans/PLAN-projects-schema-move.md` for the full mapping.

**Status:** Groups A-G and M ACCEPTED against the client's own artefacts (2026-08-02/03).
Groups H, I, J, K and N remain unbuilt, matching the slices deferred in PLAN section 4.
Grilled 2026-08-01 against the client's own artefacts.
**Slug:** project-lead-to-so
**Owner:** jayson
**Depends on:** the built `projects` module (S0-S6b), `sales_orders` / `sales_order_lines`
(AutoCount mirror), `item_packages` (AutoCount PackageDTL mirror), SCM reorder engine.
**Glossary:** `documentation/CONTEXT.md` (binding)

## Sources (read, not summarised second hand)

| Artefact | What it settled |
|---|---|
| `Presentable_Sorento_Operational_Discovery_Study_v4.drawio.pdf` (3 pages) | The end to end flow: lead sourcing, spec-in, PO to SO (5A), SO to delivery (5B), change after commit (5C) |
| `QT-004188 PS26-0143.pdf` | Quotation shape: components, zero priced companions, RM 1,805,907.02 over 60 lines |
| `Buimaco Bulk PO - Tuju Residence - (R1).pdf` | Customer PO: a SCAN, no text layer, SETS not components, two handwritten amendments dated months apart, one struck through line |
| `Delivery Schedule - Buimaco (Tuju Residences).pdf` | Schedule is a MATRIX: phase rows x product columns, with a TOTAL QTY row that reconciles to the PO |
| `Revised Delivery Schedule - SLG Construction (R2).pdf` | A revision moves every date, keeps every quantity, and can be issued by a different company than the PO |
| `SO397450 / SO397460 / SO376200 PS26-0143.pdf` | SO shape: per line delivery date, Reserve column, bill to trading house, deliver to main contractor. THREE SOs on one PO: two by area, one an early product subset raised before the PO existed |
| 4 x `Foundryx Mail - FW_ BRW-BB @ ...pdf` | The order inquiry protocol and its verbs, and the 42 minute correction that motivates netting off pre-orders |
| 3 x `MARYAM ... .xlsx` | The order inquiry column contract and the CHANGE sheet's second shape |
| ecohub `app/(dashboard)/leads`, `clients`, `prisma/schema.prisma` | Lead lifecycle reference (receive, qualify, assign, convert) and the client-as-billing-entity split |

---

## Journey (Phase 0 - this governs; the schema serves it)

### 1. Marketing sees a development before anyone can sell it

Marketing works BCI and panel channels. They open **New lead** and type what BCI gave them:
development name, location, developer, consultant, stage, value. They are never asked who
the buyer is, because on day one nobody knows: the trading house only exists once a
contractor is awarded. They pick the salesperson and press **Assign**.

The lead now reads **Awaiting acceptance by Ali**. Marketing's job is done and they can see,
on one list, every lead nobody has accepted yet.

### 2. The salesperson accepts, or does not

Ali gets one notification with the development, the developer and the value. He presses
**Accept** and the lead is his, with the date recorded. If it is not his patch he presses
**Decline** and types why, and it goes back to marketing rather than dying in his tray. If he
does neither, the clock runs out and the sales manager sees it.

Nothing is ever silently owned by somebody who never opened it.

### 3. Qualify, register, spec-in

On qualify, the clash check runs (already built, AC-O4) and the lead becomes a registered
project. Ali specs in: the BQ becomes a quotation broken down by area, priced against the
project series tier, with the price floor enforced as he types rather than discovered later.
He issues it. The customer receives the AutoCount printed QT; the structured version stays
in CRM as the thing every later check compares against.

### 4. The PO arrives as a photograph of a piece of paper

Weeks later a PO arrives. Someone uploads it: a scan, sometimes with pencil on it.

The system reads it and shows CONTACT AH what it found beside the page image: 21 lines, the PO
number, the term, the customer's order reference. Two handwritten notes are pulled out as
their own cards, each with a crop of the handwriting: *"26/1/26 amend code and description
for item (5), (20), (23)"* and *"15/5/26 cancel item (7) due to changed the price, refer to
new P/O HQ/26/05/087"*. CONTACT AH accepts one, rejects one, edits the third. She approves the PO;
Baser countersigns. Nothing was retyped.

### 5. The schedule arrives as a spreadsheet nobody designed for us

The delivery schedule is a matrix in the customer's own layout, using the customer's own
item codes (`BUI-HB-SRTWC8613-RL`). The system reads it into a grid: phases down, products
across, quantity in the cell, and the date beside each phase. It then adds up each column
and compares the total to the PO. On this project it matched exactly (927, 894, 9, 16), and
that is the point: **the customer's own total row is our checksum.** If it does not
reconcile, the schedule is not accepted and CS is told which column is out.

The customer's codes are mapped to ours once. The next schedule from that customer parses
clean.

### 6. CS opens a draft, not a blank screen

CS opens the approved PO and finds a Sales Order already drafted:

- every PO **SET** line exploded into its component lines, from the AutoCount item package
  where one exists and from the quotation's own grouping where it does not;
- lines grouped into **one SO per area** as a proposal, TOWER and COMMON AREA, each line
  carrying its own delivery date - and she can split, merge or move lines between them,
  because area is how they group it twice out of three times, not a rule;
- the cross-check already run against the quotation, line by line.

What CS reads is not 99 lines of data entry. It is a short list of exceptions: this price
disagrees with the quotation, this quantity exceeds what is left on it, this product is
discontinued, this line's code did not resolve. Those four cannot be published past. Under
them sit the warnings CS clears with a reason in her own words: the credit limit, a phase
that looks ordered twice, a wording difference in the UOM.

She fixes what needs fixing and presses **Publish**.

### 7. Eling decides where the stock comes from, with the numbers in front of her

Each line proposes its source, ranked: BRW-BB first with on hand net of commitments, then
this project's own location, then another project's location marked *held for PS26-0201,
verify with Farah*. She accepts or overrides per line. A cross project pull raises a claim
to that project's CS instead of a phone call.

### 8. Purchasing is told what to do, not sent an email

The order inquiry writes itself from the SO: item, quantity, delivery date, stock location,
and the verb. Anything already covered by a pre-order is netted off **before** the inquiry
is written, so the ORDER row that was cancelled 42 minutes later by a follow up email is
never emitted in the first place. Joey gets a task in SCM with the rows on it. The same
rows export as the Excel he receives today, for anyone outside the system.

Committed demand keeps flowing through the SO lines the reorder engine already reads.

### 9. The customer changes their mind, which is the normal case

A revised schedule arrives: every date pushed twelve months, issued this time by the main
contractor rather than the trading house. The system diffs it against the live version and
proposes the change in the language purchasing already uses:

```
DELAY   Level 2 & 7      01/07/2026 -> 07/01/2027
DELAY   Level 8 & 10     03/08/2026 -> 21/01/2027
...     12 phases, quantities unchanged, totals still reconcile
```

CS reviews it, and an OCN is raised - already filled in with the PO number, the SO number,
the change table and a link to the revised schedule. Approved, it publishes: the SO is
amended in place and keeps its number, and purchasing gets a DELAY inquiry rather than a
retyped table.

Where a quantity moves to a different SO instead, the amendment says so explicitly and the
unmoved remainder is cancelled with a number on it, exactly as `CANCEL BALANCE 30 NOS` did.

### 10. What each person holds at the end

- **Marketing** sees which of their leads were accepted and what became of them.
- **The salesperson** has a project whose quotation, PO, schedule and SOs are one chain.
- **CONTACT AH** approves documents instead of retyping them.
- **CS** reviews exceptions instead of reading 21 pages line by line, and does not lose a day
  waiting for the PO to be walked to her desk.
- **Eling** decides allocation with stock figures on screen.
- **Purchasing** receives instructions that are already netted, in a task, with history.
- **Management** can see every project's committed value, every breached draft and every
  change that was approved and by whom.

---

## Decisions taken (grill, 2026-08-01)

| # | Decision | Why |
|---|---|---|
| D1 | **CRM is the system of record for project SOs**, published to AutoCount, which returns the DocNo | Removes the double keying entirely; the cross-check only means something if we hold the structured lines |
| D2 | Publish via **new ESB outbound**; CRM adopts the returned DocNo; inbound ingest matches back, never duplicates | One record, provenance `source_system='sorento'` |
| D3 | Ship in **two stages**: stage 1 publishes an AutoCount import file carrying our ref, stage 2 swaps the transport for the ESB call | No idle waiting on a team that does not own our deadline; identical tables and statuses either way |
| D4 | **CRM is the system of record for project quotations too**, published the same way | The `price != quotation` hard stop cannot be trusted against a PDF |
| D5 | **AutoCount stays the printer.** CRM prints internal drafts only, watermarked | Never two documents with one number and two layouts |
| D6 | A lead anchors on the **development**; `customer_id` becomes nullable and means BUYER; the **informant** is recorded separately | A BCI lead has no buyer, and BCI is not a debtor |
| D7 | Handover is an **acceptance handshake with a clock**: assign, accept or decline with reason, escalate on silence | Their own note: the handover has to be explicit or the lead dies between them |
| D8 | **One company profile, many roles**, with the debtor ledger folded in where a party is linked to a customer | One deal has a buyer, a main contractor, a developer and a consultant, and the same company plays different roles on different projects |
| D9 | **Two tier gate.** Hard stops: unresolvable product, price != quotation, qty > quotation balance, schedule total != PO qty, discontinued product. Everything else warns and is cleared with a recorded reason and a name | Block only what commits us wrongly and irreversibly; forcing the rest breeds override habits |
| D10 | **Set explosion**: `item_packages` first, quotation grouping (priced parent + zero priced companions) as fallback, confirmed mapping cached per customer and PO wording | The PO says `927 SETS`, the SO must say four component lines |
| D11 | **Handwriting extracted, never auto applied.** One review card per annotation with the image crop, deduplicated by (date, item, text) across re-scans | The cancellation of item 7 exists ONLY in handwriting, and one physical PO accumulates annotations over months |
| D12 | **Schedule rows are first class delivery phases** (area group, label, sequence) on the project; the SO splits by area group | Gives the R1 to R2 diff a stable identity instead of positional matching |
| D13 | Schedule intake is **AI extraction of whatever the customer sends**, confirmed on a grid, **checksummed against the PO using the customer's own TOTAL QTY row** | Every customer formats differently and a main contractor will not adopt our template |
| D14 | Revisions are **immutable document versions plus a computed delta**, proposed in purchasing's own verbs | Their artefacts are already versioned (R1, R2, REVISED 1) and the delta is what purchasing consumes |
| D15 | **Every amendment requires an OCN**, no exceptions, auto drafted from the delta | Their only trusted hard gate; auto drafting keeps it from becoming drag |
| D16 | **Order inquiry = persisted action rows** derived from the SO or its delta; demand stays on `sales_order_lines`; Excel export retained | The inquiry answers what to do, the SO lines answer how much is committed |
| D17 | **Allocation proposed and ranked, Eling confirms per line**; cross project pulls raise a claim to that project's CS | The verification she does by phone becomes an answer with a name on it |
| D18 | **Pre-order is an SO**, flagged, anchored on the project, excluded from customer analytics and credit; re-point moves quantity date by date with an explicit cancelled balance | Hong Bee was a parking route; the project stayed constant while the customer changed |
| D19 | **Sponsorship**: the approved sponsorship form is the source document, same machinery, price zero, quotation checks skipped, costing left for Accounts | It already carries an approver, so it plays the PO's evidentiary role |
| D20 | Approved-PO-awaiting-SO runs on the **existing form SLA and handling lock**; a breached draft becomes claimable by any eligible project CS | Answers their "no backup when she is on leave" with machinery already built and tested |
| D21 | The CONTACT AH then CS **sequence is preserved** (client decision). The AI removes retyping at both steps rather than removing a step | Client's call, recorded here rather than quietly optimised away |
| D22 | Double order: quotation balance is the hard rule, same product+phase on another SO warns, cross project is shown as information only | On a 15 phase schedule every phase legitimately repeats the same products |
| D23 | **AR outstanding is ingested** from AutoCount on the existing inbound pipe | We hold `credit_limit` and `payment_terms_days` but not what they owe |
| D24 | `PS26-0143` is project sales admin's **filing reference**, stored as the project's `admin_ref` alias | Client correction: it is not a project code and not a purchase document |
| D25 | A CRM/AutoCount **divergence is flagged, reviewed and reconciled per line**; neither side auto-wins, and an unresolved divergence blocks further amendments | Silent overwrite in either direction is how two systems drift apart permanently |

---

## Group A - Lead capture and handover

- **AC-A1** A lead can be created with NO buyer. `project_leads.customer_id` becomes nullable
  and means the BUYER (the debtor who will issue the PO), set when known.
- **AC-A2** Every lead records an **informant**: a source code (`bci`, `panel`, `consultant`,
  `contractor`, `walk_in`, `referral`, `other`), a free text reference (a BCI project id), and
  optionally a party or a contact. An informant is never written to `customers`.
  **[DEVIATION from ecohub]** ecohub's `Lead.clientId` is non nullable because its lead IS a
  consumer enquiry. A BCI sighting has no counterparty at all.
- **AC-A3** Marketing may register a lead with development, location, developer, consultant,
  stage and estimated value, and nothing else is required.
- **AC-A4** Assignment sets `assigned_to` and `assigned_at` and moves the lead to
  **awaiting acceptance**. The lead is NOT owned until accepted.
- **AC-A5** The assignee may **accept** (owner set, acceptance timestamped) or **decline with a
  reason** (returns to marketing, reason recorded, visible on the lead).
- **AC-A6** An unaccepted lead escalates on the stage clock to the sales manager, using the
  existing form SLA machinery. Escalation never reassigns by itself.
- **AC-A7** Marketing has a list of leads by acceptance state, so "which of my leads has
  nobody taken" is one screen and not a question.
- **AC-A8** Qualifying a lead runs the existing clash check and creates the project; the lead
  keeps its link to every project it produced (AC-O5 stands: one lead, several phases).

## Group B - Company profile

- **AC-B1** **A CLIENT IS A `customers` ROW. No new client entity is created.** The profile
  page is keyed on `customers` for anyone who buys, and reuses everything already there:
  credit limit, payment terms, AR outstanding, sales history, contacts.
- **AC-B1a** Non-buying roles stay in `project_parties`, which is why that table exists:
  architects, consultants and developers never issue a PO and must not appear in the debtor
  ledger that syncs to AutoCount. The existing `project_parties.customer_id` bridge joins the
  two, and ONE page renders the union: it opens on the customer where a customer exists, on
  the party where it does not, and shows every role either side has played.
- **AC-B2** The role counts (buyer, developer, main contractor, consultant, trading house) are
  computed across projects from both sides of that bridge.
- **AC-B3** Contacts are listed per company with their role and phone, seeded from what the
  SO delivery block already carries (site PIC names and numbers).
- **AC-B4** The profile lists the company's projects, its documents (POs, schedules it
  issued), and its recent activity.
- **AC-B5** No UUID appears anywhere on the profile.
- **AC-B6** A party that is also a debtor is ONE page, not two.

## Group C - Quotation authorship

- **AC-C1** Project quotations are authored in CRM with versions, per line price floors and
  the below floor approval already built in S3.
- **AC-C2** A quotation is broken down by AREA (townhouse rooms, club house, surau, guard
  house), matching how the BQ is specced in.
- **AC-C3** Publishing a quotation creates the AutoCount QT document and stores the returned
  document number against the CRM quotation version.
- **AC-C4** CRM printing produces a DRAFT watermarked PDF only. The customer copy is printed
  from AutoCount.
- **AC-C5** The quotation carries a per line **ordered balance**: quantity quoted minus
  quantity committed on published SOs across every PO on the project.
- **AC-C6** **[G3]** The same product legitimately appears on several quotation lines
  (`B2155-NL-BLUE` on lines 11, 25 and 28 of QT-004188). An SO line consumes balance from the
  quotation line matching **its own area** first; where the quotation carries no usable area
  grouping, a single aggregate balance per product is used. The consumed quotation line is
  shown on the SO line, always.

## Group D - Customer PO intake

- **AC-D1** A PO is uploaded as PDF or image. A scan with no text layer is the normal case and
  extraction is vision based.
- **AC-D2** Extraction produces: PO number, PO date, term, salesperson, customer order
  reference, remark, and per line: number, stock code, description, quantity, UOM, unit price,
  amount.
- **AC-D3** Every extracted field is shown beside the page image and is editable before
  approval. Nothing is accepted silently.
- **AC-D4** Handwritten annotations and struck through lines are extracted as **separate
  review cards**, each with a crop of the region, an interpretation, and accept / edit /
  reject. A rejected annotation is recorded as rejected, not deleted.
- **AC-D5** Annotations are identified by (date, item number, text) so re-uploading the same
  scan with one new note proposes only the new one.
- **AC-D6** A struck through line is proposed as CANCELLED, never removed from the extraction.
- **AC-D7** An annotation naming a successor PO (`refer to new P/O HQ/26/05/087`) creates a
  link to that PO once it is uploaded, and flags it as expected until then.
- **AC-D8** The PO is approved by CONTACT AH and countersigned per the existing signature rule.
  CS cannot open the SO draft until it is approved (D21).
- **AC-D9** A PO carries `admin_ref` (the PS filing reference) and it is searchable.

## Group E - Delivery schedule intake

- **AC-E1** A schedule is uploaded and extracted into a grid of (area group, phase label,
  phase sequence, delivery date) x (product) -> quantity.
- **AC-E2** The extracted grid is confirmed on screen beside the source page before it binds.
- **AC-E3** Column totals are compared against **the PO VERSION the schedule names** (its
  header carries `PO: HQ 26/01/121 DD 16/1/2026`), NOT against the current amended state. A
  schedule whose totals do not reconcile against that version is REJECTED with the offending
  column named. The customer's own TOTAL QTY row, where present, is compared as a second check.
  **[G1]** The 4 March schedule still lists `SRTFV1001` x16 that the 15/5 annotation cancelled;
  reconciling against the current state would reject a schedule the customer considers valid.
- **AC-E3a** Quantities cancelled by a later PO annotation are reported as a separate
  reconciliation note on the schedule ("16 SRTFV1001 cancelled by the 15/5 amendment, successor
  PO HQ/26/05/087"), stay visible on the schedule, and never become SO lines.
- **AC-E4** Customer item codes (`BUI-HB-SRTWC8613-RL`) are mapped to our products once per
  customer and remembered; a remembered mapping is applied silently and shown as such.
- **AC-E5** Phases are created as first class rows on the project: area group, label,
  sequence, delivery date.
- **AC-E6** A schedule may be issued by a party other than the buyer; the issuer is recorded.
- **AC-E7** Ambiguous dates (`8/3/2026`) are resolved against the phase sequence and the
  surrounding cadence, and any date the resolver is not sure of is raised for confirmation.

## Group F - SO draft and the gate

- **AC-F1** An approved PO plus a bound schedule produces an SO draft automatically.
- **AC-F2** PO SET lines are exploded into component lines: `item_packages` first, the
  quotation's priced-parent-plus-zero-priced-companions grouping as fallback, reviewer
  confirmation on the fallback path only.
- **AC-F3** A confirmed explosion mapping is cached per (customer, PO code or description) and
  reused without asking again.
- **AC-F4** The draft PROPOSES one SO per schedule AREA GROUP, lines ordered by delivery date,
  each line carrying its own delivery date.
- **AC-F4a** **[G2]** Before publishing, CS may split a proposed SO, merge two, or move lines
  between them. Area is a default, not a rule: this PO produced THREE real SOs (TOWER, COMMON
  AREA, and an early product subset raised on 07/11/2025 before the PO existed).
- **AC-F4b** The grouping CS actually published is remembered per customer and proposed next
  time.
- **AC-F5** Cross check against the current quotation version, per line: price, quantity
  against the ordered balance, product resolution, discontinued status.
- **AC-F6** HARD STOPS, publish blocked: unresolvable product code; unit price differing from
  the quotation; quantity exceeding the quotation's ordered balance; schedule total not equal
  to PO quantity; discontinued product.
- **AC-F7** WARNINGS, publish allowed with a recorded reason and the acknowledger's name:
  credit limit exceeded; same product and phase already committed on another SO for this
  project; UOM wording differing; a PO line absent from the quotation.
- **AC-F8** Cross project information: a product also committed on another project for the
  same developer is SHOWN and never blocks or warns.
- **AC-F9** The credit warning reads outstanding plus this SO against the limit, with the
  as-of timestamp of the AR figure visible.
- **AC-F9a** **[G9]** The credit warning is re-evaluated AT PUBLISH, not only at draft time. A
  draft reviewed on Monday and published on Thursday is checked against Thursday's figure.
- **AC-F10** Publishing writes the SO, sets `source_system='sorento'`, and produces either the
  AutoCount import file (stage 1) or the ESB call (stage 2).
- **AC-F11** The returned AutoCount document number is adopted onto the CRM SO, and inbound
  ingest matches rather than creating a second row.
- **AC-F11a** **[G4]** Stage 1 carries NO extra reference field: both header refs are already
  occupied on the real document (`Your Ref No.` = customer PO, `Our Ref No.` = project name).
  Match-back uses a natural key: customer + customer PO number + area group + line fingerprint
  (codes, quantities and dates hashed). It survives CS importing the file by hand or retyping
  it, and needs nothing from AutoCount's configuration.
- **AC-F12** An approved PO with no published SO is SLA tracked, escalates on breach, and
  becomes claimable through the existing handling lock.

## Group G - Amendments, OCN and the delta engine

- **AC-G1** Every uploaded PO or schedule is an immutable VERSION of the same commitment.
- **AC-G2** A new version is diffed against the live one and produces an amendment proposal in
  these verbs: `ADVANCE`, `DELAY`, `QTY_CHANGE`, `ADD_LINE`, `REMOVE_LINE`, `CANCEL_BALANCE`,
  `REPOINT_SO`, `MODEL_CHANGE`.
- **AC-G3** Phases match across versions on (area group, sequence, normalised label). **[G6]**
  COMMON AREA rows carry NO label at all, so for unlabeled rows the key degenerates to (area
  group, sequence) and the match must be corroborated by the row's date and quantity vector. A
  row that cannot be corroborated is raised for confirmation, never silently treated as new.
- **AC-G4** Every amendment to a PUBLISHED SO requires an OCN, auto drafted with the PO
  number, the SO number, the change table and a link to the source document, requiring only an
  approver. **[G9]** Editing an unpublished draft is not an amendment: nothing is committed
  and no OCN is raised.
- **AC-G5** An amendment that keeps the SO number amends lines in place. One that moves
  quantity to a different SO records source SO, destination SO, quantity moved per date, and
  the explicitly cancelled remainder.
- **AC-G6** Amendment history is readable as a chain: which version, which OCN, who approved,
  what changed, in the same verbs.
- **AC-G7** Publishing an amendment updates AutoCount through the same transport as the SO.

## Group H - Allocation

- **AC-H1** Each SO line proposes ranked sources: BRW-BB, this project's own location, other
  project locations, each with on hand and committed figures.
- **AC-H2** A candidate held for another project is labelled with that project and its CS.
- **AC-H3** Eling confirms or overrides per line; the confirmed source is stamped on the line.
- **AC-H4** A cross project pull raises a claim to the other project's CS, who accepts or
  refuses with a reason. Nothing moves on silence.
- **AC-H5** A confirmed source becomes the STOCK LOCATION on the order inquiry.

## Group I - Order inquiry and SCM

- **AC-I1** Publishing an SO or an amendment derives order inquiry rows: one per (SO line,
  action), carrying SO number, item code, quantity, delivery date, stock location, verb and
  any SPO reference.
- **AC-I2** Verbs: `ORDER`, `RESERVE_AND_ORDER`, `ADVANCE`, `DELAY`, `CHANGE_SO`,
  `CANCEL_BALANCE`, `PRE_ORDERED_DO_NOT_ORDER`, `ALREADY_INBOUND` (with the SPO reference).
- **AC-I3** Quantities already covered by a pre-order or an inbound SPO are NETTED OFF before
  the rows are written. An `ORDER` row is never emitted for stock already on the water.
- **AC-I3a** **[G7]** Netting allocates the covering pool **FIFO by delivery date**: the 5,950
  pre-ordered gratings cover the earliest dated demand first. Each row states what covered it,
  so purchasing sees which dates are covered and which still need ordering.
- **AC-I4** Inquiry rows reach purchasing as a task in SCM with the rows attached, not an
  email.
- **AC-I5** The same rows export as the existing Excel, with the existing column headings, for
  anyone outside the system.
- **AC-I6** Committed demand continues to flow through `sales_order_lines`; the reorder engine
  is not changed and never reads inquiry rows as demand.
- **AC-I7** An inquiry row carries its state (raised, actioned, cancelled) so "did purchasing
  act on this" is answerable without a mailbox.

## Group J - Pre-order and parking routes

- **AC-J1** An SO may be flagged `is_pre_order` and is anchored on a project.
- **AC-J2** A pre-order SO is EXCLUDED from customer analytics, customer category and credit
  exposure. Its customer is a parking route, not a commercial fact.
- **AC-J3** A pre-order has no quotation to check against; the quotation checks are skipped and
  the project requirement is used instead.
- **AC-J4** Re-pointing a pre-order to a real customer's SO moves quantity date by date, runs
  the full quotation cross check at that moment, and records the cancelled remainder
  explicitly.
- **AC-J5** Demand is counted once: quantity leaves the pre-order line as it lands on the
  destination line.

## Group K - Sponsorship

- **AC-K1** An approved sponsorship form produces an SO draft at price zero with the delivery
  month.
- **AC-K2** Quotation price and quantity checks are skipped; product resolution and
  discontinued checks still apply.
- **AC-K3** Costing is left empty for Accounts and the SO is marked as awaiting costing.
- **AC-K4** A sponsorship SO emits order inquiry rows exactly as a commercial one does.
- **AC-K5** Sponsorship spend rolls up against the project automatically (feeds the existing
  sponsorship to PO conversion reporting).

## Group N - Divergence between CRM and AutoCount (D25)

Between P8 and P13 an SO exists in CRM and is imported into AutoCount. A CS editing it
directly there is likely, not hypothetical. **Neither side silently wins.**

- **AC-N1** Every inbound ingest of a CRM authored SO compares the AutoCount document against
  our record, line by line: product, quantity, unit price, delivery date, plus header terms.
- **AC-N2** A difference raises a DIVERGENCE, it does not overwrite. The CRM record keeps its
  values and the AutoCount values are held beside them.
- **AC-N3** The divergence is shown as a reconciliation screen: ours, theirs, the difference,
  per line, with the lines that agree collapsed so only the disagreements are read.
- **AC-N4** The reviewer resolves per line by ACCEPTING THEIRS (our record updates, and the
  reason is recorded) or KEEPING OURS (a corrective publish is queued to AutoCount).
- **AC-N5** An unresolved divergence blocks further amendments on that SO: amending a record
  we know is wrong is how the two systems drift apart for good.
- **AC-N6** Divergences are listed for management with their age, so a stack of unresolved
  ones is visible rather than discovered.
- **AC-N7** Resolution is audited: who, when, which side won, and why.

## Group M - Golden set (client instruction, 1 August 2026)

The client's own uploaded documents are the acceptance test, not a synthetic fixture.
Committed to `e2e/fixtures/project-cs/` as real files, per the standing rule that AI and file
features are tested against real user samples.

- **AC-M1** Extraction of `Buimaco Bulk PO - Tuju Residence - (R1).pdf` must reproduce every
  printed line exactly: line number, stock code, description, quantity, UOM, unit price and
  amount, for all **52 lines across 10 pages**.
- **AC-M1a** Every line must satisfy `qty x unit_price == amount`, and the sum of line amounts
  MINUS any line cancelled by annotation must equal the quotation total exactly
  (1,810,640.62 - 4,733.60 = 1,805,907.02). Measured on 2026-08-01: 52/52.
- **AC-M1b** The PO's stock-code COLUMN is truncated by the customer's own printing
  (`SRTWC86`, `2155-BLUE`). The full code lives in the description, so code resolution reads
  both and never trusts the column alone.
- **AC-M2** Both handwritten amendments must be detected as annotation cards, and the struck
  through line 7 (`SRTFV1001`, 16 NOS, RM 295.85) must be proposed as CANCELLED.
- **AC-M3** Extraction of `Delivery Schedule - Buimaco (Tuju Residences).pdf` must reproduce
  the phase rows, dates and the full quantity matrix, and its column totals must equal the PO
  (927 / 894 / 9 / 16 / 927 ...).
- **AC-M4** Extraction of the R2 revision must produce exactly the twelve tower DELAY rows and
  the three common area rows, with quantities unchanged.
- **AC-M5** **The end to end test:** PO R1 plus schedule R1, run through explosion, split and
  cross check, must reproduce the real `SO397450` (99 TOWER lines) and `SO397460` (COMMON AREA)
  line for line: same products, same quantities, same per line delivery dates, same prices.
  A difference is a failure, not a variance.
- **AC-M6** The order inquiry derived from those SOs must reproduce the rows in
  `(04).03.2026 MARYAM TUJU RESIDENCE.xlsx`, and the pre-order netting must reproduce the
  correction that arrived 42 minutes later WITHOUT a correction: no `ORDER` row for quantity
  the `SO383057` pre-order covers.
- **AC-M7** The golden set runs in CI. Extraction changes that regress it do not merge.

## Group L - RBAC, audit and provenance

- **AC-L1** New permissions: `projects.customer_po.view|create|approve`,
  `projects.sales_order.view|draft|publish`, `projects.order_inquiry.view|raise`,
  `projects.allocation.confirm`, `projects.ocn.approve`.
- **AC-L2** Publishing, approving, acknowledging a warning, confirming an allocation and
  approving an OCN are all audited with the actor, the timestamp and the reason where one was
  given.
- **AC-L3** Every CRM authored document carries `source_system='sorento'`; AutoCount ingest
  never overwrites a CRM authored row's annotations.
- **AC-L4** Company scoping applies to every new table (fail closed, `CompanyScopedMixin`).
- **AC-L5** **[G5]** No module column is added to a CORE table. `sales_orders` learns nothing
  about projects; the project link, the pre-order flag and the sponsorship link all live on
  `project_sales_orders`, module side.

---

## Dependencies outside our control

| Dependency | Needed for | Fallback if late |
|---|---|---|
| **ESB outbound direction** (publish a document into AutoCount, return DocNo) | D2, AC-F10 stage 2 | Stage 1 import file with our ref carried; ingest matches back |
| **AR outstanding ingest** (customer, outstanding, ageing, as-of) | AC-F9 credit warning | Warning degrades to order value against limit, and says so on screen |
| **Vision model quality on Malaysian handwriting** | AC-D4 | Cards are reject-by-default; a rejected card leaves the printed line untouched |

## Deliberately NOT in this phase

- Delivery, DO printing, transport booking and invoicing (discovery 5B). The SO is the boundary.
- Purchase request return, RMA and credit notes (discovery page 2, first form).
- Flower stand / own buy and claim (discovery page 2, third form).
- New product code application flow (discovery page 2, fourth form).
- Stock transfer execution. We raise the need and the claim; the transfer itself stays where
  it is today.
- Excel import of historical projects (still AC-C9 in the phase 1 UAC).
