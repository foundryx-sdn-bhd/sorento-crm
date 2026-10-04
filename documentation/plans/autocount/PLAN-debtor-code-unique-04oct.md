# PLAN: debtor code unique per company, merge duplicates (DEBTOR-CODE-UNIQUE)

Status: Build (card answered Q1-Q5 (a), crew 4 Oct; red tests, then code). Track: L (data merge across every customer
FK, a unique index, external ingest create paths).
Base: on/after #1474 (DEBTOR-CODE-LINK), which adds the `sales_orders.debtor_name` backfill and
the code-first master ingest lookup.

## Owner decision (4 Oct)

Debtor code is UNIQUE PER COMPANY: one CRM customer row per AutoCount debtor code, as in
AutoCount. This includes the `301-*` cash / Shopee / walk-in codes (AutoCount has one debtor for
them; each document keeps the buyer name in its own `debtor_name`).

## Outline (detail lands with the card)

1. Every customer create / lookup path becomes code-first, code-only per company; documents keep
   their own `debtor_name`.
2. Merge script (not an alembic auto-run): `--dry-run` (default) writes a CSV per group,
   `--apply` one transaction per company, `--revert <csv>`.
3. `customer_aliases` keeps the retired names; the chatbot name join and trigram search read
   name OR alias.
4. `merge_map` + `audit_logs` per merge so it can be undone.
5. Unique index `(company_id, lower(btrim(customer_code)))` LAST, after the merge.

Card: `documentation/plans/autocount/CARD-debtor-code-unique-04oct.md` (to come).
