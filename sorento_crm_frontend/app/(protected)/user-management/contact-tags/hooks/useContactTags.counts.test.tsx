/**
 * CONTACT-TAGS review F3: the tag picker never pays for differs counts: it asks for counts=false.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getContactTags = vi.hoisted(() => vi.fn());
vi.mock('../services/contactTagService', () => ({
  getContactTags, getContactTag: vi.fn(), createContactTag: vi.fn(), updateContactTag: vi.fn(),
  bulkTagContacts: vi.fn(),
}));

import { useContactTagOptions } from './useContactTags';

describe('useContactTagOptions', () => {
  it('requests counts=false', async () => {
    getContactTags.mockResolvedValue({ data: [{ id: 't1', name: 'Dealer' }], pagination: { total: 1 } });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useContactTagOptions(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
    });
    await waitFor(() => expect(result.current.data).toEqual([{ value: 't1', label: 'Dealer' }]));
    expect(getContactTags.mock.calls[0][1]).toMatchObject({ counts: 'false' });
  });
});
