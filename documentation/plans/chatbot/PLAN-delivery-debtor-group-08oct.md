# PLAN: DELIVERY-DEBTOR-GROUP - delivery picker groups by debtor code, not DO-level debtor name

Status: in review, PR #1536 (small fix track: no migration, no auth/RBAC, no new ingest surface)

## Owner's case (8 Oct 2026)

"check today delivery to hanlim" offered two options, "HANLIM TRADING SDN BHD - has DO" and
"HANLIM TRADING SDN BHD (CERAMIC & ELLECI) - has DO". The second name was typed on one DO at DO
level; the DO's debtor code is the same customer. Only one customer (group) exists, so no picker
should appear.

## Investigation (file:line on this branch)

- The roster rows are the resolver's customer matches. For a customer word the chatbot runs the
  customer-master probe (`entity_resolver._probe_customer`) and the DO debtor-name probes
  (`_probe_customer_debtor_name` at `sorento_crm_backend/app/services/entity_resolver.py:1191`,
  `_prefix_probe_customer_debtor_name` at :2050, the AND-mode tail at :3783).
- Before this fix those probes linked a DO-name hit to a customer by NAME only (the old
  `customer_identity.outerjoin_customer_by_order_name`: customer name or alias, same company),
  and the hit's `display` carried only the DO's own `debtor_name`.
- The gate names and groups every customer match off `display.customer_name`, else
  `display.debtor_name` (`lanes/business/gate.py:328` `_cust_name`, :339 `_display_name`); one
  entity per uuid, last match wins (:434). So a DO-name hit linked through an alias (the
  debtor-code merge records DO-level names as aliases) replaced the customer's master name
  with "<NAME> (CERAMIC & ELLECI)".
- Customer groups reach the chatbot as an exact-name map (`customer_group_service.family_overrides`,
  `app/services/customer_group_service.py:268`; `ledger_family.customer_group_of`, `app/services/ledger_family.py:112`).
  The DO-level name is no customer's name, so it is ungrouped, and an ungrouped key equal to a
  group's key is kept apart on purpose (`ledger_family.apart_from_group`, :85). Result: two
  options, both "has DO" (`turn/narrow.py:73` `_family_of`, the gate's own picker via `_cust_base` :361).
- DOs link to customers by `orders.customer_id` (FK, `app/models/order.py:413`) and
  `orders.debtor_code` (:421). The DO search filters by `Order.customer_id`
  (`app/services/order_service.py:971`), so the FK is already the identity the answer uses.

## Prod shape (crew snapshot, 6 Oct)

HANLIM is four accounts in company 0001 (300-H030 [A/C I], 300-H070 [A/C II], 300-H118 [A/C III],
300-H119 [A/C IV]); the 5 Oct debtor merge recorded "HANLIM TRADING SDN BHD" as an alias of H030
and the DO-level name as an alias of H118. Groups are stored as `customers.customer_group_id`
(`app/models/order.py:184`) onto `customer_groups` (:64), and reach the chatbot only as the
exact customer-name map above. So the two lines were two ACCOUNTS of one group. H118's
DO-name hit printed the DO name instead of its master name "... [A/C III]", which the group map
could not place. With each hit carrying its account's master name, all four accounts fall in
the group and the roster is one choice: no ask, and the pick covers every account (the DO
search filters `Order.customer_id` over them, and each DO line keeps its own debtor name).
Test `test_prod_shape_four_accounts_of_one_group_with_merge_aliases_is_one_line` printed the
owner's exact two lines on main and is one line with the fix. A different group, or an
ungrouped account, still asks.

## Fix

- `customer_identity.outerjoin_customer_of_order` (`app/services/customer_identity.py:147`)
  links an order to its customer by explicit keys first: `orders.customer_id`, then the debtor
  code in the order's own company, then the existing name/alias join (:197).
- The four probe sites select the linked customer's `customer_name` and carry it as
  `display.customer_name` (`entity_resolver._debtor_display`, :1175). The DO's own name stays
  on `debtor_name` and on the DO lines of the answer.
- A DO no link reaches stays `uuid = None`, as before: never guessed, no name similarity. The
  gate drops uuid-less customer matches from its roster, as it already did.

## Tests

`sorento_crm_backend/tests/chatbot/test_delivery_debtor_group.py`: resolver link (FK, code,
FK over a colliding name, no link, other company's code, prefix probe) and the roster chain
(probes, gate, `candidates_by_kind`, `_options` under the customer-group map), including the
owner's alias shape, which reproduced the two-line picker before the fix. Fake names only.

## Data check (crew-data-request, not run from the cloud lane)

SQL for the dev and prod snapshots is posted on the PR as a `crew-note`.
