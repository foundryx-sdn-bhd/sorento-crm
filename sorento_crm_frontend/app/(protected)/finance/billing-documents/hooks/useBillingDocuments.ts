import { useQuery } from '@tanstack/react-query';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import {
  getBillingAgentOptions,
  getBillingDocument,
  getBillingDocuments,
  getCustomerOutstanding,
} from '../services/billingDocumentService';
import type { BillingDocumentListParams } from '../types/billingDocument.types';

export const BILLING_DOCUMENTS_KEY = ['billing-documents'] as const;
export const BILLING_DOCUMENT_KEY = ['billing-document'] as const;
export const CUSTOMER_OUTSTANDING_KEY = ['billing-customer-outstanding'] as const;
export const BILLING_AGENT_OPTIONS_KEY = ['billing-agent-options'] as const;

/** One key builder for the list and the record's prev/next pager, so they share the cache. */
export function billingDocumentsListKey(params: BillingDocumentListParams) {
  return [...BILLING_DOCUMENTS_KEY, params] as const;
}

export function useBillingDocumentsQuery(params: BillingDocumentListParams, enabled = true) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: billingDocumentsListKey(params),
    queryFn: () => getBillingDocuments(params),
    enabled,
  });
}

export function useBillingDocumentQuery(id: string | null) {
  return useQuery({
    queryKey: [...BILLING_DOCUMENT_KEY, id],
    queryFn: () => getBillingDocument(id as string),
    enabled: !!id,
    retry: 1,
  });
}

/** Pass `enabled` false to skip the call when the caller lacks the view slug. */
export function useCustomerOutstandingQuery(customerId: string | null, enabled = true) {
  return useQuery({
    queryKey: [...CUSTOMER_OUTSTANDING_KEY, customerId],
    queryFn: () => getCustomerOutstanding(customerId as string),
    enabled: !!customerId && enabled,
    staleTime: 60_000,
  });
}

export function useBillingAgentOptions() {
  return useQuery({
    queryKey: BILLING_AGENT_OPTIONS_KEY,
    queryFn: getBillingAgentOptions,
    staleTime: 60_000,
  });
}
