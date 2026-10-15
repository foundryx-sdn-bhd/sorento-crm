# PLAN: price tag descriptions from the flyer product-code sheet

Status: Build (card answered 15 Oct). Track: full L (bulk prod data write, owner runs it). Lane: PRICETAG-DESC-IMPORT.
UAC: `pricetag-desc-import-15oct-acceptance-criteria.md` (alongside).

## 1. Journey

Owner, 15 Oct: marketing's sheet `Sorento_Flyer_Product_Codes.xlsx` (tab Products: No. /
Product Code / Description / Flyer Page(s), 1084 rows) maps each flyer code to its flyer text,
which is the price tag description. One script sets `products.price_tag_description` from it.
Family rows (SRTWC286-SH) reach their variants with the matching S-trap / P-trap text; a combo
"BF + MR" puts the text on the BF only.

## 2. Facts (cited)

- Field: `products.price_tag_description` (`app/models/product.py:279`), staff copy, never
  written by AutoCount. Tags read it at `app/services/dealer_kit/tag_data_service.py:351`; the FE
  renders it as a template, line by line (`sorento_crm_frontend/lib/dealer-kit/merge-fields.ts:299-318`),
  into a Konva `<Text>` (`KonvaTagLayer.tsx`), which draws `\n` as a line break. No sheet row
  contains `{`.
- Families: no curated link. `variant_of_id` (`product.py:207`) is set on 1 of 15,339 dev rows;
  `family_code` (`product.py:225`, `app/services/product_family.py:22`) is the text before the
  first dash, too coarse (SRTWC8066 holds S, SH, SC, P). The rule used is the flyer spec reader's
  R1 (`app/services/product_spec_flyer_ingest.py:359`, captain ruling 31 Aug): `<code>-<anything>`,
  longest printed code wins, a printed code is never a variant. Owner chose it on the card (Q1 a)
  with the finish / seat-material guard.
- Trap type: stored per product in `product_specifications.values.trap_type.value`
  (`s_trap` / `p_trap`, model `app/models/product_spec.py:209`). Finish and seat material are
  `values.finish.value` / `values.seat_material.value`.
- Audit: `Product` is audited (`product.py:174`). The script calls `register_audit_listeners()`
  (as `scripts/reproject_consignments.py:131`) and stamps an `AuditActor(actor_type="system",
  job_id="import_price_tag_descriptions")`, so each ORM change writes one UPDATE row with old/new.
  The embedding and spec re-derive listeners register only at app startup
  (`embedding_change_listener.py:186`, `product_spec_change_listener.py:210`), so the script
  queues no embeddings and re-derives no specs.
- Precedent script shape: `scripts/oneoff/link_mirror_shelf_flyer.py` (dry run default,
  `--apply`, `main(argv, db=None, out=None)`), test `tests/test_link_mirror_shelf_flyer.py`
  (`blank_session`, `tests/_mc_lookup_seed.product`).

## 3. Decisions (card answers, crew 15 Oct)

Q1 a variants by R1 with finish/seat guard; Q2 b merge format-equal rows, longest wins only when
it contains every shorter text, else skip; Q3 a keep the printed outlet list; Q4 a overwrite, CSV
shows old -> new; Q5 a keep line breaks, verify the tag renders them on the dev copy before prod.
Lead decision: a product whose stored trap type is not printed in a one-trap text is skipped
(1 dev case, `SRTWC201-P`), no question needed: skipping is the never-guess default.

## 4. Contract (binding for tester and coder)

`sorento_crm_backend/scripts/import_price_tag_descriptions.py`

- CLI: `--file <xlsx>` (required), `--company <code>` (default `SRT`), `--apply`.
- `main(argv: Sequence[str] | None = None, db=None, out: TextIO | None = None) -> int`:
  when `db` is given the script uses it and does not close it (tests); otherwise `SessionLocal()`.
  Exit 0 on success, 1 on a missing header / unknown company / unreadable file.
- Output (to `out`): one line per update, then a summary block that contains, each on its own line:
  `updates: <n>`, `unchanged: <n>`, `skipped: <n>`, then one `  <reason>: <n>` line per skip reason,
  then `changes report: <path>` and `skipped report: <path>`. Dry run ends with
  `DRY RUN: <n> products to update. Run with --apply to write them.`; apply ends with
  `<n> products updated.`
- Reasons (exact strings, used in the CSV and the summary): `no code`, `no description`,
  `text starts with part of a code`, `combo is not two codes`, `code not found in the CRM`,
  `same code on several rows with different text`, `variant of a sheet code that is not a product`,
  `variant has a different finish`, `variant has a different seat material`,
  `variant has a different finish and seat material`, `variant of <CODE>, which was skipped`,
  `both traps printed, product has no trap type`, `product's trap type is not printed`.
- Rules (exact strings): `exact`, `combo`, `variant`, each optionally suffixed `+trap`.
- CSVs: UAC AC-23 to AC-25 (names, columns, order).
- No other module changes. No migration. No FE change.

## 5. Test list (red first, `sorento_crm_backend/tests/test_import_price_tag_descriptions.py`)

Parse: headers by name, missing header exit 1, row numbers, blank code / blank text / `+`/`-`
fragment skipped. Exact match with code normalisation. Combo -> BF only, partner untouched, three
codes skipped. Variants: same finish/seat updated; different finish skipped; different seat
skipped; sheet code not a product -> variants skipped; printed code never a variant; longest base
wins. Trap: S block / P block exact text; both printed + no trap type skipped; one trap printed +
other trap type skipped; no trap type + one trap printed -> text unchanged. Duplicates:
format-equal merged (first row's text), contained -> longest, conflict skipped with rows + texts
in the skipped CSV, variants of a conflicted code skipped. Apply: overwrite existing text, one
transaction, one audit UPDATE row per change with old/new, second apply 0 updates + 0 audit rows,
dry run after apply 0 to update. Dry run writes nothing. Company: other company's same code
untouched, unknown company exit 1. CSV files next to the xlsx with the columns above.

## 6. Dev copy run + render check

Crew runs on the shared dev DB: dry run (expected about 1008 updates, 225 skips on
`sorento_cagent_stack` as of 15 Oct), then `--apply`, then dry run = 0. Then a browser pass on the
crew test copy: a tag binding Price tag description for `SRTWC286-SH-P` shows the P-Trap text on
separate lines. If it runs together, report before any prod run.

## 7. Prod steps (owner)

```
cd sorento_crm_backend
venv/bin/python scripts/import_price_tag_descriptions.py --file "<path>/Sorento_Flyer_Product_Codes.xlsx"
#   read the two CSVs next to the xlsx (changes: old -> new; skipped: send to marketing)
venv/bin/python scripts/import_price_tag_descriptions.py --file "<same path>" --apply
venv/bin/python scripts/import_price_tag_descriptions.py --file "<same path>"   # must say 0 products to update
```
