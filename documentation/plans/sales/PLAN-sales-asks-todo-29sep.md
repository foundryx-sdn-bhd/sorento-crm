# PLAN: sales asks as a salesperson's to-do list, date-first (lane SALES-ASKS-TODO)

Status: built to the Lavish-approved mockup (round 3, 168e1e2e) 29 Sep 2026; S3 reshape green, Phase 3 reviewed (security B1 + reviewer items fixed), browser-verified (7d); owner hand test PASSED 29 Sep 2026 on 192fc7bc; permissions re-granted by explicit role list (3.4) after the dev-DB gap; awaiting CI and merge. Track: full. The plan was first written

**Amended 1 Oct 2026 (`../chatbot/PLAN-customer-asks-refer-only-1oct.md`, owner ruling):** the `Needs attention` / `Today` split is gone: one `Open` section, oldest first by default, each card showing its own date; `Done today` stays. The Agent select reads `CODE · N open` (`needs_attention` dropped from `GET /api/v1/sales/customer-asks/agents`). Text below that describes the split is historical.
under the recommendations and each pending question is marked `[Q<n> pending]` where its answer
changes the design. Track: full (one migration, two new routes under RBAC, one portal route).
Plan created: 2026-09-29T08:20:00Z
Domain: sales (the salesperson's own surface). The rows are CORE (`stock_asks` in `public`, from
#1333); the CRM page rides on the `sales` module menu, the portal page on the existing
`customer_asks` landing kind.
UAC: `sales-asks-todo-29sep-acceptance-criteria.md` (same folder).
Builds on: PR #1333 (stock ask v2 S5 asks table + CRM Asks tab, S6 portal Customer asks), merged
to main 29 Sep 2026 (f4f70531); branch `crew/sales-asks-todo` is on origin/main from that commit.
Sibling: lane CONTACT-CUSTOMERS (contact <-> customers, agent -> customers), PR #1366. Crew ruling
29 Sep 2026 (Q6, coordination): build on `customers.sales_agent_id` now behind the one
`_agent_scope` function; swap to #1366's relation when it lands. Section 4.
Main note (29 Sep 2026): origin/main f4f70531 carries TWO alembic heads,
`chatbot_picker_domain_1352` (#1353) and `sa2_0005_stock_ask_source` (#1333), both chained on
`cpc4_cost_packaging_method`. Crew ruling (29 Sep 2026): lane ALEMBIC-JOIN adds the merge revision
`merge_29sep_batch6`; this lane adds no join of its own. That join merged as #1367 (main
7c2e4ee6, merged into this branch), so `sat_0001_stock_ask_done` chains on `merge_29sep_batch6`.

## 0. Owner words (29 Sep 2026, follow-up from PR #1333, verbatim and binding)

> "I think now we got the sales ask, good, but I need it to be more of a to-do list, like the
> salesperson can come in, see oh today my customer ask so many things, let me clear them off
> 1 by 1, so date is very important, I can know which one needs my attention, it is a list that
> requires my attention, basically this needs to be a sales tool for salesperson to see what
> their customer is asking."

Read as five requirements: (R1) the salesperson's own list, of their customers' asks; (R2)
date-first: what came in today, and what has been waiting; (R3) clear off one by one: one action
per row, the row leaves the list; (R4) counts of what is open and what needs attention; (R5) quick
to scan on a phone and on a desktop.

## 0b. Owner rulings (29 Sep 2026, relayed by crew; binding, they override the recommendations)

- **Q1 (c)**: one shared component on both the portal `Customer asks` kind and a CRM page.
- **Q2 (a)**: one CTA `Done` per row, `Reopen` to undo, note optional.
- **Q3 (a)**: date-first groups: `Needs attention` pinned, `Today`, `Yesterday`, earlier days; done
  behind `Show done`; counts header. AND the sorting must be customizable and remembered per
  contact / user, exactly like the existing remembered DataGrid listing feature (sorting + column
  reordering persisted); reuse that mechanism. Design in 3.2 and 3.3.
- **Q4 (a)**: overdue = still open from before today (Malaysia time).
- **Q5 (a)**: ALL four branches, `incoming` and `console` rows included.
- **Q6 (a)** (crew): `customers.sales_agent_id` behind `_agent_scope`; swap to CONTACT-CUSTOMERS (#1366) later.
- **Q7 (c)**: the manager CRM page with the Agent filter AND team leaders see only their team
  (`sales.teams.leader_sales_agent_id`). Design in 3.4.
- **Q8 (a)**: no digest.
- Order: update the mock, re-file the hand test when the mock reflects these, then build.

## 0c. Lavish rulings on the mockup (29 Sep 2026, binding; the approved mock is the contract)

Round 1 (owner, verbatim points): (1) "it is not reusing the component from other forms like
price tag request": reuse the Price Tag Request portal list: Filter button, Sort button
(Created), list / card view toggle, same card shell; sorting remembered per user like the
remembered DataGrid listing. (2) "this card too many info already, only need to know the
customer, what he asked and what the system answered, and the datetime, don't need the Sent or
what". (3) "the note can be saved?": explicit, obvious note saving. (4) "can open the card to view
the chat history": opening a card shows the conversation, the chat history panel in a less
technical way. Round 2: no counts line; no relative age label; no Open link, the whole card opens
the chat panel; the list view is the system DataGrid. Round 3: Done is the RIGHTMOST column; ONE
Needs attention section (not one per day); the opened card's Asked / Answered block jumps to the
exact chat bubble of that ask. Approved as round 3.

## 1. Journey

Actor: a salesperson (a `sales_agents` row) whose WhatsApp contact is linked to that agent
(`sales_agents.contact_id`). They arrive from the WhatsApp message S4 sent them ("Stock ask -
Hock Lee Trading asked about SRT5674 ...") or from habit, first thing in the morning.

1. **Arrive.** They open the portal (`/portal/c/<slug>` -> Customer asks tab) or, when they hold
   a CRM user linked to the same contact, Sales > My customer asks. The system already knows who
   they are (portal token -> contact -> agent; CRM user -> `users.respond_contact_id` -> agent),
   which customers are theirs (`customers.sales_agent_id`), and every ask the chatbot answered for
   those customers (`stock_asks`). Nothing is asked of them.
2. **See the day.** The first screen is the counts line, `Open 7 · Needs attention 2 · Done
   today 3`, then the groups: `Needs attention` (open, asked before today, oldest first) pinned at
   the top, then `Today` (newest first), then `Yesterday`, then one group per earlier day only
   when it still has open rows. Each row is one ask: customer, contact, product x quantity, the
   branch badge, the chatbot's answer line, the time asked (with "2 days ago" on the old ones),
   and one `Done` button.
3. **Clear one.** They call the customer, then tap `Done`. The row fades out of its group, the
   counts move (`Open 6 · Done today 4`), and the row appears under `Done today` at the bottom,
   greyed, with `Reopen`. No dialog, no page reload. An optional note stays editable on the row
   before or after (the `note` #1333 already has).
4. **Repeat** until `Open 0`. The empty state reads "Nothing waiting. Asks your customers make on
   WhatsApp land here." with the day's cleared rows still under `Done today`.
5. **Look back (optional).** `Show done` reveals every done ask, newest first, paged, the #1333
   list; nothing else changes.
6. **The office** keeps the customer's Asks tab (#1333 S5) with the same rows: a `done` set on
   either side shows on the other, with who did it and when.
7. **The sales admin / owner** opens Sales > Customer asks: every agent's list in the same shape,
   an Agent filter and open / needs-attention counts per agent. `[Q7 pending]`

What the actor never decides: which customers are theirs, what "overdue" means, what order the
rows come in, whether a `Done` needs a reason.

## 2. What exists today (measured on the lane branch, #1333 merged in; file:line)

- `stock_asks` (`sorento_crm_backend/app/models/stock_ask.py:39-69`): customer, contact,
  product, quantity, branch, `answer_summary`, `notified_agent` + `notify_skip_reason`, `state`
  (`open | done`, :63), `note`, `source`, `created_at`, `updated_at`. No `done_at`, no "done by".
- Agent scope: `_agent_scope` (`app/services/stock_ask_service.py:588-596`) =
  `customers.sales_agent_id == agent.id`. Agent from a portal contact: `sales_agent_for_contact`
  (`app/services/price_tag_request_service.py:109`) -> `app/services/sales/portal_agent.py:19`.
- Lists: `_page` (`stock_ask_service.py:525-535`) newest first, paged; `list_for_agent`
  (:599-634) with `q` and `state`; `update_for_agent` (:637-644) and `_apply_update` (:538-546)
  set state and note. CRM: `list_for_customer` / `update_for_customer` (:559-585).
- Routes: portal `GET/PATCH /api/v1/public/portal/customer-asks[/{id}]`
  (`app/api/v1/public/portal_customer_asks.py:53-75`, gate `_agent_id` :37-51: linked agent AND
  the per-contact `customer_asks` switch); CRM `GET/PATCH
  /api/v1/order-management/customers/{id}/asks[/{ask_id}]`
  (`app/api/v1/order_management/customers.py:145-172`).
- Serializer: `serialize` (`stock_ask_service.py:471-522`), names never ids; schema
  `StockAskResponse` / `StockAskUpdate` (`app/schemas/stock_ask.py:10-34`).
- Portal FE: `customer_asks` landing kind; `PortalLanding.tsx:383-387` reads the open count for
  the badge (`limit=1, state=open`), :667-668 renders `CustomerAsksList` as the tab body
  (`app/(auth)/portal/components/CustomerAsksList.tsx:44-295`, cards or grid, State filter);
  cells `components/stock-asks/AskEditCells.tsx:15-103`; words `lib/stock-asks.ts`.
- CRM FE: `CustomerAsksTab.tsx` (DataGrid), `services/stockAskService.ts`, hooks
  `useCustomerAsks.ts`.
- CRM user -> salesperson: `users.respond_contact_id` (`app/models/user.py:72`) ->
  `sales_agents.contact_id` (`app/models/sales_agent.py:96`). No "agent for user" helper exists;
  `app/api/v1/sales/analysis.py:348` does the reverse join by raw SQL.
- Sales module: menu `config/menu.config.tsx:78-118` (Targets, Opportunities, Sales Teams, Sales
  Agents, Yearly comparison; `moduleKey: 'sales'`); permissions `_crud("sales", ...)`
  (`app/rbac/permission_registry.py:829-834`); routes `app/api/v1/sales/*.py`.
- To-do precedent in the CRM: `project-sales/my-tasks/components/MyTasksClient.tsx:14-20`
  (bucketed by urgency, `PageHeader`, one row per task).
- Malaysia wall clock: BE `_MALAYSIA` (`stock_ask_service.py:40`, `asked_at_label` :51); FE
  `formatDateTimeInMalaysia`, `formatDateInMalaysia` (`lib/helpers.ts:330, 451`).

## 3. Design

### 3.1 The rows: two columns on `stock_asks` (migration `sat_0001_stock_ask_done`)

```
stock_asks
  done_at             TIMESTAMP NULL   when state last became done (naive UTC, like created_at)
  done_by_user_id     VARCHAR NULL     FK users.id ON DELETE SET NULL: the CRM user, or the user
                                       linked to the portal contact (users.respond_contact_id)
  done_by_contact_id  TEXT NULL        FK respond_contacts.id ON DELETE SET NULL: the portal
                                       contact, when the write came through the portal
```

The audit actor contract (`PLAN-unified-identity-26sep.md` section 8.3, PR checklist "Who did
this") rules the shape: a "who did this" column is `<verb>_by_user_id`, never a name; a column a
portal route writes also gets `<verb>_by_contact_id`, and a contact who holds a user is recorded
as both. (An earlier draft of this plan proposed a name snapshot; retired 29 Sep 2026 before any
code.)

Set in ONE place, `_apply_update(db, ask, data, *, actor_user_id=None, actor_contact_id=None,
now=None)` (`stock_ask_service.py:538`): a transition to `done` stamps `done_at = now` and the
two actor ids as given; a second `done` leaves them; a transition to `open` clears all three; a
`note`-only PATCH touches none. Every route (portal cells, CRM Asks tab, the new to-do buttons)
goes through it. The portal route passes `actor_contact_id = token.contact_id` and, when a user
is linked to that contact, `actor_user_id` too (one query on `users.respond_contact_id`); the CRM
routes pass `actor_user_id = current_user["id"]`. Backfill: rows already `done` get `done_at =
updated_at`, both ids NULL (shown as "Done", no name). `[Q2 pending: (b) adds a `contacted` state
to the CHECK; (c) makes the note required on done]`

`StockAskResponse` gains `done_at` and `done_by`, a LABEL resolved by `serialize` (the user's
name when `done_by_user_id` is set, else the contact label, else None); no actor id is on the
wire (no UUIDs in the UI). Asserted in a test: `response_model` drops undeclared fields.

### 3.2 The to-do read: one payload, grouped on the client from one server boundary

`GET /api/v1/public/portal/customer-asks/todo` (portal, same gate as the list) and
`GET /api/v1/sales/customer-asks/todo` (CRM, section 3.4) return the same shape:

```
{
  "today_start": "2026-09-28T16:00:00Z",   Malaysia midnight of today, as UTC
  "open":       [StockAskResponse, ...],   state open, EVERY branch (Q5 (a)), oldest first,
                                           cap 500, `truncated: bool`
  "done_today": [StockAskResponse, ...],   state done, done_at >= today_start, newest first
  "truncated":  false
}
```

`service.todo_for_agent(db, agent_id, now)`: the same `_agent_scope` as #1333, every branch
(Q5 (a): no branch filter; an `incoming` row is a to-do like the others and keeps its badge),
`today_start` computed once from `_MALAYSIA` (the server owns the day boundary; the FE never
guesses a timezone). No paging: a salesperson's open asks are tens, and a to-do list with a
"next page" is not a to-do list; the cap and `truncated` flag are the guard, shown as "Showing
the oldest 500 open asks".

FE `lib/stock-asks-todo.ts`: `bucketTodo(payload, sort)` (pure, unit-tested) -> `{ counts: { open,
needs_attention, done_today }, sections: [{ key: 'needs_attention' | 'today', label, days: [{ key:
'<yyyy-mm-dd Malaysia>', label: 'Yesterday' | 'Sat 27 Sep', asks }] }] }`. Q3 (a) + Q4 (a): the
`Needs attention` section is pinned first and holds every open ask asked before `today_start`,
split into one day group per Malaysia date, oldest day first; the `Today` section holds the rest
under one day group. A day group with no open row is not rendered. `sort` is `{ id: 'asked_at'
| 'customer' | 'product' | 'branch', desc: boolean }` and orders the rows INSIDE every day group
(default `asked_at` ascending, so the oldest waits at the top). One rule, one function, both
mounts.

**Remembered sort (Q3 (a), "exactly like the remembered DataGrid listing feature").** The
mechanism reused is the per-user per-listing view preference row `user_list_column_configs`
(`app/models/user.py:745`), whose payload already carries `sorting: [{id, desc}]`
(`UserListColumnConfigPayload.sorting`, `app/schemas/list_query.py:181`, validated as
`ListSortEntry`) behind `GET|PUT /api/v1/list-query/column-config/{listing_key}`
(`documentation/reference/LISTING-COLUMN-PREFERENCES.md`), and the FE hook that reads and
debounce-writes it, `lib/listing-column-preferences/useListingViewPreferences.ts` (the hook the
DataGrid listings use for their remembered sort and filter). The CRM mount calls that hook with
listing key `sales.customer_asks.view::todo` and maps its `sorting[0]` to the Sort select; no new
table, no new endpoint. The portal has no user row to key that table on (the #1333 grid already
passes `listingKey={null}` for that reason), so the portal mount remembers the same `{id, desc}`
under the portal's own per-contact remembered-preference pattern, `localStorage` keyed by the
contact id exactly as the landing's default tab is (`PortalLanding.tsx:312-333`,
`sorento.portalDefaultTab.<contact_id>`; here `sorento.portalAsksSort.<contact_id>`). A
server-side per-contact preference is not built: the trigger is a salesperson using two devices
who asks for it. Column reordering does not apply to the to-do (its rows are not columns).

### 3.3 The to-do surface: one component, two mounts

`components/stock-asks/AskTodoList.tsx` (shared, like `AskEditCells`): props `{ payload,
loading, error, onDone(askId), onReopen(askId), onNote(askId, note), showAgent?: boolean }`.

- Counts line at the top: `Open N · Needs attention M · Done today K` (plain text with the
  numbers in `tabular-nums`; the M turns `text-destructive` when > 0), with the Sort
  `SearchableSelect` (not clearable, one required value) on its right: `Oldest first`, `Newest
  first`, `Customer A to Z`, `Product A to Z`, `Branch`; the mount owns persistence (3.2).
- Sections as headed blocks: `Needs attention` (red) with its day sub-headings (`Yesterday`,
  `Sat 27 Sep`, ...), then `Today`; each day a `<ul>` of rows.
- A row: line 1 customer name (bold) and the contact name; line 2 `SRT5674 x 50` and the
  branch `Badge`; line 3 the answer summary (truncate + title); line 4 the time asked
  (`formatDateTimeInMalaysia`) with the age ("2 days ago") on `Needs attention` rows, the
  `Console` badge on console rows, and the `Notified` badge; right column (or below at 375px):
  `Done` (primary, `size="sm"`) and the note input from `AskEditCells`. `showAgent` adds the
  agent code on line 1 (CRM manager page).
- `Done today` section at the bottom: the same rows greyed (`opacity-60`), `Done by <name>
  <time>`, `Reopen` (outline). Empty when none.
- Empty state (no open, no done today): icon, "Nothing waiting", hint "Asks your customers make
  on WhatsApp land here." No button (one CTA per page rule; there is no CTA here at all: asks
  come from the chatbot).
- `Show done` (a `Button variant="ghost"` at the foot) toggles the #1333 list (`CustomerAsksList`
  in list view with `state=done`) under the to-do, so history stays one tap away and the to-do
  stays a to-do.
- Motion: a done row leaves with `motion.ts`'s fade preset (reduced-motion: none); nothing else
  animates. No skeleton pulse beyond the existing `Skeleton`.
- 375px: one column, buttons full width under the row text; 1280px: row text left, actions
  right (`sm:grid-cols-[1fr_auto]`). No horizontal scroll, no DataGrid (a to-do is not a grid).

Portal mount: `CustomerAsksList` becomes the to-do (`AskTodoList` fed by
`customer-asks-service.ts` -> `/customer-asks/todo`), the State filter and cards/list toggle go
(the to-do has its own sections; `Show done` replaces the filter). The landing badge keeps
reading the open count. Ruled Q1 (c): both mounts.

CRM mount: `app/(protected)/sales/customer-asks/page.tsx` -> `MyCustomerAsksClient` with
`PageHeader` title "Customer asks", the same `AskTodoList`. Menu entry Sales > Customer asks
(`moduleKey: 'sales'`, permission `sales.customer_asks.view`). Hooks `useCustomerAsksTodoQuery`
/ `useAskDoneMutation` (invalidate + toast) over `services/stockAskService.ts` ->
`/api/v1/sales/customer-asks/todo` and `PATCH /api/v1/sales/customer-asks/{id}`.

### 3.4 CRM routes and scope (`app/api/v1/sales/customer_asks.py`)

- `GET /api/v1/sales/customer-asks/todo?agent_id=` and `PATCH
  /api/v1/sales/customer-asks/{ask_id}` `{state, note}`, both under `sales.customer_asks.view`
  (`_crud("sales", "customer_asks", "Customer Asks")`, `.edit` gates the PATCH), migration
  grants `.view` + `.edit` to every role holding `sales.opportunities.view` and to `admin`
  (the `sales_0003_opportunities` sweep shape), integration roles excluded. `view_all` goes to
  `admin` and `superadmin` ONLY among the sweep (security review B1, 29 Sep 2026: sweeping it
  onto the salesperson roles would have made every salesperson a manager and voided Q7 (c)).
  **Owner check-in 29 Sep 2026 (the sweep reached only admin on dev, where nobody else holds
  `sales.opportunities.view`): grants are an EXPLICIT role list, in the migration and in the
  crew-migration SQL, roles absent on an install skipped:**

  | role | view + edit (own list) | view_all (every agent) |
  | --- | --- | --- |
  | `salesperson` | yes | no (a team leader reaches their team by leading it) |
  | `director`, `project_sales_manager`, `project_sales_coordinator`, `customer_service` | yes | yes |
  | `admin`, `superadmin` | yes | yes (they bypass the check anyway) |
  | `guest`, `portal_user`, `purchasing`, `integration_*` | no | no |

  Posted as a crew-ask for the owner to confirm; any other role is granted by hand in Roles.
- "Me": `agent_for_user(db, user)` (new, `app/services/sales/portal_agent.py`, beside
  `agent_for_contact`): `users.respond_contact_id` -> `agent_for_contact`. Without `agent_id`
  the list is mine; a user linked to no agent gets `{open: [], done_today: [], ...,
  agent: null}` and the page shows "You are not linked to a sales agent" (no 403: the page is
  still theirs to open).
- Who else the caller may look at (Q7 (c)), computed once per request as the caller's
  **pickable agents**: with `sales.customer_asks.view_all` (one extra slug, registered beside the
  `_crud` four, granted with them) every agent; else, when the caller's own agent is
  `leader_sales_agent_id` of one or more active `sales.teams` rows, the CURRENT members of those
  teams (`sales.team_members.valid_to IS NULL`, the same predicate `team_service.list_teams`
  uses at `app/services/sales/team_service.py:348`) plus the leader themself; else nobody but
  themself. `agent_id` given: must be pickable, else 403 (`NOT_YOUR_AGENT`); an id that is no
  agent at all is 404. `agent_id=all` = every pickable agent's rows, each carrying `agent_code`.
  A leader is never granted a slug for this: leading a team IS the grant. Security review nits
  recorded as triggers, not built: a `view_all` caller may name another tenant's agent id and
  get its code back (every `sales_agents` row is shared today; filter by `company_id` when
  tenant-owned agents arrive); a future-dated team move counts from the day it is written, the
  same as `team_service.list_teams` (a `valid_from <= today` predicate when a leader complains).
- PATCH scope = the view scope (Q7 (c), ruled 29 Sep after the coder flagged that a leader
  could see a member's row but not clear it): the ask's customer's agent is one of my pickable
  agents, which is mine, or a current member of a team I lead, or anyone with `view_all` (the
  office marking on
  an agent's behalf); otherwise 404, never 403 (no id probing). Actor label for `done_by`: the
  user's full name.

Manager and leader page: the same `page.tsx` with an `Agent` `SearchableSelect` (clearable,
`All agents` as its first option) fed by `GET /api/v1/sales/customer-asks/agents` ->
`[{agent_id, code, name, open, needs_attention}]`, which lists the caller's pickable agents
(view_all: every agent with at least one open ask; a leader: every current member of their
teams, counts included even when 0; neither: `[]`). The select renders only when that list is
non-empty, so one rule (the pickable set) drives both the API and the screen. `showAgent` on the
list when an agent other than mine, or all, is chosen.

### 3.5 Portal route

`GET /api/v1/public/portal/customer-asks/todo` in `portal_customer_asks.py`, same `_agent_id`
gate, `todo_for_agent`. PATCH stays the #1333 one (`update_for_agent`), now stamping `done_by`
with the contact's label (`_contact_label`). Nothing else on the portal changes.

### 3.6 The reshape to the approved mock (replaces 3.3's list; 3.2's payload and 3.4's scope stay)

**What stays from the build before the pause:** the payload (3.2; `today_start`, `open`,
`done_today`, `truncated`, `agent`), the routes and scopes (3.4, 3.5, plus AC-ST105b from
section 4), the done stamps (3.1), the Done by column on the office Asks tab and the portal
history, the sort persistence (3.2: CRM `useListingViewPreferences` under
`sales.customer_asks.view::todo`, portal `localStorage` per contact), the Agent select.

**What changes on screen (both mounts, one shared component set):**

- **Toolbar** = the portal landing's own `LandingToolbar` (`app/(auth)/portal/components/
  LandingToolbar.tsx`: Filter popover with an active count, Sort dropdown, `ListBoardViewToggle`),
  fed by the landing's field contract (`landing-fields.ts`). Asks are adapted to the
  `PortalSubmissionSummary` shape the toolbar's helpers take (`askToSummary`, pure: `title` =
  `CODE x Q`, `customer_name`, `created_at`, `status` = `open | done`, plus `contact_name`,
  `answer` and `branch` as extra keys the asks fields read). Fields for this kind: Customer
  (text), Answer (text over the branch label), Asked (date), State (status). Sort options: Created
  (default, oldest first), Customer, Product, Answer; the choice is the remembered sort. The CRM
  page imports the same toolbar. No New button.
- **Groups** (Q3 as re-ruled): one `Needs attention` section (open, asked before `today_start`,
  oldest first), then `Today`, then `Done today` (greyed, Reopen). No counts line, no day
  sub-headings, no age label, no branch / notified / console badge. `bucketTodo` keeps its
  section shape with one day per section.
- **Card** = the landing card shell extracted from `PortalLanding.tsx`'s `SubmissionCard`
  (tint, the top-right slot, the press wiring: tap or Enter opens, long-press is unused here)
  into a shared shell both use, so the two cards stay one component. Content: line 1 customer
  (bold) + contact name; `Asked: CODE x Q`; `Answered: <the answer sentence>` (`askAnswerText`:
  `answer_summary` with its `CODE x Q:` prefix removed and the first letter upper-cased); the
  datetime (`formatDateTimeInMalaysia`). The slot holds `Done` (or `Reopen` on a done card); a
  click on the button never opens the card. Whole card opens the conversation panel.
- **List view** = the system `DataGrid` on both mounts (fixed layout, resizable, `listingKey`
  `sales.customer_asks.view::todo` on the CRM, `null` on the portal, which has no user row):
  columns Asked at, Customer, Contact, Asked, Answered, then on the CRM Agent (only when more
  than one agent is shown) and Done by, and Done / Reopen as the RIGHTMOST column. The three
  groups are section rows (a full-width row per group) so the date-first order survives; a
  header sort orders inside a group. Row click opens the conversation panel.
- **Opened card** = portal: the `Drawer` (vaul, bottom sheet); CRM: the `Sheet` (right side).
  Content in order: header (customer, contact and phone, asked at; CRM adds the agent code);
  the Asked / Answered block, a button labelled "Jump to message" that scrolls the conversation to
  that ask's bubble and flashes it; `Conversation`: the messages around the ask (see the
  endpoint), inbound left on `muted`, outbound right on `primary` soft, the ask's answer bubble
  tagged "This ask", a day separator; `Show the whole day`; CRM only: `Open in Conversations`
  (the existing chat history page for that contact); `Note`: textarea + `Save note` button +
  "Saved <time>" line (explicit save, never on blur); foot: `Done` (or `Reopen`). No turn ids, no
  parser output, no delivery status.
- **Portal `Show done`** stays at the foot (the history grid with its Done by column); the State
  filter set to Done shows the same history in place. The CRM has no history (deviation stands).

**The one new endpoint, the conversation around an ask** (core, `stock_ask_service`):
`GET /api/v1/public/portal/customer-asks/{ask_id}/conversation` and
`GET /api/v1/sales/customer-asks/{ask_id}/conversation`, both `?whole_day=false`, under the same
scope as the PATCH (404 outside it) -> `{ "messages": [{ "id", "direction": "in" | "out",
"text", "at" }], "ask_message_id": <id | null>, "contact_id": <respond_contacts.id, for the CRM
"Open in Conversations" link only> }`. Source: `chat_histories`
(`app/models/chat_history.py:21`: `contact_id`, `type` = `incoming | outgoing`, `message`,
`sent_at`), for the ask's contact: `chat_histories.contact_id` holds the Respond.io contact id, so the ask's
`contact_id` (a `respond_contacts.id`) is resolved to that row's `respond_io_id` first, exactly as
`conversation_thread_service.py:309` filters (`ChatHistory.contact_id == contact.respond_io_id`);
a contact with no `respond_io_id` answers an empty list. Window: `sent_at` within 30
minutes either side of the ask's `created_at`, oldest first, at most 60 rows; `whole_day=true`
widens to the ask's Malaysia calendar day (at most 200 rows). `ask_message_id` = the outgoing
row nearest after `created_at` whose `message` contains the ask's `answer_summary` line, else
the nearest outgoing row after `created_at`, else null. Nothing else of the row is on the wire
(no Respond ids, no result set, no latency). An ask with no contact answers an empty list.
Security review of S3 (29 Sep): the contact-link branch of `_agent_scope` ties the link row and
the linked customer to the ask's `company_id` (B1, AC-ST105c); the portal payload omits
`contact_id` (N1); `ask_message_id` is one of the returned messages (N2); `chat_histories`
has no company column, so a contact who talks to two companies' bots has both in one window,
the same limit the Conversations screens carry today (N3, noted, not a lane defect).

**Deleted by the reshape:** the counts line, the age label, the in-line Sort select, the day
sub-headings, the Notified / Console / branch badges on the to-do, the inline note cell on the
to-do rows (the note lives in the opened card; `AskEditCells` stays for the office tab).

## 4. Coordination with CONTACT-CUSTOMERS

Every read in this lane goes through `_agent_scope(db, agent_id)` (`stock_ask_service.py`), and
that function reads the relation from ONE expression, `_owning_agent_column()`. Three call sites
use it: `_agent_scope` (the to-do, the lists, the PATCH scopes), `serialize(with_agent=True)`
(the `agent_code` of the All agents view) and `agent_counts` (the Agent select's counts). When
that lane lands its agent -> customers relation, `_owning_agent_column` is the one edit; the
to-do, the counts, the CRM page and the portal page all follow. (The S4 salesman notification,
`stock_ask_service.py` around the `sales_agent_id` read in the send path, is #1333's and is
outside this lane.) Nothing here
adds a second copy of "whose customers", and no schema for the relation is added here.
Ruled (crew, 29 Sep 2026): (a), build on `customers.sales_agent_id` now; swap when PR #1366 lands.

**#1366 read (29 Sep 2026, crew: build against it; its branch is merged into this one):** #1366
keeps `customers.sales_agent_id` as "handled by" and exposes contact -> customers through
`respond_contact_customers` (multi-select, no primary in that lane). So "my customers" needs no
swap: `_owning_agent_column()` already IS #1366's relation. What does change is the ask with NO
customer: `resolve_customer` returns None for a contact linked to several customers with no
primary (`contact_customer_service.py:48-63`), and multi-select linking makes that common, so
such an ask would sit on nobody's list. The swap this lane makes in `_agent_scope` (the one
function): an ask belongs to an agent when its customer is handled by the agent, OR when it has
no customer and its contact is linked to a customer the agent handles (#1366's seam
`contact_customer_service.agents_for_contact(db, contact_id)`, the same join). The row then
shows the contact's name with no customer name. Nothing is written on the ask; the relation is
read every time, so a later link or move is reflected at once. Built in the reshape round with
its own tests (AC-ST105b).

## 5. Simplest thing, not built (and the trigger that would build it)

- No digest or reminder (Q8 (a)): S4's per-ask WhatsApp stays the nudge.
- No middle state, no assignment, no due date, no snooze (Q2 (a)). Trigger: a salesperson asks
  to park an ask.
- No settings row for the overdue rule (Q4 (a)). Trigger: a second threshold is asked for.
- No new preference table for the sort (Q3): the existing view-preference row for the CRM, the
  portal's per-contact local storage for the portal. Trigger: a two-device salesperson.
- No team hierarchy beyond one level: a leader sees current members, not members' teams (Q7 (c)).
- A customer-less ask (reached through a contact link, AC-ST105b) shows `-` in the Agent column
  of the All agents view: it may belong to several agents. Trigger: a manager asks whose it is.
- `agent_counts` runs one small query per pickable agent (the single scope expression, plan
  section 4, over a second grouped copy of the rule). Trigger: `/agents` p95 over about 300 ms or
  more than about 150 `sales_agents` rows.
- No `Show done` on the CRM mount (reviewer should-fix 2, ruled 29 Sep 2026): it would need a
  cross-customer done-list endpoint, while the CRM already has the customer's Asks tab for
  history and the to-do shows `Done today`. The portal keeps its `Show done` (the salesperson has
  no other history there). Trigger: a CRM salesperson asks for history beyond today.
- No paging on the to-do (cap 500 + flag). Trigger: an agent whose open asks exceed the cap.
- No new table: two columns on the row that exists. No registry for the day buckets: one pure
  function.

## 6. Slices

### S1: done stamps + the to-do payload + the portal to-do (the owner's ask)

Migration `sat_0001_stock_ask_done` (`ADD COLUMN IF NOT EXISTS` x2, backfill done rows,
re-parented onto main's head at PR time; chains on `sa2_0005_stock_ask_source` until #1333
merges). Backend: `_apply_update` stamps, `todo_for_agent`, `StockAskResponse` fields, portal
`todo` route. Frontend: `bucketTodo`, `AskTodoList`, the portal mount, the landing badge
unchanged. Tests: section 7, AC-ST101 to AC-ST118.

### S2: the CRM mount, mine and (with `view_all`) every agent

Permissions + grant sweep in the same migration file as S1 (one migration for the lane), routes
`app/api/v1/sales/customer_asks.py`, `agent_for_user`, menu entry, page, hooks, service. Tests:
AC-ST201 to AC-ST214.

Phase order per `/feature`: Phase 1 FE mock (both mounts, mocked service, 375 and 1280, agent-
browser evidence) -> crew-handtest -> Phase 2 tester-first (red pytest + vitest) -> coder ->
Phase 3 reviewer + security-reviewer (portal ingest and RBAC are touched, so it runs) + browser
verification.

## 7. Tests (tester first; the captain's list is in the UAC, one line per AC)

pytest (Postgres, `tests/_pg_fixture.py`, every test seeds its own agent, contact, customers,
asks): `tests/test_stock_ask_todo.py` (service: scope, branch filter, ordering, cap, today_start,
done stamps and clears, backfill), `tests/test_portal_customer_asks_todo.py` (route: gate, shape,
PATCH stamps `done_by` with the contact label), `tests/test_sales_customer_asks_api.py` (CRM:
mine via `respond_contact_id`, unlinked user, `agent_id` without `view_all` 403, PATCH out of
scope 404, permission grants), `tests/test_migration_sat_0001.py` (columns, backfill, grants).
vitest: `lib/stock-asks-todo.test.ts` (bucket table), `AskTodoList.test.tsx` (counts, groups,
Done moves a row, Reopen, empty state, `showAgent`), `CustomerAsksList.test.tsx` rewritten for
the to-do body, `MyCustomerAsksClient.test.tsx`, `menu.config` test for the entry.

## 7b. Phase 1 evidence run (29 Sep 2026, agent-browser, sandbox dev server, mock store)

Deviation recorded: the coder ran in the lane checkout, not a worktree (a cloud sandbox with no
concurrent editor). Sandbox-only seeds, never committed: every catalog module enabled for the
default tenant, the five `sales.customer_asks.*` slugs inserted so superadmin's permission list
carries them (Phase 2's migration is what adds them for real).

- CRM, 1280x800: signed in as a superadmin, sidebar Sales group -> `Customer asks` listed after
  Opportunities -> `/sales/customer-asks`. `PageHeader` "Customer asks", Agent select (view_all),
  counts `Open 5 · Needs attention 2 · Done today 1`, groups `Needs attention` (2 rows, oldest
  first, red age labels "2 days ago" / "Yesterday") and `Today` (3 rows, one with the Console
  badge). `Done` on the oldest row: counts `Open 4 · Needs attention 1 · Done today 2`, the row
  under `Done today` reading "Done by Sean Ibrahim 29/09/2026, 3:43 pm" with `Reopen`. Console:
  no error; `errors`: none.
- CRM, 375x812: `scrollWidth == clientWidth` (360), one column, `Done` full width under the row
  text. `Reopen` restores `Open 5 · Needs attention 2 · Done today 1`.
- Portal, 375x812: `/portal/c/sean` with a verified token planted in local storage; the landing
  selector lists `Customer asks 6` (the #1333 badge, one higher than the to-do's Open because the
  mock list still counts the `incoming` ask; Q5 decides); picking it renders the same counts and
  groups as the CRM. `Show done` opens the #1333 grid under the to-do (`state=done`, 1 row) and
  reads `Hide done` while open. No page errors.
- Portal, 1280x800: two-column rows, actions right; the done row greyed with its note.
- Coder's Phase 1 divergences, standing for Phase 2 unless the grill moves them: one flat
  `Needs attention` group (oldest first, age label per row) instead of per-day groups, because
  a row cannot sit in both a pinned aging group and its day group (AC-ST113's day labels are
  kept as `dayLabel`); `agent_id=all` added to the CRM todo contract for the manager's "All
  agents" choice (AC-ST210); the portal search filters the to-do client-side.
- Vitest breakage to hand the tester: `CustomerAsksList.test.tsx` (10, pins the replaced body),
  `PortalLanding.customerAsks.test.tsx` (2, the body and the server-side search),
  `menu.config.sales.test.ts` (2, the new entry).

## 7c. Phase 2 evidence run (29 Sep 2026, agent-browser, sandbox dev server, REAL backend on `sat_0001`)

Sandbox seeds (never committed): superadmin user, agent SEAN I linked to portal contact "Sean
Tan" (slug `sean`, verified token, `customer_asks` switch on), agent LCL, three customers, seven
asks (two before today, four today across all four branches incl. one `console`, one done).

- CRM, 1280x800: sign in, sidebar Sales -> Customer asks (after Opportunities). Admin is linked to
  no agent: "You are not linked to a sales agent" with the Agent select listing `All agents`,
  `LCL · 1 open · 0 need attention`, `SEAN I · 4 open · 2 need attention` (view_all). Picking
  SEAN I: `Open 5 · Needs attention 2 · Done today 1` (Q5: the `incoming` row counts),
  `Needs attention` with day sub-headings `Sun 27 Sep` then `Yesterday` (oldest day first, red
  age labels), `Today` with no duplicate sub-heading, `Incoming` and `Console` badges present,
  `Done today` with the backfilled row reading "Done" (no name). Sort select: picking `Customer
  A to Z` fires `PUT /api/v1/list-query/column-config/sales.customer_asks.view%3A%3Atodo` (200)
  and survives a full reload. `Done` on the oldest row: `PATCH /api/v1/sales/customer-asks/{id}`
  200, counts `Open 4 · Needs attention 1 · Done today 2`, the row under `Done today` reading
  "Done by Sandbox Admin 29/09/2026, 4:19 pm" (`done_by_user_id` resolved to the name); `Reopen`
  restores `Open 5 · Needs attention 2 · Done today 1`. `errors`: none.
- CRM, 375x812: `scrollWidth == clientWidth`, one column, full-width `Done`.
- Portal, 375x812: `/portal/c/sean`, selector `Customer asks 5`, the same counts and groups,
  Sort `Oldest first` default; `Newest first` written to `localStorage
  sorento.portalAsksSort.sbx-contact-sean` as `{"id":"asked_at","desc":true}`. `Done` on the
  oldest row: "Done by Sean Tan 29/09/2026, 4:20 pm" (`done_by_contact_id` resolved to the
  contact label), counts move. No page errors. 1280x800: two-column rows.
- Suites: lane + #1333 files 93 passed; neighbours (`tests/chatbot/test_stock_ask_record.py`,
  `test_stock_ask_notify.py`, `test_rbac.py`) 68 passed; vitest by area after the Phase 3 fix
  round (29 Sep 2026, 245cfa15): `app/(auth)/portal` 61 files / 447 tests, `app/(protected)/sales`
  20 / 194, `app/(protected)/order-management` 14 / 59, `components/stock-asks` 1 / 19,
  `lib/stock-asks-todo` + `lib/list-query` 3 / 56, `services` 86 / 614, `config` 9 / 50, all
  green (the whole suite in one run exceeds the sandbox's 25-minute window, so it ran by area).

## 7d. S3 evidence run (29 Sep 2026, agent-browser, sandbox rebuilt from the current models on `sat_0001`, real backend, seeded chat rows)

- CRM, 1280x800: sidebar Sales -> Customer asks; admin (no agent) reads "Not linked to a sales
  agent" + hint with the Agent select (view_all) listing `All agents`, `LCL · 1 open`, `SEAN I ·
  5 open · 2 need attention`. SEAN I picked: toolbar `Filter`, `Sort` (Created), list / cards
  toggle, no New button, no counts line; sections `Needs attention`, `Today`, `Done today`; cards
  read customer + contact, `Asked: CODE x Q`, `Answered: <sentence>`, the datetime, `Done`
  top-right; no age label, no badge. Card body click opens the right Sheet "Customer ask":
  customer, contact, "Asked 27/09/2026, 6:51 pm · SEAN I", the Asked / Answered block with
  `Jump to message`, the conversation (5 seeded bubbles, the answer tagged `This ask`), `Show
  the whole day`, `Open in Conversations`, `Note` + `Save note`, `Done` at the foot (an open
  ask; the earlier `Reopen` sighting was a stale row from an accidental click, not reproduced).
  `Jump to message`: the tagged bubble carries the flash class. `Done` at the foot: the sheet
  closes, the card lands under `Done today` reading "Done by Sandbox Admin, 29/09/2026, 9:10 pm";
  `Reopen` restores it. List view: `Asked at | Customer | Contact | Asked | Answered | Done by |
  (action)` with `Done` in the LAST cell, section rows `Needs attention`, `Today`, `Done today`.
  `All agents`: `Asked at | Agent | Customer | Contact | Asked | Answered | Done by | (action)`.
  375: `scrollWidth == clientWidth`. `errors`: none.
- Portal, 375x812: `/portal/c/sean`, selector `Customer asks 4`; the same toolbar and sections,
  cards identical, no sideways scroll. Card body click opens the bottom Drawer: customer, "Asked
  28/09/2026, 6:51 pm", the block with `Jump to message`, the conversation with `This ask`, `Show
  the whole day`, no `Open in Conversations`, `Note` + `Save note` ("Saved" line, `PATCH
  /api/v1/public/portal/customer-asks/{id}` 200), `Done` at the foot: the card lands under `Done
  today` reading "Done by Sean Tan, 29/09/2026, 9:12 pm"; `Reopen` restores it. List view (the
  DataGrid at phone width, sideways scroll inside the grid): `Asked at | Customer | Contact |
  Asked | Answered | (action)` with `Done` last, the three section rows; no `Done by` on the
  portal.
- Suites after the fix round: backend 117 on the lane set (four lane files + conversation, #1333,
  uuid principle, company scope); vitest 713 across the portal, the sales page, the stock-asks
  components, the customers tab, config and list-query; type-check clean.

## 8. Risks

- #1333 not merged: this branch carries its 33 commits; a #1333 fix round changes files this lane
  edits (`stock_ask_service.py`, `CustomerAsksList.tsx`). Mitigation: merge #1333's head into
  this branch on every crew notice, and keep this lane's edits additive (new functions, new
  component) where it can.
- The migration chains on `sa2_0005_stock_ask_source`; if #1333 is re-parented before merge, this
  one re-parents with `./scripts/alembic-reparent.sh`.
- `CustomerAsksList.test.tsx` and `PortalLanding.customerAsks.test.tsx` from #1333 pin the cards /
  grid body; S1 rewrites the first and keeps the second green (the badge and kind are unchanged).
