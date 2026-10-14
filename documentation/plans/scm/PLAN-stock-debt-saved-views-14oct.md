# PLAN: saved views (preset filters) on Stock Debt

Status: Phase 1 discovery done, behaviour card posted to the owner (PR #1683). Track: small fix
(no migration, no RBAC change, FE-only diff expected). Phase 2 waits on the owner's answers.

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

There is NO project filter and NO project location filter on the screen.
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

## Behaviour card

Posted as `crew-ask` on PR #1683. Answers recorded here once they arrive.
