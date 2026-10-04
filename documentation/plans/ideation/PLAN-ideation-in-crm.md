# PLAN: Ideation inside the Sorento CRM portal (one system, one domain)

Status: Option C chosen (owner, 2 Oct 2026). Size L, full pipeline track. Slice M static mock
APPROVED by the owner 2 Oct ("no problem"; the ask's recommendations accepted: Promote one click,
track-page vote count read-only, Product hidden by default, Phase 1 screens kept and reworked).
Slice U done: `ideation-in-crm-acceptance-criteria.md`. Now in Phase 2 build (B1-B5 folded into one
lane round): red tests, then gateway + native pages green, then review, security review, browser
pass, hand test. Lane IDEATION-IN-CRM.

## 0. Owner decisions (2 Oct 2026)

| Q | Decision |
|---|---|
| Q1 | **Option C**: CRM-native Ideas pages over the ss embed API, CRM backend as the gateway. |
| Q2 | **Baseline AND extras**: list, detail, capture, vote, status, archive/delete, attachments, PLUS board reorder, merge/unmerge and promote-to-BR. |
| Q3 | **Scope the ss embed connection to the CRM workspace's `ideation_product_id`.** Apply on dev only; crew gets the exact prod setting and asks the owner before touching prod (section 14). |
| Q4 | **The public track page reuses the existing customer portal** (its routes and domain), not a new public path. Proposed URL in section 11. |
| Q5 | **Separate ss lane** for `public_link_base_url` (crew runs it). This lane states the contract it needs (section 13). |
| Add | **Fold in IDEATION-COMMENTS** in the CRM pages: comments (staff, and public read + post on the track page), upvote-only with the prominent vote box, primary "Move to <next state>" + secondary Edit. Design source: the approved mock `documentation/mockups/IDEATION-COMMENTS/index.html` (v2) on ss branch `crew/ideation-comments`, rebuilt with CRM components (section 9). |
| 2nd ask Q1 | Comment delete = CRM deferred countdown (D7), not the mock's confirm. (crew, 2 Oct) |
| 2nd ask Q2 | **(a), owner 2 Oct:** new CRM permission `ideation.ideas.manage` gates status move, edit, merge/unmerge, board reorder, archive, delete, promote; view, capture, vote and comment stay on `ideation.board.view`. Seeded in B1 (permission registry + admin grants). |
| 2nd ask Q3 | Promote shown to manage holders; ss's 403 message surfaced when the email has no ss BR-manage user. |
| 2nd ask Q4 | Public comment author = the idea's submitter (same as IDEATION-COMMENTS P1 a). |
| 2nd ask Q5 | Track URL `{FRONTEND_BASE_URL}/portal/ideas/{status_token}` approved. |

Repos read: `jayson-odoo/sorento-crm` (this repo, "CRM") and `jayson-odoo/foundryx-shared-service`
("ss", read-only clone at main, 2 Oct 2026). Paths below prefixed `ss:` are in the ss repo;
`FE` = `sorento_crm_frontend/`, `BE` = `sorento_crm_backend/`; `ssFE` = `service_frontend/`,
`ssBE` = `service_backend/`.

## 1. Journey and goal

Owner (2 Oct): customers and staff should use ONE system. Today ideation lives in ss and Sorento
hands out ss links.

- J1. A Sorento staff member opens Ideas from the CRM sidebar, browses, hides columns, captures an
  idea, opens one, votes or changes its status, and it looks and behaves like the rest of the CRM.
- J2. A customer who submitted an idea over WhatsApp taps "Track it here" and lands on a page on the
  Sorento CRM domain, never the ss domain.
- J3. A deep link `/ideas/<id>` pasted into chat opens that idea inside the CRM.

## 2. Requirements

| # | Requirement | Source |
|---|---|---|
| R1 | Ideation is reached inside the CRM portal (sidebar entry, CRM shell, CRM auth). | Lane brief |
| R2 | Every public or shared ideation link uses the Sorento CRM domain, never the ss domain. | Lane brief |
| R3 | Deep links `/ideas` and `/ideas/<id>` open the right view and survive refresh, new tab and ctrl-click. | Lane brief |
| R4 | Hiding columns and resizing column widths in the Ideas list is saved and survives refresh. | Owner feedback (1) |
| R5 | A staff member can capture an idea manually from inside the CRM. | Owner feedback (2) |
| R6 | Ideas follow the CRM theme (light/dark and the CRM tokens), live when the user toggles. | Owner feedback (3) |
| R7 | Ideas show dates in the CRM format (`dd/MM/yyyy`, date-time `dd/MM/yyyy, h:mm AM/PM`). | Owner feedback (4) |
| R8 | Archive and Delete work from the CRM (found while scouting R4, same root cause). | Scout |
| R9 | Board (status lanes, drag to reorder), merge / unmerge, promote-to-BR are available in the CRM. | Owner Q2 |
| R10 | Staff can read, post, reply to (one level) and edit/delete their own comments on an idea; triage users can delete any. | IDEATION-COMMENTS |
| R11 | Votes are upvote-only. A prominent vote box sits left of the title on the idea page and leads every list row and board card; clicking it votes without opening the idea; orange when the viewer has voted. | IDEATION-COMMENTS |
| R12 | The idea page's primary action is "Move to <next status>" (tenant label); Edit is an outline button to its left. Restore is primary when archived, Unmerge when merged; Edit is primary when there is no next move. | IDEATION-COMMENTS |
| R13 | The customer's track page lives under the CRM customer portal and shows comments, with public read and post. | Owner Q4 + IDEATION-COMMENTS |

## 3. What exists today (measured, not assumed)

### 3.1 CRM side

- Menu entry `Ideas` -> `/ideas`, gated by `ideation.board.view` (`FE/config/menu.config.tsx:71-76`,
  `FE/app/(protected)/ideas/layout.tsx:7`).
- `/ideas` and `/ideas/[id]` render `IdeationEmbed` (`FE/app/(protected)/ideas/page.tsx:10-17`,
  `FE/app/(protected)/ideas/[id]/page.tsx:13-30`). The iframe src is
  `${iframe_url}#token=${token}` (`FE/components/ideas/IdeationEmbed.tsx:93`), sandbox
  `allow-scripts allow-same-origin allow-forms allow-popups` (`:102`).
- The CRM already listens for `ideation-embed:token-refresh-request` with an origin check and posts
  `ideation-embed:token` back (`IdeationEmbed.tsx:40-62`). These are the only two messages; no theme,
  date, locale or navigation is passed.
- Session mint: `POST /api/v1/integrations/ideation/embed-session`
  (`BE/app/api/v1/integrations/ideation_embed.py:34-61`). It signs a 120 s HS256 assertion
  (`aud=ideation-embed`, `iss=sorento`, `sub`, `email`, `name`, `connection_id`) with the
  per-workspace signing secret and exchanges it at ss `POST /embed/session`
  (`BE/app/services/ideation_embed_service.py:190-278`). `iframe_url = fe_base + "/embed/ideas[/id]"`
  (`:273`).
- Config lives on the default `RespondWorkspace` row with `.env` fallback
  (`BE/app/models/respond_workspace.py:38-53`, `BE/app/config.py:293-302`):
  `ideation_shared_service_url`, `ideation_intake_api_key`, `ideation_embed_signing_secret`,
  `ideation_embed_connection_id`, `ideation_embed_fe_base_url`, `ideation_product_id`.
- The CRM already talks to ss server-side for JSON (httpx): products
  (`BE/app/api/v1/system/respond_workspaces.py:46-112`), create-idea
  (`BE/app/services/ideation_turn_service.py:660-669`), status-events
  (`BE/app/services/ideation_status_update_service.py:70-76`).
- **The CRM never builds an ss link; it relays the one ss returns.** The chatbot reply uses
  `result.get("link")` from create-idea (`ideation_turn_service.py:189`) and the composer rejects any
  other URL (`:302-312`). The status-update WhatsApp appends `Track it here: {track_url}`
  (`ideation_status_update_service.py:197-200`).
- Proxying: `FE/next.config.mjs:50-69` rewrites `/api/v1` only in dev. Production is nginx in the FE
  container (`FE/nginx.conf`: `X-Frame-Options SAMEORIGIN` `:17`, `/api/v1/` -> backend `:40-61`,
  `/` -> Next `:85-95`). There is no `middleware.ts` and no CSP. The public host nginx (blue/green)
  is outside this repo (`.github/workflows/deploy.yml:1604-1656`).
- CRM conventions an ideation page would get for free: theme = next-themes, `attribute="class"`,
  `storageKey="nextjs-theme"` (`FE/providers/theme-provider.tsx:10-17`), tokens in
  `FE/css/config.reui.css` (`:root` `:4`, `.dark` `:126`). Dates = `formatDate` `dd/MM/yyyy` and
  `formatDateTime` `dd/MM/yyyy, h:mm AM/PM` (`FE/lib/helpers.ts:128-134`, `:465`). Column prefs =
  `user_list_column_configs` via `/api/v1/list-query/column-config/{listing_key}`
  (`BE/app/api/v1/list_query.py:252-324`, `FE/components/ui/data-grid.tsx:403-417`). Public pages
  already exist under `FE/app/(public)/` (the portal `c/`).

### 3.2 ss side

- Next.js 15 App Router, no `basePath`/`assetPrefix` (`ss:ssFE/next.config.mjs:1-14`). Embed pages
  `ssFE/app/embed/ideas/page.tsx` and `[id]/page.tsx`, mode detected by path
  (`ssFE/.../embed-app.tsx:18-43`). Public track page `ssFE/app/(public)/public/ideas/[token]`.
- Embed auth is cookie-less: the 5-minute embed token (`typ="embed"`, signed with ss `JWT_SECRET`,
  claims `tenant_id, connection_id, product_id, idea_id, email, name, scope`) is held in memory only
  (`ss:ssFE/lib/embed-auth-store.ts:27-56`) and sent as a Bearer header
  (`ss:ssBE/modules/ideation/services/embed.py:228-313`, `routers/embed.py:164-181`).
- **ss's own staff-auth dependency refuses every embed token**:
  `if payload.get("typ") == "embed": raise _CREDENTIALS_EXC` (`ss:ssBE/app/dependencies.py:65-66`).
  Any ss endpoint behind `get_current_user` is therefore unusable from the iframe.
- Public links are minted by the ss backend only:
  `f"{settings.frontend_url.rstrip('/')}/public/ideas/{idea.status_token}"`
  (`ss:ssBE/modules/ideation/services/sinks.py:45-60`), used for intake replies and the
  status-event `track_url` (`services/intake.py:664,713,725`, `services/status_events.py:112,136`).
  `frontend_url` is ONE global setting per ss deployment (`ss:ssBE/app/config.py:121`), not per tenant.
- Frame headers: `ss:ssFE/middleware.ts:18-20` sets `frame-ancestors` only for omnichannel and
  webchat; `/embed/ideas` has no CSP and no `X-Frame-Options`, so any origin can frame it. The stored
  `EmbedConnection.allowed_origins` (`ss:ssBE/modules/ideation/models.py:300-303`) is never enforced.
- The child's token listener accepts `ideation-embed:token` with no `event.origin`/`event.source`
  check (`ss:ssFE/lib/embed-token-refresh.ts:34-40`). The TODO in `ss:ssFE/lib/api-client.ts:64-69`
  saying the Sorento listener is unshipped is stale: it shipped in `IdeationEmbed.tsx:40-62`.

## 4. Root cause of each owner complaint

### R4: column hide/width not saved

**Not third-party storage.** ss stores list prefs on its backend, not in localStorage or cookies:
`useViewPreferences` loads/saves through `GET/PATCH /me/preferences/{viewKey}`
(`ss:ssFE/hooks/use-view-preferences.ts:43,58-70`, `ss:ssFE/services/preferences-service.ts:18,25`),
table `user_view_preferences` (`ss:ssBE/app/models/view_preference.py:16-28`). In embed mode the
view key is `ideation.ideas.embed` (`ss:ssFE/app/(protected)/ideation/ideas/use-ideas-list-config.tsx:305`)
and the request carries the embed token, which `get_current_user` rejects with 401
(`ss:ssBE/app/dependencies.py:65-66`). `preferencesService.save` swallows the error
(`preferences-service.ts:24-32`); the source even says "its best-effort save 401s harmlessly"
(`use-ideas-list-config.tsx:304`). Every load starts from defaults.

Consequence for the options: a same-origin reverse proxy does **not** fix this by itself; the save
would still 401. The fix is either an embed-principal prefs endpoint in ss, or CRM-owned prefs.

### R5: cannot capture an idea manually

The "Capture idea" button is not hidden in embed mode (`use-ideas-list-config.tsx:314-315`,
`ss:ssFE/components/platform/resource-list/resource-list.tsx:441-448`), and the dialog is a normal
Radix portal, so the iframe is not the problem. The blocker is product scope:

- The dialog cannot submit without a product (`ss:ssFE/app/(protected)/ideation/ideas/idea-capture-dialog.tsx:45`).
- In embed mode the product list is synthesised (`ss:ssFE/services/ideation-embed-service.ts:63-89`):
  a product-scoped connection gives one product; an **unscoped** connection derives products from
  existing ideas (empty list, so Submit stays disabled, if there are none).
- Even with a product picked, ss rejects create on an unscoped connection:
  403 `embed_scope_required` "This embed connection is not scoped to a product; create is
  unavailable." (`ss:ssBE/modules/ideation/routers/embed.py:304-309`).

So capture works only if Sorento's `EmbedConnection` row has a `product_id`. Which state production
is in is DB data I cannot read from here; the symptom fits "unscoped". The CRM already knows the
right product (`ideation_product_id` on the workspace, `respond_workspace.py:38-53`), which the
WhatsApp intake path uses.

Second, smaller defect found on the way: attachments dropped into the capture dialog are silently
discarded in both modes (`ideation-embed-service.ts:108-118`, `ss:ssFE/services/ideation-service.real.ts:88-101`).

### R6: theme not aligned

ss uses its own tokens (`ss:ssFE/css/foundryx-tokens.css`), plus a tenant stylesheet chosen by the
**ss Host header** (`ss:ssFE/app/layout.tsx:61,73-75`, `lib/branding-ssr.ts:31-51`), and next-themes
with localStorage key `nextjs-theme` (`ss:ssFE/providers/theme-provider.tsx:13-20`). In a cross-site
iframe that localStorage is partitioned from the CRM's, so the iframe follows the OS theme, not the
CRM toggle. Nothing is passed from the host. The ideation embed has no theme message; the
omnichannel embed in the same repo does (`init/theme {theme, colorScheme}`, origin-checked,
`ss:ssFE/components/platform/omnichannel-embed/embed-protocol.ts:15-60`,
`use-embed-session.ts:60-66,152-168`), so "can the host pass theme dynamically?" is **yes, with a
copyable precedent**.

### R7: date format not aligned

ss formats with `Intl.DateTimeFormat('en-GB', {day:'2-digit', month:'short', year:'numeric'})`,
giving `02 Oct 2026` (`ss:ssFE/lib/datetime.ts:36-47,59-66`), used by the list
(`use-ideas-list-config.tsx:255,297`) and detail (`components/idea-form-fields.tsx:230`). Timezone is
`session.user.timezone` or the browser zone (`ss:ssFE/hooks/use-datetime.ts:27-37`); the iframe has
no session. The CRM standard is `dd/MM/yyyy` (`FE/lib/helpers.ts:128-134`). Two formatters, two
answers.

### R8: Archive and Delete

They are deferred actions that call `POST /api/v1/pending-actions`
(`ss:ssFE/services/pending-actions-service.real.ts:10`), which needs `get_current_user`
(`ss:ssBE/app/api/v1/pending_actions.py:54`) and so 401s on the embed token, same root cause as R4.
Inferred from code, not run.

### R3: deep links

`/ideas/<id>` maps to `/embed/ideas/<id>` on first load only. Navigation inside the iframe is never
reflected in the CRM URL, and ctrl/cmd-click on a row opens `window.open(href,'_blank')`
(`ss:ssFE/components/ui/data-grid-table.tsx:675`): an ss URL with no token and no parent, which
shows "Session expired".

### R2: public links

ss mints `track_url` and the intake `link` from its single global `frontend_url`, so every link
the CRM relays is on the ss domain. No CRM-side page exists for it.

## 5. Options

All three keep ss as the system of record for ideas (ss staff keep their operator UI for BR
promotion and triage). They differ in where the Sorento-facing UI is rendered.

### Option A: keep the iframe on the ss domain, add a host bridge

Fix each complaint inside ss and teach the CRM host to drive it.

| Req | How | Repo |
|---|---|---|
| R4 | New `GET/PATCH /embed/preferences/{viewKey}` behind `require_embed_principal`, keyed `embed-user:{sub}` + connection (reuse `user_view_preferences` with a nullable `user_id` plus a `principal_key` column, or a small sibling table). Point `preferencesService` at it in embed mode. | ss |
| R5 | Set `product_id` on Sorento's `EmbedConnection` (config, zero code) or let the assertion carry `product_id` from the CRM's `ideation_product_id`. Send attachments on create. | ss (+ CRM 2 lines if via assertion) |
| R6 | Add `ideation-embed:theme {colorScheme, tokens}` to the ideation embed, copied from the omnichannel protocol; CRM posts it on load and on every next-themes change. Map the CRM token values onto ss variable names. | both |
| R7 | Add `ideation-embed:locale {dateFormat, timeZone}`; ss `useDatetime` honours it in embed mode. Or hard-set embed mode to `dd/MM/yyyy`. | both |
| R8 | Embed-principal variant of pending actions, or direct `DELETE /embed/ideas/{id}` with the server-deferred window. | ss |
| R3 | `ideation-embed:navigate {path}` child to parent so the CRM URL follows; ctrl-click posts a "open in new tab" message and the CRM opens `/ideas/<id>`. | both |
| R2 | ss gets a per-tenant (or per-connection) `public_link_base_url`; Sorento's points at `https://<crm>/public/ideas`. The CRM serves `FE/app/(public)/public/ideas/[token]/page.tsx`, which calls a new CRM BE route that proxies ss `GET /public/ideas/{token}` (the CRM already proxies ss JSON with httpx). | both |
| Security | `frame-ancestors` from `allowed_origins` on `/embed/ideas`; origin check in `embed-token-refresh.ts`. | ss |

Fixes for free: nothing. Every item is a deliberate change, about half of them cross-repo.
Leaves: two token sets and two formatters that will drift again; a postMessage protocol to version
across two deploys; the ss UI still renders inside a CRM page (inner scrollbars, its own toolbar
style).
Effort: M. Roughly 60% ss, 40% CRM.

### Option B: same-origin reverse proxy (`<crm>/ideation/*` -> ss)

Serve ss under the CRM host so the iframe (or a full page) is same-origin.

| Req | How | Repo |
|---|---|---|
| R1/R2 | CRM nginx `location /ideation/ { proxy_pass <ss-fe>; }` before `location /`, plus the public host nginx (not in this repo). ss needs `basePath: '/ideation'` and its own build, because `basePath` and `NEXT_PUBLIC_BACKEND_API_URL` are baked at build time (`ss:ssFE/next.config.mjs`, `lib/api-client.ts:13-14`) and paths like `'/embed/ideas'` are hard-coded (`embed-app.tsx:23-25`). A dedicated ss frontend container for Sorento. ss `frontend_url` must become per-tenant, or the Sorento container gets its own backend config. | both + ops |
| R4 | **Not fixed for free.** Prefs are server-side and 401 (section 4). Same ss endpoint as A. | ss |
| R5 | Same as A (connection scope). | ss |
| R6 | Partly free: same origin means ss next-themes reads the CRM's `nextjs-theme` localStorage key (both use that key) and the `storage` event syncs a toggle live. Colours still differ (`foundryx-tokens.css` vs `config.reui.css`), and ss tenant branding resolves from the Host header (`ss:ssFE/lib/tenant.ts:75-88`), which is now the CRM host, so a wrong or default tenant brand. | ss |
| R7 | Not fixed. Same as A. | ss |
| R3 | Better: a same-origin parent can read `iframe.contentWindow.location` and mirror it. Ctrl-click still opens an ss page with no token. | CRM |
| R8 | Same as A. | ss |

New risks: both apps run NextAuth v4 with the default cookie name `__Secure-next-auth.session-token`
on path `/` of the same host. ss's root layout mounts a SessionProvider, so ss code would read (and
on sign-out clear) the CRM's session cookie, signed with a different secret. Needs a cookie-name
override in ss before it is safe. Two Next apps on one host also share `/_next/` unless basePath is
set everywhere.
Fixes for free: dark/light mode only, and same-origin storage (which is not what breaks R4).
Effort: M-L, much of it ops (second ss build per host, nginx in two places), and the least
reusable.

### Option C: CRM-native Ideas pages over the ss embed API (CRM backend as the gateway)

The CRM renders Ideas with its own components; the CRM backend calls ss's existing embed API
server-side, so no iframe and no ss domain reaches the browser.

| Req | How | Repo |
|---|---|---|
| R1 | `FE/app/(protected)/ideas/page.tsx` becomes a CRM DataGrid list, `[id]/page.tsx` a CRM detail page (View = Edit layout, `RecordNavigation`), capture as a CRM modal. Layering per PRINCIPLES: UI -> `useIdeasQuery`/mutations -> `services/ideationService.ts` -> `lib/api-client` -> BE. | CRM |
| Gateway | New BE router `/api/v1/ideation/*` (permission `ideation.board.view`, write actions under a new `ideation.ideas.manage` if the owner wants to split). Each call mints a short assertion for the calling user exactly as today (`ideation_embed_service.py:190-209`), exchanges it once for an embed token, caches it per user for its 5-minute life, and forwards to ss `/embed/ideas`, `/embed/ideas/{id}`, `/vote`, `/status`, `/attachments`, `POST /embed/ideas`. Those ss routes already exist with "full parity" and per-id scope checks (`ss:ssBE/modules/ideation/routers/embed.py:198-215`). | CRM |
| R4 | **Free.** The CRM DataGrid persists to `user_list_column_configs` (`list_query.py:252-324`) with listing key `ideation.board.view`. | none |
| R5 | Capture modal posts through the gateway. The CRM sends no product (ss forces the connection's), so the one prerequisite is the connection's `product_id` (config). Attachments: CRM uploads after create via `POST /embed/ideas/{id}/attachments`, which works today. | config |
| R6 | **Free.** CRM components, CRM tokens, CRM next-themes. | none |
| R7 | **Free.** `formatDate`/`formatDateTime` from `FE/lib/helpers.ts`. ss returns ISO timestamps. | none |
| R8 | **Free.** CRM deferred-action delete pattern calling the gateway, which calls ss `DELETE /embed/ideas/{id}` (exists). | none |
| R3 | **Free.** Real CRM routes; ctrl-click, refresh and share all work. | none |
| R2 | Same public-link change as A: ss per-tenant `public_link_base_url` (small ss change) plus a CRM public page `FE/app/(public)/public/ideas/[token]` over a CRM BE proxy of ss `GET /public/ideas/{token}` (keep ss's `no-store`, `noindex`, `no-referrer` headers, `ss:ssBE/modules/ideation/routers/public_ideas.py:45-51`). Stop-gap if ss cannot ship first: the CRM rewrites `link`/`track_url` it relays by swapping the ss origin for its own (it already parses both, `ideation_turn_service.py:189`, `ideation_status_update_service.py:197`). | both |
| Security | The embed token never reaches the browser. The iframe, the fragment token and the postMessage handshake are deleted from the CRM. ss's frame-header and origin-check gaps stop mattering to Sorento (still worth fixing in ss for other tenants). | CRM |

Cost: a second UI for ideas exists (ss operator UI stays for ss staff). Scope decides size: list +
detail + capture + vote + status + archive/delete + attachments is the baseline; board (kanban
reorder), merge/unmerge and promote-to-BR are optional extra slices (all have embed routes:
`PUT /embed/ideas/reorder`, `POST /embed/ideas/merge`, `/unmerge`, `/promote`).
Fixes for free: R3, R4, R6, R7, R8. R5 needs one config value. R2 needs the same small ss change as A.
Effort: L for the baseline (ss changes are only the public-link base). Repo precedent: ADR 0005
already rejected embedding ss by iframe for the page builder, citing this embed's cost
(`documentation/adr/0005-page-builder-built-here-not-the-shared-service-template-engine.md:22-27`).

### Scorecard

| Req | A: iframe + bridge | B: same-origin proxy | C: CRM-native |
|---|---|---|---|
| R1 in portal | yes (iframe) | yes (iframe or page) | yes (native) |
| R2 CRM-domain links | ss + CRM change | ss + CRM + ops | ss + CRM change |
| R3 deep links | protocol work | partial | free |
| R4 column prefs | ss endpoint | ss endpoint (NOT free) | free |
| R5 manual capture | config | config | config |
| R6 theme | protocol + token map | dark mode free, colours not | free |
| R7 dates | protocol | ss change | free |
| R8 archive/delete | ss change | ss change | free |
| New risk | protocol drift | cookie collision, per-host ss build | duplicate UI to keep in step |

## 6. Recommendation

**Option C**, built in this order:

1. **Now, no code, any option:** set `product_id` on Sorento's ss `EmbedConnection` to the same
   product as the CRM workspace's `ideation_product_id`. This unblocks manual capture (R5) in the
   current iframe today, before anything else ships.
2. **Slice 1 (CRM):** gateway router + native list + detail + capture modal + vote/status +
   archive/delete + attachments; delete `IdeationEmbed` and `useIdeationEmbedSession`.
3. **Slice 2 (ss, small):** per-tenant `public_link_base_url` used by `mint_idea_link`.
   **Slice 3 (CRM):** public track page `/public/ideas/[token]` + BE proxy. Until slice 2 lands, the
   CRM rewrites the origin of relayed links (stop-gap).
4. Board / merge / promote only if the owner wants them in the CRM (question 2). Superseded: the
   owner chose all of them; the slice order is now section 12.

Why: the owner's goal is one system. All four complaints (and archive/delete and deep links) are the
same problem, two apps with two theme systems, two date formatters and two auth models stitched
together by an iframe. A patches each symptom across two repos and two deploys and leaves them free
to drift; B costs ops work and a cookie risk and, measured against the code, fixes only dark mode
for free (the prefs bug is a backend 401, not browser storage). C deletes the stitching: five of the
eight requirements need no extra work at all, and the ss embed API it calls already exists.

If the owner wants the cheapest step now and C later: do step 1, plus Option A's R4 and R7 items,
and treat C as the follow-up. Most of A's protocol work would be thrown away by C, which is why it
is not the recommendation.

## 7. First crew-ask (answered 2 Oct, see section 0)

1. Which option: A, B or C? Recommendation C.
2. Scope of the CRM Ideas pages: (a) baseline list/detail/capture/vote/status/archive/attachments, or
   (b) also board (kanban reorder), merge/unmerge and promote-to-BR. Recommendation (a), add (b) only
   for what Sorento staff actually use.
3. Every idea captured from the CRM goes to one product, the workspace's `ideation_product_id`
   (scope the ss embed connection to it)? Recommendation yes; it also fixes manual capture today.
4. Public track link shape `https://<crm domain>/public/ideas/<token>`, and links already sent on the
   ss domain keep working there (no redirect)? Recommendation yes to both.
5. May this lane open the small ss PR (per-tenant public link base) in `foundryx-shared-service`, or
   does ss work go to a separate ss lane? Recommendation a separate ss lane, with the CRM rewrite
   stop-gap until it merges.

## 8. Side findings in ss (report, not in this lane's scope)

- `/embed/ideas` has no `frame-ancestors`; `EmbedConnection.allowed_origins` is stored but never
  enforced (`ss:ssFE/middleware.ts:18-20`, `ss:ssBE/modules/ideation/models.py:300-303`).
- `embed-token-refresh.ts:34-40` accepts a token message from any origin.
- Capture-dialog attachments are silently dropped (section 4, R5).
- Stale TODO in `ss:ssFE/lib/api-client.ts:64-69` (the Sorento listener shipped).

## 9. CRM screens (design = IDEATION-COMMENTS mock v2, rebuilt with CRM components)

The mock (ss `crew/ideation-comments`, commit `0841b7c`, `documentation/mockups/IDEATION-COMMENTS/index.html`)
is the approved layout. Its element map (section 6) names ss components; the CRM equivalents:

| Mock element | CRM component | Note |
|---|---|---|
| Page frame, breadcrumb, Back | `FE/components/common/PageHeader.tsx` | One CTA per page, in the header (DESIGN-LANGUAGE section 6). |
| Vote box left of title (md), list rows and board cards (sm) | new `FE/components/ideas/VoteBox.tsx` (Button + `ChevronUp` + count; filled primary-accent when `myVote === 'up'`; disabled on a merged child) | Upvote only. Clicking in a list row or card stops propagation so the row does not open. |
| Status under title | `Badge` (status pill per PR-CHECKLIST), colour from ss `statusColor` | Label = ss `statusLabel` (tenant label, never hard-coded). |
| Primary "Move to <next>" / Restore / Unmerge, Edit outline to its left | PageHeader actions: primary `Button` + outline `Button` | Next move = ss `advanceTransitionId` + its target label from `transitions` (`ss:ssBE/modules/ideation/schemas.py` IdeaOut). Archived: primary Restore. Merged child: primary Unmerge, no Edit. No next move: Edit primary. |
| "..." menu: Promote to BR, Archive, Delete | `DropdownMenu` with icon-button label | Archive and Delete are `useDeferredAction` countdowns (`FE/hooks/useDeferredAction.tsx`), never a confirm dialog (D7). |
| Prev/next pager "1 / 9" | `FE/components/common/RecordNavigation.tsx` | |
| Tabs Details / Attachments / Business Requirements | CRM line tabs | View = Edit layout: editing swaps values for inputs in place. BR tab: not shown in this lane; ss `IdeaOut` carries no BR links (measured 2 Oct, code review), so it waits for the ss ask in section 13. |
| Details rows | CRM detail field rows | "Votes" row removed (the box is the only vote control). Dates via `formatDateTime` (`FE/lib/helpers.ts:465`), so `21/07/2026, 9:05 AM`, not the mock's `21 Jul 2026, 9:05 AM` (R7 wins). |
| Comments under Details (oldest first, one reply level, "edited" tag, "Comment deleted" placeholder when it has replies) | new `FE/components/ideas/IdeaComments.tsx` with `Textarea`, `Button`, `avatar.tsx` | Composer hidden (not disabled) on a merged child. Own comment: Edit / Delete; triage users may delete any (ss enforces). Empty state: heading + hint, no button. |
| Comment delete | `useDeferredAction` countdown | The mock uses an AlertDialog confirm; the CRM forbids confirm dialogs (D7, `ConfirmDeleteDialog` retired). See section 15 Q1. |
| Ideas list with vote box first column | CRM `DataGrid` (`tableLayout: { width: 'fixed', columnsResizable: true }`, `columnResizeMode: 'onChange'`, explicit `size`, `truncate` + `title`), `listingKey="ideation.board.view"` | Column prefs persist through `user_list_column_configs` (R4). Toolbar: search, status filter (`SearchableSelect`, clearable), Add = "Capture idea". `rowHref` = `/ideas/{id}`. |
| Capture idea | CRM modal (create = modal by default) | Fields per `ss IdeaCreateIn`; no product picker (the connection is product-scoped, Q3). Attachments uploaded after create via the attachments route. |
| Triage board | `FE/components/ui/kanban.tsx` + `sortable.tsx`, route `/ideas/board` | Lanes from ss `GET /embed/board`; drag = `PUT /embed/ideas/reorder`; card leads with the sm vote box. |
| Merge | CRM modal from list multi-select or the detail "..." menu, target picked with `SearchableSelect` | `POST /embed/ideas/merge`. "Merged from" list on the target's detail page; Unmerge on the child. |
| Promote to BR | "..." menu item, CRM modal for any fields ss requires | `POST /embed/ideas/promote`. ss resolves the CRM user's email to an ss user with `ideation.business_requirements.manage` (`ss:ssBE/modules/ideation/routers/embed.py:337-351`); otherwise 403. See section 15 Q3. |

Both widths: usable and non-clipped at 375px and 1280px (mock section 5 is the 375px reference).

## 10. Gateway contract (CRM backend -> ss embed API)

New router `BE/app/api/v1/ideation/` mounted at `/api/v1/ideation`, wrapped in the module guard like
every other domain. Per request: resolve the caller, mint the 120 s assertion exactly as
`BE/app/services/ideation_embed_service.py:190-209` does, exchange it at ss `POST /embed/session`,
cache the 5-minute embed token per CRM user (in-process or redis, key = user id, evict 30 s before
`expires_at`, drop on a 401 and retry once). The token never leaves the backend.

| CRM route | ss route | CRM permission |
|---|---|---|
| `GET /ideas?filter=` | `GET /embed/ideas?filter=` | `ideation.board.view` |
| `GET /ideas/board` | `GET /embed/board` | view |
| `GET /ideas/{id}`, `GET /ideas/{id}/merged` | same under `/embed` | view |
| `POST /ideas` | `POST /embed/ideas` | view (capture is open to every viewer, as in ss) |
| `PATCH /ideas/{id}` | `PATCH /embed/ideas/{id}` | manage (section 15 Q2) |
| `POST /ideas/{id}/vote` | `POST /embed/ideas/{id}/vote` (always `up`) | view |
| `POST /ideas/{id}/status` | `POST /embed/ideas/{id}/status` | manage |
| `PUT /ideas/reorder`, `POST /ideas/merge`, `POST /ideas/{id}/unmerge`, `POST /ideas/promote` | same under `/embed` | manage |
| `POST /ideas/{id}/attachments`, `GET .../attachments/{aid}/content` (streamed) | same under `/embed` | view (upload: manage) |
| Delete / Archive | pending-action handlers in `BE/app/services/record_actions.py` (`entity_type="idea"`) that call ss `DELETE /embed/ideas/{id}` / `POST .../status {status:"archived"}` as the user who started the action | manage |
| `GET/POST /ideas/{id}/comments`, `PATCH/DELETE /ideas/{id}/comments/{cid}` | ss comment routes from the IDEATION-COMMENTS lane (not on ss main yet) | view (post/edit own); delete-any enforced by ss |

**Commenter identity (IDEATION-COMMENTS security check, 2 Oct).** Every comment write through the
gateway must reach ss with the commenter's display name, not only the email, so portal readers see a
real name. Facts: the CRM assertion already carries `name` (`BE/app/services/ideation_embed_service.py:143`)
and ss copies it into the embed token, but ss's `EmbedTokenPrincipal` keeps only `sub` and `email`
(`ss:ssBE/modules/ideation/services/embed.py:78-81`), so the name is dropped before a comment is
written. Contract:
- CRM: the assertion `name` is always a non-empty display name: `users.name` trimmed, else the
  literal "Sorento staff". It never falls back to the email address. A gateway test asserts the
  claim for a user with a blank name.
- ss (IDEATION-COMMENTS lane): carry `name` onto the principal and store it as the comment's author
  display name; the email stays server-side (author key only).
- Public read (`/public/ideas/{token}/comments`) returns author display name and an `isSubmitter`
  flag only, never an email. The CRM public portal proxy (section 11) additionally whitelists the
  fields it forwards (id, parentId, authorName, isSubmitter, body, createdAt, edited, deleted), so an
  email added upstream later can never reach a portal user. A test asserts no `@` email value
  appears in the proxied payload. Staff pages also show names only (no UUIDs, no emails).

Errors: an ss 4xx passes through as an `AppException` with ss's message; ss down or timeout = 502
"The Ideas workspace isn't reachable right now." Unconfigured = 404, as the embed route does today.

## 11. Public track page under the customer portal (Q4)

The customer portal is the `(auth)/portal` route tree on the CRM frontend host
(`FE/app/(auth)/portal/`), whose links the backend builds from `settings.frontend_base_url`
(`BE/app/services/portal_service.py:428-448`, `BE/app/config.py:305`). The portal already has a
token-as-credential page with no OTP: `/portal/ticket-draft/[token]`
(`FE/app/(auth)/portal/ticket-draft/[token]/page.tsx`).

**Proposed URL: `{FRONTEND_BASE_URL}/portal/ideas/{status_token}`**, for example
`https://fe-sorento.foundryx.my/portal/ideas/Ab3dEf9hJk2LmN0p` (host per environment; the
`fe-sorento.foundryx.my` host is the one the CRM already cites for template links,
`BE/app/services/respond_messaging_service.py:37`).

- Route: `FE/app/(auth)/portal/ideas/[token]/page.tsx`. A static `ideas` segment outranks the
  existing dynamic `/portal/[type]/[id]` (`FE/app/(auth)/portal/[type]/[id]/page.tsx`), which would
  otherwise `notFound()` on an unknown kind. Inherits the portal layout and branded shell.
- Data: new CRM public routes `GET /api/v1/public/portal/ideas/{token}` and
  `GET|POST /api/v1/public/portal/ideas/{token}/comments`, proxying ss `GET /public/ideas/{token}` and
  the ss public comment routes. Keep ss's `Cache-Control: no-store`, `X-Robots-Tag: noindex`,
  `Referrer-Policy: no-referrer` and the token regex `^[A-Za-z0-9_-]{16,64}$`
  (`ss:ssBE/modules/ideation/routers/public_ideas.py:28,45-51`). Rate-limit public POST per token.
- The token is the credential (as on ss today and as WhatsApp promises: "no login needed",
  `FE/services/whatsappTemplateService.ts:403-405`). No portal OTP, no slug: ss mints the link and
  does not know portal slugs.
- Links already sent on the ss domain keep working there; no redirect.

## 12. Slices (size L)

| # | Slice | Contents | Depends on | Gate |
|---|---|---|---|---|
| S0 | Behaviour card | Sections 0-2, this plan. | - | Done 2 Oct |
| M | **Static mock per CRM screen, owner approval** (`documentation/mockups/ideation-in-crm/index.html`), then the in-app Phase 1 mock | In the CRM app against mock data, no backend, no tests: (M1) `/ideas` list with vote box + capture modal; (M2) `/ideas/{id}` detail incl. header action states A-E from the mock, tabs, comments with reply and deleted-with-replies; (M3) `/ideas/board`; (M4) merge modal + merged-from + promote modal; (M5) `/portal/ideas/{token}` track page with public comments. Each at 375px and 1280px, dark and light. | - | Owner hand-test of the mock (built + agent-browser verified 2 Oct, 375/1280, light/dark) |
| U | UAC file | `ideation-in-crm-acceptance-criteria.md` from sections 2, 9-11 and the approved mock. | M | Owner sign-off |
| B1 | Gateway + baseline | Router (section 10), token cache, list / detail / capture / vote / status / edit / archive / delete / attachments wired; `/ideas` and `/ideas/{id}` switch from the iframe to the native pages (no feature flag). Tester-first. | U | pytest + vitest green, reviewer + security-reviewer |
| B2 | Board, merge/unmerge, promote | M3, M4 wired. | B1 | same |
| B3 | Staff comments | Comment routes through the gateway; IdeaComments wired. | B1 + ss IDEATION-COMMENTS merged | same |
| B4 | Portal track page | Section 11 public routes + page, public comments. | B1 + ss IDEATION-COMMENTS + ss `public_link_base_url` lane merged | same + security-reviewer (public ingest) |
| B5 | Remove the iframe | Delete `IdeationEmbed.tsx`, `useIdeationEmbedSession.ts`, `POST /integrations/ideation/embed-session` callers; keep the session mint code that the gateway reuses. | B1-B4 | browser pass |

B3 and B4 can be mocked (M2, M5) and built against the ss branch contract before ss merges; they
only ship after it does.

## 13. Cross-lane contracts

- **ss `public_link_base_url` lane (SS-PUBLIC-LINK-BASE):** the setting supports a `{token}`
  placeholder (crew, 2 Oct); `mint_idea_link` substitutes the status token when the tenant (or
  Sorento's connection) has it set, else today's `{frontend_url}/public/ideas/{token}`. Sorento's
  value = `{FRONTEND_BASE_URL}/portal/ideas/{token}`. Both the intake `link` and the status-event `track_url` must use it
  (`ss:ssBE/modules/ideation/services/sinks.py:45-60`, `services/intake.py:664,713,725`,
  `services/status_events.py:112,136`). The CRM relays both unchanged
  (`BE/app/services/ideation_turn_service.py:189`, `BE/app/services/ideation_status_update_service.py:197-200`),
  so no CRM change is needed for links once ss ships.
- **ss IDEATION-COMMENTS lane:** the CRM needs, on the embed API: list (oldest first, one reply
  level, author name, `edited`, `deleted` with replies kept), create (with optional `parentId`),
  edit own, delete (own, or any with triage); on the public API: list and create by status token,
  with a stated author identity for public posts (section 15 Q4). Comment authors are carried by
  display name from the embed `name` claim, never shown by email (section 10, commenter identity). Votes: `myVote` only ever `up`.
  Its components must stay free of a hard-coded domain (the mock already says so).

- **ss IDEATION-COMMENTS, read 2 Oct (branch head `f9ce5aee`), three gaps the CRM cannot close alone:**
  1. Embed comment author: `routers/embed.py` stores `author_name=(principal.email or "").strip() or "Portal user"`.
     It must store `principal.name` (the CRM always sends a display name, AC-A-07), falling back to
     "Portal user", never the email. Reads already mask email-shaped names (`_project_names`), so
     today a CRM comment would show as "Portal user".
  2. Public comment throttle keys on `client_ip(request)`. Behind the CRM proxy every customer has
     the CRM backend's IP, so the 20-per-IP limit becomes one global limit. The CRM limits per real
     client IP itself (AC-H-07) and forwards `X-Forwarded-For`; ss should trust that header only
     from the CRM (or exempt the CRM caller from the per-IP bucket and keep the per-token one).
  3. Embed viewers never moderate (`can_moderate=False`), so a CRM manage holder can delete only
     their own comments. Accepted for this lane; raise with the owner if moderation from the CRM is
     wanted.

- **Further ss asks from Phase 3 review (2 Oct):** (4) BR links on an idea (field on `IdeaOut` or an
  embed `GET /embed/ideas/{id}/business-requirements`) so the CRM can show the Business requirements
  tab; (5) a `can_manage` claim in the assertion and embed token so ss enforces triage writes itself
  instead of trusting the CRM route shape (security review C1, defence in depth); (6) public comment
  per-IP throttle: exempt the CRM caller, since the CRM no longer forwards `X-Forwarded-For` and
  enforces per-token + global limits itself.

- **ss asks from the end-of-lane browser pass (2 Oct):** (7) `GET /embed/ideas/{id}` passes
  `voter_id=None`, so `myVote` is always null on the idea page; (8) embed create passes `actor=None`,
  so CRM captures show submitter "Unknown embed" (should use the principal's `name`); (9) embed list
  `search` matches the title only, not idea number or submitter; (10) idea hard delete fails when the
  idea has attachments (ss BL-SS-285).

## 14. Embed connection product scope (Q3)

What: ss `embed_connections.product_id` for Sorento's connection = the CRM default workspace's
`respond_workspaces.ideation_product_id`.

This sandbox may not connect to any shared DB, so crew applies it on **dev**:

1. Read the two values from the CRM dev DB:
   `SELECT ideation_product_id, ideation_embed_connection_id FROM respond_workspaces WHERE is_default IS TRUE;`
   If `ideation_embed_connection_id` is blank, the value is `IDEATION_EMBED_CONNECTION_ID` in the
   backend `.env` (row first, `.env` fallback: `BE/app/services/ideation_embed_service.py:151-187`).
2. Set it on ss dev, preferably through the ss admin UI `/ideation/embed-connections` (edit the
   Sorento connection, Product = that product) or `PATCH /ideation/embed-connections/{connection_id}`
   `{"product_id": "<ideation_product_id>"}` (`ss:ssBE/modules/ideation/routers/embed_admin.py:134`).
   SQL equivalent on the ss dev DB:
   `UPDATE embed_connections SET product_id = '<ideation_product_id>' WHERE connection_id = '<connection_id>' AND product_id IS NULL;`
3. Check: open Ideas in the CRM dev iframe; "Capture idea" shows one product and saves (no 403
   `embed_scope_required`).

**Prod: the same two steps with prod values. Held for the owner; crew asks before touching prod.**
Side effect to state to the owner: a scoped connection only shows that product's ideas in the
embed (`_assert_in_scope`, `ss:ssBE/modules/ideation/routers/embed.py:198-215`), and the Product
column disappears in embed mode. Today Sorento's ideas all come from intake with that product
(`ideation_turn_service.py:649`), so nothing should vanish; ideas captured in ss under another
product would.

## 15. Second crew-ask (all answered 2 Oct, see section 0)

1. Comment delete: the approved mock confirms with a dialog; CRM rule D7 forbids confirm dialogs.
   (a) CRM deferred countdown (10 s, Cancel) like every other CRM delete, (b) keep the mock's
   confirm. Recommendation (a).
2. Who may run the triage actions in the CRM (status move, edit, merge/unmerge, board reorder,
   archive, delete, promote)? (a) new CRM permission `ideation.ideas.manage`, while view, capture,
   vote and comment stay on `ideation.board.view`, (b) everyone with `ideation.board.view` (ss
   enforces nothing per embed user today). Recommendation (a).
3. Promote-to-BR only works for a CRM user whose email is also an ss user with BR-manage. (a) show
   Promote only to `ideation.ideas.manage` holders and surface ss's 403 message, (b) hide it unless
   ss confirms the mapping (needs a new ss lookup). Recommendation (a).
4. Who is the author of a comment posted on the public track page? (a) the idea's submitter (the
   token holder; name from the idea), (b) a name typed by the poster. Recommendation (a): the link
   only reaches the submitter's WhatsApp. The ss IDEATION-COMMENTS lane must implement the same.
