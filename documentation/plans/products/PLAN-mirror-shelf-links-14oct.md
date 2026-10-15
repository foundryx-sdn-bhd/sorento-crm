# PLAN: mirror / shelf -> cabinet links (Part 1: data + product page + import)

Status: Merge-ready (owner hand test PASS, reviewer READY, security CLEAN on fe10c3297; PR #1690). Track: full (migration + prod data import). Lane: MIRROR-LINKS.
UAC: `mirror-shelf-links-14oct-acceptance-criteria.md` (alongside).
Builds on: `documentation/plans/products/PLAN-wc-seat-compat-12oct.md` (PR #1590).
Part 2 (the chatbot answering "mirror for SRTBF31415") is a separate later lane after #1613
merges. The only chatbot edit here is the seat cover kind filter in section 2.

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
- Every v1 seat cover lookup reads `kind = 'seat_cover'` links only: `covers_for` / `wcs_for`
  and, after #1613 merged, `chatbot/lanes/business/find.py::_linked_codes_holding` (a one-line
  join filter, the lane's only chatbot edit; regression tests `test_product_find.py::test_f1_*`,
  `tests/chatbot/test_seat_as_product.py::test_r4b/test_r4c`).
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

## 5. Contract (binding for tester and coder)

### Migration `wc_compat_0002_kind` (down_revision = main's head at merge time, `frc_0001_debtor_type_no_cs`)

Idempotent SQL, same style as `wc_compat_0001_table.py`:

- `ALTER TABLE product_compatibility ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'seat_cover'`
- constraint `ck_product_compatibility_kind CHECK (kind IN ('seat_cover','mirror','shelf'))`
  (added only if missing, via a `DO $$ ... pg_constraint` guard)
- `DROP INDEX IF EXISTS uq_product_compatibility_pair`;
  `CREATE UNIQUE INDEX IF NOT EXISTS uq_product_compatibility_pair_kind ON product_compatibility (seat_cover_product_id, wc_product_id, kind)`
- downgrade: delete rows with kind <> 'seat_cover', drop the new index, recreate the old pair
  index, drop the constraint and the column.

Model `ProductCompatibility` gets `kind = Column(String(20), nullable=False, default="seat_cover", server_default="seat_cover")`
and the same CheckConstraint + unique Index (name `uq_product_compatibility_pair_kind`).

### Service `app/services/product_compatibility_service.py`

```
KIND_SEAT_COVER = "seat_cover"; KIND_MIRROR = "mirror"; KIND_SHELF = "shelf"
#: kind -> Kind(accessory noun, host noun, accessory product_type). product_type is a value of
#: the spec registry's product_type list (product_spec_registry.py SPEC_REGISTRY_SEED); crew
#: asked for it (PV2-FIX, 14 Oct) so the PV2 relation kinds need no extra migration. Its reader
#: is the Part 2 chatbot lane.
KINDS = {"seat_cover": Kind("seat cover", "WC", "toilet_seat"),
         "mirror": Kind("mirror", "cabinet", "mirror"),
         "shelf": Kind("shelf", "cabinet", "towel_shelf")}
ROLE_ACCESSORY = "accessory"; ROLE_HOST = "host"
ROLE_SEAT_COVER = "seat_cover"; ROLE_WC = "wc"            # legacy, kind seat_cover only

covers_for(db, wc_ids)        # unchanged signature; seat_cover links ONLY
wcs_for(db, cover_ids)        # unchanged signature; seat_cover links ONLY
accessories_for(db, host_ids, kind) -> {host_id: [Product]}      # ordered by code
hosts_for(db, accessory_ids, kind) -> {accessory_id: [Product]}  # ordered by code
list_for_product(db, product_id) -> {"covers": [row], "fits": [row]}
    covers = links where this product is the host (wc_product_id), every kind
    fits   = links where this product is the accessory (seat_cover_product_id), every kind
    row    = {id, product_id, product_code, product_name, source, is_discontinued, kind}
    ordered by kind (seat_cover, mirror, shelf), then product_code
add_manual(db, product_id, other_product_id, other_role, user_id, kind="seat_cover") -> row
    other_role: accessory | host | seat_cover (== accessory, kind must be seat_cover) | wc (== host, kind must be seat_cover)
    unknown kind or role, or a legacy role with another kind -> 400 (handle_validation_error)
    self -> 400; other product in another company -> 404
    -SC guard ("<code> is a seat cover, not a WC.") only when kind == seat_cover
    same two products already linked under the SAME kind, either orientation -> 409
      message "<accessory code> is already linked to <host code>."
    the same pair under a different kind is allowed
```

### API (`app/api/v1/master_data/product_compatibility.py`, `app/schemas/product_compatibility.py`)

- `ProductCompatibilityCreate`: `other_product_id: str`,
  `other_role: Literal["accessory","host","seat_cover","wc"]`,
  `kind: Literal["seat_cover","mirror","shelf"] = "seat_cover"`. Bad kind/role -> 422.
- `ProductCompatibilityRow` gains `kind: str`.
- Permissions unchanged (view: session or API key; add: session only, `master_data.products.edit`).

### Loader `scripts/oneoff/link_mirror_shelf_flyer.py`

- `FLYER_ROWS: tuple[tuple[str, str, str], ...]` = (kind, accessory_code, host_code), the 42
  rows of the lane brief in order.
- `plan(db) -> {"links": [...], "missing": [...]}`, every company (`company_scope(db, None)`),
  codes matched with `_norm_sql(Product.product_code) == normalize_code(code)`:
  - a company "holds" a row when it has either code; for each (row, company that holds a code
    of the row): both found -> link dict `{kind, accessory_code, host_code, company_id,
    accessory_id, host_id, already, inactive}`; else a missing dict `{kind, accessory_code,
    host_code, company_id, not_found: [codes]}`.
  - a row whose codes no company holds -> one missing dict with `company_id=None` and both codes
    in `not_found`... (only codes absent everywhere are listed).
  - `already`: the same accessory, host and kind linked, either orientation.
- `apply(db, plan) -> (written, already)`: adds `ProductCompatibility(kind=..., source="flyer")`, flushes.
- `main(argv=None, db=None, out=None) -> int`: prints `LINK  <kind>  <accessory> -> <host>`
  (+ `  (inactive SKU)` / `  (already linked)`), `MISSING  <kind>  <accessory> -> <host>  not found: <codes>`,
  then `DRY RUN: N links to write. Run with --apply to write them.` (no flag; writes nothing)
  or `N links written, M already linked.` (`--apply`, commits). Exit 0. Never raises on a missing code.

### Frontend

- types: `CompatibilityKind = 'seat_cover'|'mirror'|'shelf'`, `CompatibilityRole = 'accessory'|'host'`,
  row gains `kind: CompatibilityKind`.
- service `addProductCompatibility(productId, otherProductId, otherRole, kind)` posts
  `{other_product_id, other_role, kind}`; hook mutate args `{otherProductId, otherRole, kind}`.
- `ProductCompatibilitySection`:
  - covers grouped by kind, headings "Seat covers that fit" / "Mirrors that fit" / "Shelves that fit";
    fits grouped by host noun, headings "Fits these WCs" / "Fits these cabinets".
  - when the product has no mirror/shelf link, today's layout stays exactly (both seat cover
    headings, empty states "No seat covers" / "No WCs"). When it has any mirror or shelf link,
    only the groups that have rows are shown.
  - add dialog: a "Link type" radio group (Seat cover / WC = seat_cover, Mirror / cabinet =
    mirror, Shelf / cabinet = shelf), then "The other product is a" radio with the kind's two
    nouns capitalised (Seat cover/WC, Mirror/Cabinet, Shelf/Cabinet), values accessory/host.
  - default on open: a product with fits rows -> kind of its first fits row, role host; else
    covers rows -> kind of its first covers row, role accessory; else seat cover heuristic
    (`looksLikeSeatCover`) -> seat_cover/host, otherwise seat_cover/accessory.
