# PLAN: CS routing conditions (product lines, debtor type) + No CS finish

**Status:** Owner ruling 22:02 built (No CS stays Approved); reviewer READY at b8fb0fbdf; browser pass + owner hand test next · Track: normal (migration) · Lane FORM-ROUTING-CONDITIONS · branch
`crew/form-routing-conditions`, base origin/main 760ed152b
**Domain:** forms / procurement / customers
**UAC:** `documentation/plans/forms/form-routing-conditions-acceptance-criteria.md` (the contract)
**Card:** `documentation/plans/forms/form-routing-conditions-behaviour-card.md` (owner answers 14 Oct 2026)
**Builds on:** `PLAN-form-cs-routing-conditions.md` (predicate engine), `PLAN-cs-routing-targets.md` (#1664).

Phase 1 (FE mock) skipped by crew rule: no new screen; every UI change adds options to existing
components (PredicateBuilder condition rows, the rule PIC SearchableSelect, a Customers select in the
Account level pattern, the existing AsyncCombobox picker). Recorded in the PR body.

## Journey

See the UAC `Journey`. Fewest decisions: the admin adds one rule per salesman; the salesman picks a
customer they already pick today (now from the customer list), and Sales type fills itself.

## Design

### One small module: `app/services/debtor_type.py`

```python
DEBTOR_TYPES = ("project", "project_cash", "cash_sales")
DEBTOR_TYPE_LABELS = {"project": "Project", "project_cash": "Project cash", "cash_sales": "Cash sales"}
# Owner Q5 (14 Oct 2026). The ONE place the PR sales-type auto-fill is decided.
SALES_TYPE_FOR_DEBTOR_TYPE = {"project": "project", "project_cash": "cash_sales", "cash_sales": "cash_sales"}

def default_debtor_type(customer_code, customer_name) -> Optional[str]:
    # 302- -> project_cash; 301- whose name starts with the word CASH -> None; else project
```

The migration backfill re-states the same rule in SQL (a migration must not import app code that can
change later); a test pins both to the same answers for the DT-2 cases.

### Migration `frc_0001_debtor_type_no_cs` (down_revision `ac_consign_0001_doc_type`)

1. `customers.debtor_type VARCHAR(20) NULL` + `CHECK (debtor_type IS NULL OR debtor_type IN (...))`.
2. Backfill `WHERE debtor_type IS NULL`: `customer_code LIKE '302-%'` -> project_cash;
   `customer_code LIKE '301-%' AND customer_name ~* '^\s*CASH\M'` -> stays NULL; else project.
   Dev (`sorento_cagent_stack`): 890 x 302-, 29 x 301 CASH, rest project.
3. `purchase_requests.customer_id UUID NULL REFERENCES customers(id) ON DELETE SET NULL` + index.
4. `respond_contact_cs_routing.cs_pic_user_id DROP NOT NULL` (NULL = No CS).
Downgrade: delete NULL-PIC rules, SET NOT NULL, drop index/FK/columns.
All DDL `IF NOT EXISTS`-safe (dev DB converges by create_all, `sorento_crm_backend/CLAUDE.md`).

### Customers (DT)

- `Customer.debtor_type` column (`app/models/order.py`), plus a `before_insert` mapper listener filling
  it from `default_debtor_type` when None. Every creation path is ORM (`master_ingest_service._insert`
  :1491, `order_service._upsert_customer` :2214 and create :3696, `customer_import_service` :649,
  `project_lead_service` :369), so one listener covers them. Never runs on update.
- Schemas `app/schemas/order.py` (create :48, update :61, response :85): `debtor_type:
  Optional[Literal[...]]`. Route `customers.py` needs no new permission (plain customer edit).
- FE `order-management/customers`: types, `CustomerForm.tsx` select (clearable SearchableSelect),
  `CustomerDetail.tsx` read-only value, both next to Account level.

### Form customer link (PC)

- `PurchaseRequestHeader.customer_id` (`app/models/procurement.py:1094`); schemas
  `app/schemas/procurement.py` (:1231, :1303, :1503) gain `customer_id: Optional[str]`.
- Shared lookup function (in `app/services/debtor_type.py` or the procurement service) used by:
  - portal `GET /lookups/debtors` (`portal.py:495-508`): source switches from DISTINCT
    `orders.debtor_name` to active `customers` matching name or code, ordered by name, same `limit`.
    Item `{customer_id, debtor_name, customer_code, sales_type}`. Scoped to the token contact's
    companies by the portal company scope (`Customer` is CompanyScopedMixin,
    `company_scope_resolver.py:287-341`; a contact with no company gets Sorento).
  - internal `GET /procurement/purchase-requests/lookups/customers` gated by the PR edit permission.
- `portal_service._editable_fields` PR/SF tuple (:1654) adds `customer_id`; portal row serializers
  (:1928, :2053) return it; a customer_id that does not exist -> 422 (portal save + internal
  create/update). Revision diff labels (`portal_revision_service.py:236`) do not list customer_id (no
  UUID in UI); the snapshot still carries it so a revision keeps the link.
- FE: `portal-client.ts` `DebtorLookupItem` widened; `SubmissionForm.tsx` `debtor-async` (:2980) sets
  `customer_id` on pick, clears it on free text, and for PR sets `sales_type` from `item.sales_type`
  when non-null. `PurchaseRequestForm.tsx` customer input (:621) becomes an async search with the
  same rules (reuse an existing CRM async combobox; no new component). PriceTagRequestForm keeps using
  `debtor_name` only.

### Routing conditions (RC)

- `cs_routing_service._CURATED_FIELDS['purchase_requests']` (:165) gains two enum fields with options;
  `routable_fields` (:178) emits `options` for enum entries.
- `form_sla_service._load_form_field_values` (:1076): for `purchase_request` / `sponsorship_form` add
  computed `product_lines` (`has` if any `purchase_request_lines.item_code` with non-blank trim, else
  `none`) and `customer_debtor_type` (joined customer's current value, else None). One extra query,
  only at CS-stage start.

### No CS outcome (NC) - owner ruling 14 Oct 22:02: the form stays Approved

- `cs_routing_service`: `replace_all` / `upsert` accept `cs_pic_user_id` None = No CS (skip
  `is_eligible_pic`); `list_for_contact` tolerates None ids. Duplicate-conditions check unchanged.
- `form_sla_service._start_for_config`: for `_reads_cs_pin(config)` configs, resolve the winning rule
  FIRST (`_winning_rule_is_no_cs`, strict form-values load: a load failure is "not No CS"). If No CS:
  return None before any team/tier lookup. No status change: the form stays `approved`.
- No `completed` status anywhere (removed from the report, void-blocked set, revision terminal set,
  labels, revision options and the FE).
- FE `PurchaseRequestDetail.tsx`: CS actions (Processed by CS / Close) only when the approved form has a
  `customer_service` tracker in its already-loaded SLA trackers; hidden while loading.
- FE `ContactCsRoutingRules.tsx` PIC select gains the No CS choice (internal sentinel, saved as null;
  "no choice yet" stays distinct and is still skipped on save).

## Slices (one lane, one PR)

- **S1 DT** migration parts 1-2, model + listener, schemas, Customers FE.
- **S2 PC** migration part 3, lookups, form save, both pickers + sales type auto-fill.
- **S3 RC + NC** migration part 4, routable fields, computed values, No CS outcome (stays Approved).

## Testing seams

- Pure: `default_debtor_type`, `SALES_TYPE_FOR_DEBTOR_TYPE`, `routable_fields` options.
- Service on Postgres (`tests/_pg_fixture.py`, seed own chain, no LIMIT 1): `_load_form_field_values`
  computed keys, `_start_for_config` No CS vs PIC vs no match, `replace_all` null PIC, No CS form stays
  approved, customer insert listener, lookup item shape, portal save customer_id.
- Migration: upgrade on a scratch schema with the DT-2 rows, assert values; downgrade clean.
- FE vitest: PredicateBuilder enum options render, ContactCsRoutingRules No CS save payload,
  SubmissionForm picker sets customer_id + sales_type, CustomerForm debtor type select.

## Not doing (triggers named)

- No customer_id backfill on existing forms (trigger: owner asks to route already-open forms).
- No Customers list column / filter for debtor type (trigger: office needs to bulk-check the backfill).
