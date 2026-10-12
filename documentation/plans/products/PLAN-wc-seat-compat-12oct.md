# PLAN: WC to seat cover compatibility (data + admin slice)

Status: Build (backend green, frontend next). Track: full (new table + migration).
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
| Cover for SRTWC8383-RL-UF? | SRTSCBD331-UF, once the owner ticks it (printed SRTWC8383, row 3) |
| Cover for SRTWC8383-RL-MBL? | SRTSCBD823-MBL only (printed SRTWC8383-MBL, row 24) |
| Cover for an SRTWC8383 SKU in a finish the flyer does not print (e.g. an MK segment)? | Nothing. UNRESOLVED on the review screen (finish not printed) |
| Cover for a plain (no colour) SRTWC8066-S SKU? | Nothing. UNRESOLVED (S is printed only with a colour) |
| Cover for SRTWC8122? | SRTSCBD333-UF and SRTSCBD333A-UF |
| Cover for a pink SRTWC202 SKU? | SRTSCBD326 (row 22 prints SRTWC202 with the note "Pink") |
| Cover for SRTWC191-G2? | Nothing (intelligent seat, no code printed) |
| WCs for SRTSCBD824-BL? | The SKUs ticked under printed SRTWC8383-BL and SRTWC8152-BL |
| SRTWC8383-SC? | A seat cover. Never listed as a WC; cannot be linked as one |

### Mapping a printed WC code to SKUs (`product_compatibility_service._classify`)

For each SKU of a printed model (`products.family_code`), `-SC` SKUs excluded:

1. Candidates: printed codes of the model whose segments the SKU holds as whole segments
   (`SRTWC8383-MBL` -> {MBL} is held by `SRTWC8383-RL-MBL`, never by `...-RL-BL`).
2. The most specific candidate wins; two equally specific = UNRESOLVED (`ambiguous`).
3. The SKU's other segments must all be plain variant markers: `RL SH P S UF NEW N PP`, a
   size number, or `S150`-style. Otherwise UNRESOLVED:
   - `sub_model_not_printed`: a segment the flyer prints only on other codes of the model;
   - `finish_not_printed`: a finish (`BL MBL GY RG GM NL NK CR FRG WH MG MK MW BW PINK BLUE`);
   - `unknown_segment`: anything else (`FT`, `LID`, `WEPLS`, `289UF`, ...).
4. A finish printed as a row note (row 22 Pink, row 23 Blue) counts as printed for that row.
5. A ready SKU whose printed cover is not in the catalogue is `cover_missing`, not tickable.

Calibrated against the dev catalogue's 212 SKUs of the 35 printed models (crew, 12 Oct; data
not committed). The review screen shows every SKU's extra segments so the owner judges each
tick; UNRESOLVED SKUs are listed and cannot be ticked. Staff link them by hand on the product
page if the owner decides they fit.

## 4. Design

### Table `product_compatibility` (migration `wc_compat_0001_table`, additive)

`id`, `company_id` (`CompanyScopedMixin`), `seat_cover_product_id` FK products CASCADE,
`wc_product_id` FK products CASCADE, `source` (`flyer` | `manual`, CHECK), `created_at`,
`created_by`. Unique (cover, wc); CHECK cover <> wc; index on `wc_product_id`. Audited
(`__audit_track__`, entity `product_compatibility`).

### Flyer data

`app/services/seat_cover_flyer_data.py`: the 35 printed rows, codes only, plus the row-note
finishes. The PDF is not committed.

### API

- `GET  /api/v1/master-data/products/{id}/compatibility` (products.view) -> `{covers, fits}`
- `POST /api/v1/master-data/products/{id}/compatibility` (products.edit, session only)
  `{other_product_id, other_role: seat_cover|wc}`
- `GET  /api/v1/master-data/product-compatibility/flyer-review` (products.view)
  -> `{models: [{family_code, printed: [{flyer_row, wc_code, covers: [{code, found}]}], skus:
  [{product_id, product_code, status, reason, printed_code, flyer_row, cover_codes,
  extra_segments, ...}]}], counts, seat_cover_spec}`
- `POST /api/v1/master-data/product-compatibility/flyer-review/apply` (products.edit, session
  only) `{wc_product_ids}`. The server re-derives every id; anything not ready is skipped with
  its reason. Idempotent. No proposal tables: the review is derived on read from the fixed
  flyer list and the live catalogue.
- Pending actions: `product_compatibility.unlink` (reversible, products.edit) and
  `seat_cover_spec.retire` (destructive, spec_registry.edit; refused 409 before any flyer
  link exists). Retire deactivates the key first, reverts every value through
  `product_spec_write.apply_spec_values`, and writes one audit row holding the removed values.

### Frontend

- Review screen `/master-data-management/products/seat-cover-flyer`, linked from the Products
  list header. One card per printed model: printed codes with their covers (found / missing),
  then the SKU table with tick boxes for ready SKUs, Linked badges, and UNRESOLVED rows with
  their reason. Model checkbox ticks every ready SKU of the model. Sticky apply bar. Retire
  card at the top.
- `ProductCompatibilitySection` under `ProductAlternativesSection` in the Variants tab: "Seat
  covers that fit" and "Fits these WCs", Add dialog (pick product + role), Unlink countdown.

## 5. Test list

Backend `tests/test_product_compatibility.py` (41): flyer data counts; table CHECKs and unique;
reads with no inheritance; page API both directions, empty, 403s, 409 duplicate both ways,
400 self, 422 role, -SC never a WC, cross-company 404 (GET, add, park); unlink park 202/404/403;
review mapping by whole segment, every UNRESOLVED reason, row-note finish, -SC never listed,
missing cover / model; apply only ticked, idempotent, skips unresolved / not-on-flyer, 422 bad
id; retire registered destructive, 403 / 409 at park, removes values + deactivates + audit.

Frontend (vitest): review screen states and ticking; section lists, empty state, add, unlink.
