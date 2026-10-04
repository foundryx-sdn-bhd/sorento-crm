# PLAN: simplify chatbot DO asks (DO-ASK-SIMPLIFY)

Status: built on PR #1433 (FULL track), all rules red-first then green, the 4 Oct rulings included; reviewer APPROVE (4 Oct); awaiting CI and the owner's hand test.
Track: FULL (a field-reveal (access) change, so the security reviewer joins; the data-seed
migration was removed in the 4 Oct crew review).

## Owner answers (2 Oct 2026)

- Q1 (a): cap = 31 days, inclusive, rolling.
- Q2 (b), SUPERSEDED 4 Oct (see below): rules 3-4 apply to DEALERS only. Until ACCESS-MODEL lands, a dealer is a contact
  linked to at least one customer and holding no office access type
  (`contact_customer_scope(...).enforced`), behind ONE helper `app/services/chatbot/do_ask.py::
  is_dealer` that ACCESS-MODEL replaces with the Dealer role (crew ruling).
- Q3 (a): quantity asks exempt. Built extra under the same reason: the `outstanding` and
  `so_outstanding` order buckets (no delivery date to range over) are exempt too.
- Q4 (a): one switch per field (five keys).
- Q5, EXTENDED 4 Oct (see below): EVERY existing contact gets the five grants at deploy (all are internal; crew data: tier
  is NULL for all 100 dev contacts); new contacts start hidden. Owner rule change (2 Oct
  2026, after the tester pass): every existing contact ENDS with all five switched ON, a switch
  already off is turned on (`ON CONFLICT ... DO UPDATE SET granted = true`), so no current user
  sees any change; dealers are not in yet, so none is left out (closes security S3).
- Q6: header grouping as proposed.

## Owner rulings (4 Oct 2026)

- **Every DO field reveal defaults ON, new contacts included** (owner, ~01:25): the owner turns
  off per contact what he wants hidden. `contact_field_reveal_service.DEFAULT_ON_KEYS` (the
  `delivery_orders.*` keys): `granted_keys` counts a DO key with NO row as granted, a
  `granted=False` row hides it, and `set_granted_keys` writes that row when a DO key is turned
  off. Every other reveal key (cost, supplier, PO placed, sellable, SO, sales report, low stock
  report) stays default HIDDEN. The migration's seed for existing contacts stays.
- **Rules 3-4 apply to EVERY contact, not only dealers** (owner, ~01:35, replacing Q2 (b)):
  `do_ask.range_reply` has no dealer gate; office and internal contacts get the same period
  question and 31-day cap. Exemptions unchanged. 22 chatbot test files that ran a dateless DO
  ask as one step of another flow (escalation, carry, routing, top selling, outstanding) now
  date that step; where the dateless ask is the point, they expect the period question
  (owner chose option (a), ~02:50).
- **Dated DO miss hint** (lead decision on the crew-ask, pending crew's answer): the miss reply
  offers `Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct)` instead of
  `Reply 'all dates' to search without the date filter`, since a dateless DO ask now asks which
  period. The escalation clause is unchanged: every no-answer still ends with the escalation
  offer for contacts allowed to escalate.
- **No migration** (crew review, 4 Oct): migration `do_ask_0001_reveals` seeded every DO key
  `ON CONFLICT ... DO UPDATE SET granted = true`, which on prod would turn back ON every DO
  switch the owner had turned OFF, and its downgrade deleted owner-set rows. With
  `DEFAULT_ON_KEYS` a missing DO row already reads ON, so the migration is deleted; a test pins
  that an owner-OFF DO switch survives `alembic upgrade head`.
- **Refusal line after the main merge (#1435)**: "Sorry, that isn't under your account. I can
  only check on ..." groups by `ledger_family.group_names` (the name rule, #1435's code), so an
  ungrouped ledger loses its account marker there; the DO header and period question keep
  CUSTOMER-GROUP ruling (b). Crew-ask open (recommend one rule).

## As built

| Rule | Where |
|---|---|
| 1 header | `tail/scope_block.py::family_words`, used by `_axis_words` (gate rows) and `_focus_words` (carried rows) for the Customer axis only |
| 2 reveals | default ON per `contact_field_reveal_service.DEFAULT_ON_KEYS` (4 Oct); `sorento_crm_mcp/presenters.py::_orders_list` (keys + `b.restrict` per field), `catalog.py` orders_list `restricted_fields`, `contact_field_reveal_service.FIELD_REVEAL_KEYS` (no migration since the 4 Oct crew review) |
| 3-4 range | `app/services/chatbot/do_ask.py::range_reply` (every contact since 4 Oct), called in `lanes/business/__init__.py::run_fetch` right before the trigger is built; answered with `_fixed_reply` (no fetch, no header, no escalate offer) |

The ask-back names its suggestions as words to type (no numbered pick), so no pending kind was
added: the answer ("this month") is an ordinary dated message, and the engine carries the open
ask's subject onto it (`tests/chatbot/test_do_ask_range_engine.py` pins this end to end).
The "Product: all products" drop (old Q5) was not built; the owner did not ask for it.

Review round 1 (security + reviewer, PR #1433), all red-first:
- The "hasn't been delivered yet" miss line names the order's status only with
  `delivery_orders.status` (`answer.not_found_error_message(granted_keys=...)`).
- A DO list ask that names a transporter, from a contact without `delivery_orders.transporter`,
  is refused in one line ("Sorry, delivery orders can't be looked up by transporter for your
  account."), nothing fetched: filtering would reveal the carrier, and dropping the filter
  silently would answer a different question.
- The period question runs after the customer-scope backstop, so it never names a customer
  outside the contact's links.
- The empty-list (miss) header groups customers the same way (`family_words` moved to the core
  module `app/services/ledger_family.py`, shared by both headers).
- Refusal wording: whole calendar months in months, anything else in days; swapped dates are
  put right; suggestions never name a month still to come.

## Journey

A dealer or staff contact asks the WhatsApp bot about delivery orders (DO). The reply opens
with a short header (one customer name, not every ledger variant), shows only the DO fields
that contact may see, and an ask with no date range gets a "which period?" question with ready
options. A range longer than the cap is refused with a suggestion.

## Behaviour card

### What a "DO ask" is (scope of rules 3 and 4)

An order-domain business turn whose single fetch is `crm_order_management_orders_list` or
`crm_order_management_orders_by_product_list` (the two tools the scope header already covers,
`turn/policy_rows.py:154-160`). NOT covered: `crm_outstanding_report` (SO/DO pending backlog,
its own header and its own `order_date` filter), `crm_sales_report` / sales analysis / top
selling (own lanes), `order_status=so_outstanding` (open SO lines, no DO yet).

### Today (file:line)

| Area | Today |
|---|---|
| Header | `tail/scope_block.py:242-286` `search_scope_header` prints `Customer:` / `Product:` / `Dates:`, prepended by `answer_bridge.py:116-167` `apply_scope_block` (engine call `engine.py:4914-4966`). The customer words come from `_axis_words` (`scope_block.py:115-168`): typed token, else parser raw, else EVERY gate row's `display_name` (one name per ledger, `:165-167`). |
| Fields | `sorento_crm_mcp/presenters.py:411-438` `_orders_list`: Company, Order Number, Customer, Order Date, Actual Delivery Date, Status, Pickup Time, Transporter, Driver, Lorry Plate, Warehouse, Products. `_orders_by_product` (`:614`): Company, Order Number, Customer, Order Date, Actual Delivery Date, Products. Only `company_name` carries a field key (3-tuple); the gate in `lanes/business/fetch.py:2645-2700` skips any field without a key (`_has_key`, `:1570`), so none of these can be hidden today. |
| Date range | Parser `date_filter_start/end` (`head/parser.py:133-135`) map to `actual_delivery_date_from/to` (`lanes/business/fetch.py:435-439`, applied `:707-718`). No date = all dates, no default window, no ask-back, no cap anywhere (`scope_block.py:266`: "all dates"). The route caps rows at 20 without a date (`api/v1/order_management/orders.py:33`). |
| Specific numbers | Entity hint `order` (`contracts.py:190-206`); resolver types `order` / `customer_order` / `order_number` (`turn/state.py:104`, `lanes/business/answer.py:2250`) map to `order_ids` (`fetch.py:313-316`). |
| Staff | `turn/state.py:211-218` `is_staff_profile`: `respond_contacts.chatbot_profile.tier == "office"`. |
| Reveals | `contact_field_reveals` (`models/access.py:753`), keys frozen in `contact_field_reveal_service.py:41-62` `FIELD_REVEAL_KEYS`, pinned to the MCP catalogue's `restricted_fields` by `tests/chatbot/test_field_reveal_keys_pinned_to_catalog.py`. Default hidden. |

### Rule 1: compact header

**Reconciled with CUSTOMER-GROUP (#1441, merged 3 Oct 2026):** the company a line names is
its explicit customer GROUP (`ledger_family.customer_header_words` over the turn's
`customer_groups`); a ledger in no group prints its own full name (owner ruling (b)). This
lane's name-rule `group_names` / `family_words` are removed; every place below uses main's
formatter. The paragraph that follows is the earlier statement of the same rule.

**Owner rule, 2 Oct 2026 (supersedes the counts below):** wherever the chatbot names a customer
company (DO headers hit and miss, the period question, the customer-scope "not under your
account" line) it prints the GROUP NAME ONLY, e.g. `HANLIM TRADING SDN BHD`: no
`(N accounts)`, no `and N more`, no ledger marker. Several groups are listed by name, comma
separated. `app/services/ledger_family.py::group_names` is the one rule (a family keeps the
brackets every ledger shares, such as `(SENTUL)`; a single ledger drops its trailing marker).

- The `Customer:` line groups the in-scope customer rows by ledger family (`ledger_family_key`,
  `app/services/ledger_family.py:43`, the rule the narrower and the stock-ask card already use)
  and prints the family label (`ledger_family_label`, `:54`):
  - one family, one row: the row's own name, unchanged (`HANLIM TRADING SDN BHD [A/C I]`).
  - one family, several rows: `HANLIM TRADING SDN BHD (6 accounts)`.
  - once PR #1432 lands and every row shares one `customers.account_level` N:
    `HANLIM TRADING SDN BHD, Account N`.
  - several families: first family label + `and N more` (`CHIN CHUN HARDWARE SDN BHD and 3 more`).
  - a typed word that resolved to many rows keeps printing the typed word (`_one_typed_word`,
    `scope_block.py:199`), unchanged.
- (Not built: dropping `Product: all products` was proposed; the owner did not take it.)
- `Dates:` prints as today.
- Each DO row keeps its own `*Customer:*` line with the full ledger name, so the reader still
  sees which account each DO is on.

### Rule 2: per-contact DO field reveals

**Owner hand test, 3 Oct 2026 (supersedes the five-field table below):** EVERY field the DO
list prints is its own switch: `delivery_orders.order_number`, `.customer`, `.order_date`,
`.delivery_date`, `.status`, `.pickup_time`, `.transporter`, `.driver`, `.lorry_plate`,
`.warehouse`, `.products`. The by-product DO list prints the warehouse as its own field (it
used to sit inside Products as "X (1) @ BRW"). The Company line (which of our companies, on
multi-company installs) is not a DO field and is not switched. Existing contacts: all on
(seed); new contacts: all hidden.

| Field | Key | Default for a new contact |
|---|---|---|
| Order Number, Customer, Order Date, Actual Delivery Date, Products, Company | none (always shown) | shown |
| Warehouse | none (always shown) | shown |
| Status | `delivery_orders.status` | hidden |
| Pickup Time | `delivery_orders.pickup_time` | hidden |
| Transporter | `delivery_orders.transporter` | hidden |
| Driver | `delivery_orders.driver` | hidden |
| Lorry Plate | `delivery_orders.lorry_plate` | hidden |

- Built the same way as `purchase_orders.supplier`: the presenter passes the 3-tuple key and
  calls `b.restrict(...)`; the catalogue declares the pairs on the orders list tool's
  `restricted_fields` only (the by-product list shows none of the five);
  `FIELD_REVEAL_KEYS` gains the five pairs (the pin test enforces both).
  The Contacts > Access > Field reveals checklist then lists them with no UI change.
- Naming: `delivery_orders.<field>` sits beside `sales_orders.*` / `purchase_orders.*`. For
  the ACCESS-MODEL table (`PROMPT_GATES`, PR #1429) these are field parts of domain `order`;
  they gate no prompt block, so nothing is added to `PROMPT_GATES` (crew-note to that lane).
- Existing contacts keep what they see today. Crew data (dev, 2 Oct 2026): `chatbot_profile.tier`
  is NULL for all 100 contacts (the bot no longer writes it, `engine.py:4316-4321`), and the
  owner states every current respond contact is internal. So the data migration inserts
  `granted=true` rows for the five keys for EVERY existing `respond_contacts` row (idempotent,
  `ON CONFLICT DO NOTHING`); a contact created after deploy starts with the five hidden (Q5 (a),
  crew recommendation). Nobody loses a field at deploy.

### Rule 3: a DO ask carries a date range, unless it names numbers

- A dealer's DO ask with no `date_filter_start` and no `date_filter_end` (this turn or carried
  by `_spec_window`, `turn_runtime.py:2327`) fetches nothing and asks back (as built):

  ```
  Which period for HANLIM TRADING SDN BHD (6 accounts)?
  - This month (Oct 2026)
  - Last month (Sep 2026)
  Or type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct).
  ```
  A typed period answers it and the original ask runs with that window (no numbered pick, no
  pending kind; see "As built").
- Exception: the ask names one or more DO/SO/order numbers (entity hint `order`, or a resolver
  hit typed `order` / `customer_order` / `order_number`): no range needed, no cap.
- "This month" means the whole calendar month (01/10/2026 to 31/10/2026), not month to date.

### Rule 4: range cap

- Cap (owner Q1 (a)): at most 31 days, inclusive, counted from start to end (rolling, not "same
  calendar month"). So "January", "February", "last week", "this month", "15 Sep to 10 Oct",
  "20 Dec to 10 Jan" are all fine; "January to June" and "January and February" (59 days) are
  refused. A start with no end ("since August") is measured to today.
- Refusal, fetching nothing, suggesting the asked range's last month, then its first (as built):

  ```
  That is 6 months (01/01/2026 to 30/06/2026). I can show up to 31 days of delivery orders at a time:
  - Jun 2026
  - Jan 2026
  Or type a month or dates.
  ```

### Real examples (dev replay fixtures under `sorento_crm_backend/tests/chatbot/replay_turns/console/`)

| # | Message | Header today | Header proposed |
|---|---|---|---|
| 1 | "Delivery to hanlim" then pick all (handpass3-owner-17sep..., turn 0) | `*orders* for HANLIM TRADING SDN BHD [A/C II], HANLIM TRADING SDN BHD [A/C I], HANLIM TRADING SDN BHD [A/C III], HANLIM TRADING SDN BHD [A/C IV], HANLIM TRADING SDN BHD, HANLIM TRADING SDN BHD (CERAMIC & ELLECI):` | ask-back "Which period for HANLIM TRADING SDN BHD (6 accounts)?"; after "1": `Customer: HANLIM TRADING SDN BHD (6 accounts)` / `Dates: 01/10/2026 to 31/10/2026` |
| 2 | "All" on the Chin Chun picker (same file, turn 2) | `*orders* for CHIN CHUN HARDWARE SDN BHD - [A/C I]` x6, `CHIN CHUN HOMEMART SDN BHD - [A/C I]` x4, `... AND TIMBER TRADING` x3, `CONTACT A - I` x2 | `Customer: CHIN CHUN HARDWARE SDN BHD (N accounts) and 3 more` (identical names count once) |
| 3 | "delivery status for hanlim" (case-072, turn 1); rows 202609-0916, 202609-0927 | `Customer: hanlim` / `Product: all products` / `Dates: all dates`, rows with Status `Picked Up / In Transit`, Driver `DRIVER B`, Lorry Plate `PLATE-1` | ask-back for the period; a dealer then sees rows without Status / Pickup Time / Transporter / Driver / Lorry Plate; a staff contact sees them as today |
| 4 | "delivery for hanlim rpacc" then "only in 2026" (owner-15sep-chain-001, turns 6-7) | `Dates: all dates`, then `Dates: 01/01/2026 to 31/12/2026` | turn 6 asks the period; turn 7 is refused: "That is 12 months ... - Dec 2026 - Jan 2026" |
| 5 | "where is DO 202609-0916" | (no fixture) | no ask-back, no Dates line: `Order: 202609-0916` and the row |

### Edge cases

- A pick answer ("1") after a customer picker: the period question comes after the pick, not
  before (the subject must be known first).
- A carried window: a follow-up ("and chin chun?") keeps the previous window, no re-ask.
- A brand switch inside an open list (`order_list.py` R3/R5) keeps the window.
- Year boundary: "20 Dec to 10 Jan" = 22 days, allowed; "December" asked in January means the
  previous December (parser's job, unchanged).
- Ledgers whose marker is a dash suffix, not brackets (`ZZT BATHIDEA MARKETING - IBORN`,
  `- CERAMIC`, `- A/C I`) are different families to `ledger_family_key`, so they print
  `ZZT BATHIDEA MARKETING - IBORN and 2 more`. Widening the family rule would also change the
  customer picker that shares it, so it stays out of this lane.
- One ledger reached twice (a picked option carrying its uuid twice) is one account.
- Empty result with a valid window: `EMPTY_LIST_LINE` as today.
- A refusal or ask-back never escalates and never offers the team picker.

### Questions (recommendations first)

1. Cap: (a) <= 31 days rolling, (b) one calendar month (start and end in the same month).
   Recommend (a): "15 Sep to 10 Oct" and "last 30 days" are ordinary asks that (b) would refuse.
   Either way "January and February" is refused.
2. Who gets rules 3-4: (a) every contact, staff included, (b) dealers and end users only.
   Recommend (a): one rule is simpler to explain, and staff also get the 20-row cap today.
3. Quantity asks on the same tools ("how many SRT320-CR did hanlim take", `include_summary`):
   (a) exempt, keep today, (b) same range rules. Recommend (a): a total is a report, and a one
   month cap would break the ask.
4. Reveal keys: (a) five per-field keys as tabled, (b) one key `delivery_orders.logistics`
   for all five. Recommend (a): the owner named fields one by one, and status is likely to be
   granted to some dealers without driver/lorry.
5. (revised by crew data) Seed: (a) the five grants for EVERY existing contact (all internal
   today), new contacts hidden, (b) wait for ACCESS-MODEL roles. Crew recommends (a); taken.
6. Header with a product: keep `Product: <code>` (yes), and drop `Product: all products`
   (recommend yes, it carries no information).

## Regression scenarios (DO asks)

Contact D = dealer (linked to a customer, no office access type; a NEW contact, so no DO
reveal grants). S = staff (an office access type or no customer link; an existing contact,
so the five grants were seeded). Today = Fri 2 Oct 2026. "Pinned" names the test that holds it.

| # | Contact | Message | Expected | Pinned |
|---|---|---|---|---|
| R1 | D | "delivery to hanlim" | period ask-back, This month (Oct 2026) / Last month (Sep 2026); nothing fetched | `test_do_ask_range.py`, `test_do_ask_range_engine.py` |
| R2 | D | R1 then "this month" | the same ask runs for 01/10/2026 to 31/10/2026, carried customer | `test_do_ask_range_engine.py::test_the_answer_runs_the_same_ask_over_that_window` |
| R3 | D | R1 then "August" | list for 01/08/2026 to 31/08/2026 | same path as R2 (parser dates) |
| R4 | D | "delivery to hanlim this month" | list, no ask-back; rows without Status / Pickup Time / Transporter / Driver / Lorry Plate | `test_do_ask_range.py` (fetched), `test_do_field_reveals.py` (hidden) |
| R5 | S | "delivery to hanlim" (no date) | list as today, no ask-back, all five fields | `test_staff_need_no_range`, `test_a_contact_with_every_grant_reads_today_s_reply` |
| R6 | D | "delivery to hanlim last week" | list 21/09/2026 to 27/09/2026 | parametrised 31-day test |
| R7 | D | "delivery to hanlim September" | list 01/09/2026 to 30/09/2026 | parametrised 31-day test |
| R8 | D | "delivery to hanlim 15 Sep to 10 Oct" | list (26 days) | parametrised 31-day test |
| R9 | D | "delivery to hanlim 20 Dec 2025 to 10 Jan 2026" | list (22 days, year boundary) | parametrised 31-day test |
| R10 | D | "delivery to hanlim January to June" | "That is 6 months (01/01/2026 to 30/06/2026) ..." - Jun 2026 / - Jan 2026 (whole months in months, anything else in days: "That is 32 days") | `test_january_to_june_is_refused_with_suggestions` |
| R11 | D | "delivery to hanlim January and February" | refused, "2 months" | `test_january_and_february_is_refused` |
| R12 | D | "delivery to hanlim this year" | refused; suggestions stop at the current month: - Oct 2026 / - Jan 2026 | `test_suggestions_never_name_a_future_month` |
| R13 | D | "where is DO 202609-0916" | the row, no ask-back | `test_naming_an_order_number_needs_no_range` |
| R14 | D | "how many SRT320-CR did hanlim take" | quantity ask, no ask-back (Q3) | `test_a_quantity_ask_needs_no_range` |
| R15 | D | "delivery to hanlim since August" | refused, measured to today (01/08/2026 to 02/10/2026) | `test_a_start_with_no_end_is_measured_to_today` |
| R16 | D | "delivery to hanlim 1 Sep to 2 Oct" | refused (32 days) | `test_32_days_is_refused` |
| R17 | S | "delivery to hanlim January to June" | list, never capped | `test_staff_are_never_capped` |
| R18 | any | six HANLIM ledgers in scope | header `Customer: HANLIM TRADING SDN BHD (6 accounts)` | `test_do_ask_header.py` |
| R19 | any | one ledger picked | header prints that ledger's full name | `test_one_ledger_prints_its_own_full_name` |
| R20 | D | "outstanding DO for SRTWT7443" | outstanding report, unchanged (not a DO list ask) | report tool is not in `ORDER_TOOLS` |
| R21 | D | brand switch / "and chin chun?" inside an open dated list | carried window, no re-ask | carried by `_spec_window` (not separately pinned) |
