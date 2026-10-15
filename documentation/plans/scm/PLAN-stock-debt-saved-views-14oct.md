# PLAN: saved views (preset filters) on Stock Debt

Status: round 3 built and reviewed (Location = real warehouses; reviewer READY + security PASS at d76c086c4, kill 14/14, browser PASS 1280/375 on the crew copy), waiting on the owner hand test (PR #1683). Track: M fix round (no migration, no RBAC change).

Owner ask (14 Oct 21:10): save the Stock Debt filters (date range, project, project location)
as a named view, like the sponsorship form report's saved views, and apply them in one pick.
Reuse the existing saved-view feature; do not build a second one.

## Discovery (origin/main 4577b9cb)

### 1. The sponsorship report's saved views

- Table `report_views`, model `sorento_crm_backend/app/models/report_view.py:21-48`: keyed by
  `report_key` + `owner_user_id`, unique name per owner per report (`:41`), at most one
  `is_default` per report (partial unique index `:42-47`). Not company-scoped.
- Routes `sorento_crm_backend/app/api/v1/reports/reports.py:329-417`
  (`/reports/{key}/views`, publish, set-default, delete). Every route runs `_authorised`
  (`:68-83`), which 404s any key not in the report registry (`:55-59`), and save validates the
  view against the registered report definition (`engine.validate_view`, `:372`).
- Config shape `sorento_crm_backend/app/schemas/report.py:183-186`: `params` + `detail` +
  a REQUIRED `pivot`. Service `app/services/reports/views_service.py`.
- Frontend `components/reports/ReportViewsMenu.tsx`.
- So Stock Debt cannot use `report_views` as is: it is not a registered report (no
  definition, no pivot). Making it one would mean rebuilding the page on the report engine.

The same feature already exists generalised for any page: `saved_views`.

- Model `sorento_crm_backend/app/models/saved_view.py:36-69`, "generalised from
  `report_views`" (`:3`). Keyed by `listing_key` (`:43`) + owner, unique name per owner
  (`:57`), one default per key (`:63-68`), company-scoped (`CompanyScopedMixin`, `:36`).
- Routes `sorento_crm_backend/app/api/v1/list_query.py:352-440`. Gate is
  `_can_view_listing_key(..., fail_closed=True)` (`:74-105`): the key is
  `<permission slug>::<id>`, and the caller needs that permission. Publish and set-default
  need `list_query.saved_views.publish` (`app/services/saved_views_service.py:39`).
  Delete runs through the deferred-action countdown (`saved_view.delete`, `list_query.py:357`).
- Config `sorento_crm_backend/app/schemas/saved_view.py:48-59`: `filters`, `sort`,
  `columns`, `column_order`, and `quick_filters: Dict[str, str]` (opaque, frontend-owned).
- Frontend `components/list/SavedViewsMenu.tsx:63-73`: inputs are `listingKey`,
  `currentViewId`, `currentConfig`, `onApply`. Personal default (pin) per user, published
  default for everyone, delete countdown (`:48-61`). Used today by
  `app/(protected)/scm/reorder/components/PlanLinesGrid.tsx:77`.
- A new page key needs NO migration: `projects.stock_debt.view::stock-debt` passes the gate
  because `projects.stock_debt.view` is a real permission (the page itself requires it,
  `app/(protected)/project-sales/stock-debt/page.tsx:15`).
- Neither menu has Rename. To change a view: save under the same name is a 409, so save a
  new one and delete the old.

### 2. Stock Debt filters today

All React state in `StockDebtClient.tsx`, no URL params (`:187-209`):
search (`:187-192`), book all/project/retail (`:193`), suppliers multi (`:195`),
sales order delivery date range `dateFrom`/`dateTo` (`:199-200`), only in debt (`:203`),
projected balance (`:206`), show aux columns (localStorage display pref, `:209-223`).

The owner's "choose project" is the Book filter set to Project (`StockDebtClient.tsx:702`,
values all|project|retail; crew correction, 14 Oct). There is no project LOCATION filter on
the screen. The saved view stores ALL current filters: search, only in debt, book, suppliers,
the sales order delivery date range, projected, and Location if Q1 brings it back.
- Backend `GET /projects/stock-debt` takes `group` (ownership group suffix: BB, IB, ...)
  (`app/api/v1/projects/stock_debt.py:49-56`) and returns the `groups` facet
  (`app/services/scm/stock_debt_service.py:498, 906-917`), but the Ownership group control
  was removed from the screen in the owner's hand test R16 (`StockDebtClient.tsx:55-57`).
- There is no project parameter at all (`stock_debt.py:44-104`).

### 3. Date range: fixed or relative

Report views store FIXED periods only: `year`, `month_range` or `custom` from/to dates
(`app/services/reports/engine.py:160-227`). The one guard is that the shared default view
drops its period on open (`views_service.py:122-145`); a picked view keeps its dates. The
`DateRangePicker` has no relative presets. So a saved "next 30 days" needs a choice.

### 4. ED locations (owner, 21:12)

Stock Debt reads only bins with `warehouses.fulfilment_planning = true`
(`stock_debt_service.py:892-904`, `fulfilment_planning_predicate`), and the `groups` facet
lists only groups those bins carry (`:906-917`). Migration 443 set that flag only for codes
matching `-(BB|IB|IR|NTC|AM)$` (`alembic/versions/443_fulfilment_planning_flag_tba_date.py:51`).
Unless an ED bin has been switched on since, ED stock and demand are outside Stock Debt
entirely, not merely missing from a filter. Keeping ED separate is another lane.

## Behaviour card and answers

Posted as `crew-ask` on PR #1683. Owner, 14 Oct 23:10:
- Q1 YES: bring back a Location filter, GENERIC (every group present in the data, never a
  hard-coded list), multi-select; the view stores whichever locations are picked.
- Q2 YES: rolling date range, recomputed when the view is applied; the chip shows real dates.

Location listing check (owner asked): a location is listed only when its bins are active and
flagged `fulfilment_planning` (`stock_debt_service.py:906-920`, `planning_predicate.py`).
The suffix parse is already generic (`sales_agent_service.py:346-360`). An admin switches a
location on at Inventory Management > Warehouses > edit > "Fulfilment planning"
(`WarehouseForm.tsx:251-263`); that also puts the bin into fulfilment planning everywhere.

## Build

1. Backend: `group` repeatable on list, cell and export (`stock_debt.py`, `StockDebtExportIn`,
   `StockDebtService._warehouses/_project_bins`). One value behaves as today.
2. Frontend: Location multi-select in the Filters panel, threaded to list, cell and export.
3. Frontend: `SavedViewsMenu` in the toolbar, config via a pure helper
   (`stock-debt/lib/stockDebtSavedView.ts`) that turns dates into day offsets and back.

UAC: `stock-debt-saved-views-acceptance-criteria.md`.

## ED views (owner, 14 Oct 23:16)

"Project view" (every location except ED) and "ED only view" (ED alone), both saved views.
`group` already scopes the calculation: `_warehouses` builds the bin span
(`stock_debt_service.py:859-890`) and `_assignments` reads supply and demand for that span
only (`:1085`, `:1098-1108`); proven by `test_the_group_filter_recomputes_rather_than_filters`.
Save both with Book = Project (under Book = All the site pools stay in the span, `:887-890`).
A saved include-list does not pick up a location added later; no exclude mode unless asked.
How ED gets in: crew-ask on PR #1683, recommendation is the admin flag (no code).

## Dev data (crew, 15 Oct, shared dev DB, read-only)

Location facet on dev: ACT, AM, BB, IB, IR, NTC; no `-ED` warehouse exists on dev, so IB stands in
for ED in the hand test. B2155-NL-BLUE (Book All, no range, Only in debt), Oct / Nov / Dec / TBA /
Total: whole book -681 / -5,622 / -5,813 / -23,668 / -68,154; Only IB -940 / -1,084 / -833 /
-3,576 / -10,115; All except IB 355 / -4,538 / -4,980 / -20,092 / -57,943; Only IR -335 / -1,236 /
-916 / -912 / -5,691; All except IR -250 / -4,386 / -4,897 / -22,756 / -62,367. Only X plus All
except X is not the whole book (HQ reserve left out under Only; pools sealed).

## Round 3: the real warehouse (owner hand test, 15 Oct)

Owner: "the location use the true location, don't do grouping like that". Crew answer to the
behaviour card: (a), every warehouse in the current Book's span, site pools and HQ included.

- Backend: `location` / `exclude_location` (repeatable warehouse ids) on list, cell and export.
  `_warehouses(group, book)` keeps main's rule and is then intersected with `location` and
  minus `exclude_location`, so a pick only ever narrows the span and the recompute is the same
  walk over fewer warehouses. HQ reserve and site pools need no rule of their own. `group`,
  `exclude_group` and the multi-group `_group_set` go back to main (not on screen).
- Envelope gains `locations` = `[{id, code, name}]` over the Book's span before the pick.
- Frontend: the Location select lists those (id value, code label, name in brackets when it
  differs); state, query keys, cell drill, export and the saved-view keys move from
  `groups` / `groups_mode` to `locations` / `locations_mode`.
- Dev data (read-only, sorento_cagent_stack): 26 flagged bins (BRW-, DC1-, MWH-, RSW-, WH3- x
  AM/BB/IB/IR/NTC/ACT, plus DC1) and the site pools (BRW, MWH, RSW, WH3, DC1, HQ, DEFECT, ...).
  B2155-NL-BLUE on hand: BRW-IB 1,360, MWH-IB 3,165, WH3-IB 3,381, BRW 97.
