# PLAN customer-bulk-ops (CUSTOMER-BULK-OPS)

Status: Review READY (reviewer + security-reviewer, round 1 fixed at aac7c4373); browser pass + owner hand test pending; alembic gate waits ALEMBIC-JOIN5. M track, no migration.

Behaviour card: `CARD-customer-bulk-ops.md`. UAC: `customer-bulk-ops-acceptance-criteria.md`.

## Slices
1. Pager keeps list state after Save, customers + suppliers edit pages.
   - `app/(protected)/order-management/customers/[id]/edit/page.tsx:29,51` and
     `app/(protected)/procurement-management/suppliers/[id]/edit/page.tsx` (Back link + onSuccess push):
     append the current search string, as `master-data-management/products/[id]/edit/page.tsx:20-22,63` does.
2. Customers list bulk "Set sales agent".
   - `CustomersList.tsx` passes `bulkActions` to `DataGridListToolbar` (`components/ui/data-grid-list-toolbar.tsx:203`).
   - Small dialog: `SearchableSelect` of agents from `hooks/useCustomerSalesAgentOptions.ts`, Apply calls
     `assignSalesAgentCustomers` (`master-data-management/sales-agents/services/salesAgentService.ts:172`,
     BE `POST /sales-agents/{id}/customers`). Success: invalidate `['customers']`, toast, clear selection.
     Error: `extractApiError` message toast, selection kept.
   - Action shown only with `master_data.sales_agents.edit` (the endpoint's permission).
3. Contact Customers card bulk Unlink.
   - `ContactCustomersSection.tsx`: per-row checkbox + header "Unlink (n)"; `hooks/useDeferredBulkAction.tsx`
     with action key `contact_customer_link.unlink` (one pending action per link, one countdown, one Cancel).
4. CSV import DROPPED (owner 3 Oct).

## Not doing
- No customers-router bulk endpoint: the sales-agents one already exists and checks company per customer.

## Bulk config flow (owner approved 3 Oct: Q1 yes, Q2 card only, Q3 reuse Copy settings)
5. Contacts list bulk "Link customers (n)" (U4): `ContactsList.tsx:445` bulkActions + Dialog with the picker from
   `ContactCustomersSection.tsx:9,12`; one existing `POST /contacts/{id}/customers` per contact (`contacts.py:787`).
6. Customers column + "No customers linked" filter (U5): BE `GET /contacts` query param `customers=none`
   beside `chatbot_memory_level` (`contacts.py:96`, `contact_service.py:120`) + linked codes in the list payload;
   FE `lib/listQuery.ts` `contactsListFilters`.
- Access agents: reuse existing "Copy settings to n users" (`BulkCopySettingsFromContactDialog.tsx`). No change.
- Bulk customer group: approved 3 Oct, slice 7.
7. Customers list bulk "Set customer group (n)" + "Remove from group (n)" (U6, owner 3 Oct). FE only, all existing:
   `customer-groups/services/customerGroupService.ts:46,86,103` (create, assign, select), SearchableSelect
   `createOption` (`components/common/SearchableSelect.tsx:156`), deferred `customer.remove_from_group`
   (`services/record_actions.py:1330`) via `hooks/useDeferredBulkAction.tsx`.
