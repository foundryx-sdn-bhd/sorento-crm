# PLAN: AutoCount DO ingest, FromDocDtlKey 0 means "no link" (DO-SO-LINE-LINK)

Status: **built, reviewed, CI green on PR #1409, awaiting hand test + merge** (small fix track: one service file plus tests, no migration, no
auth/RBAC change, no new ingest surface). Branch `claude/do-so-line-link-qnsztu` (the session's
designated branch; the brief named `crew/do-so-line-link`), cut from `origin/main` `b8cdbebe`.
UAC: `do-so-line-link-1oct-acceptance-criteria.md` (AC-DSL001 onward).

## Problem (premises verified on `b8cdbebe`)

AutoCount sends `FromDocDtlKey = 0` on every DO line (3,841 of 3,841 item lines in the 01-03 Sep
snapshot, 3,718 of them with `FromDocType "SO"`; found on the #1408 sim).

- `_int(0)` returns `0`, not None: `sorento_crm_backend/app/services/autocount_doc_ingest_service.py:159-170`,
  applied to `FromDocDtlKey` at `:440`.
- The DO line guard `line.from_dtl_key is not None` (`:846`) therefore looks up DtlKey 0;
  `_so_line` (`:728-738`) matches `source_ref == "0"` or `LIKE "%:0"`, finds nothing, and the
  line gets `so_line_unresolved` (`:849`). On a one-month pull (job acee633a) 6,484 of 6,487 rows
  carried warnings, burying the real ones.
- The same 0 is stored in `order_lines.from_dtl_key` and keeps the line in the waiting-link fill
  (`:1084`), which re-runs the same dead lookup every batch.
- The GRN path has the same guard (`:927`): a 0 there skips the `OurPONo` document link
  (`:932`) and warns `po_line_unresolved` instead.
- No line-level fallback exists today. The header links to the SO by `RefDocNo` (`:813`), and
  `test_do_ref_doc_no_links_the_sales_order_only` pins that `RefDocNo` alone links no line.

## Change

1. **0 is no key.** `_parse` stores `FromDocDtlKey` as None when it is missing, 0 or negative
   (AutoCount DtlKeys are positive identities). It applies to DO and GRN lines alike, so a GRN
   line with 0 now takes the existing `OurPONo` path instead of a dead exact lookup.
2. **Natural-key fallback for DO lines.** A DO line with no usable `FromDocDtlKey`, a `FromDocNo`,
   and `FromDocType` absent or `SO` links to the SO line in the anchor company inside the SO
   numbered `FromDocNo` with the same product. Exactly one match fills `sales_order_line_id`.
   None or several: null plus `so_line_unresolved` (several = a product on two lines of one SO;
   no guess). An exact `FromDocDtlKey`, when sent, still wins and is not second-guessed.
3. **Waiting-link fill** uses the same resolver, so a DO that landed before its SO links on a
   later batch either way. Rows stored with `from_dtl_key = 0` by the old code are treated as
   no key, so they heal on the next batch without a re-push.
4. A line with no `FromDocNo` and no key stays unlinked with no warning (unchanged).

## Not done, and why

- **Line order tie-break** (FromDocNo + ItemCode + position) for an SO carrying the same product
  on several lines. It would need `sales_order_lines.line_no`, which is NULL on SO lines today
  (issue #1400). This fallback does **not** depend on #1400. Trigger to build the tie-break: #1400
  lands and the ambiguous count on a real pull is material.
- No backfill migration for stored `from_dtl_key = 0`: the waiting fill heals them (point 3).

## Tests

`sorento_crm_backend/tests/test_ingest_autocount_do_grn.py`, red first:
AC-DSL001..AC-DSL006 in the UAC file, plus a company B scope pin and a GRN stored-0 heal test.
Reviewer kill tests: ignore-product, link-first-of-several, drop DO waiting-fill heal and drop GRN
heal all go red. Dropping the explicit `company_id` filter in `_do_so_line` stays green because the
ingest already runs under `company_scope` (ORM-level filter); the scope test pins the outcome.

## Rollout note

The first pull after deploy reports every DO the old code stored with `from_dtl_key = 0` as
`updated` once (0 becomes NULL on the line). Expected, one time only.
