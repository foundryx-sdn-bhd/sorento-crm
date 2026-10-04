# UAC: Ideas inside Sorento CRM (lane IDEATION-IN-CRM)

Plan: `PLAN-ideation-in-crm.md` (sections 0, 9-14 are the design). Approved mock:
`documentation/mockups/ideation-in-crm/index.html` (owner "no problem", 2 Oct 2026; the open
recommendations in that ask were accepted: Promote is one click, the track-page vote count is
read-only, Product is hidden by default, the Phase 1 screens are kept and reworked).
ss contracts: embed API on ss `main`; comment routes from ss lane IDEATION-COMMENTS (branch
`crew/ideation-comments`, ss plan `sprint-5/19-ideation-comments.md`).

Terms: "gateway" = the new CRM router `/api/v1/ideation`; "ss" = the shared service; "manage" =
the new CRM permission `ideation.ideas.manage`; "view" = existing `ideation.board.view`.

## A. Gateway and embed token

- **AC-A-01** Every gateway READ authenticates the CRM user (Bearer session or X-API-Key act-as user);
  every gateway WRITE requires a real staff session (no X-API-Key), per the
  `require_permission_with_api_key` policy (security review M1). All routes require view; a user without view gets 403 and ss is never called.
- **AC-A-02** The gateway mints the assertion exactly as `ideation_embed_service.mint_embed_assertion`
  does, exchanges it at ss `POST /embed/session`, and caches the embed token per CRM user id until
  30 s before `expires_at`. A second call inside the window does not call `/embed/session` again.
- **AC-A-03** An ss 401 on a cached token drops the cache entry, re-mints once and retries once; a
  second 401 returns 502.
- **AC-A-04** The embed token never appears in any gateway response body or header, and is never
  logged.
- **AC-A-05** ss unreachable or timing out returns 502 "The Ideas workspace isn't reachable right
  now."; Ideas not configured returns 404 "The Ideas workspace isn't available on this deployment."
- **AC-A-06** An ss 4xx is passed through with ss's status and message (`detail`/`message`), e.g. a
  promote 403 surfaces ss's text.
- **AC-A-07** The assertion `name` claim is the CRM user's display name: `users.name` trimmed, else
  the literal `Sorento staff`. It is never the email address (a user with a blank name and an email
  gets `Sorento staff`).
- **AC-A-08** Route map (method, CRM path -> ss path, permission):
  `GET /ideas?filter=` -> `GET /embed/ideas?filter=` (view);
  `GET /ideas/board` -> `GET /embed/board` (view);
  `GET /ideas/{id}` and `GET /ideas/{id}/merged` (view);
  `POST /ideas` (view);
  `PATCH /ideas/{id}` (manage);
  `POST /ideas/{id}/vote` -> body `{"dir":"up"}` always (view);
  `POST /ideas/{id}/status` (manage);
  `PUT /ideas/reorder`, `POST /ideas/merge`, `POST /ideas/{id}/unmerge`, `POST /ideas/promote` (manage);
  `POST /ideas/{id}/attachments` (view: attachments are not a triage action in owner Q2, and capture lets every viewer attach), `GET /ideas/{id}/attachments/{aid}/content` streamed (view);
  `GET|POST /ideas/{id}/comments`, `PATCH|DELETE /ideas/{id}/comments/{cid}` (view).
  A view-only user calling a manage route gets 403 and ss is never called.
- **AC-A-09** Archive and Delete run as server-deferred pending actions (`record_actions` handlers
  `idea.archive` / `idea.delete`, entity `idea`, permission manage; comment delete is
  `idea_comment.delete`, entity `idea_comment`, permission view, payload `{idea_id}`): the handler calls ss as the user who started the action (status `archived` / ss
  `DELETE /embed/ideas/{id}`); Cancel inside the window means ss is never called.

- **AC-A-10** Every id that reaches an ss path (idea, comment, attachment ids in routes, and
  `entity_id` / `payload.idea_id` in pending actions, at park time and at commit) must be a UUID,
  else 422 and ss is never called; `call_ss` also percent-encodes each path segment. Request bodies
  forwarded to ss are rebuilt from an explicit field list per route (comments: `body`, `parentId`
  only). Regression tests cover `%2E%2E`, `%3F`, `%23` and an extra-field body (security review C1,
  code review B1).
- **AC-A-11** Archive and comment delete have no direct gateway write: the status route refuses
  `archived` (only the pending action archives) and there is no gateway DELETE comment route (only
  the pending action deletes).
- **AC-A-12** The embed token cache key is (user id, connection id, ss base URL); a pending action
  whose requester is inactive at commit is refused.

## B. Permission

- **AC-B-01** `ideation.ideas.manage` exists in the permission registry (module ideation) and is
  granted to admin/superadmin by the reference seed; the FE reads it through the normal permission
  check (the Phase 1 admin fallback is removed).
- **AC-B-02** Without manage, the idea page shows the vote box and comments only: no Edit, no Move,
  no "..." menu; the board does not allow drag; the list shows no row actions beyond voting.

## C. Ideas list (`/ideas`)

- **AC-C-01** Reached by the sidebar "Ideas" entry; renders a DataGrid (fixed layout, resizable
  columns, explicit sizes, truncate + title) with columns Votes, Idea, No., Product (hidden by
  default), Submitter, Channel, Status, Captured (dd/MM/yyyy).
- **AC-C-02** Column visibility and widths persist per user via
  `/api/v1/list-query/column-config/ideation.board.view` and survive a reload.
- **AC-C-03** The vote box toggles the viewer's upvote via the gateway without opening the row; the
  count and filled state update; a merged child's box is disabled.
- **AC-C-04** Status filter (SearchableSelect, clearable) includes Archived; default shows active
  ideas; search is passed to ss (`search`), which today matches the title only (number and submitter
  search need ss; PLAN section 13 item 9).
- **AC-C-05** Header CTA "Capture idea" opens a modal with Problem statement (required, inline
  "Problem statement is required"), Proposed solution, Impact, Department, Original message,
  Attachments; no Product picker. Save calls `POST /ideas` then uploads each dropped file to the new
  idea; the new idea appears in the list.
- **AC-C-06** Empty list: heading + hint, no button. Gateway error: error state with Retry.

## D. Idea page (`/ideas/{id}`)

- **AC-D-01** PageHeader title and breadcrumb are the idea number; the record card shows the vote
  box (md), full title, status Badge (tenant label and colour from ss), metadata strip (Submitter,
  Channel, Product, Captured dd/MM/yyyy, h:mm AM/PM), RecordNavigation, tabs Details / Attachments /
  Business requirements.
- **AC-D-02** Header states (manage holder): primary = "Move to <label of advance target>" from
  `advanceTransitionId`/`transitions`, Edit outline to its left; no next move: Edit primary;
  archived with an outgoing transition: Restore primary (posts that transition's `toStatusId`; no
  hardcoded status key anywhere) + Edit; merged child: Unmerge primary, no Edit, vote disabled, no comment
  composer.
- **AC-D-03** Edit swaps values for inputs in place (same layout); Save calls `PATCH /ideas/{id}`.
- **AC-D-04** "..." menu: Promote to BR (one click: `POST /ideas/promote` with `{ideaIds:[id],
  title:<idea title>}`, success toast naming the new BR by its title (ss returns no BR number); ss 403 message
  shown as an error toast), Merge into another idea, Archive (5 s countdown), Delete (10 s countdown,
  then back to the list). No confirm dialog anywhere.
- **AC-D-05** Attachments tab lists files (download goes through the gateway, buffered, and saves as a
  file; it is never opened inline; link attachments open only for http/https) and a dropzone for
  every viewer; uploads above the ss attachment cap are refused by the gateway with 413 before
  reading the whole body. The Business requirements tab is NOT shown in this lane: ss returns no BR
  links on an idea (cross-lane ask, PLAN section 13); it returns when ss does.
- **AC-D-06** Unknown id (404): not-found state; any other error: error state with Retry; no UUID is shown anywhere in the UI.

## E. Comments (staff)

- **AC-E-01** Comments render under Details oldest first, one reply level (ss flat list grouped by
  `parentId`), author display name, time dd/MM/yyyy, h:mm AM/PM, an "edited" tag when `editedAt`.
- **AC-E-02** Posting and replying call the gateway; a reply to a reply posts the top-level parent id.
- **AC-E-03** Edit / Delete show only when ss says `canEdit` / `canDelete`; Delete is a 10 s
  countdown with Cancel, no confirm dialog.
- **AC-E-04** A deleted comment with replies shows "Comment deleted" and keeps its replies; a
  deleted comment without replies is not shown (ss omits it).
- **AC-E-05** A comment posted from the CRM is stored in ss with the CRM user's display name (AC-A-07),
  never the email; another reader (staff or portal) sees that name. SHIP GATE: needs the ss
  IDEATION-COMMENTS change (store `principal.name`); until then the gateway masks any email-shaped
  `authorName` in comment POST/PATCH responses.
- **AC-E-06** Bodies render as plain text (no HTML); empty body is refused before calling ss.

## F. Board (`/ideas/board`)

- **AC-F-01** Reached by the List / Board switch on the list; lanes and order from ss `GET
  /embed/board`; cards show the sm vote box, idea number, title clamped to 2 lines, submitter.
- **AC-F-02** A manage holder drags by the grip: within a lane calls `PUT /ideas/reorder`; to another
  lane calls `POST /ideas/{id}/status` with the transition to that lane; a move with no allowed
  transition is refused ("This idea cannot move to that status.") and the card returns.

## G. Merge and promote

- **AC-G-01** Merge modal picks the survivor (SearchableSelect of active ideas, excluding this idea
  and merged children) and calls `POST /ideas/merge`; the child then shows state E and the survivor
  lists it under "Merged ideas" (`GET /ideas/{id}/merged`).
- **AC-G-02** Unmerge calls `POST /ideas/{id}/unmerge` and restores the child's normal state.

## H. Customer track page (`/portal/ideas/{token}`)

- **AC-H-01** Route `FE/app/(auth)/portal/ideas/[token]/page.tsx` in the portal shell, no login.
  Data from CRM public routes `GET /api/v1/public/portal/ideas/{token}` and
  `GET|POST /api/v1/public/portal/ideas/{token}/comments`, proxying ss `GET /public/ideas/{token}` and
  ss `GET|POST /public/ideas/{token}/comments`.
- **AC-H-02** A token not matching `^[A-Za-z0-9_-]{16,64}$` is a 404 without calling ss; an ss 404 is
  a uniform 404; the page shows "Idea not found" / "This link is not valid."
- **AC-H-03** Responses carry `Cache-Control: no-store`, `X-Robots-Tag: noindex`,
  `Referrer-Policy: no-referrer`.
- **AC-H-04** The page shows title, status badge, idea number, submitted date, vote count READ-ONLY
  (no vote control), the idea text, and the comment thread.
- **AC-H-05** Posting a comment calls the CRM public POST; the comment shows the submitter's name
  (ss sets it server-side) with a "You" tag for the just-posted comment; a client-sent name is never
  forwarded.
- **AC-H-06** Field allow-list: the CRM public proxy forwards only `id, parentId, authorName,
  isSubmitter (authorKind == "public"), body, isDeleted, createdAt, editedAt` per comment and only
  the documented idea fields; no email, author id, phone, or internal id beyond the comment id ever
  reaches the browser. A test asserts no value containing `@` survives the proxy for a seeded email
  author name.
- **AC-H-07** Rate limit: the CRM public POST is limited per token (5 per 15 min, key = sha256 of the
  token) and by one global ceiling across all tokens (200 per 15 min) before calling ss, answering
  429 with `Retry-After` and "Too many comments. Try again later.". No per-IP bucket and no
  `X-Forwarded-For` is sent to ss: behind the CRM's nginx the left-most XFF is client-controlled
  (security review M2). Body limit 2000 characters, matching ss's public limit.
- **AC-H-08** Usable at 375px and 1280px; not-found state at both.

## J. Known ss-side gaps found by the end-of-lane browser pass (2 Oct, local ss on crew/ideation-comments)

Not CRM defects; recorded so the hand test expects them. Each is a PLAN section 13 ask.
- Deleting an idea that has an attachment fails in ss (pending action outcome "failed"; ss backlog
  BL-SS-285, attachments FK). Ideas without attachments delete fine.
- The idea page vote box does not show the viewer's own vote: ss `GET /embed/ideas/{id}` serialises
  with `voter_id=None`. List and board show it correctly.
- An idea captured from the CRM shows submitter "Unknown embed": ss embed create passes `actor=None`
  and no submitter name. Same root as AC-E-05 (ss ignores the `name` claim).

## K. Ideas list rework (owner hand test #1, 3 Oct 2026)

Mirrors the ss Ideas list: ss `service_frontend/app/(protected)/ideation/ideas/{ideas-view.tsx,use-ideas-list-config.tsx,select-idea-rows.ts}`
over `components/platform/resource-list/resource-list.tsx` (ss main `3b36e393`). Built on the CRM
standard list pieces the Customers / Contacts lists use: `components/ui/data-grid-list-toolbar.tsx`
(`DataGridListToolbar`), `components/ui/data-grid.tsx`, `components/ui/data-grid-select-column.tsx`
(`buildSelectColumn`), `components/ui/data-grid-column-header.tsx` (`DataGridColumnHeader`),
`components/ui/data-grid-column-visibility.tsx`, `components/common/ListSearchInput.tsx`,
`components/common/SearchableSelect`, `app/(protected)/scm/components/BulkActionsMenu.tsx` ("Actions"
dropdown). No new layout: no Lavish mock (owner ruling). This section supersedes AC-C-01..C-04 for
the list.

- **AC-K-01** Data: the list loads every idea once (`filter=all`) and filters, searches, sorts and
  paginates client side, as ss does (10 / 25 / 50 / 100 per page). Search (debounced) matches idea
  title and problem, submitter and product.
- **AC-K-02** Toolbar left, in order: search ("Search ideas..."), info icon (hover card "You can
  search by" Idea / Submitter / Product, aria-label "What can I search?"), Status picker ("All
  statuses" plus every status label present in the data, clearable) directly next to search, then the
  Active | Archived segmented toggle (`ToggleGroup`; Active = `statusIsArchived` false). Changing the
  toggle clears the selection.
- **AC-K-03** Toolbar right when nothing is selected: Filters (Submitter, Channel, Submitted date
  range; count badge; Clear), Export (CRM standard export: "Download Excel" of the selected rows, or
  of every row matching the current filters when the toolbar offers all-records export), Columns
  (column visibility), Capture idea (primary, in the toolbar; no longer in the page header). The List
  | Board switch stays.
- **AC-K-04** Columns in order: select checkbox (header select-all of the page), Votes (vote box),
  Idea (title clamped to 2 lines, link to the idea, "N merged" chip when `mergedCount > 0`, "Merged"
  chip on a merged child), Submitter, Channel, Product, Status (badge), Submitted (dd/MM/yyyy), row
  actions ("..." with the same actions as the bulk menu for that one row). Every data column is
  sortable from its header (asc, desc, clear); default sort Votes descending, then newest first.
  Column visibility and widths persist (list prefs key `ideation.board.view`). No "No." column (ss
  has none).
- **AC-K-05** Selecting rows replaces the right cluster with "N selected", an "Actions" dropdown,
  Export (selected rows) and Clear. The Actions dropdown is shown only to manage holders and lists,
  with ss's visibility rules:
  - Promote to BR: one or more rows, none archived; disabled when rows span more than one product;
    one call `POST /ideas/promote {ideaIds, title: first selected idea's title}`; success toast.
  - Merge: two or more rows, none archived; opens the merge dialog to pick the survivor among the
    selected ideas; `POST /ideas/merge {survivorId, ideaIds}`.
  - Unmerge: every selected row has `mergedCount > 0`; one unmerge per row.
  - "Move to <label>" when every row's next move has the same label, else "Advance to next stage":
    no row archived; disabled when any row has no `advanceTransitionId`; one status call per row.
  - Archive: no row archived; one deferred `idea.archive` per row (5 s, Cancel), no confirm.
  - Restore: every row archived and each has an outgoing transition; one status call per row.
  - Delete: always; one deferred `idea.delete` per row (10 s, Cancel), no confirm.
  After a bulk action the selection clears and the list refetches; a partial failure toasts how many
  failed.
- **AC-K-06** A user without manage sees the select column and Export but no Actions dropdown and no
  row "..." actions other than opening the idea.
- **AC-K-07** "My ideas | All ideas" is out of scope here (crew, 3 Oct): it belongs to lane
  IDEATION-CAPTURE (#1444), built on ss#111 (`mine=true` / `isMine`). This list keeps the List | Board
  switch and leaves an empty slot (`toolbarScopeSlot` prop, renders nothing by default) right after
  the Active | Archived toggle next to search, for that lane to fill.
- **AC-K-08** Empty: "No ideas match these filters." when filters or search are active, else "No ideas
  yet" + hint; load error: error state with Retry. Usable at 1280 and 375 (toolbar wraps, grid scrolls
  inside its card), light and dark.

### Captain's test list for section K (vitest, red first)
- `components/ideas/IdeasListView.test.tsx`: K-02 toolbar order (search, info, status picker,
  Active|Archived) and the hover-card text; K-02 status picker filters to one status and Active /
  Archived split by `statusIsArchived`, toggle clears selection; K-01 search matches title, submitter,
  product; K-03 Filters by channel and submitter; K-04 column headers in order, no "No." column,
  "N merged" chip, sorting Votes / Idea / Submitted toggles row order, default votes desc then newest;
  K-05 selecting rows shows "N selected", Actions, Export, Clear; each bulk action's visibility and
  disabled rules; Promote posts all selected ids with the first title; Merge opens the dialog limited
  to the selected ideas; Move to label vs "Advance to next stage"; Archive / Delete start one deferred
  action per row and no confirm dialog; selection clears after an action; K-06 no Actions for a
  view-only user; K-01 pagination 10/25/50/100.

## I. Removal and regression

- **AC-I-01** `/ideas` and `/ideas/{id}` no longer render an iframe; `IdeationEmbed.tsx` and
  `useIdeationEmbedSession.ts` are deleted; `POST /integrations/ideation/embed-session` stays (unused
  by the FE) until a follow-up removes it.
- **AC-I-02** Every screen is usable at 375px and 1280px, light and dark, with no console errors.
- **AC-I-03** No em or en dash in any changed file.

## Captain's test list (tester writes these RED first)

Backend (`sorento_crm_backend/tests/`, Postgres fixture `tests/_pg_fixture.py`, ss mocked at the
httpx boundary, never a real network call):
- `test_ideation_gateway.py`: A-01 403 without view and ss not called; A-02 token cached (one
  `/embed/session` call for two requests); A-03 401 re-mint once then 502; A-04 no token in body or
  headers; A-05 502 on timeout, 404 unconfigured; A-06 ss 403 message passthrough; A-07 name claim
  `users.name` and `Sorento staff` for blank name, never the email; A-08 each route forwards to the
  right ss path/method/body, vote body is `{"dir":"up"}`, manage routes 403 for view-only and ss not
  called; A-09 pending-action handlers call ss on commit and not on cancel.
- `test_ideation_manage_permission.py`: B-01 slug registered and granted to admin/superadmin.
- `test_public_portal_ideas.py`: H-02 bad token 404 without ss call, ss 404 uniform; H-03 headers;
  H-05 client name not forwarded; H-06 allow-list strips email/author ids (seeded email author name
  never appears); H-07 429 after 5 per token and 20 per IP with Retry-After, X-Forwarded-For forwarded.

Frontend (vitest, `sorento_crm_frontend`):
- `services/ideasService.test.ts`: every function hits the documented `/api/v1/ideation/...` path
  and method; vote always posts up; promote posts `{ideaIds, title}`.
- `components/ideas/IdeaDetail.test.tsx`: D-02 states A-E and no-manage F; D-04 menu items and
  countdown, no confirm; promote 403 toast text from the error.
- `components/ideas/IdeaComments.test.tsx`: E-01 order and one level; E-02 reply-to-reply posts the
  top-level id; E-03 edit/delete visibility from flags, countdown; E-04 placeholder; E-06 plain text.
- `components/ideas/IdeasListView.test.tsx`: C-01 Product hidden by default; C-03 vote does not
  navigate; C-05 required message.
- `components/ideas/PortalIdeaTrack.test.tsx`: H-04 no vote control; H-05 You tag; not-found state.
