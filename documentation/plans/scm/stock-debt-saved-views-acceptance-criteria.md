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

- AC-F1 The Filters panel has a "Location" `SearchableMultiSelect` under Book; options are
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
