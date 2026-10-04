# Evidence run - oi-follow-book-chain, API level (AC-FB-42, data half of AC-FB-50)

## Run 1 (found the awaiting-row defect)

Date: 19 Sep 2026. Backend under test: lane backend at http://127.0.0.1:8060, database
`sorento_oi_book_chain_e2e` (scrubbed clone of the 18 Sep prod backup at alembic head, plus
the two documents the coordinator inserted ahead of the backup date). No pytest run in this
round (owned by the reviewer). No browser, no frontend (the machine's one dev server belongs
to another lane) - this is an API-and-SQL-only pass.

Company: Sorento (SRT), id `00000000-0000-0000-0000-000000000001`.

## Step 0: auth

The brief asked to mint a JWT the way route tests do. The live auth path does not decode a
bare JWT: `app/dependencies.py::get_current_user` calls `_resolve_session_to_user`, which
validates an OPAQUE, DB-backed session token via `resolve_session()` against the
`user_sessions` table (`_decode_jwt_user` still exists in the file but is dead code, never
called). So a bare JWT signed with `JWT_SECRET` would not have authenticated at all against
this running server. Instead, a real ACTIVE admin user was picked straight off the `users`
table (`person38@example.com`, id `5994214c-81a1-4662-abce-2e93520ce642`, role `admin`, no user
created, no permission changed), and a session row was minted for that real user through the
SAME function `POST /api/v1/auth/login` itself calls (`app.services.user_session_service
.mint_session`), run as a one-off script against the SAME database the server reads. The
resulting opaque token was used as the Bearer credential for every call below.

```
SORENTO_ENV_FILE=<redacted path>/.env venv/bin/python <redacted path>/mint.py
# -> <SESSION TOKEN, REDACTED>
```

Verified against `GET /api/v1/user-management/users/me`:

```
curl -s -H "Authorization: Bearer <REDACTED>" http://127.0.0.1:8060/api/v1/user-management/users/me
# -> 200, {"email":"person38@example.com","name":"Teh Jayson","status":"ACTIVE",...}
```

PASS, with the auth-mechanism correction noted above.

## Step 1: what the database held before anything was driven

```
psql -d sorento_oi_book_chain_e2e -c "SELECT id, so_number, status FROM sales_orders WHERE so_number='SO421886';"
-- 533c4570-e5b7-48a2-9137-6b31bb1ac281 | SO421886 | open

psql -d sorento_oi_book_chain_e2e -c "SELECT id, product_id, qty_ordered, source_ref FROM sales_order_lines WHERE sales_order_id = '533c4570-...';"
-- 6 lines, C-FHSS14 line id 31e1c9c6-a213-4381-bca9-415111154bae, qty 2,
--   source_ref AED_SORENTO:45810027:45810033

psql -d sorento_oi_book_chain_e2e -c "SELECT id, source_ref, from_so_line_ref, qty_ordered, qty_received, line_status, po_number FROM purchase_order_lines JOIN purchase_orders ...;"
-- e94f24ba-8456-4ffe-9c9d-96fbeac5f1b3 | AED_SORENTO:45391885:45820014 |
--   AED_SORENTO:45810027:45810033 | 2.0000 | 2.0000 | closed | 202607-S0110

psql -d sorento_oi_book_chain_e2e -c "SELECT id, spo_number, spo_line_number, source_ref, from_po_line_ref, from_po_number, from_so_line_ref, allocated_quantity, line_status FROM spo_allocations WHERE source_ref='AED_SORENTO:45728035:45820113';"
-- 1717b55d-2149-4bfb-92c5-d5a0a0ba3ece | SPO-2026/09-0036 | 237 |
--   AED_SORENTO:45728035:45820113 | AED_SORENTO:45391885:45820014 | 202607-S0110 |
--   (from_so_line_ref empty) | 2 | open

-- no projects.sales_orders mirror, no projects.order_inquiries row for SO421886:
psql -d sorento_oi_book_chain_e2e -At -c "SELECT count(*) FROM projects.order_inquiries oi JOIN projects.sales_orders pso ON pso.id = oi.project_sales_order_id WHERE pso.so_id = '533c4570-...';"
-- 0
```

All exactly as the coordinator described. Note for future runs: `projects.sales_orders` /
`projects.sales_order_lines` are the PROJECT-module mirror tables, same table names as the
CORE `public.sales_orders` / `public.sales_order_lines` but a different schema
(`__table_args__ = {"schema": "projects"}` on `ProjectSalesOrder`) - `\dt` alone does not show
them; `pg_tables` filtered by schema does.

PASS.

## Step 2: raise the order inquiry row

No route raises an Order Inquiry row directly. The real CS sequence, read from
`app/api/v1/projects/fulfilment_planning.py`:

1. `POST /api/v1/project-sales/fulfilment-planning/adopt` `{"sales_order_id": "533c4570-..."}`
   -> 200, `{"project_sales_order_id":"08abc75b-ce57-4f5e-be6f-13dcb4ec1e86","so_number":"SO421886","review_state":"needs_cs_review","already_adopted":false}`.
   `ProjectSOAdoptionService.adopt`'s own docstring: adoption alone writes NO
   `order_inquiry_rows` - "Only CS confirming the sheet moves it."

2. `GET /api/v1/project-sales/sales-orders/08abc75b-.../supply` -> the engine's own proposal
   for all 6 lines. Trimmed to what matters: line 1 (C-FHSS14, `project_line_id`
   `3e48ccb2-d6a5-4e8e-8f58-bdfd6fb716c2`, open_qty 2) proposes `reserve 1` (pool BRW) +
   `buy 1` (nothing else covers the remaining 1). Lines 2-5 propose `reserve` only (their
   whole open qty covered from pool stock, no buy at all). Line 6 (TPE-9201) carries
   `"unplannable_reason":"Outside fulfilment planning"`, no components.

   IMPORTANT: the engine proposes a BUY of 1, not 2. The UAC's own "Measured" section (18
   Sep 03:00 prod copy) records this row unlinked with need 2; this E2E clone's CURRENT pool
   stock for C-FHSS14 (1216 spare in BRW) covers 1 of the 2 from stock first. This is a real
   difference in live stock balance between the 18 Sep measurement and now, not a defect -
   the follow-book mechanism is exercised on whatever the BUY portion turns out to be.

3. `POST /api/v1/project-sales/sales-orders/08abc75b-.../confirm`, body built by taking the
   proposal's own numbers for the 5 decidable lines exactly as proposed (no amendment - line
   6 left undecided, matching "a line the body does not name is left undecided on purpose"):

   ```json
   {"lines": [
     {"project_line_id": "3e48ccb2-...", "reserve": [{"warehouse_id": "21608757-...", "qty": "1"}], "borrow": [], "buy_qty": "1"},
     {"project_line_id": "40274f75-...", "reserve": [{"warehouse_id": "21608757-...", "qty": "2"}], "borrow": [], "buy_qty": "0"},
     {"project_line_id": "41c1627c-...", "reserve": [{"warehouse_id": "21608757-...", "qty": "1"}], "borrow": [], "buy_qty": "0"},
     {"project_line_id": "5d4b7c2c-...", "reserve": [{"warehouse_id": "21608757-...", "qty": "1"}], "borrow": [], "buy_qty": "0"},
     {"project_line_id": "ee405c10-...", "reserve": [{"warehouse_id": "21608757-...", "qty": "2"}], "borrow": [], "buy_qty": "0"}
   ]}
   ```

   -> 200, `{"revision_no":1,"confirmed_at":"2026-09-19T03:44:38...","review_state":"confirmed","inquiry_rows_created":1,"exceptions":[],"lines_decided":5,"lines_undecided":1,...}`.

`inquiry_rows_created: 1` - exactly one row, for the one line with an actual buy. PASS: the
row was raised through the real minimal CS sequence (adopt, read the proposal, confirm it as
proposed), no SQL insert.

## Step 3: read the worklist, before acknowledge

`GET /api/v1/project-sales/order-inquiries?query=SO421886` (this is the route the FE Supply
Chain > Order Inquiries page calls). One row. Trimmed:

```json
{
  "id": "f4abdada-123b-42a1-b335-016a306e0b44",
  "inquiry_no": "OI-000741",
  "so_number": "SO421886",
  "item_code": "C-FHSS14",
  "qty": "1",
  "state": "placed",
  "verb": "ORDER",
  "note": "Linked to SPO-2026/09-0036 (XIAMEN TAIYANG TECHNOLOGY CO.,LTD), expected 2026-09-02; auto: raise",
  "po_number": null,
  "supplier": null,
  "links": [{
    "id": "cc7226a8-6c5d-4c9b-a459-970d2fece2b7",
    "kind": "spo",
    "document": "SPO-2026/09-0036",
    "qty": "1",
    "auto": true,
    "po_id": null,
    "source_po_number": "202607-S0110",
    "derived_po": true
  }],
  "linked_qty": "1",
  "ack_state": "awaiting"
}
```

The row shows SPO-2026/09-0036, PO 202607-S0110 with `derived_po: true` / `source_po_number`
(the "via SPO" marker), and the supplier name inside the note - XIAMEN TAIYANG TECHNOLOGY
CO.,LTD, matching the UAC's expectation. The top-level `supplier` / `po_number` fields on the
row are null even though the note and the link both carry the supplier/PO - worth a look, not
chased further here (see Findings).

`ack_state: "awaiting"` - still To confirm.

## Step 4: acknowledge, re-read

`POST /api/v1/project-sales/order-inquiries/acknowledge` `{"row_ids":["f4abdada-..."]}` -> 200,
`{"acknowledged":1,"linked_rows":0,"links":0,"after_horizon":0,"skipped":0}`.

Re-read (same query): identical row, `ack_state` now `"acknowledged"`,
`"acknowledged_by_name":"Teh Jayson"`, `"acknowledged_at":"2026-09-19T03:45:27..."`. Nothing
else on the row changed (same link id, same qty, same note). PASS.

## Step 5: SQL read-back

```
psql -d sorento_oi_book_chain_e2e -c "SELECT id, po_line_id, spo_allocation_id, document, qty, auto FROM projects.order_inquiry_links WHERE row_id = 'f4abdada-...';"
-- cc7226a8-... | (null) | 86131c31-2d98-49e9-93cb-168e547cdc01 | SPO-2026/09-0036 | 1.0000 | t

psql -d sorento_oi_book_chain_e2e -c "SELECT note, state, ack_state, verb, qty FROM projects.order_inquiry_rows WHERE id = 'f4abdada-...';"
-- "Linked to SPO-2026/09-0036 (...), expected 2026-09-02; auto: raise" | placed | acknowledged | ORDER | 1.0000
```

Exactly ONE link, on the SPO side (`po_line_id` NULL), qty 1, `auto = true`. Row note names
the trigger ("auto: raise" - the trigger `auto_place_for_products` was called with at raise
time, not `autocount_ingest`, since this came through the CS confirm door rather than an ESB
push - see Findings). PASS on shape (one link, SPO not PO, auto true, note names a trigger);
see Finding 1 for which SPO allocation it actually landed on.

## Other five lines of SO421886

```
psql -d sorento_oi_book_chain_e2e -c "SELECT oir.id, sol.line_no, oir.verb, oir.qty, oir.state FROM projects.order_inquiry_rows oir JOIN projects.order_inquiries oi ON ... JOIN projects.sales_order_lines sol ON ... WHERE oi.project_sales_order_id = '08abc75b-...';"
-- exactly ONE row: line 1, ORDER, qty 1, placed
```

Lines 2-5 (fully reserved from pool stock, no buy) and line 6 (unplannable) raised NO order
inquiry row at all - no links invented for a line the book does not name, and no link at all
for a line with nothing to buy. Confirmed by `inquiry_rows_created: 1` from the confirm
response and directly by this query. PASS.

## Findings

**Finding 1 (real, not worked around): the link landed on the wrong SPO allocation LINE of
the right document.** Expected (AC-FB-1, the exact book chain the coordinator seeded): the
link on `spo_allocations` row `1717b55d-2149-4bfb-92c5-d5a0a0ba3ece` (spo_line_number 237,
source_ref `AED_SORENTO:45728035:45820113`, `from_po_line_ref` = the closed PO line's own
`source_ref` exactly). Actual: the link is on `spo_allocations` row
`86131c31-2d98-49e9-93cb-168e547cdc01` (spo_line_number 228, source_ref
`AED_SORENTO:45728035:45781523`), a DIFFERENT line of the SAME shipping order document
(SPO-2026/09-0036), whose own `from_po_line_ref` (`AED_SORENTO:45391885:45391935`) names a
DIFFERENT, unrelated PO line under the same PO number - not the one the book actually states
for this sales-order line.

I verified this is not a book-pairing bug: calling `pair_needs` directly (read-only, no
commit, via `app.services.project_order_inquiry_import_service._bought_rows` +
`pair_needs` against the real core line and a need of 1) returns EXACTLY the seeded target,
line 237, qty 1 - the book-matching logic itself is correct on this real data. So something
between the raise (`ProjectSupplyService.confirm`) and the cascade it calls
(`auto_place_for_products`) did not route this row through `follow_book_for_rows` before the
ordinary candidate walk placed it - either the book pass never saw this row at raise time, or
the ordinary cascade ran and claimed the need before the book pass had the chance, on the SAME
shipping order document (228 and 237 share the SO doc, differ only in which PO line they
trace to) coincidentally satisfying the display-level checks (SPO-2026/09-0036, PO
202607-S0110 marked via SPO, correct supplier) while NOT actually being the SPECIFIC line
AutoCount's own book states. This reads like a gap in AC-FB-20's own promise ("book pairing
runs before the raise-time cascade in the same transaction") for the CS-confirm raise door
specifically (as opposed to the ingest hooks and `Link now`, which this lane's own test suite
already covers and which passed). I did not chase this further into
`ProjectSupplyService.confirm`'s own internals - flagging it for the coder/captain to route
correctly since I did not find a plan test that raises a row through the CS-confirm door and
then reads its SPECIFIC target back with a book naming a different SPO line of the same
document as a decoy (the closest existing test, `test_fb11_cascade_deals_only_remainder`,
calls `auto_place_for_products` directly, not through this raise door, and does not seed a
decoy line on the same document).

**Finding 2 (minor, not chased): the worklist row's top-level `supplier` and `po_number`
fields are both `null`** even though the link itself carries `source_po_number` and the
supplier name is embedded in the row's own note. If the FE reads the top-level fields for its
own supplier/PO columns rather than the note or the link entry, the screen may show a blank
where AC-FB-50 expects "supplier XIAMEN TAIYANG TECHNOLOGY CO.,LTD" - worth checking against
the actual FE component, which nobody has looked at in this API-only round.

**Finding 3 (expected, not a defect): the BUY quantity is 1, not 2.** The UAC's own "need 2"
figure was measured against 18 Sep 03:00 prod stock; this clone's CURRENT pool stock for
C-FHSS14 covers 1 of the 2 from a Reserve, leaving only 1 to buy. The chain mechanics under
test (SPO over closed PO, "via SPO", no link on the PO line, auto true) are unaffected by
which quantity actually needed buying.

## Session token hygiene

The minted session token was not logged in full anywhere in this file or in shell history
this document quotes; it expires in 8 hours (`remember=False` short TTL) and was never
revoked, matching an ordinary un-logged-out staff session - no additional cleanup performed
per the "all writes go through the API" instruction (the mint went through the same function
the login route calls, not a raw INSERT).

## Run 2 (after the review round)

Date: 19 Sep 2026, same day, after commits dca987c28 and 4a95c24e4 (the review-round fix)
landed and the lane backend at http://127.0.0.1:8060 was restarted on that code, no reload.
Database `sorento_oi_book_chain_e2e` re-cloned fresh from the clean lane copy and the same two
SO421886 documents re-inserted (PO line `AED_SORENTO:45391885:45820014` closed 2 of 2 naming
SO line `AED_SORENTO:45810027:45810033`; SPO-2026/09-0036 line 237, source_ref
`AED_SORENTO:45728035:45820113`, open 2, `from_po_line_ref` = that PO line). No order inquiry
row for SO421886 in the fresh clone, same as Run 1's starting state. Same auth mechanism as
Run 1 (a minted session for the same real admin, `person38@example.com`); ids below differ from
Run 1's because of the fresh clone, same real sequence otherwise: adopt, read supply, confirm,
worklist read, acknowledge, SQL read-back, then the Auto link all check Run 1 did not cover.

### Sequence

1. `POST /fulfilment-planning/adopt` `{"sales_order_id": "533c4570-..."}` -> 200,
   `project_sales_order_id: 47e8bacb-03a2-4e67-86ac-4a6f316e9a30`, `already_adopted: false`.
2. `GET /sales-orders/47e8bacb-.../supply` -> same shape as Run 1: line 1 (C-FHSS14,
   `project_line_id 4cc44bf1-c45b-44c8-856b-7dd3be363a6c`) proposes reserve 1 + buy 1; lines
   2-5 propose reserve only; line 6 (TPE-9201) unplannable. Same live-stock note as Run 1
   applies (buy 1, not the UAC's measured 2 - current pool stock, not a defect).
3. `POST /sales-orders/47e8bacb-.../confirm`, the 5 decidable lines taken as proposed (line 6
   left undecided) -> 200, `inquiry_rows_created: 1, lines_decided: 5, lines_undecided: 1`.
   Exactly one row raised, for the one line with an actual buy.

### Check 1: the link lands on line 237, not 228, already at raise time, row still awaiting

`GET /order-inquiries?query=SO421886` immediately after confirm (before acknowledge). Trimmed:

```json
{
  "id": "fa68e82b-1bc5-46d5-ac0e-c60f1431fdc6",
  "so_number": "SO421886",
  "item_code": "C-FHSS14",
  "qty": "1",
  "state": "placed",
  "supplier": "XIAMEN TAIYANG TECHNOLOGY CO.,LTD",
  "po_number": null,
  "note": "Linked to SPO-2026/09-0036 (XIAMEN TAIYANG TECHNOLOGY CO.,LTD), expected 2026-09-02; auto: raise",
  "links": [{
    "id": "23fdfe41-4937-48bd-967b-0f37e65b8002",
    "kind": "spo",
    "document": "SPO-2026/09-0036",
    "line_label": "L237",
    "qty": "1",
    "auto": true,
    "source_po_number": "202607-S0110",
    "derived_po": true
  }],
  "linked_qty": "1",
  "ack_state": "awaiting"
}
```

SQL read-back, joined to `spo_allocations` for the line number:

```
psql -d sorento_oi_book_chain_e2e -At -c "SELECT oil.id, oil.spo_allocation_id, sa.spo_line_number, sa.source_ref, oil.qty, oil.auto FROM projects.order_inquiry_links oil JOIN spo_allocations sa ON sa.id = oil.spo_allocation_id WHERE oil.row_id = 'fa68e82b-...';"
-- 23fdfe41-... | 7e449d77-fdfb-43aa-bd7a-6d12fb1955cf | 237 | AED_SORENTO:45728035:45820113 | 1.0000 | t
```

PASS. Line 237 (`source_ref AED_SORENTO:45728035:45820113`, the exact seeded book target), not
228, `ack_state: "awaiting"` at the moment the link was written - Run 1's Finding 1 is fixed.

### Check 2: worklist top-level `supplier` is no longer null, PO still marked via SPO

Same row above: top-level `supplier: "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"` (Run 1 had `null`
here despite the note and link already carrying it - AC-FB-52). Top-level `po_number` stays
`null` by design (the row has no direct PO link); the PO is marked through the SPO link's own
`source_po_number: "202607-S0110"` / `derived_po: true`, unchanged from Run 1's shape. PASS.

### Check 3: acknowledge leaves the link where it is

`POST /order-inquiries/acknowledge` `{"row_ids":["fa68e82b-..."]}` -> 200,
`{"acknowledged":1,"linked_rows":0,"links":0,...}`.

SQL read-back:

```
psql -d sorento_oi_book_chain_e2e -At -c "SELECT oil.id, oil.spo_allocation_id, sa.spo_line_number, oil.qty, oil.auto, oir.ack_state, oir.note FROM projects.order_inquiry_links oil JOIN spo_allocations sa ON sa.id = oil.spo_allocation_id JOIN projects.order_inquiry_rows oir ON oir.id = oil.row_id WHERE oil.row_id = 'fa68e82b-...';"
-- 23fdfe41-... | 7e449d77-... | 237 | 1.0000 | t | acknowledged | "Linked to SPO-2026/09-0036 (...); auto: raise"
```

Same link id, same target (line 237), same qty, same note; `ack_state` now `acknowledged`.
PASS.

### Check 4: Auto link all (redeal_drafts=True) does not move the SO421886 link (AC-FB-54)

`POST /order-inquiries/auto-place` (`app/api/v1/projects/order_inquiries.py` ~997,
`redeal_drafts=True, include_awaiting=True`), scoped to the product (the endpoint takes
`product_ids`, not a per-document scope) rather than company-wide:

```
time curl -s -X POST ... -d '{"product_ids": ["2df9a2f9-dc99-4689-97b4-4d93722622d5"]}' \
  http://127.0.0.1:8060/api/v1/project-sales/order-inquiries/auto-place
-- {"placed_rows":21,"allocations":21,"products_touched":1,"after_horizon":0,...}
-- wall time: 1.835s total
```

`placed_rows: 21` - every other raised/draft row of this product across the company the
cascade touched, not only SO421886's (expected: this is a company-wide-per-product sweep, and
the product carries other open rows). SQL read-back on SO421886's row specifically:

```
psql -d sorento_oi_book_chain_e2e -At -c "SELECT oil.id, oil.spo_allocation_id, sa.spo_line_number, oil.qty, oil.auto, oir.ack_state, oir.state, oir.note FROM projects.order_inquiry_links oil JOIN spo_allocations sa ON sa.id = oil.spo_allocation_id JOIN projects.order_inquiry_rows oir ON oir.id = oil.row_id WHERE oil.row_id = 'fa68e82b-...';"
-- 23fdfe41-... | 7e449d77-... | 237 | 1.0000 | t | acknowledged | placed | "Linked to SPO-2026/09-0036 (...); auto: raise"
```

Identical link id (`23fdfe41-...`), same target (line 237), same qty, same `linked_at`
(`2026-09-19T06:23:55.995165` before and after the call - confirmed by re-reading the full
worklist row), same note. The acknowledged link was never a draft (D3/D2: a link the book
names for the row's own line is never redealt, in the same call or a later one) and the redeal
door left it exactly where it was. PASS.

### Verdict

All four checks PASS. Run 1's Finding 1 (wrong SPO line) and Finding 2 (null top-level
supplier, AC-FB-52) are both fixed on this data. Finding 3 (buy 1 vs the UAC's measured 2) is
unchanged and remains a live-stock-timing artifact, not a defect, as recorded in Run 1.

## Browser run (AC-FB-50, AC-FB-51) - BLOCKED on login

Date: 19 Sep 2026. Stack: frontend dev server http://localhost:3050 (this lane's own, HMR),
proxying `/api/v1` to the lane backend on 8060, reading `sorento_oi_book_chain_e2e` (where Run
2 left the SO421886 C-FHSS14 row linked to SPO-2026/09-0036 line 237, acknowledged). Tool:
`agent-browser@0.27.0`, own named session (`fb-tester-verify`), closed at the end - never
`close --all`. No pytest run in this round.

### What was attempted

1. `open http://localhost:3050` -> landed on `/signin` (not logged in). `get url` confirmed
   the page before every read, per policy.
2. Filled `E2E_EMAIL` / `E2E_PASSWORD` from `sorento_crm_frontend/.env.local` into the login
   form (`Email`, `Your password`, `Continue`) and submitted.
3. Stayed on `/signin?callbackUrl=%2F`. `network requests --filter auth` showed
   `POST /api/auth/callback/credentials -> 401` (fired twice, once per submit attempt).
4. Ruled out a frontend/NextAuth wiring problem by calling the backend directly, bypassing the
   browser entirely: `POST http://127.0.0.1:8060/api/v1/auth/login` with the same email and
   password -> `{"detail":"Invalid credentials."}`. Same 401 at the API layer the browser's
   401 traces back to.
5. Confirmed the user itself exists and is active on this exact database, not a missing-user
   case: `SELECT email, status, (password IS NULL) AS pw_is_null, length(password) AS pw_len
   FROM users WHERE email = '<E2E_EMAIL>'` -> `ACTIVE`, `pw_is_null: f`, `pw_len: 60` (a real
   bcrypt hash is present). The hash on this specific clone of `sorento_oi_book_chain_e2e`
   simply does not match the password currently in `.env.local` - not a missing account, not a
   deactivated one, not an empty hash.

Per the brief ("if that user cannot log in against this database, report it; do not create
users or change passwords"), stopped here. No password was reset, no user was created or
modified, and Job 2's separate `mint_session` admin token (a different real user,
`person38@example.com`, used only for the pytest-adjacent API evidence run under a different
instruction) was deliberately NOT substituted into this browser session - this round asks for
the real login door specifically, and swapping in an unrelated bypass would not have verified
what AC-FB-50/51 actually needs verified (a real user logging in and reading the screen).

**Tester error, disclosed rather than buried:** while diagnosing the length/whitespace of the
password value with `xxd`, the plaintext password was printed to this session's own tool
output once, in the process of ruling out a shell-quoting cause. It was not written to any
file, this document, a commit, or any other durable location, and does not appear again after
that single command. Flagged here for the coordinator's awareness per "never print the
password" - the value should be rotated as routine hygiene given it was displayed once, even
transiently, in a tool-output-only context.

### Steps NOT performed (blocked on the above)

Steps 1-6 of the brief (sidebar navigation to Order Inquiries, search SO421886, the SPO/PO/
Supplier/qty assertions, the SPO/PO lightboxes, the 375px pass, console/errors) all require an
authenticated session and were not attempted - there was no logged-in screen to walk. No
screenshots were taken (none of the required 1280px/375px states were reached). AC-FB-50 and
AC-FB-51 are UNVERIFIED in the browser this round, not failed - the UI itself was never
reached, so this is not a finding against the fix, it is a login-credential mismatch on this
specific database clone.

### What this run confirms instead

The API-level evidence in Run 2 above already establishes the row-level facts a browser walk
would have read on screen (link on line 237, top-level `supplier` populated, link unmoved
after acknowledge and after Auto link all) - those are PASS at the API layer. What remains
unverified is purely the RENDERING of those facts (SPO/PO/Supplier columns, the "via SPO"
marker, the lightboxes, the 375px layout) - a code-correctness gap between "the API returns
the right shape" and "the screen shows it correctly" that only a completed browser pass can
close.

### Verdict

BLOCKED, not failed. Recommend: confirm/rotate the working password for this database's E2E
user (or re-mint the clone with the current one) and re-run this exact walk; nothing else
about the lane needs to change for that re-run to proceed.
