# UAC contact-bulk-access-3oct

Status: approved basis - owner answered Q1-Q5 (a) on 4 Oct 2026. Card:
`CARD-contact-bulk-access-3oct.md`.

"Access set" below = access types, chatbot tier (`chatbot_profile.tier`), the five chatbot switches
(stock checks, notify salesman, packing list, ETA buffer days, escalation), field reveals, agent access
(card Q1 (a)).

## A1 Copy access endpoint (one call, preview and apply)
- A1.1 `POST /api/v1/user-management/contacts/bulk-copy-access` `{source_contact_id, target_contact_ids, dry_run}`
  answers one result row per requested target, in request order, plus counts.
- A1.2 `dry_run: true` writes nothing (every table of the access set unchanged after the call).
- A1.3 `dry_run: false`: every `changed` target ends with exactly the source's access set (replace, card Q2 (a)):
  access types equal, tier equal, five switches equal, reveal keys equal, allowed agents equal with the
  source's valid_from/valid_to.
- A1.4 Linked customers, companies, CS routing, media limits, memory level and facts, name and phone of every
  target are unchanged; the source is unchanged.
- A1.5 A target already equal to the source answers `unchanged` with no changes and no write.
- A1.6 The source listed among targets answers `skipped` ("this is the source contact").
- A1.7 An unknown target id answers `failed` ("contact not found"); the other targets still apply.
- A1.8 A target whose write raises answers `failed` with the reason and keeps its previous access untouched
  (per-target transaction); the other targets still apply.
- A1.9 Unknown source answers 404 and writes nothing. More than 500 targets answers 422.
- A1.10 Each change row names facet, label, before, after; reveal keys and agents report added and removed
  separately, with human labels (no ids).
- A1.11 Without `user_management.contacts.edit` answers 403.
- A1.12 A legacy agent row on the target keyed only by phone (NULL `respond_contact_id`) is treated as held
  by that target: no duplicate row is created.
- A1.13 Preview of the same request equals what apply then writes (same change rows when nothing moved in between).

## A2 Contacts list access columns and filters
- A2.1 Each list row carries `chatbot_tier` and `cost_visible` (holds `purchase_orders.cost`), batched for the page
  (no per-row query); `escalation_allowed`, `packing_list_allowed`, `chatbot_stock_allowed` already exist.
- A2.2 Columns Tier, Cost, Escalation, Packing list, Stock checks with explicit `size`, status shown with `Badge`,
  hideable via column preferences.
- A2.3 Filters, each a clearable `SearchableSelect`, sent as query params and kept in the URL:
  `access_type=<code>`, `tier=<dealer|office|end_user|none>`, `cost=<yes|no>`, `escalation=<yes|no>`,
  `packing_list=<yes|no>`, `stock=<yes|no>`, `customer_id=<id>` (plus today's `customers=none`),
  `access_differs_from=<contact id>` (card Q4 (a)).
- A2.4 Each filter returns exactly the matching contacts; combined filters AND together; count and pagination
  match; a filter change resets to page 1.
- A2.5 `access_differs_from=X` lists exactly the contacts a copy from X would answer `changed` for, never X itself.
- A2.6 `customer_id` only matches links inside the caller's company scope (same rule as `customers=none`).

## A3 Copy access dialog
- A3.1 Select 1+ contacts: bulk strip shows "Copy access from contact" (only with `contacts.edit`); it replaces
  today's "Copy settings to N users".
- A3.2 Step 1: searchable source pick (selected targets excluded); shows the source's access set and the
  "Not copied" line.
- A3.3 Step 2: preview per target from `dry_run: true`; counts "will change" / "already the same"; removals struck red,
  additions green.
- A3.4 Apply: one `dry_run: false` call; the button is disabled while it runs; the dialog ends on the result table
  (Updated / No change / Skipped / Failed with reason) on success, partial failure, HTTP error or timeout
  (HTTP error or timeout: one message "Nothing confirmed; check the list with 'Access differs from'"). Never an
  endless spinner.
- A3.5 After apply the list refetches; "Check who still differs" sets `access_differs_from=<source>`.
- A3.6 Usable at 375 px and 1280 px without page horizontal scroll.
