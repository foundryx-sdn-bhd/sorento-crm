# PLAN: Ideas features on the Sorento CRM side (IDEA-CRM-SIDE)

Status: Build (full track, size L: gateway + native screens; no migration, no new permission).
Lane IDEA-CRM-SIDE, PR #1628. UAC: `idea-crm-side-13oct-acceptance-criteria.md`.

Owner 13 Oct: the new ideas features (ss IDEA-PORTAL #123, IDEA-CORE #124) must also work inside
Sorento CRM. Crew answer 13 Oct (owner-approved direction): native Sorento screens, not an iframe.

## 0. What exists (measured)

- There is no `IdeationEmbed.tsx` on main. Since PLAN-ideation-in-crm, Sorento renders native Ideas
  pages over a backend gateway: `BE/app/api/v1/ideation/ideas.py` calls ss `/embed/ideas*` as the
  signed-in user through `BE/app/services/ideation_gateway_service.py` (`call_ss`, embed token cached
  per user, 401 re-mint once).
- The gateway rebuilds every forwarded body from an explicit field list (`_pick`, ideas.py create
  and update), so new ss fields are dropped until whitelisted.
- `ideas_manage` is already a claim of the embed assertion (`ideation_embed_service.mint_embed_assertion`,
  `user_ideas_manage` = CRM `ideation.ideas.manage`), and the gateway cache keys on it. The ss
  portal's `canManage` derives from that claim, so brief item 1 ("send ideas_manage") needs no change.
- Comment delete is the `idea_comment.delete` pending action (`record_actions.py`), embed DELETE.

## 1. ss contract (crew, from ss #124 head b6cffdec and #123 head aac4369d; NOT merged, may move)

IDEA-CORE (`/embed`, existing embed token):
- Idea fields: `problem` (label Business problem), `desiredOutcome` (str|null), `useCases` (list[str],
  default []), `currentWorkaround` (str|null), `title` (str|null, now writable). In create and update.
- `GET /embed/ideas/{id}/similar` -> `{available: bool, ideas: IdeaOut[]}`; `available=false` = AI down,
  show Retry, no fallback.
- `POST /embed/ideas/merge` `{survivorId, ideaIds[], fields?: {fieldKey: [ideaId,...]}}`; fieldKey in
  `title|problem|desiredOutcome|useCases|currentWorkaround|rawText`. One id = that idea's value,
  several = combined in order, omitted = survivor unchanged.
- Comments and attachments read on a survivor carry `fromIdea {id, ideaNumber}` and are read-only.

IDEA-PORTAL (`/portal/ideas/*`, separate Bearer PORTAL token):
- `POST /portal/ideas/session/embed {token: <embed token>}` -> `{token, principal{kind,name,role,productId,canManage}}`, TTL 2h.
- Reads: `roadmap`, list (`q, column*, sort=top|new, mine, includeClosed, page, pageSize<=100`),
  `changelog`, `{id}`, `{id}/voters`, `{id}/activity?order`, `{id}/comments`.
- Writes: `{id}/vote` (toggle), `{id}/comments` POST, `{id}/comments/{cid}/like`, `{id}/hidden` PUT
  (manager), `{id}/note` PUT (manager), `{id}/comments/{cid}` DELETE.
- `useCases` is a STRING in #123 today (crew asked IDEA-PORTAL to align on list[str]); the CRM reads
  both shapes.

## 2. Design

### Backend (gateway only)

1. `ideas.py` create and update whitelists add `title, desiredOutcome, useCases, currentWorkaround`.
   The legacy `proposedSolution, impact, department` stay forwardable (old clients, chatbot) but the
   CRM UI no longer sends them.
2. `GET /api/v1/ideation/ideas/{id}/similar` (view) -> relay ss `/embed/ideas/{id}/similar`.
3. Merge forwards `fields`, validated in the gateway: a dict whose keys are in the six-key set and
   whose values are non-empty lists of UUIDs; anything else is a 422 before ss is called.
4. Portal gateway: `ideation_gateway_service.call_portal(db, user, method, path, ...)` exchanges the
   user's embed token at `POST /portal/ideas/session/embed`, caches the portal token by the same key
   and claims as the embed token until 60 s before its 2 h TTL (expiry read from the response when
   present, else 2 h), and on a portal 401 drops BOTH cached tokens, re-exchanges once and retries
   once. Same error mapping as `call_ss`.
5. Router `BE/app/api/v1/ideation/portal.py` mounted under `/api/v1/ideation/portal`:
   - view (`ideation.board.view`, reads accept api key like the ideas reads):
     `GET roadmap` (`mine`), `GET ideas` (`q, column[], sort, mine, includeClosed, page, pageSize`),
     `GET changelog`, `GET ideas/{id}`, `GET ideas/{id}/voters`, `GET ideas/{id}/activity` (`order`),
     `GET ideas/{id}/comments`.
   - view, staff session: `POST ideas/{id}/vote`, `POST ideas/{id}/comments` (`body, parentId`),
     `POST ideas/{id}/comments/{cid}/like`.
   - manage, staff session: `PUT ideas/{id}/hidden` (`hidden` bool), `PUT ideas/{id}/note` (`note` str <= 500).
   - Query params forwarded from an explicit list; every path id a UUID; `productId` never forwarded
     (the connection is scoped to the workspace's product). Comment author email masked as in ideas.py.
   - Portal comment delete reuses the `idea_comment.delete` pending action (same comment rows).
   - Portal create is not proxied: the CRM's Capture modal (embed create, now with title and the new
     fields) is the one capture path.

### Frontend

1. `types/ideas.ts`: `Idea` gains `desiredOutcome`, `useCases: string[]`, `currentWorkaround`;
   `normalizeUseCases(value)` turns a string (newline-split, trimmed, blanks dropped) or a list into
   `string[]`; the service applies it to every idea it returns. Portal types added.
2. Capture modal and detail Details tab: Title, Business problem (required), Desired outcome,
   Use cases (textarea, one per line -> list), Current workaround, Original message. Proposed
   solution / Impact / Department hidden (slice 1, done). View and edit same order.
3. Detail tabs: Details, Similar ideas, Attachments. Similar ideas lists `ideaNumber`, title (link
   to `/ideas/{id}`), status badge, upvotes; empty state "No similar ideas"; `available=false`
   shows "Similar ideas are unavailable right now" with a Retry button. A manager sees a "Merge"
   button per similar row that opens the merge modal preloaded with both ideas.
4. Merge modal (replaces the single-select modal): pick ideas to merge (SearchableMultiSelect,
   preloaded with the current idea), pick the one to keep (radio per column header), then a grid:
   one column per idea, one row per field (Title, Business problem, Desired outcome, Use cases,
   Current workaround, Original message), a checkbox per cell. Default: survivor's cells ticked.
   Submit sends `fields` only for rows whose ticks differ from survivor-only. Bulk merge dialog in
   the list keeps its behaviour but opens the same modal.
5. Portal: sidebar Ideas becomes a group with `All ideas` (`/ideas`) and `Roadmap` (`/ideas/portal`),
   same permission. `/ideas/portal` page with line tabs Roadmap / Feedback / Changelog (`?tab=`):
   Roadmap = one card column per status column with count; Feedback = search + sort (Top / New) +
   Mine toggle list; Changelog = delivered items with date and note. A card opens
   `/ideas/portal/{id}`: header (number, title, status badge, vote box with count), Business
   problem / Desired outcome / Use cases / Current workaround, timeline, voters, comments
   (reply, like, delete via the deferred action), activity; managers see the Hide from portal
   switch and the changelog note (edit in place, Save).

## 3. Out of scope

- ss changes, the ss portal's own public product picker, portal create (Capture covers it).
- Any new CRM permission: view = `ideation.board.view`, manager = `ideation.ideas.manage`.

## 4. Risks

- ss #123/#124 are unmerged: field and route names may move; crew notifies. The gateway names are
  each in one place (the whitelist tuples and the portal route table).
