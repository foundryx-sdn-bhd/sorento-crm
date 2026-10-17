/**
 * The list query the record page's prev/next walks. The list writes its page, sort, search and
 * filters into the record URL on a row click (`billingFilterQuery`); this parses them back so
 * the pager steps through the same filtered page the reader came from, off the same cache key.
 */
import type { QueryKey } from '@tanstack/react-query';
import type { ListPagerPage, ListPagerParams } from '@/hooks/useListPager';
import { billingDocumentsListKey } from '../hooks/useBillingDocuments';
import { getBillingDocuments } from '../services/billingDocumentService';
import type {
  BillingDocumentListParams,
  BillingDocumentStatus,
  BillingDocumentType,
} from '../types/billingDocument.types';

export const DEFAULT_BILLING_SORT = { id: 'doc_date', desc: true } as const;

/** The list's filters as the record URL carries them. Empty values are dropped. */
export function billingFilterQuery(f: {
  documentTypes?: BillingDocumentType[];
  status?: string;
  customerId?: string;
  agentCode?: string;
  dateFrom?: string | null;
  dateTo?: string | null;
  openOnly?: boolean;
}): string {
  const sp = new URLSearchParams();
  if (f.documentTypes?.length) sp.set('document_type', f.documentTypes.join(','));
  if (f.status) sp.set('status', f.status);
  if (f.customerId) sp.set('customer_id', f.customerId);
  if (f.agentCode) sp.set('agent_code', f.agentCode);
  if (f.dateFrom) sp.set('date_from', f.dateFrom);
  if (f.dateTo) sp.set('date_to', f.dateTo);
  if (f.openOnly) sp.set('open_only', 'true');
  return sp.toString();
}

export function billingListParamsFromUrl(params: ListPagerParams): BillingDocumentListParams {
  const f = params.filters;
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting.length ? params.sorting : [DEFAULT_BILLING_SORT],
    searchQuery: params.searchQuery,
    documentTypes: f.document_type
      ? (f.document_type.split(',') as BillingDocumentType[])
      : undefined,
    status: (f.status as BillingDocumentStatus | undefined) || undefined,
    customerId: f.customer_id || undefined,
    agentCode: f.agent_code || undefined,
    dateFrom: f.date_from || undefined,
    dateTo: f.date_to || undefined,
    openOnly: f.open_only === 'true' ? true : undefined,
  };
}

export const billingPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    billingDocumentsListKey(billingListParamsFromUrl(params)) as unknown as QueryKey,
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getBillingDocuments(billingListParamsFromUrl(params)),
};
