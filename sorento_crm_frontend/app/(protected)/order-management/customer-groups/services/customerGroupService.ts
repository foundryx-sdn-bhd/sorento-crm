/**
 * Customer groups - feature service.
 *
 * Layering: components -> hooks (useCustomerGroups) -> THIS service -> lib/api-client.
 *
 * Backend (order_management router, reads `order_management.customers.view`, writes `.edit`):
 *   GET   /api/v1/order-management/customer-groups?page&limit&sort&dir&query&agent_mixed
 *   GET   /api/v1/order-management/customer-groups/select?query -> { data: [{ id, name, ledger_count }] }
 *   POST  /api/v1/order-management/customer-groups  { name }     (409 on a duplicate name)
 *   GET   /api/v1/order-management/customer-groups/{id}
 *   PATCH /api/v1/order-management/customer-groups/{id}  { name }
 *   GET   /api/v1/order-management/customer-groups/{id}/customers?page&limit&query&sort&dir
 *   POST  /api/v1/order-management/customer-groups/{id}/customers  { customer_ids } -> { data }
 * Delete and Remove-from-group have NO route: pending actions `customer_group.delete` and
 * `customer.remove_from_group` (payload { customer_group_id }), parked by the deferred hooks.
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';
import type {
  CustomerGroup,
  CustomerGroupSelectOption,
  GroupLedger,
} from '../types/customerGroup.types';

const BASE = '/api/v1/order-management/customer-groups';

export type CustomerGroupsListParams = DataGridApiFetchParams & { agent_mixed?: boolean };

export async function getCustomerGroups(
  params: CustomerGroupsListParams,
): Promise<DataGridApiResponse<CustomerGroup>> {
  const response = await apiFetch(
    `${BASE}?${buildDataGridParams(params, { agent_mixed: params.agent_mixed || undefined }).toString()}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load customer groups'));
  }
  return response.json();
}

export async function getCustomerGroup(id: string): Promise<CustomerGroup> {
  const response = await apiFetch(`${BASE}/${id}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load customer group'));
  }
  return response.json();
}

export async function createCustomerGroup(data: { name: string }): Promise<CustomerGroup> {
  const response = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to create customer group'));
  }
  return response.json();
}

export async function updateCustomerGroup(
  id: string,
  data: { name: string },
): Promise<CustomerGroup> {
  const response = await apiFetch(`${BASE}/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save customer group'));
  }
  return response.json();
}

/** The ledgers in one group, one DataGrid page. */
export async function getCustomerGroupCustomers(
  id: string,
  params: DataGridApiFetchParams,
): Promise<DataGridApiResponse<GroupLedger>> {
  const response = await apiFetch(`${BASE}/${id}/customers?${buildDataGridParams(params).toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load ledgers'));
  }
  return response.json();
}

/** Move ledgers into the group in one request (from another group too). */
export async function addCustomerGroupCustomers(
  id: string,
  customerIds: string[],
): Promise<GroupLedger[]> {
  const response = await apiFetch(`${BASE}/${id}/customers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_ids: customerIds }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to add ledgers'));
  }
  const body: { data?: GroupLedger[] } = await response.json();
  return body.data ?? [];
}

/** Groups for a select, searched on the server. */
export async function searchCustomerGroupsSelect(
  query: string,
): Promise<CustomerGroupSelectOption[]> {
  const search = new URLSearchParams({ limit: '50' });
  if (query.trim()) search.set('query', query.trim());
  const response = await apiFetch(`${BASE}/select?${search.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load customer groups'));
  }
  const body: { data?: CustomerGroupSelectOption[] } = await response.json();
  return body.data ?? [];
}
