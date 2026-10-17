/**
 * useBillingDocuments hooks: each hook calls its service with the right arguments, the
 * outstanding query is skippable when the caller lacks the view slug, and the list key is
 * shared with the record page's prev/next pager.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const service = vi.hoisted(() => ({
  getBillingAgentOptions: vi.fn(),
  getBillingDocument: vi.fn(),
  getBillingDocuments: vi.fn(),
  getCustomerOutstanding: vi.fn(),
}));
vi.mock('../services/billingDocumentService', () => service);

import {
  billingDocumentsListKey,
  useBillingAgentOptions,
  useBillingDocumentQuery,
  useBillingDocumentsQuery,
  useCustomerOutstandingQuery,
} from './useBillingDocuments';

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const PARAMS = { pageIndex: 0, pageSize: 20, sorting: [], searchQuery: '', openOnly: true };

beforeEach(() => {
  Object.values(service).forEach((fn) => fn.mockReset());
});

describe('useBillingDocumentsQuery', () => {
  it('loads the list with the params it was given', async () => {
    service.getBillingDocuments.mockResolvedValue({ data: [], pagination: { total: 0 } });
    const { result } = renderHook(() => useBillingDocumentsQuery(PARAMS), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(service.getBillingDocuments).toHaveBeenCalledWith(PARAMS);
  });

  it('does not call the service when disabled', () => {
    renderHook(() => useBillingDocumentsQuery(PARAMS, false), { wrapper });
    expect(service.getBillingDocuments).not.toHaveBeenCalled();
  });

  it('builds the key the pager shares: list params are part of it', () => {
    const key = billingDocumentsListKey(PARAMS);
    expect(key[0]).toBe('billing-documents');
    expect(key[1]).toEqual(PARAMS);
  });
});

describe('useBillingDocumentQuery', () => {
  it('loads one record by id', async () => {
    service.getBillingDocument.mockResolvedValue({ id: 'd1' });
    const { result } = renderHook(() => useBillingDocumentQuery('d1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(service.getBillingDocument).toHaveBeenCalledWith('d1');
  });

  it('waits for an id', () => {
    renderHook(() => useBillingDocumentQuery(null), { wrapper });
    expect(service.getBillingDocument).not.toHaveBeenCalled();
  });
});

describe('useCustomerOutstandingQuery', () => {
  it('loads the customer total', async () => {
    service.getCustomerOutstanding.mockResolvedValue({
      customer_id: 'c1',
      ar_outstanding: 15230.4,
      ar_as_of: '2026-10-26T09:00:00',
    });
    const { result } = renderHook(() => useCustomerOutstandingQuery('c1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(service.getCustomerOutstanding).toHaveBeenCalledWith('c1');
    expect(result.current.data?.ar_outstanding).toBe(15230.4);
  });

  it('makes no call without the view slug (enabled false) or without a customer', () => {
    renderHook(() => useCustomerOutstandingQuery('c1', false), { wrapper });
    renderHook(() => useCustomerOutstandingQuery(null), { wrapper });
    expect(service.getCustomerOutstanding).not.toHaveBeenCalled();
  });
});

describe('useBillingAgentOptions', () => {
  it('loads the agent options', async () => {
    service.getBillingAgentOptions.mockResolvedValue([{ value: 'AG-X', label: 'AG-X' }]);
    const { result } = renderHook(() => useBillingAgentOptions(), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual([{ value: 'AG-X', label: 'AG-X' }]));
  });
});
