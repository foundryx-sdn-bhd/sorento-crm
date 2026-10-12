# PLAN: WC to seat cover compatibility (data + admin slice)

Status: Plan (behaviour card posted, waiting on owner answers). Track: full (new table + migration).
Lane: WC-SEAT-COMPAT, PR #1590. Chatbot slice is a later lane, after #1582 merges.
UAC: `wc-seat-compat-12oct-acceptance-criteria.md` (alongside).

## 1. Journey

Owner goal: a customer asks "which seat cover fits SRTWC8088" or "which WC fits
SRTSCBD330-UF". Today the answer sits in a flyer ("SORENTO SEAT COVER COMPATIBLE WATER
CLOSET MODEL", UPDATE 01.04.2026) and in 11 hand-typed `seat_cover` spec values. A spec
holds one value per SKU (`app/models/product_spec.py:209-254`), not a product link and not a
list, so it cannot carry this. `product_alternatives` means "use this instead"
(`app/models/product.py:410`) and `product_sets` means "made of": neither fits.

This lane stores the links, imports the flyer once through owner review, and gives staff a
section on the product page to keep them right. The chatbot reads them in a later lane.

## 2. Behaviour card (real examples)

Flyer data: 35 seat cover rows, 36 cover codes, 48 WC codes, 49 pairs; 7 intelligent-seat WCs
with no cover code (page 2). Transcribed by code in
`sorento_crm_backend/app/data/seat_cover_flyer_2026_04.py` (codes only, no PDF).

| Ask | Answer the data gives |
|---|---|
| Which cover fits SRTWC8383-RL-UF (white)? | SRTSCBD331-UF, via the model link SRTWC8383 (flyer row 3) |
| Which cover fits SRTWC8383-RL-MBL? | SRTSCBD823-MBL only (exact SKU link, row 24). Never 331-UF |
| Which cover fits SRTWC8383-RL-BL? | SRTSCBD824-BL only (row 25) |
| Which cover fits a colour SKU the flyer does not list, e.g. an SRTWC8152-...-MBL? | None. No white-cover fallback (owner decision b) |
| Which cover fits SRTWC8122? | SRTSCBD333-UF and SRTSCBD333A-UF, both (row 6, decision c) |
| Which cover fits SRTWC191-G2? | None stored (intelligent seat, no code printed, decision e) |
| Which WCs does SRTSCBD330-UF fit? | Model SRTWC8088 (every non-colour, non-spare SKU of it) |
| Which WCs does SRTSCBD824-BL fit? | The exact SKUs matching SRTWC8383-BL and SRTWC8152-BL |
| SRTWC8383-SC (spare)? | Not imported (decision d), and never inherits the model link |

### Resolution rule (one function, `covers_for_wc`)

For a WC SKU:
1. If the SKU has exact links, those and only those.
2. Else, if the SKU is a colour SKU or a spare (`-SC`) SKU: nothing.
3. Else the links of its model (`products.family_code`, `app/services/product_family.py`).

Colour SKU: a code segment after the model is one of `BL`, `MBL`, `GY`, `RG` (the four
finishes the flyer prints, a subset of `FINISH_SUFFIXES` in
`app/services/product_spec_derivation.py:459`). Spare: a segment `SC`.

### Flyer code to SKU (the review screen shows this)

- Plain WC code (no segment after the model, e.g. `SRTWC8383`): one model link,
  `family_code = SRTWC8383`. The review card lists the SKUs it covers today, and greys the
  colour and spare SKUs it skips.
- Colour WC code (e.g. `SRTWC8383-MBL`, `SRTWC8066-S-BL`): one exact link per SKU of that
  model whose segments contain every printed segment as a whole segment (`MBL` matches
  `SRTWC8383-RL-MBL`, never `...-BL`). The card shows `SRTWC8383-MBL -> SRTWC8383-RL-MBL`.
  A colour code that matches no SKU is shown as "No SKU found" and cannot be ticked.
- A seat cover code must exist exactly; if not, the row is shown "Seat cover not found" and
  cannot be ticked. No name matching anywhere.

## 3. Design

### Table `product_compatibility` (additive, one migration)

| column | type | note |
|---|---|---|
| id | uuid pk | |
| company_id | uuid | `CompanyScopedMixin`, same as `product_alternatives` |
| seat_cover_product_id | uuid FK products ON DELETE CASCADE | not null |
| wc_family_code | varchar(100) null | model link |
| wc_product_id | uuid FK products ON DELETE CASCADE null | exact SKU link |
| source | varchar(20) | `flyer` or `manual` (CHECK) |
| created_at, created_by | | |

- CHECK exactly one of `wc_family_code`, `wc_product_id`.
- Unique partial indexes: (company, cover, family) and (cover, wc_product_id).
- Index on `wc_family_code`, `wc_product_id` for the WC-side read.
- `__audit_track__ = True`, entity type `product_compatibility`.

### Import: proposal -> owner review -> apply, with no proposal tables

The flyer is a fixed list in code, so the proposals are derived on read from that list and the
live catalogue (same reasoning as `ProductSetProposalBatch`, which keeps no state it can
derive; here not even a batch is needed). The apply payload names flyer pair keys
(`row:wc_code:cover_code`), never product ids; the server re-derives each pair and writes it.
A pair already linked shows "Linked" and is not re-applied. Re-running apply is idempotent.

- `GET  /api/v1/master-data/product-compatibility/flyer-proposals`
- `POST /api/v1/master-data/product-compatibility/flyer-proposals/apply` `{keys: [...]}`

### Product page

`Compatible` section in the Variants tab under Alternatives
(`products/[id]/components/ProductVariantsTab.tsx:402`), modelled on
`ProductAlternativesSection.tsx`:

- "Seat covers that fit" (this product as a WC): resolved by the rule above; each row says
  `This SKU` or `Model SRTWC8383`.
- "Fits these WCs" (this product as a cover): model rows and SKU rows.
- Add (one button, dialog): pick the other product, pick its role (seat cover / WC), pick
  scope for the WC (this SKU / whole model). Unlink is the deferred pending action, like
  Alternatives.
- Endpoints: `GET /products/{id}/compatibility`, `POST /products/{id}/compatibility`,
  unlink through `pending_actions` like `product_alternative`.

### Retiring the `seat_cover` spec

After apply: remove the 11 `seat_cover` values through `product_spec_write` and set registry
key `seat_cover` `is_active=false`. #1582 must not expose it. (Question 4.)

## 4. Questions (posted as crew-ask)

See PR #1590 comment. Answers recorded here when they arrive.

## 5. Test list (red first)

Backend (`tests/test_product_compatibility.py`, Postgres):
1. CHECK rejects both / neither of family and product.
2. `covers_for_wc`: exact beats family; colour SKU without exact gets nothing; `-SC` gets
   nothing; plain SKU gets family; SRTWC8122 gets both 333 covers; unlisted WC gets nothing.
3. `wcs_for_cover`: model rows + SKU rows.
4. Flyer data: 49 pairs, 36 distinct covers, 48 distinct WC codes, no smart-WC rows.
5. Flyer proposals: colour code to SKU segment match (MBL never matches BL); missing cover
   and missing SKU are not tickable; already-linked pair reports linked.
6. Apply writes only the ticked keys, source `flyer`, idempotent, unknown key 422.
7. Permission: view needs `master_data.products.view`, writes need `master_data.products.edit`.
8. Product page add / list / unlink, company scoping (other company's product 404).

Frontend (vitest): review screen renders mapping + disabled rows; section renders both lists,
empty state, add dialog scope choice.
