# PLAN: WC to seat cover compatibility (data + admin slice)

Status: Review (scope cut 12 Oct: one-off script + product-page section; mock v3 approved). Track: full (new table + migration).
Lane: WC-SEAT-COMPAT, PR #1590. Chatbot slice is a later lane, after #1582 merges.
UAC: `wc-seat-compat-12oct-acceptance-criteria.md` (alongside).

## 1. Journey

A customer asks "which seat cover fits SRTWC8088" or "which WC fits SRTSCBD330-UF". Today the
answer sits in a flyer ("SORENTO SEAT COVER COMPATIBLE WATER CLOSET MODEL", UPDATE 01.04.2026)
and in a few hand-typed `seat_cover` spec values. A spec holds one value per SKU
(`app/models/product_spec.py`), not a product link and not a list. `product_alternatives`
means "use this instead" and `product_sets` means "made of": neither fits.

This lane stores the links, imports the flyer once through owner review, gives staff a
Compatible section on the product page, and retires the old spec. The chatbot reads the
links in a later lane.

## 2. Owner rulings (12 Oct, final)

- **Match only what the flyer prints; fabricate nothing.** No inheritance and no guesses
  beyond the printed rows. A SKU has a cover only when a link names it. No link = no answer.
- A colour SKU with its own link shows only its colour-matched cover; an unlisted colour SKU
  gets nothing (no white fallback).
- SRTWC8122: both SRTSCBD333-UF and SRTSCBD333A-UF, as printed.
- `-SC` SKUs are seat covers, not WCs. Not in the flyer, so no links from the import, and
  never treated or listed as WCs.
- The 7 intelligent-seat WCs (page 2) print no cover code: nothing stored.
- Q4: retire the old `Seat Cover` spec with a button, enabled after the flyer pairs are
  applied, with an audit trail.
- Q5: one Compatible section under Alternatives, existing view/edit permissions.

## 3. Behaviour card (real examples)

| Ask | Answer the data gives |
|---|---|
| Cover for SRTWC8383-RL-UF? | SRTSCBD331-UF, after the script runs with --apply (printed SRTWC8383, row 3) |
| Cover for SRTWC8383-RL-MBL? | SRTSCBD823-MBL only (printed SRTWC8383-MBL, row 24) |
| Cover for an SRTWC8383 SKU in a finish the flyer does not print (e.g. an MK segment)? | Nothing. The script reports it UNRESOLVED (finish not printed) and never links it |
| Cover for a plain (no colour) SRTWC8066-S SKU? | Nothing. UNRESOLVED (S is printed only with a colour) |
| Cover for SRTWC8122? | SRTSCBD333-UF and SRTSCBD333A-UF |
| Cover for a pink SRTWC202 SKU? | SRTSCBD326 (row 22 prints SRTWC202 with the note "Pink") |
| Cover for SRTWC191-G2? | Nothing (intelligent seat, no code printed) |
| WCs for SRTSCBD824-BL? | The SKUs the script links under printed SRTWC8383-BL and SRTWC8152-BL |
| SRTWC8383-SC? | A seat cover. Never listed as a WC; cannot be linked as one |

### Mapping a printed WC code to SKUs (`product_compatibility_service._classify`)

For each SKU of a printed model (`products.family_code`), `-SC` SKUs excluded:

0. A SKU grouped under a printed model by hand (its own code's model differs from its
   `family_code`) is UNRESOLVED (`not_printed`).
1. Candidates: printed codes of the model whose segments the SKU holds as whole segments
   (`SRTWC8383-MBL` -> {MBL} is held by `SRTWC8383-RL-MBL`, never by `...-RL-BL`).
2. The most specific candidate wins; two equally specific = UNRESOLVED (`ambiguous`).
3. The SKU's other segments must all be plain variant markers: `RL SH P S UF NEW N PP`, a
   size number, or `S150`-style. Otherwise UNRESOLVED:
   - `sub_model_not_printed`: a segment the flyer prints only on other codes of the model;
   - `finish_not_printed`: a finish (`BL MBL GY RG GM NL NK CR FRG WH MG MK MW BW PINK BLUE`);
   - `unknown_segment`: anything else (`FT`, `LID`, `WEPLS`, `289UF`, ...).
4. A finish printed as a row note (row 22 Pink, row 23 Blue) counts as printed for that row.
5. A SKU whose printed cover is not in its company's catalogue is MISSING (`cover_missing`), not linked.

Calibrated against the dev catalogue's 212 SKUs of the 35 printed models (crew, 12 Oct; data
not committed). The script prints every UNRESOLVED SKU with its reason and never links it.
Staff link one by hand on the product page if the owner decides it fits.

## 4. Design (owner scope decision 12 Oct, after mock v2)

No flyer review page and no flyer routes. The flyer is linked ONCE by a script; from then on
people keep the links by hand in the Compatible section of the product page. Mock v3
(`mockups/07-wc-seat-compat.html`) was approved ("ok").

### Table `product_compatibility` (migration `wc_compat_0001_table`, additive)

`id`, `company_id` (`CompanyScopedMixin`), `seat_cover_product_id` FK products CASCADE,
`wc_product_id` FK products CASCADE, `source` (`flyer` | `manual`, CHECK), `created_at`,
`created_by`. Unique (cover, wc); CHECK cover <> wc; index on `wc_product_id`. Audited.

### One-off script `sorento_crm_backend/scripts/oneoff/link_seat_cover_flyer.py`

- Carries the 49 printed pairs (35 rows) as data, codes as printed, plus the row-note
  finishes (row 22 Pink, row 23 Blue). No PDF.
- Section 3's rule decides each SKU: LINK, UNRESOLVED (printed with its reason, never linked)
  or MISSING (no SKU for the printed model, or the printed cover is not in that company).
  `-SC` SKUs are never treated or listed as WCs.
- Dry run by default (prints every LINK / UNRESOLVED / MISSING line); `--apply` writes the
  links with source `flyer` in each WC's own company; idempotent (an existing pair, flyer or
  manual, is counted, never duplicated).
- `--retire-spec` (only with `--apply`): removes every `seat_cover` spec value through
  `product_spec_write.apply_spec_values` (op revert), sets the key inactive and clears its
  derivation rules (derivation reads inactive keys too), with one audit row holding the
  removed values per company. Refused, writing nothing, while a company holding a value has
  no flyer link to replace it.

### API (product page only)

- `GET  /api/v1/master-data/products/{id}/compatibility` (products.view) -> `{covers, fits}`
- `POST /api/v1/master-data/products/{id}/compatibility` (products.edit, session only)
  `{other_product_id, other_role: seat_cover|wc}`. A pair already linked in either
  orientation is 409; a `-SC` SKU as the WC is 400.
- Unlink: pending action `product_compatibility.unlink` (reversible, products.edit).

### Frontend

`ProductCompatibilitySection` in the product page's **Variants** tab, directly under
Alternatives (`products/[id]/components/ProductVariantsTab.tsx`): "Seat covers that fit" and
"Fits these WCs", Flyer / Manual badge, Add dialog (pick product + role), Unlink countdown.

## 5. Test list

- `tests/test_link_seat_cover_flyer.py` (9): flyer data counts; plan links only unambiguous
  SKUs (colour row by whole segment, row 6 both covers, row-note Pink), every unresolved
  reason, -SC never listed, missing cover; dry run prints the lines and writes nothing; apply
  writes source flyer and is idempotent; keeps a manual link; links per company; retire needs
  apply; apply + retire removes values, deactivates, clears rules, audits, idempotent; retire
  refused while another company has no flyer link.
- `tests/test_product_compatibility.py` (20): table CHECKs/unique; reads with no
  inheritance; page API both directions, empty, 403s, 409 both orientations, 400 self / -SC,
  422 role, cross-company 404; unlink park 202/404/403.
- vitest `ProductCompatibilitySection.test.tsx`.
