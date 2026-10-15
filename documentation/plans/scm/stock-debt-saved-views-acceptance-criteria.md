# UAC: saved views (preset filters) on Stock Debt

Plan: `PLAN-stock-debt-saved-views-14oct.md`. Owner answers 14 Oct 23:10: Q1 yes (generic
Location filter), Q2 yes (rolling date range).

## Backend: Location is multi-valued

- AC-B1 `GET /api/v1/project-sales/stock-debt` takes `group` REPEATABLE
  (`?group=BB&group=IB`). The read spans the flagged bins of ANY listed group, and the balance
  is recomputed over that span (same rule as today's single `group`, `_project_bins`).
  One value keeps today's behaviour exactly. Matching is case-insensitive.
- AC-B2 Same on `GET /stock-debt/{product_id}/cell`: the drill foots with the board under the
  same groups.
- AC-B3 `POST /stock-debt/export` body field `group` accepts a list of strings; a bare string
  is still accepted (old callers). The workbook spans the same bins as the board.
- AC-B4 The `groups` facet stays generic: every group suffix (`group_of_warehouse_code`) of an
  active bin with `fulfilment_planning = true`, sorted, no hard-coded list. A new suffix
  (e.g. `-FG`) on a flagged bin appears with no code change; an unflagged `-ED` bin does not.
- AC-B5 `book=retail` still ignores `group` (single or many).
- AC-B6 No migration.

## Frontend: Location filter

- AC-F1 The Filters panel has a clearable "Location" `SearchableMultiSelect` after Supplier; options are
  exactly the envelope's `groups` (label = the code, e.g. `ED`). Empty = every location.
- AC-F2 Picked locations travel to the list (`group` repeated), the cell drill and the export,
  and are part of their query keys.
- AC-F3 Chip: one picked reads `Location: ED`; two or more read `Locations: BB, ED`. Clearing
  the chip clears the pick. Counts as one active filter.
- AC-F4 Under Book = Retail the Location select is disabled and neither its chip nor its count
  shows (the backend ignores it there).
- AC-F5 Changing Location resets to page 1.

## Frontend: saved views

- AC-V1 The toolbar shows the existing `SavedViewsMenu` (left actions) with listing key
  `projects.stock_debt.view::stock-debt`. No new saved-view component, table or endpoint.
- AC-V2 Saving stores in `quick_filters`: `query`, `book`, `suppliers` (comma-joined),
  `groups` (comma-joined), `only_debt`, `projected` ("true"/"false"), and the delivery date
  range as `date_from_days` / `date_to_days`: whole days from TODAY (local date) at save time,
  '' when that end is open. No fixed date is stored. `filters` null, `sort`/`columns`/
  `column_order` empty. "Show stock and PO columns" is a display preference, not saved.
- AC-V3 Applying a view sets every one of those filters; the date range is recomputed from
  today: saved on 14 Oct as 14 Oct to 13 Nov (`0` / `30`), applied on 20 Oct it reads 20 Oct
  to 19 Nov, and the chip shows those real dates. A missing key falls back to the screen
  default.
- AC-V4 "No view" (`onApply(null)`) resets every saved filter to the screen default: search
  '', Book All, no supplier, no location, no dates, Only in debt on, Projected off.
- AC-V5 The menu's own behaviour is unchanged: personal by default, Share / Set as default for
  everyone need `list_query.saved_views.publish`, personal default (pin), delete countdown,
  default view auto-applies on open (with its dates recomputed).
- AC-V6 Usable at 375px and 1280px.

## Round 2 (crew audit, 14 Oct, on 498f7dbd)

### Exclude mode ("all except ED")
- AC-X1 Backend: `exclude_group` (repeatable) on list and cell, `exclude_group` (list, bare
  string accepted) in the export body. The span is the flagged bins whose group is NOT listed;
  a location flagged later is in it with no change. Case-insensitive. HQ project reserves stay
  in the span under exclude mode (they are no group's stock, and "all except X" removes only
  X); `group` (include) still drops them, as today. `book=retail` ignores both.
- AC-X2 Frontend: beside the Location select, a mode switch `Only` / `All except` (default
  `Only`). In `All except` the picked locations go out as `exclude_group` on the list, the cell
  drill and the export, and are part of their query keys.
- AC-X3 Chip in `All except`: one picked reads `Location: all except ED`, two or more
  `Locations: all except BB, ED`. Clearing the chip clears the pick and returns to `Only`.
- AC-X4 Saved views store `groups_mode` (`include` | `exclude`) beside `groups`; a view saved
  before this round (no key) applies as `include`. "No segment" resets to `include`.

### BRW columns
- AC-L1 The three BRW columns are a fixed site-pool (retail) reference, not scoped by Location
  or Book: their headers read `BRW Retail On Hand Qty`, `BRW Retail PO Qty`,
  `BRW Retail Incoming Qty`, on screen and in the workbook. `Dealer O/standing Qty` unchanged.

## Round 3 (owner hand test, 15 Oct): Location is the real warehouse

Owner: "the location use the true location, don't do grouping like that". Crew answer to the
behaviour card (15 Oct): option (a). This round SUPERSEDES the group-based Location of AC-B1..B5,
AC-F1..F4 and AC-X1..X4: the board no longer sends `group` or `exclude_group`. `group` and the
`groups` facet go back to exactly what origin/main has (single string, not on screen);
`exclude_group` is removed.

### Backend
- AC-R1 `GET /stock-debt` and `GET /stock-debt/{product_id}/cell` take `location` and
  `exclude_location`, each a REPEATABLE warehouse id. The span is the current Book's span
  (main's `_warehouses(group, book)`), then, when `location` is given, only those of its
  warehouses whose id is listed, then minus every id in `exclude_location`. The balance is
  recomputed over that span (supply and demand of those warehouses only), not a row filter.
- AC-R2 A location never widens the read: an id outside the Book's span (another Book, an
  unflagged or inactive bin, another company's warehouse, a made-up value) matches nothing.
  `location` listing only such ids gives an EMPTY span (no rows), never the whole book.
- AC-R3 No special cases: a site pool (e.g. `BRW`, `MWH`) and an HQ project reserve are
  locations like any other. Under `Only` each is in only when picked; under `All except` each is
  in unless picked. `book=retail` honours both params over the pools.
- AC-R4 `POST /stock-debt/export` body takes `location` and `exclude_location` (lists; a bare
  string is accepted as one id; null is empty). The workbook spans the same warehouses as the
  board.
- AC-R5 The list envelope carries a `locations` facet: `[{id, code, name}]`, one per warehouse
  in the current Book's span (after `group`, before any `location` / `exclude_location`
  narrowing, so the list does not shrink as the reader picks), sorted by code. Generic: a newly
  flagged bin (e.g. `PRJ-ED`) appears with no code change.
- AC-R6 No migration. The three BRW Retail columns stay a fixed retail-pool reference (AC-L1).

### Frontend
- AC-R7 The Location `SearchableMultiSelect` (after Supplier, clearable, with its built-in
  Select all) lists the `locations` facet: value = the id, label = the code, or
  `CODE (Name)` when the name is non-empty and differs from the code ignoring case (e.g.
  `HQ (HEADQUARTER)`, but `BRW-IB`). Enabled under every Book.
- AC-R8 The `Only` / `All except` switch stays. Under `Only` the picks go out as repeated
  `location`, under `All except` as repeated `exclude_location`, on the list, the cell drill and
  the export, and both the picks and the mode are in their query keys. Changing either resets
  to page 1.
- AC-R9 Chip: `Location: BRW-IB`, `Locations: BRW-IB, MWH-IB`, `Location: all except BRW-IB`,
  `Locations: all except BRW-IB, MWH-IB`, using the same labels as the select. A picked id not in
  the current facet reads `Unknown location` (no id is ever shown). Clearing the chip clears the
  picks and returns to `Only`. Counts as one active filter under every Book.
- AC-R10 Saved views store `locations` (comma-joined ids) and `locations_mode`
  (`include` | `exclude`) in place of `groups` / `groups_mode`. A view with no `locations` key
  (including any view saved with the old `groups` key, which only ever existed on dev copies)
  applies as Every location with `Only`. "No segment" resets to Every location, `Only`.
