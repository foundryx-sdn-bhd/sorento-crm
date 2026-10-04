/**
 * Contact -> customers links - feature service.
 *
 * Layering: ContactCustomersSection -> hooks (useContactCustomers) -> THIS service -> lib/api.
 *
 * Backend contract (PLAN-contact-customers-29sep D2), read `user_management.contacts.view`,
 * write `user_management.contacts.edit`:
 *   GET   /api/v1/user-management/contacts/{contact_id}/customers
 *           -> { data: ContactCustomerLink[] }, ordered by created_at.
 *   POST  /api/v1/user-management/contacts/{contact_id}/customers
 *           body { customer_id } -> 201 (any other key is a 422) ContactCustomerLink
 *           Idempotent on the pair. Unknown or out-of-scope customer, unknown contact: 404.
 *   Unlink has NO route: it is the pending action `contact_customer_link.unlink`, entity type
 *   `contact_customer_link`, entity id = the LINK id (`ContactCustomerLink.id`), reversible
 *   window, permission `user_management.contacts.edit`. Parked by `useDeferredRowAction`.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface ContactCustomerLink {
  /** The link row id. Never rendered; it is the unlink action's entity id. */
  id: string;
  customer_id: string;
  customer_code: string;
  customer_name: string;
  /** The customer's company: the contact page reads every granted company. */
  company_name?: string | null;
  is_active: boolean;
  source: string;
  sales_agent_id: string | null;
  sales_agent_code: string | null;
  sales_agent_name: string | null;
  created_at: string;
}

export interface ContactCustomers {
  data: ContactCustomerLink[];
}

const base = (contactId: string) => `/api/v1/user-management/contacts/${contactId}/customers`;

// ---- service -----------------------------------------------------------------------------

export async function getContactCustomers(contactId: string): Promise<ContactCustomers> {
  const response = await apiFetch(base(contactId));
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load customers'));
  }
  const body: Partial<ContactCustomers> = await response.json();
  return { data: body.data ?? [] };
}

export async function linkContactCustomers(
  contactId: string,
  customerIds: string[],
): Promise<ContactCustomerLink[]> {
  const response = await apiFetch(base(contactId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_ids: customerIds }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to link customers'));
  }
  const body: { data?: ContactCustomerLink[] } = await response.json();
  return body.data ?? [];
}
