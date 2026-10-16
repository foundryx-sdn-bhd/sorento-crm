/**
 * CONTACT-TAGS re-review blocker: a section the viewer may not read comes back with
 * current / expected / decided_by null (and no conflict tags). The Tags section must render the
 * generic label and no values, never crash.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../services/contactTagSettingsService', () => ({
  getContactTagSettings: vi.fn().mockResolvedValue({
    status: 'differs',
    tags: [{ id: 't1', name: 'Dealer' }],
    items: [
      { section: 'agents', key: 'x', label: 'Chatbot agents', current: null, expected: null,
        decided_by: null, conflict: false, conflict_tags: null, differs: true },
      { section: 'stock_visibility', key: 'mode', label: 'Stock visibility', current: null, expected: null,
        decided_by: null, conflict: true, conflict_tags: null, differs: true },
    ],
  }),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('../../components/ApplyTagSettingsDialog', () => ({ default: () => null }));

import ContactTagSettingsSection from './ContactTagSettingsSection';

describe('ContactTagSettingsSection withheld items', () => {
  it('shows the generic labels and does not crash on null values', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={client}>
        <ContactTagSettingsSection contactId="c1" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Chatbot agents')).toBeInTheDocument();
    expect(screen.getByText('Stock visibility')).toBeInTheDocument();
  });
});
