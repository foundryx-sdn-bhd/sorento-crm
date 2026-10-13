# UAC: IDEA-CRM-SIDE (Ideas features on the Sorento CRM side)

Plan: `PLAN-idea-crm-side-13oct.md`. Each AC is pinned by a test (pytest `tests/test_ideation_crm_side.py`,
vitest next to the component) unless marked browser.

## A. Gateway (backend)

- AC-A-01 `POST /api/v1/ideation/ideas` forwards `title, problem, desiredOutcome, useCases,
  currentWorkaround, rawText` to ss and drops any other field a client sends (e.g. `productId`, `foo`).
- AC-A-02 `PATCH /api/v1/ideation/ideas/{id}` forwards the same fields; unknown fields dropped.
- AC-A-03 `GET /api/v1/ideation/ideas/{id}/similar` needs `ideation.board.view` (403 without) and
  relays ss's `{available, ideas}` unchanged; a non-UUID id is 422 with no ss call.
- AC-A-04 `POST /api/v1/ideation/ideas/merge` forwards `fields` when it is a dict of allowed keys to
  non-empty UUID lists; an unknown key, an empty list, a non-list value or a non-UUID id is 422 and
  ss is not called; without `fields` the body is `{survivorId, ideaIds}` only.
- AC-A-05 Portal session: the first portal call exchanges the embed token at
  `POST /portal/ideas/session/embed` with `{token: <embed token>}`; the second call reuses the cached
  portal token (one exchange); calls carry `Authorization: Bearer <portal token>`.
- AC-A-06 A portal 401 drops the portal token, re-exchanges once and retries once; a second 401 is
  a 502 "The Ideas workspace isn't reachable right now."
- AC-A-07 Portal reads (`roadmap`, `ideas`, `changelog`, `ideas/{id}`, `voters`, `activity`,
  `comments`) need `ideation.board.view`; only the listed query params reach ss (`productId` never).
- AC-A-08 `vote`, `comments` POST, `like` need view and a staff session; comment author emails are
  masked to "Sorento staff".
- AC-A-09 `hidden` and `note` need `ideation.ideas.manage` (403 without, ss not called); `hidden`
  must be a bool and `note` a string of at most 500 chars, else 422.
- AC-A-10 A deployment without the connection configured answers 404 on every portal route.

## B. Fields (frontend)

- AC-B-01 Capture modal shows Title, Business problem (required, "Business problem is required"),
  Desired outcome, Use cases, Current workaround, Original message, Attachments; no Proposed
  solution / Impact / Department; Use cases typed one per line are sent as a list with blanks dropped.
- AC-B-02 Detail Details tab shows the same fields in the same order in view and edit; Save sends
  `title, problem, desiredOutcome, useCases, currentWorkaround, rawText` and never the retired fields.
- AC-B-03 An idea whose `useCases` arrives as a string (ss #123 shape) shows one line per use case.

## C. Similar ideas (frontend)

- AC-C-01 Detail has a "Similar ideas" tab listing number, title (link to `/ideas/{id}`), status and
  upvotes in the order ss returns.
- AC-C-02 No similar ideas: heading "No similar ideas" with a hint and no button.
- AC-C-03 `available=false`: "Similar ideas are unavailable right now" and a Retry button that refetches.
- AC-C-04 A manager sees "Merge" on each row; it opens the merge modal with both ideas.

## D. Merge (frontend)

- AC-D-01 The merge modal shows one column per selected idea and one row per field (Title, Business
  problem, Desired outcome, Use cases, Current workaround, Original message), with a checkbox per cell.
- AC-D-02 By default the survivor's column is ticked and submit sends `{survivorId, ideaIds}` without `fields`.
- AC-D-03 Ticking cells of another idea sends `fields` with exactly the changed rows, ids in column order.
- AC-D-04 A row with no tick at all blocks submit with "Pick at least one value for <field>".

## E. Portal (frontend)

- AC-E-01 Sidebar: Ideas group with "All ideas" (`/ideas`) and "Roadmap" (`/ideas/portal`), both
  gated by `ideation.board.view`.
- AC-E-02 `/ideas/portal` has line tabs Roadmap, Feedback, Changelog; the tab is in `?tab=` and
  survives refresh.
- AC-E-03 Roadmap renders one column per ss column with its title and count, cards linking to
  `/ideas/portal/{id}`.
- AC-E-04 Feedback: search, Top/New sort and Mine toggle are sent to the service; empty state
  "No feedback yet".
- AC-E-05 Changelog lists delivered items with date (`dd/MM/yyyy`) and note; empty state "Nothing shipped yet".
- AC-E-06 Card page shows number, title, status, vote box (toggles via the portal vote), the four
  fields, timeline, voters, comments with reply and like, and activity.
- AC-E-07 A manager sees "Hide from portal" switch and the changelog note editor; a non-manager sees neither.
- AC-E-08 Comment delete is the 10 s deferred countdown (`idea_comment.delete`), no confirm.
- AC-E-09 Usable at 375px and 1280px (browser).
