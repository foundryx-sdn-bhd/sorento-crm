# UAC - Quotation as a DOCUMENT (multi-scope, cover letter, issue snapshot)

**Status:** BUILT (S1-S8) on `feat/project-lead-to-so`, 2026-08-04. Open questions were answered
by the client the same day and are folded in below (Groups A, E, F, H).
**Slug:** project-quotation-document
**Source:** client review of the built quotation screens (images 40-41) plus the real
artifact `Cabana Elmina - nadi cergas R2.xlsx` (Sorento → Nadi Cergas, 2026-02-26,
TOTAL AMOUNT RM 696,923).

Verified end to end in a browser against real production-copy data, not fixtures: signed as
Sorento, minted the counter-sign link, signed as the customer at 375px, and confirmed AC-H7 by
reading the database (scope `won`, project `won`).

Two ACs are NOT met, recorded here rather than quietly dropped:

- **AC-F1's sender block** still prints `Sorento` alone. `companies` holds no legal name,
  registration number, address or phone, which the artifact table below already marks `✗`.
- **AC-H3's reusable saved signature** is not built. The pad captures every time; nothing is
  stored against the user for one-click re-use.

The client's words: *"got a header as like the excel, then can add multiple tabs (meaning
add multiple scope), then in each scope can add lines, then the total we should always put
at the bottom of the corresponding column ... we should also have a cover letter template
also ... a template designer that can design the template for the cover letter, and every
quotation just preset to it"*.

---

## 0. What the real artifact actually contains

Read off the sample workbook rather than imagined, because every gap below is a gap against
a document Sorento has already sent a customer:

| Part | In the sample | In the CRM today |
|---|---|---|
| Sender block | SORENTO SDN BHD (694526-P), address, Tel | ✗ |
| Refs | `Our Ref: (R2)`, `Your Ref:`, `Date: 2026-02-26` | ✗ |
| Recipient block | `To:` party, 4 address lines, 2 phones | ✗ |
| `Attn:` | Kelly | ✗ |
| Subject line | CADANGAN MEMBINA PANGSAPURI RUMAH IDAM | ✗ (project title only) |
| Column set | ITEM / PRODUCT IMAGE / TECHNICAL SPEC / DESCRIPTION / BRAND / PRODUCT CODE / QTY / UNIT RATE / COMPLETE SET / GRAND TOTAL | partial |
| Section bands | `BILL NO 3 PAGE 15/4`, `15/5`, `15/6`, `15/7`, `15/8` | ✗ |
| Item letters | A, B, C … grouping sub-lines (WC + angle valve + hose all under A) | ✗ |
| Alternates | `OPTION`, `OPTIONAL ITEMS FOR OKU TOILET` | ✗ |
| Unpriced-but-quoted | `rate only` / `RATE ONLY` in the total column | ✗ |
| Grand total | `TOTAL AMOUNT … 696923`, under the money column | ✗ (was a toolbar chip) |
| Terms | 8 numbered clauses | ✗ |
| Sign-off | "We trust the above prices are to your satisfaction", Thank You, BASER CONTACT P, 019-0000506 | ✗ |

**The sample is ONE sheet with bands.** Other projects (the client's example: townhouse /
guard house / reception) split those bands into separate tabs. Both are the same shape: a
document that carries several priced scopes. Tabs and bands are a rendering choice, not two
data models.

---

## Journey (Phase 0 - written before any schema)

**Actor:** the salesperson who has walked a development and has to send a price.

1. Opens the project → **Quotations** → sees the quotation documents already sent, each
   with its ref, date, recipient and total. Clicks one, or **New quotation**.
2. The new document arrives **already filled in**: sender block from the company, recipient
   block from the project's developer / main contractor party (address, phones), `Attn:`
   from the party's primary contact, subject line from the project title, `Our Ref` from the
   series, date = today, cover letter and terms from the active template. **The only thing
   asked is what is not already known.**
3. Adds a scope tab - *Townhouse* - and prices it. Product picked from the catalogue brings
   its own code, description, brand, image, list price. Quantity is typed; the line total
   computes. A line can be marked **rate only** - quoted, printed, excluded from the total.
4. Adds *Guard house*, *Reception* the same way. Each tab shows its own total at the bottom
   of the money column; the document header shows the grand total across tabs.
5. Reviews the cover letter - it is already written, from the template, with the names filled
   in. Edits this one if this customer needs different words. The template is untouched.
6. **Issue.** The document is stamped `R1`, frozen, and a PDF (and Excel) is produced that
   looks like the workbook above. What the customer holds is now a fact in the system.
7. The customer negotiates. The salesperson opens a revision: `R2`. Only what changed is
   re-priced; the previous issue stays readable exactly as it was sent.
8. Each scope is won or lost **on its own**, and that keeps working: the project stays live
   while any scope is open.

What they hold at the end: a PDF identical in structure to what Sorento sends today, and a
CRM record that can answer "what exactly did we quote them, and when".

---

## Group A - The document

- **AC-A0** `Our Ref` is a **running number from the existing numbering feature**, not free text
  and not derived from the project code. A `document_numbering_rules` row with
  `doc_type = 'project_quotation'` supplies prefix, digit count and reset policy, exactly as
  purchase requests and sponsorship forms already do, and an admin edits it in Setup without a
  deploy. The number is claimed by `NumberingService.get_next_number` at document creation and
  never changes; the ISSUE appends the revision the customer quotes back - `SRT/Q/2026/0141 (R2)`.
- **AC-A1** A quotation DOCUMENT belongs to a project and carries: `our_ref`, `your_ref`,
  `doc_date`, recipient party, recipient address snapshot, recipient phones snapshot,
  `attn_name`, `subject_title`, `signatory_name`, `signatory_phone`.
- **AC-A2** Every one of those is **prefilled** from the project, the party and the company on
  create (Journey step 2). A field the system can derive is never presented as a blank.
- **AC-A3** The recipient block is **snapshotted onto the document**, not read live from the
  party. A party address edited next year must not rewrite what was sent last year - the same
  doctrine `ProjectQuotationLine` already applies to product facts.
- **AC-A4** A document holds one or more **scopes**, ordered, each with a label. The scope is
  what the UI renders as a tab and the PDF renders as a band.
- **AC-A5** **Per-scope outcome survives.** Won / lost / open, loss reason and decided-at stay
  a property of the scope, and the project outcome stays derived from them (AC-E10 of the
  pipeline UAC). A document with a won townhouse and an open guard house is still live.
- **AC-A6** Deleting a document with any issue is refused, not silently hidden. An unissued
  draft deletes with the standard confirmation.

## Group B - Revisions and what the customer holds

- **AC-B1** An **issue** is a stamped, frozen snapshot of the whole document: `R1`, `R2`, …
  It records which version of each scope's lines it contained, the rendered cover letter, the
  rendered terms, and the grand total at that moment.
- **AC-B2** `Our Ref` prints the issue label the customer quotes back at us (`(R2)` in the
  sample). Current = MAX(issue_no); everything below it is frozen. **No `is_current` flag** -
  two facts that must agree is one fact too many (the rule `ProjectQuotationVersion` already
  states).
- **AC-B3** Re-issuing does not force every scope to change. A scope untouched since R1 keeps
  its lines; the issue records which line-version each scope contributed.
- **AC-B4** A frozen issue is readable, printable and re-downloadable forever, and is never
  edited in place. Editing after issue opens the next revision.
- **AC-B5** Existing bindings keep working: a Project PO or a sample that points at a
  quotation version still resolves, and the detail screens still name the scope it was
  against.

## Group C - Lines, as the real document has them

- **AC-C1** A line carries, in addition to what it carries today: `item_label` (the A / B / C
  letter, optional and free text), `brand_snapshot`, `technical_spec`, `complete_set`.
- **AC-C2** **Rate-only lines.** A line can be quoted with a unit rate and no line total,
  printing `rate only` in the money column and contributing **zero** to every total. The
  sample has five. A system that silently added them would overstate the quote by RM 400+.
- **AC-C3** A scope can contain **section bands** - a labelled break between groups of lines
  (`BILL NO 3 PAGE 15/4`, `OPTION`, `OPTIONAL ITEMS FOR OKU TOILET`). The label is **free text
  copied off the customer's own BQ**; the CRM does not model bill and page numbers, because the
  next customer numbers their BQ differently. A band is a line-level marker, ordered with the
  lines, not a separate table.
- **AC-C4** A line picked from the catalogue snapshots code, description, brand, image and
  list price at the moment it is added, exactly as today.
- **AC-C5** Every existing floor / non-standard alert keeps firing per line, unchanged.

## Group D - Totals (this is where the client was explicit)

- **AC-D1** Each scope tab shows its own total **at the bottom of the money column**, in the
  table's own footer row - never as a chip beside the toolbar. (Shipped for POs already:
  `DataGrid` renders a `<tfoot>` from `columnDef.footer`.)
- **AC-D2** The document shows a **grand total** across scopes, which is what `TOTAL AMOUNT`
  is in the sample.
- **AC-D3** Rate-only lines are excluded from both (AC-C2), and the UI says so where a reader
  might otherwise doubt the arithmetic.
- **AC-D4** Every list on these screens uses the standard pagination bar - "1 - 1 of 1", page
  picker, rows per page - and never a sentence like "3 scopes in this quotation".

## Group E - Cover letter and terms

- **AC-E1** A **cover letter template** and a **terms template** exist **per company** - SRT has
  its set, MOCHA has its own - editable by an admin, with merge fields for the facts the letter
  needs (project title, recipient, attn, our ref, date, grand total, signatory). One active set
  per company per kind; not per project type, and not a library chosen per quotation.
- **AC-E2** A new document renders the active template into its own editable copy. Editing the
  document's letter never touches the template; editing the template never rewrites a document
  already created, and **never** rewrites one already issued (AC-B4).
- **AC-E3** An issue snapshots the RENDERED letter and terms text. What was sent stays
  readable even after the template is rewritten.
- **AC-E4** The editor is rich text with a merge-field picker: bold, lists, links, and
  `{{field}}` insertion. **Deliberately NOT a drag-and-drop block designer in this slice** -
  see the plan's scope note. A cover letter is a page of prose, and the block editor in
  dreamz_ems (~3.5k lines of editor plus its own render pipeline) buys nothing a rich-text
  field with merge fields does not, for this artifact.

## Group F - Output

- **AC-F1** A document issue renders to **PDF** in the layout of the sample: sender block,
  refs, recipient, attn, subject, then each scope as a banded table with the sample's column
  set, then the grand total, terms, sign-off.
- **AC-F2** The same issue exports to **Excel with one sheet per scope** (the client's "tabs"),
  because the customer's QS works in Excel and re-types otherwise. Each sheet carries its own
  total; the grand total is stated on the first sheet. Deliberately NOT the sample's single
  banded sheet: a QS re-pricing the guard house should not have to scroll past the townhouse.
- **AC-F3** Both are produced from the ISSUE snapshot, not from live rows, so a re-download
  next year is byte-for-byte what was sent.
- **AC-F4** Product images print in the `PRODUCT IMAGE` column when the line has one, and the
  column collapses when no line in the scope has an image (an empty column of blank cells on
  every page is worse than no column).

## Group H - Signing (both sides)

The client's reference is the ecohub handover screen: a drawn signature on a white canvas, with
`SIGNED AT`, `IP ADDRESS` and `GPS LOCATION` recorded beside it.

- **AC-H1** The **project owner signs before issuing**, and **an unsigned document cannot be
  issued**. There is never an unsigned Sorento quotation in circulation to explain.
- **AC-H2** Three ways to produce a signature, all in one control: **draw** (mouse, trackpad or
  finger on a touch screen), **type** a name rendered in a signature face, or **initials**. The
  result is one image either way, so everything downstream has a single shape to render.
- **AC-H3** A signature is **saved to the user** and reused with one click on the next quotation.
  It can be re-drawn at any time, and re-drawing does not alter a signature already applied to an
  issued document (the snapshot rule again).
- **AC-H4** Applying a signature records `signed_at`, the signer, IP address and user agent. GPS
  is recorded when the browser gives it and shown as `-` when it does not - the ecohub screen
  shows `-` rather than hiding the field, and that is the honest rendering.
- **AC-H5** The **customer counter-signs**. An issue carries a tokenised public link (the same
  `(auth)` portal family the CRM already uses for contacts, with the existing identity
  confirmation), showing the quotation read-only and a Sign action. The signature, its metadata
  and the acceptance timestamp are stored against the ISSUE.
- **AC-H6** A counter-signed issue is stamped **Accepted**, and the accepted PDF - with BOTH
  signatures on it - is stored and downloadable. That file is the record of what was agreed.
- **AC-H7** **Acceptance WINS the quotation.** Client decision, 2026-08-04, overruling the
  evidence-only reading this document first proposed. When the customer counter-signs an issue,
  every scope that issue carried is set to `won` and the project's outcome derives to won through
  the existing rule. The signature is the commitment, so the system stops pretending it is only
  paperwork.
 - **A scope already marked `lost` is NOT flipped.** Somebody decided that deliberately, and a
    signature on a document that still lists it must not silently overrule a human decision. It
    stays lost and the acceptance is recorded anyway. **[FLAG]** say so if the opposite is wanted.
 - Winning is recorded with `decided_at` and an audit trail naming the acceptance as the cause,
    so "why is this won" is answerable without reading the signature blob.
- **AC-H8** Nothing about counter-signing is required for the CRM record to be complete: a
  customer who never signs leaves the issue in `Issued`, which is a legitimate resting state and
  reads as one, not as an error.

## Group G - What must not break

- **AC-G1** Existing quotations migrate to exactly one document each, with one scope carrying
  the current `scope_label`, outcome, versions and lines. No data is dropped and no total
  changes.
- **AC-G2** PO binding, sample binding, task links, the amendment/divergence flows and the
  pipeline's derived project outcome all keep working against the migrated shape, proven by
  the existing tests continuing to pass without being edited to fit.
- **AC-G3** Company scoping holds on every new table (`CompanyScopedMixin`), with a leak test
  per table.

---

## Answered by the client (2026-08-04)

| # | Question | Answer |
|---|---|---|
| 1 | `Our Ref` format | **Use the existing running-number feature** (`document_numbering_rules`), issue appends the revision (AC-A0) |
| 2 | Who signs | **Project owner**, and **both sides e-sign** - draw / type / initials, customer counter-signs (Group H) |
| 3 | Band labels | **Free text off the customer's BQ** (AC-C3) |
| 4 | Excel export | **One sheet per scope** (AC-F2) |
| 5 | Terms scope | **One standard set per company** (AC-E1) |

Still to settle, raised BY those answers:

- ~~AC-H7~~ ANSWERED 2026-08-04: acceptance WINS the quotation. Folded in above.
- The counter-sign link's identity check: reuse the contact portal's existing confirmation, or
  let anyone holding the link sign? Written as reuse.
