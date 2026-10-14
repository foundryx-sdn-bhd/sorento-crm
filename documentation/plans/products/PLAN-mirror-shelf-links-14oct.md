# PLAN: mirror / shelf -> cabinet links (Part 1: data + product page + import)

Status: Build. Track: full (migration + prod data import). Lane: MIRROR-LINKS.
UAC: `mirror-shelf-links-14oct-acceptance-criteria.md` (alongside).
Builds on: `documentation/plans/products/PLAN-wc-seat-compat-12oct.md` (PR #1590).
Part 2 (the chatbot answering "mirror for SRTBF31415") is a separate later lane after #1613
merges. No chatbot code changes here.

## 1. Journey

Owner, 14 Oct: the flyer "SORENTO UPDATED MIRROR & BATHROOM FURNITURE LIST" (UPDATE 03.08.2026)
prints which mirror (`SRTMR...`) and which shelf (`SRTSV...`) is "Suitable For Cabinet" for each
basin cabinet (`SRTBF...`). Staff need the same links the seat cover flyer got: stored once from
the flyer, visible and kept by hand on the product page.

## 2. Decisions

- The link table `product_compatibility` gains `kind` (`seat_cover` | `mirror` | `shelf`),
  NOT NULL, default `seat_cover`, CHECK constrained. Existing rows stay `seat_cover`.
- Kind lives on the link. It is never inferred from a code prefix (SRTSV310-DIY and
  SRTSV334-RED exist and are not flyer shelves; dev product names equal codes).
- Columns are NOT renamed: `seat_cover_product_id` is the accessory end (seat cover, mirror,
  shelf), `wc_product_id` is the host end (WC, cabinet). Renaming would rewrite a live table for
  no behaviour gain. The model documents the mapping.
- Unique index becomes (accessory, host, kind).
- Service: a small `KINDS` dict (kind -> accessory noun, host noun). `covers_for` / `wcs_for`
  keep their names and now read `seat_cover` links only, so the chatbot's fits filter
  (`product_find._run_fits`) never sees a mirror as a seat cover. Generic `accessories_for` /
  `hosts_for(kind=...)` serve Part 2.
- `add_manual` takes `kind` + `other_role` (`accessory` | `host`); the legacy `seat_cover` /
  `wc` roles still work and mean kind `seat_cover`. The `-SC` guard applies to seat cover links
  only.
- API: `GET /products/{id}/compatibility` rows carry `kind`; `covers` (this product is the host)
  and `fits` (this product is the accessory) keep their keys and now hold every kind.
- Product page: the existing Compatible section groups rows by kind with the kind's labels
  ("Seat covers that fit" / "Fits these WCs", "Mirrors that fit" / "Shelves that fit" /
  "Fits these cabinets"). Empty groups for kinds the product has no link of are hidden, except
  the seat cover pair is shown when the product has no link at all (today's empty state). The add
  dialog gains a link type picker (Seat cover / WC, Mirror / cabinet, Shelf / cabinet) above the
  side picker.
- Loader: `scripts/oneoff/link_mirror_shelf_flyer.py`, rows `kind,accessory_code,host_code` in
  the script (`FLYER_ROWS`), exact code match per company, dry run by default, `--apply`,
  idempotent, source `flyer`, skips and reports codes it cannot find (SRTMR11417, SRTSV11417 are
  not on dev).

## 3. Prod step (owner, after deploy)

```
cd sorento_crm_backend
venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py          # dry run, read the report
venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py --apply  # write
venv/bin/python scripts/oneoff/link_mirror_shelf_flyer.py          # second dry run: 0 to write
```

## 4. Test list (red first)

Service: kind column default, mirror/shelf add_manual both orientations, no `-SC` guard for
mirror, duplicate same kind refused either orientation, covers_for ignores mirror links,
list rows carry kind. API: POST with kind, invalid kind 422, legacy role still works, GET rows
carry kind. Script: dry run writes nothing, apply writes 40 links (42 rows less 2 missing codes)
per company that holds them, second apply writes 0, missing codes reported. Migration: column,
check, unique index on head. UI: groups by kind with labels, kind picker posts kind.
