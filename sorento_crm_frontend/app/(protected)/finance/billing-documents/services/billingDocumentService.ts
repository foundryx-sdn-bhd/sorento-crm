/**
 * Billing documents - feature service (PLAN-finance-billing-documents-27sep section 3.5;
 * UAC S2, S3; CARD-ac-billing-17oct S5 outstanding). Read only.
 *
 * Layering: components -> hooks (useBillingDocuments) -> THIS service -> lib/api.
 *
 * Backend contract (module `finance`, mounted at /api/v1/finance behind its module guard):
 *   GET /finance/billing-documents                 -> BillingDocumentListResponse   .view
 *       DataGrid params via buildDataGridParams (page, limit, sort, dir, query) plus
 *       document_type (comma separated, multi), status, customer_id, agent_code,
 *       date_from, date_to, open_only (true), sales_order_id
 *   GET /finance/billing-documents/{id}            -> BillingDocumentDetail         .view
 *       header, lines and related in one response. Prev and next walk the list page the
 *       user came from (ListPager), so the record carries no prev_id or next_id.
 *   GET /finance/customers/{customer_id}/outstanding
 *                                                  -> CustomerOutstanding           .view
 *       ar_outstanding and ar_as_of are null when the customer has not been synced.
 *   GET /finance/billing-documents/agent-options   -> {data: {value, label}[]}      .view
 *   Customer options reuse searchCustomersSelect (order-management customers/select).
 *   Export: POST /list-query/export, resource `finance_billing_documents`, needs
 *   `finance.billing_documents.export` and `.view`.
 *
 * Phase 1: USE_MOCK serves everything from mock/billingDocuments.mock.ts. Phase 2 flips it
 * to false and deletes the mock.
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import { searchCustomersSelect } from '@/app/(protected)/order-management/customers/services/customerService';
import {
  mockAgentOptions,
  mockCustomerOptions,
  mockDetail,
  mockList,
  mockOutstanding,
} from '../mock/billingDocuments.mock';
import type {
  BillingDocumentDetail,
  BillingDocumentListParams,
  BillingDocumentListResponse,
  BillingSelectOption,
  CustomerOutstanding,
} from '../types/billingDocument.types';

export const USE_MOCK = true;

const BASE = '/api/v1/finance/billing-documents';

async function read<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

function mockDelay<T>(value: () => T, ms = 250): Promise<T> {
  return new Promise((resolve, reject) => {
    setTimeout(() => {
      try {
        resolve(value());
      } catch (e) {
        reject(e);
      }
    }, ms);
  });
}

export async function getBillingDocuments(
  params: BillingDocumentListParams,
): Promise<BillingDocumentListResponse> {
  if (USE_MOCK) return mockDelay(() => mockList(params));
  const usp = buildDataGridParams(params, {
    document_type: params.documentTypes?.length ? params.documentTypes.join(',') : undefined,
    status: params.status,
    customer_id: params.customerId,
    agent_code: params.agentCode,
    date_from: params.dateFrom,
    date_to: params.dateTo,
    open_only: params.openOnly ? true : undefined,
    sales_order_id: params.salesOrderId,
  });
  return read(await apiFetch(`${BASE}?${usp.toString()}`), 'Failed to load billing documents');
}

export async function getBillingDocument(id: string): Promise<BillingDocumentDetail> {
  if (USE_MOCK) {
    return mockDelay(() => {
      const doc = mockDetail(id);
      if (!doc) throw new Error('Billing document not found');
      return doc;
    });
  }
  return read(await apiFetch(`${BASE}/${id}`), 'Failed to load billing document');
}

export async function getCustomerOutstanding(customerId: string): Promise<CustomerOutstanding> {
  if (USE_MOCK) return mockDelay(() => mockOutstanding(customerId), 150);
  return read(
    await apiFetch(`/api/v1/finance/customers/${customerId}/outstanding`),
    'Failed to load outstanding',
  );
}

export async function searchBillingCustomerOptions(query: string): Promise<BillingSelectOption[]> {
  if (USE_MOCK) return mockDelay(() => mockCustomerOptions(query), 100);
  const rows = await searchCustomersSelect(query);
  return rows.map((r) => ({ value: r.value, label: r.label }));
}

export async function getBillingAgentOptions(): Promise<BillingSelectOption[]> {
  if (USE_MOCK) return mockDelay(() => mockAgentOptions(), 100);
  const body = await read<{ data: BillingSelectOption[] }>(
    await apiFetch(`${BASE}/agent-options`),
    'Failed to load agents',
  );
  return body.data;
}
