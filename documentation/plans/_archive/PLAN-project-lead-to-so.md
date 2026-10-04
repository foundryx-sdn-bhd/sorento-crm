# PLAN - Lead to Sales Order (module `projects`, phase 2)

> **Table names in this document predate the schema move.** On 2026-08-15 the projects
> module's 47 tables moved into a dedicated `projects` Postgres schema and the 34 that
> carried a `project_` prefix dropped it: `project_leads` is now `projects.leads`,
> `project_quotation_lines` is `projects.quotation_lines`, and so on. The 13 unprefixed
> ones only changed schema. Nothing else in this document changes. See
> [ADR-0011](../../adr/0011-project-sales-tables-live-in-the-projects-schema.md) and
> `documentation/plans/PLAN-projects-schema-move.md` for the full mapping.

**Status:** P1-P11 BUILT, including P8a as of 2026-08-03. Only P12 and P13 remain.
P1-P8 and P11 were client-reviewed 2026-08-02/03. Grilled with the client 2026-08-01
(24 decisions, D1-D24), then the PLAN itself grilled against the source documents
(9 findings, G1-G9, all resolved).

**Built:** P1 lead informant + handshake, P2 company profile, P3 quotation publish, P4 PO
intake, P5 handwriting cards, P6 schedule intake, P7 SO draft + gate, P8 publish + import
file, P8a divergence reconciliation, P9 allocation, P10 order inquiry + SCM handoff,
P11 amendments/OCN/delta. Proven against the client's own 10 page PO: 51 lines,
1,810,640.62 to the cent, and the handwritten cancellation reconciling to the quotation
total.

**Not built, in dependency order:** P12 pre-order and sponsorship paths, P13 ESB outbound
swap + real AR ingest (blocked on the ESB team, not on us).

**Known gaps inside what is built:** the multi-code line split (a description naming A, B
and C becoming three lines) is unbuilt because zero of the client's 51 lines exhibit it,
so there is nothing to verify against. The reconciliation fix actions are unit-tested but
not yet exercised against a live unconfirmed schedule. P8a's stage 1 upload parser reads by
column heading because AutoCount's real export layout has not been seen yet: one real
export from the client would confirm it, and changing it then costs a mapping table rather
than a rebuild.
**Acceptance criteria:** `UAC-project-lead-to-so.md` (binding). The Journey there governs.
**Slug:** project-lead-to-so
**Classification:** extends the existing MODULE `projects`; `public` schema, normal FKs,
company scoped.
**Builds on:** phase 1 (S0-S6b, all built), `sales_orders` / `sales_order_lines` and
`item_packages` (AutoCount mirrors), SCM reorder engine, form SLA + handling lock.

---

## 1. Why

Phase 1 made the pursuit visible: lead, registration, quotation, samples, forecast, staleness.
It stops at the moment the money actually arrives. Everything after the customer PO is still a
scan walked from desk to desk, 99 lines retyped into AutoCount, and an Excel emailed to
purchasing that is corrected by a second email 42 minutes later.

Three costs, all visible in the client's own files:

1. **Retyping.** `SO397450` is 99 lines across 21 pages, read by hand off a scanned PO and a
   matrix schedule, twice (CONTACT AH, then CS).
2. **No structured commitment.** The quotation exists only as a PDF, so "does this PO agree
   with what we quoted" is a human reading two documents side by side. `QT-004188` item 7 was
   cancelled by hand months later because the price had changed. Nothing caught it.
3. **Instructions that go stale between systems.** The order inquiry says ORDER for stock a
   pre-order already covers, and the correction arrives as a third email.

## 2. Shape

```
LEAD ─accept─> PROJECT ─spec in─> QUOTATION(v) ──publish──> AutoCount QT
                 │                    │
                 │                    │ ordered balance
        CUSTOMER PO (scan) ───────────┤
           + DELIVERY SCHEDULE (matrix, phases)
                 │  extract, explode, split, cross check
                 ▼
            SO DRAFT ──gate──> SO(v) ──publish──> AutoCount SO  (stage 1 file / stage 2 ESB)
                 │                 │
                 │                 ├─ allocation (Eling confirms ranked source)
                 │                 └─ ORDER INQUIRY rows ──task──> SCM / purchasing
                 │                        (netted against pre-orders + inbound SPO)
       revised PO / revised schedule
                 │  version + delta
                 ▼
            AMENDMENT ──OCN──> amend in place, or re-point + cancel balance
```

Two invariants worth stating once:

- **Demand is `sales_order_lines`.** The order inquiry says what to DO; it is never a second
  source of how much is committed. SCM's reorder engine is untouched.
- **The project is the anchor, not the customer.** A parking route (Hong Bee) changes the
  customer on a document while the requirement stays with the project. Every join that
  matters goes through the project.

## 3. Data model (indicative)

New tables:

```
project_delivery_phases      project_id, area_group, label, sequence, delivery_date,
                             source_schedule_version_id            (D12)

project_po_versions          purchase_order_id, version_no, attachment_id, page_count,
                             extracted_json, extraction_model, arithmetic_passed,
                             arithmetic_total, extracted_total, confirmed_by/at   (D14)
project_po_lines             po_version_id, line_no, stock_code_raw, description_raw,
                             qty, uom_raw, unit_price, amount, is_cancelled,
                             resolved_product_id, resolution_source, arithmetic_ok
project_po_annotations       po_version_id, dedup_key(date,item,text_hash), page_no,
                             crop_attachment_id, raw_text, written_date, refers_to_lines,
                             interpretation, interpretation_json,
                             state(proposed|accepted|edited|rejected), actioned_by  (D11)

                             -- CORRECTION, 2026-08-02, while writing the schema.
                             -- There is NO `customer_pos` table. Phase 1 already owns the
                             -- customer PO row (`project_purchase_orders`: project,
                             -- quotation_version, issuing party, number, date, amount, plus
                             -- per line model/price mismatch flags and the mismatch notify).
                             -- A second header table would mean two answers to "what did the
                             -- customer commit to", and a user who records a PO by hand in
                             -- project management and then uploads the scan would end up with
                             -- two unrelated POs. Phase 2 WIDENS the phase-1 row instead:
                             --   project_purchase_orders + term_days, sales_person,
                             --   customer_order_ref, admin_ref, status, supersedes_po_number,
                             --   superseded_by_po_id, approved_by/at, countersigned_by/at
                             -- and reuses its existing quotation cross-check rather than
                             -- duplicating it. Extraction stays raw and immutable on the
                             -- version; the confirmed state is the phase-1 PO lines.

delivery_schedules           project_id, purchase_order_id, po_version_id, issuer_party_id,
                             label, revision_no, issued_at
                             -- po_version_id is what the checksum reconciles against  [G1]
delivery_schedule_versions   delivery_schedule_id, version_no, document_attachment_id,
                             extracted_json, checksum_state(ok|mismatch), confirmed_at
delivery_schedule_cells      version_id, phase_id, product_id, qty                  (D13)

customer_item_code_map       customer_id, customer_code, product_id, confirmed_by   (AC-E4)
set_explosion_map            customer_id, po_code_or_text, product_id, component_json (D10)

project_sales_orders         project_id, purchase_order_id, area_group, so_id(->sales_orders),
                             provisional_ref, autocount_doc_no, is_pre_order,
                             is_sponsorship, sponsorship_form_id, grouping_origin,
                             status(draft|blocked|ready|published|amended)
                             -- every project-side fact lives HERE, never on sales_orders [G5]
so_grouping_preferences      customer_id, grouping_json, learned_from_so_id          [G2]
so_draft_findings            project_sales_order_id, line_id, severity(hard|warn|info),
                             code, detail_json, acknowledged_by, acknowledged_reason (D9)

so_amendments                project_sales_order_id, source_version_ref, ocn_id,
                             verb, delta_json, status, approved_by, published_at   (D14,D15)
order_change_notices         project_id, purchase_order_id, project_sales_order_id,
                             reason, approver_id, approved_at, source_document_id  (D15)

order_inquiries              project_sales_order_id, amendment_id, raised_at, state
order_inquiry_rows           order_inquiry_id, so_line_id, item_code, qty, delivery_date,
                             stock_location, verb, spo_ref, state                  (D16)

so_line_allocations          so_line_id, source_type(brw|own|other_project|order),
                             warehouse_id, qty, claim_id, confirmed_by             (D17)
allocation_claims            from_project_id, to_project_id, product_id, qty,
                             state(requested|accepted|refused), decided_by, reason
```

Changed:

```
project_leads.customer_id            -> nullable, means BUYER                       (D6)
project_leads  + informant_source, informant_ref, informant_party_id,
                 informant_contact_id, assigned_at, accepted_at,
                 declined_reason, acceptance_state                                  (D6,D7)
projects       + admin_ref (PS filing reference)                                    (D24)
customers      + ar_outstanding, ar_ageing_json, ar_as_of                           (D23)
(sales_orders is CORE and is left alone: no project_id, no is_pre_order)          [G5]
```

Reused as is: `project_purchase_orders` / `project_purchase_order_lines` (widened, see the
correction above), `project_parties` (+ its existing `customer_id` bridge), `item_packages`,
`sales_orders` / `sales_order_lines`, `purchase_requests` (sponsorship forms), the form SLA
and handling lock, the activities adapter, the status engine.

## 4. Slices

Each slice follows the three phase loop (FE prototype on mocks, then BE + tests, then review).
Tests land in phase 2 of each slice, never deferred.

| # | Slice | Delivers | Depends on |
|---|---|---|---|
| **P1** | Lead informant + acceptance handshake | AC-A1..A8. Nullable buyer, informant fields, assign/accept/decline, SLA escalation on silence, marketing's acceptance list | - |
| **P2** | Company profile on `customers` | AC-B1..B6. A client IS a customers row, reused not duplicated; parties cover non-buying roles; one page renders the union across the existing bridge | P1 |
| **P3** | Quotation publish + ordered balance | AC-C1..C5. Publish adapter (stage 1 file), returned doc number stored, per line ordered balance, draft watermark printing | - |
| **P4** | Customer PO intake | AC-D1..D9. Upload, vision extraction, field confirm beside the page, approval + countersign, admin_ref | P3 |
| **P5** | Handwriting review cards | AC-D4..D7. Annotation extraction, crops, accept/edit/reject, dedup across re-scans, successor PO link | P4 |
| **P6** | Schedule intake + phases | AC-E1..E7. Matrix extraction, confirm grid, checksum against PO, customer code map, phases as first class rows | P4 |
| **P7** | SO draft: explosion, split, gate | AC-F1..F9, F12. Set explosion with cached mappings, area split, cross check, hard stops and warnings, SLA + handling lock on drafts | P3, P5, P6 |
| **P8** | Publish + AutoCount round trip (stage 1) | AC-F10, F11. Import file with our ref, adopt returned doc number, ingest match back rather than duplicate | P7 |
| **P8a** | Divergence reconciliation (BUILT) | AC-N1..N7 plus AC-F11/F11a, which turned out to be unbuilt. Natural-key match back, line by line compare on ingest, reconciliation screen, accept theirs / keep ours with a corrective publish, amendment block while unresolved, management list. See `PLAN-project-so-divergence.md` | P8 |
| **P9** | Allocation (BUILT) | AC-H1..H5. Ranked sources with live figures, per line confirm, cross project claims | P7 |
| **P10** | Order inquiry + SCM handoff (BUILT) | AC-I1..I7. Derived rows, verb vocabulary, pre-order and inbound netting, SCM task, Excel export, row state | P8, P9 |
| **P11** | Amendments, OCN, delta engine | AC-G1..G7. Version diffing, verb proposals, phase matching, auto drafted OCN, amend in place vs re-point with cancelled balance | P8, P10 |
| **P12** | Pre-order and sponsorship paths | AC-J1..J5, AC-K1..K5 | P11 |
| **P13** | ESB outbound swap + AR ingest | D3 stage 2, AC-F9 real credit figures | ESB team, ESB inbound |

Sequencing note: P1 to P3 carry no AI and no external dependency, so they can land while the
extraction work is still being tuned. P13 is deliberately last and is the only slice that
cannot be finished by us alone.

## 5. What the AI does, precisely

Four jobs, each with a deterministic check behind it. This matters: everywhere the AI produces
a number, something arithmetic verifies it.

| Job | Input | Deterministic check behind it |
|---|---|---|
| PO extraction | Scanned PDF or photo | Line amounts must equal qty x unit price; total must equal the sum of lines |
| Handwriting reading | Region crops | Never applied without a human accept; item numbers must exist on the PO |
| Schedule extraction | Customer formatted matrix | Column totals must equal the quantities on the PO VERSION the schedule names, and the customer's own TOTAL QTY row where present [G1] |
| Set explosion | PO line + catalogue | `item_packages` is authoritative; the quotation fallback must reproduce the quoted quantities exactly |

The AI never decides a price, a quantity or a date on its own. It proposes, and something
countable agrees or the draft is blocked.

## 5a. The golden set (client instruction)

Their uploaded documents ARE the acceptance test. Committed to `e2e/fixtures/project-cs/`.

```
INPUT   Buimaco Bulk PO R1 (scan, 21 lines, 2 handwritten amendments)
      + Delivery Schedule R1 (matrix, 15 phases)
EXPECT  SO397450  99 TOWER lines   ] line for line: product, qty,
        SO397460  COMMON AREA      ] delivery date, unit price
      + order inquiry rows matching (04).03.2026 MARYAM TUJU RESIDENCE.xlsx
      + NO order row for quantity the SO383057 pre-order covers
INPUT   Delivery Schedule R2
EXPECT  12 tower DELAY rows + 3 common area rows, quantities unchanged
```

This is the difference between "the AI extracts POs" and "the AI extracts THIS PO correctly".
It runs in CI; an extraction change that regresses it does not merge. It also gives P4, P5, P6
and P7 an objective done condition instead of a demo.

## 5b. Extraction spike result (2026-08-01, measured not assumed)

Run before sizing P4/P5, on the real scan, scored against the real documents.

| Model | numbers | struck-through | handwriting | tokens (10 pages) |
|---|---|---|---|---|
| `gemini-2.5-flash` | **52/52** | **1/1** | successor PO + dates correct | 6,371 in / 7,997 out, 134s |
| `gemini-2.5-pro` | 27/27 (2 pages) | 1/1 | correct | same input cost |
| `gemini-3.1-pro-preview` | 27/27 (2 pages) | 1/1 | correct | 2x input cost |
| `gpt-4o-mini` | 25/36 | **0/1 (missed it)** | date and successor PO both mangled | 51,680 in / 1,117 out |

**The self-proving reconciliation:** extracted line amounts sum to 1,810,640.62; minus the
handwritten cancellation of line 7 (4,733.60) that is exactly the quotation total of
1,805,907.02. One number validates the extraction, the cancellation reading and the
cross-check together.

Decisions this settles:

- **Pin `gemini-2.5-flash`.** It matched both pro models exactly and costs a fraction. A
  `GeminiProvider` adapter is needed in `llm_provider.py` (about 40 lines, same shape as the
  OpenAI and Anthropic ones); Gemini is not currently supported there.
- `gpt-4o-mini` is unusable for this: it missed the struck-through cancellation entirely, which
  no arithmetic check can catch. Its two numeric errors WOULD have been caught, which is the
  two-tier gate working as designed.
- Sorento needs its own Gemini key; the spike borrowed another project's.

## 5c. Schedule spike result (2026-08-02, measured)

Same harness, both schedule versions, `gemini-2.5-flash`. The result splits from the PO.

| Check | R1 | R2 |
|---|---|---|
| Column sums == the schedule's own TOTAL QTY row | 29/37 | 35/38 |
| Column sums == PO quantity for that product | 28/37 | 30/38 |
| Phases matched R1 -> R2 | 13/13 | - |
| Date moves extracted | **13/13, exactly as the DELAY email lists them** | - |

Three conclusions, and they change the build:

1. **The delta engine is validated.** The twelve TOWER moves came out identical to Maryam's
   email (`01/07/2026 -> 07/01/2027` through `01/06/2027 -> 10/06/2027`). Nothing about the
   revision design needs to change.
2. **Pure vision is NOT good enough for the cell matrix** (roughly 20% of columns wrong on R1).
   The checksum caught every one, which is the design working, but a whole-document reject would
   reject nearly every real schedule. Intake becomes **hybrid**: the schedule HAS a text layer,
   so quantities are parsed deterministically from it and vision is used only for STRUCTURE
   (which column is which product, which row is which phase). Reconciliation then runs
   **per column**, and only the columns that fail are put in front of CS to correct.
3. **G6 is real, not theoretical.** The unlabeled COMMON AREA rows collapsed into a single key,
   turning three phases into one. Phase identity MUST be `(area_group, sequence)` with the date
   and quantity vector corroborating, exactly as G6 specified.

## 6. Open risks

- **Vision quality on Malaysian site handwriting.** Mitigated by reject-by-default cards, but
  a missed annotation is a silent wrong commitment. Worth a measured sample before P5 sizing.
- **Schedule layout drift.** Every customer differs and the same customer may restructure
  between revisions. The checksum catches quantity errors; it does not catch a phase label
  that changed meaning. AC-G3 raises unmatched labels rather than guessing.
- **ESB outbound may not land.** Stage 1 covers it, at the cost of one manual import click.
- **AR ingest may not land.** The credit warning degrades and says so on screen.
- **Two systems of record during the transition.** RESOLVED as D25: the ingest match back
  (AC-F11) detects it, the difference is flagged and reconciled per line (Group N), neither
  side auto-wins, and an unresolved divergence blocks further amendments on that SO. Ships as
  slice P8a, immediately after the first publish path exists.
- **Scope pressure toward delivery.** 5B (DO, transport, invoice) is out. It will be asked for
  the moment the SO works.
- **Natural-key match-back can collide [G4].** Two SOs on one PO within one area group would
  key identically but for the line fingerprint. The fingerprint is therefore part of the key,
  and an ambiguous match must be raised to CS rather than resolved by guessing.
- **The CONTACT AH step stays (D21), and CS reviews rather than retypes** (client confirmed at plan
  review). The day in the process remains; the keying does not. If they later want the day
  back, it is a workflow change, not a rebuild.

## 7. What this does not touch

Delivery and invoicing, purchase request returns and RMA, flower stand claims, new product
code application, physical stock transfer execution, and the historical Excel import. All
listed in the UAC with reasons.
