import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { Customer, CustomerFormData, CustomerDetail } from '../types/customer.types';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';


export async function getCustomers(params: DataGridApiFetchParams & { status?: string; customer_group_id?: string }): Promise<DataGridApiResponse<Customer>> {
  const { pageIndex, pageSize, sorting, searchQuery, status, customer_group_id } = params;
  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(status ? { status } : {}),
    ...(customer_group_id ? { customer_group_id } : {}),
  });
  const response = await apiFetch(`/api/v1/order-management/customers?${queryParams.toString()}`);
  if (!response.ok) throw new Error('Failed to fetch customers');
  return response.json();
}

export async function getCustomer(id: string): Promise<CustomerDetail> {
  const response = await apiFetch(`/api/v1/order-management/customers/${id}`);
  if (!response.ok) throw new Error('Failed to fetch customer');
  return response.json();
}

export async function createCustomer(data: CustomerFormData): Promise<Customer> {
  const response = await apiFetch('/api/v1/order-management/customers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to create customer'));
  }
  return response.json();
}

export async function updateCustomer(id: string, data: Partial<CustomerFormData>): Promise<Customer> {
  const response = await apiFetch(`/api/v1/order-management/customers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update customer'));
  }
  return response.json();
}

export async function deleteCustomer(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/order-management/customers/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to delete customer'));
  }
}

export interface CustomerSalesAgentOption {
  id: string;
  sales_agent: string;
  person_label: string | null;
}

/**
 * Every active sales agent, for the customer form's "Sales agent" select.
 *
 * Served off `GET /customers/sales-agents-select` rather than the sales-agents master's own
 * list (`master_data.sales_agents.view`) or the SCM one (`scm.dashboard.view`): a role that
 * may edit a customer does not necessarily hold either. Unpaged - ~38 active agents today,
 * comfortably below one page - so there is no capped dropdown to worry about.
 */
export async function getCustomerSalesAgentsSelect(): Promise<CustomerSalesAgentOption[]> {
  const response = await apiFetch('/api/v1/order-management/customers/sales-agents-select');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load sales agents'));
  }
  const body: { data?: CustomerSalesAgentOption[] } = await response.json();
  return body.data ?? [];
}

/** Page size of the customers select. */
export const CUSTOMER_SELECT_PAGE_SIZE = 50;

/** A customers-select option; `salesAgentId` lets a caller disable "already on this agent". */
export type CustomerSelectOption = SearchableSelectOption & {
  salesAgentId?: string | null;
  customerGroupId?: string | null;
  customerGroupName?: string | null;
};

/**
 * One page of customers for the contact card's "Add customers" and the sales agent tab's
 * "Assign customers". Server-searched, keyed by customer ID (a code is not unique).
 *
 *   GET /api/v1/order-management/customers/select?limit=50&offset&query
 *     -> { data: { id, customer_code, customer_name, sales_agent_id, sales_agent_code,
 *          sales_agent_name }[] }
 *   The three sales_agent_* fields are additive (UAC AC-31), null when unassigned.
 *
 * value = customer id, label = `code - name`, description = the customer's current agent as
 * `code - name`, or "No sales agent".
 */
export async function searchCustomersSelect(
  query: string,
  pageIndex = 0,
  options?: { companyScope?: 'grants' },
): Promise<CustomerSelectOption[]> {
  type Row = {
    id: string;
    customer_code: string;
    customer_name: string;
    sales_agent_id?: string | null;
    sales_agent_code?: string | null;
    sales_agent_name?: string | null;
    customer_group_id?: string | null;
    customer_group_name?: string | null;
    company_name?: string | null;
  };
  const search = new URLSearchParams({
    limit: String(CUSTOMER_SELECT_PAGE_SIZE),
    offset: String(pageIndex * CUSTOMER_SELECT_PAGE_SIZE),
  });
  if (query.trim()) search.set('query', query.trim());
  if (options?.companyScope) search.set('company_scope', options.companyScope);
  const response = await apiFetch(`/api/v1/order-management/customers/select?${search.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load customers'));
  }
  const rows = ((await response.json()) as { data?: Row[] }).data ?? [];
  return rows.map((c) => ({
    value: c.id,
    label: options?.companyScope && c.company_name
      ? `${c.company_name} · ${c.customer_code} - ${c.customer_name}`
      : `${c.customer_code} - ${c.customer_name}`,
    description: c.sales_agent_code
      ? `${c.sales_agent_code} - ${c.sales_agent_name ?? ''}`.replace(/ - $/, '')
      : 'No sales agent',
    salesAgentId: c.sales_agent_id ?? null,
    customerGroupId: c.customer_group_id ?? null,
    customerGroupName: c.customer_group_name ?? null,
  }));
}

export interface CustomerLinkedContact {
  /** The link row id. Never rendered. */
  id: string;
  contact_id: string;
  name: string | null;
  phone_number: string | null;
  created_at: string;
}

/**
 * The WhatsApp contacts linked to a customer, for the customer detail page. Read-only.
 *
 *   GET /api/v1/order-management/customers/{customer_id}/linked-contacts
 *     -> { data: { id, contact_id, name, phone_number, created_at }[] }
 *   Gated `order_management.customers.view`; ordered by created_at; a customer hidden by
 *   the caller's company scope, or unknown, is a 404.
 */
export async function getCustomerLinkedContacts(customerId: string): Promise<CustomerLinkedContact[]> {
  const response = await apiFetch(`/api/v1/order-management/customers/${customerId}/linked-contacts`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load linked contacts'));
  }
  const body: { data?: CustomerLinkedContact[] } = await response.json();
  return body.data ?? [];
}
