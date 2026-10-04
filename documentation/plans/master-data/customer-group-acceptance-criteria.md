# UAC: Customer groups

Plan: `PLAN-customer-group-2oct.md`. Mock: `documentation/mockups/customer-group/index.html`.
Examples use the shared dev DB (`sorento_cagent_stack`, 6,358 customers, Sorento + Mocha).

## Journey

1. A sales admin opens Order Management > Customer Groups from the sidebar. The list already
   holds the seeded groups (Sorento 527, Mocha 269), each with its ledger count and the numbered
   accounts it holds.
2. They open HANLIM TRADING SDN BHD and see its 6 ledgers (code, name, account level, status).
3. They open JUBIN BMS SDN BHD, which the name rule merged with two other legal entities, remove
   JUBIN BMS (NS) and (KLANG) (each a 5 s countdown with Cancel, no confirm), and create a group
   for KEDAI PAPAN HENG CHOON (UTARA) SDN BHD, then add its ledgers.
4. On a customer's own edit form they set or clear its Group; the customer detail shows the
   group and its sibling ledgers; the Customers list shows and filters by Group.
5. Next chatbot message: every place that groups ledgers into one company uses the group. A
   ledger joins a company line ONLY through its explicit group; a ledger with no group is its own
   customer under its own name (owner ruling (b), 2 Oct 2026: "we shouldn't do automated process
   like this, very dangerous"). Groups are set by hand only (3 Oct 2026: explicit links only,
   see `PLAN-customer-group-seed-review-3oct.md`).

## Phase 2 - backend

Route paths below are under `/api/v1/order-management`.

- AC-1 [BE] Migration creates `customer_groups` (id, company_id, name, timestamps) with a
  per-company case-insensitive unique name, and nullable `customers.customer_group_id`
  (FK, ON DELETE SET NULL, indexed). Additive and re-runnable.
- AC-2 [BE] SUPERSEDED 3 Oct 2026 (owner ruling: explicit links only; the migration is DDL only,
  see `PLAN-customer-group-seed-review-3oct.md`). Was: the migration seed creates one group per (company, `ledger_family_key`) family that
  has 2+ rows and at least one row with `account_level` set, and points every family row at it:
  796 groups / 2,804 rows on dev (Sorento 527). Group name = `ledger_family_label` of the member
  with the lowest account level (tie: lowest code). No CASH, SHOPEE or LAZADA family is seeded.
  Rows that already have a group are not touched (re-run is a no-op).
- AC-3 [BE] `GET /customer-groups` lists the caller's company groups with `ledger_count` and
  `account_levels` (sorted distinct), paged, searchable by name, sortable by name / ledger_count.
- AC-4 [BE] `POST /customer-groups` creates; a duplicate name in the same company (any case)
  is 409 "A group with this name already exists"; blank name is 422. `PATCH /customer-groups/{id}`
  renames under the same rules. `GET /customer-groups/{id}` returns name, ledger_count,
  account_levels.
- AC-5 [BE] `GET /customer-groups/{id}/customers` lists member ledgers (code, name,
  account_level, is_active), paged, searchable. `POST /customer-groups/{id}/customers`
  `{customer_ids}` moves the ledgers into the group (from another group too), all or nothing:
  an unknown or out-of-scope id is 404, a customer of another company is 422; nothing written.
- AC-6 [BE] Pending action `customer.remove_from_group` (5 s reversible) clears the customer's
  group only while it is still that group (409 otherwise). Pending action `customer_group.delete`
  (10 s) hard-deletes the group; its ledgers keep existing with no group.
- AC-7 [BE] `CustomerResponse` carries `customer_group_id` and `customer_group_name`; create /
  update accept `customer_group_id` (null clears; another company's group is 422);
  `GET /customers?customer_group_id=` filters. The change appears in the customer's history.
- AC-8 [BE] Permissions: reads need `order_management.customers.view`; create, rename, add,
  remove need `order_management.customers.edit`; delete needs `order_management.customers.delete`
  (403 otherwise). A group of another company is 404.
- AC-9 [BE] `GET /customer-groups/select?query=` returns `{id, name, ledger_count}` for the
  form's select, searched on the server.

## Phase 2 - chatbot

- AC-10 [BE] With groups loaded for the turn, `ledger_family_key(name)` / `ledger_family_label(name)`
  return the group's key / name for a customer name whose rows all sit in one group; for any
  other name they return exactly today's name rule. A name whose rows sit in two different
  groups falls back to the name rule.
- AC-10b [BE] Owner ruling (b): an ungrouped ledger is never joined to a group by its name
  (`UNGROUPED_JOINS_NAME_MATCHED_GROUP = False`); every "Customer:" line prints group names
  only (no "(N accounts)", no "and N more"), an ungrouped ledger under its own full name.
- AC-11 [BE] HANLIM TRADING SDN BHD (6 ledgers, in a group of the same name once the office sets it by hand): the which-customer
  roster, header and account refusal read exactly as before this lane (golden: today's output).
- AC-12 [BE] After CHIN CHUN HOMEMART SDN BHD ledgers are put in the CHIN CHUN HARDWARE SDN BHD
  group, a roster of both families shows ONE line "CHIN CHUN HARDWARE SDN BHD" whose pick
  carries every member uuid.
- AC-13 [BE] After JUBIN BMS (NS) SDN BHD [A/C I] is removed from the JUBIN BMS group (and has
  no group), it is its own roster line, separate from the JUBIN BMS SDN BHD line.
- AC-14 [BE] `gate._cust_base`, `session_state` family uuids, `compose` header dedupe,
  `resolve_gate` exact-name / no-such-account and `stock_ask_service` contact labels all follow
  AC-10 (one test per call site).
- AC-15 [BE] Renaming or regrouping changes the next turn's answer (no cache across turns).

## Phase 1/2 - frontend

- AC-16 [FE] Sidebar "Customer Groups" under Order Management, after Customers, shown with
  `order_management.customers.view`; list page as mock section 1 (DataGrid fixed layout,
  resizable, explicit sizes, truncate + title), empty state heading + hint, no button.
- AC-17 [FE] "Add group" modal (mock 2): name required, 409 shown inline; success opens the group.
- AC-18 [FE] Group detail (mock 3): header card with name, ledger count, accounts, prev/next,
  Edit (rename in place, mock 4), Delete (deferred countdown, no confirm); Ledgers line tab
  with search, paging, Add ledgers multi-picker and per-row deferred Remove (mock 5); empty state.
- AC-19 [FE] Customer detail Details tab shows Group (link, or "No group") and a Group ledgers
  card listing siblings with this ledger marked; no group -> "Not in a group" + hint (mock 6).
- AC-20 [FE] Customer form Group select: SearchableSelect, clearable, server-searched (mock 7).
- AC-21 [FE] Customers list: Group column + clearable Group filter (mock 8).
- AC-22 [UX] All of the above usable and unclipped at 375 and 1280. No new motion.
