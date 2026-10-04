# NS-FETCH-STATES browser evidence (1 Oct 2026)

Branch claude/ns-fetch-states-levers-pknhh0, FE `next dev` :3000, BE :8000 on private DB sorento_browse.
Driven with agent-browser 0.27.0, session `ns`. Every screen reached from the sidebar starting at `/`.

Setup note: the DB had no module installed, so the sidebar showed only "Dashboards" for both users
(`/system/modules/me` returned every module installed=false). I installed base, product, dealer_kit,
projects, sales via `POST /api/v1/system/modules/install` as ns.admin. Data only, no source edited.

| Shot | Width | User | What it shows | Result |
| --- | --- | --- | --- | --- |
| s1-pricetags-1280-restricted.png | 1280 | restricted | Dealer Kit > Room Designer > Price Tag Requests (`/dealer-kit/price-tag-requests`), API 403 | PASS |
| s1-pricetags-375-restricted.png | 375 | restricted | Same, mobile. scrollWidth 375 | PASS (see note A) |
| s2-users-role-filter-1280-restricted.png | 1280 | restricted | Users & Access > People > Administrative Users (`/user-management/users`), Filters > Role condition, value menu open | PARTIAL (note B) |
| s2-users-role-filter-375-restricted.png | 375 | restricted | Same, mobile. scrollWidth 375 | PARTIAL (note B) |
| s3-register-project-1280-restricted.png | 1280 | restricted | Project Sales > Pipeline > Start > Register a project (`/project-sales/new`), Project type menu open | PASS |
| s3-register-project-developer-menu-1280-restricted.png | 1280 | restricted | Developer menu open | PASS |
| s3-register-project-375-restricted.png | 375 | restricted | Form at 375. scrollWidth 360 (scrollbar), no overflow | PASS |
| s4-users-before-1280-admin.png | 1280 | admin | Users list with rows, backend up | PASS |
| s4-users-error-1280-admin.png | 1280 | admin | Backend killed, search "zzq" typed: error state | PASS (note C) |
| s4-users-error-375-admin.png | 375 | admin | Same, mobile. scrollWidth 375 | PASS |
| s4-users-recovered-1280-admin.png | 1280 | admin | Backend restarted, Retry clicked, search cleared, rows back | PASS |

## Exact text read

1. Price Tag Requests (restricted): grid body text "You don't have access to this list" + "Your role does not
   include this. Ask an administrator if you need it." Footer "1 - 0 of 0". Checks: "Permission required"
   absent, no Retry button, "No data" absent. Network: `GET /api/v1/dealer-kit/price-tag-requests?... 403` (x2). No toast.
2. Users (restricted): list loaded (2 rows). Role filter value trigger reads **"All roles"** (not "No access").
   Opened menu text: "You don't have access to this list." followed by the option "All roles". Network:
   `GET /api/v1/user-management/roles/select 403` (x2 over two page visits, one per filter open). Toasts:
   MutationObserver on the toast region plus polling saw none (0, not 1), so no raw slug text.
3. Register a project (restricted): page rendered, no skeleton (0 pulse nodes). `combobox "Developer": No access`,
   `combobox "Project type": No access`. Both menus read exactly "You don't have access to this list." No toast.
4. Admin Users list: backend killed (pid 7027), "zzq" typed into search at t=0. By the first poll (~6s wall,
   2s poll interval plus command overhead; connection refused is immediate) state was final: text
   "Internal Server Error" + "Retry" button, "No data" absent, 0 `animate-spin` nodes, no toast. Backend restarted
   with the exact brief command, `/docs` 200. Retry click: refetch returned 200, with query=zzq the grid then
   legitimately says "No data available"; Clear search refetched and "NS Admin" row is back, no Retry.

## Notes and defects

A. 375 Price Tag Requests: the access-denied message sits inside the grid's own horizontal scroller and the
   hint is cut off ("Ask an admi..."; the first line is fully visible). Page itself does not overflow. Minor
   clipping, same pattern as any grid empty row at 375.
B. Brief expected the Role value trigger to read "No access". It reads "All roles" because the draft condition
   carries value `all`, which matches the injected "All roles" option, so the failure placeholder
   (`selectLoadFailurePlaceholder`, `components/common/SelectLoadFailure.tsx`) never shows. The menu message is
   correct. Decide whether the trigger should show "No access" when `loadError` is set; today it cannot unless
   the value is empty. The Project type / Developer pickers (empty value) do read "No access".
C. Admin error text is the generic "Internal Server Error": the Next proxy turns a refused backend
   connection into a 500. Honest, but not a friendlier "could not reach server" message.
D. Out of lane: seeded users have non-UUID ids ("ns-restr-0001"), so `GET /resource-management/upload-activity?scope=me`
   returns 500 (DataError invalid uuid) and shows a toast "Failed to fetch upload activity (500)" on every
   dashboard load for the restricted user. Fixture issue, not a lane defect.
E. Out of lane: Users filter popover loses its draft conditions on a route remount (re-adding needed at 375).
   Also `fill @search ""` does not trigger a refetch; only the "Clear search" button does.
F. Product page admin sidebar: no Master Data > Brands entry visible in this DB (only Products group), so the
   Users list was used for screen 4.
