/**
 * CONTACT-TAGS S2, UAC T4.1 / mock view G: the contact detail Tags section's cross-check.
 * Assumed: ./ContactTagSettingsSection default export, props { contactId: string }; reads
 * getContactTagSettings(contactId) from ../services/contactTagSettingsService, answering the
 * contract shape { status, tags: [{id,name}], items: [{section,key,label,current,expected,
 * decided_by: string[], conflict, conflict_tags: [{name,value}], differs}] }.
 * Differing rows show label, current, expected and "decided by"; conflicts are marked "Conflict";
 * matching items are not listed; matches / none show an empty state heading.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getContactTagSettings = vi.hoisted(() => vi.fn());
vi.mock('../services/contactTagSettingsService', () => ({ getContactTagSettings }));

import ContactTagSettingsSection from './ContactTagSettingsSection';

const item = (over: Record<string, unknown>) => ({
  section: 'chatbot', key: 'k', label: 'L', current: null, expected: null, decided_by: [],
  conflict: false, conflict_tags: [], differs: false, ...over,
});

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  rtlRender(
    <QueryClientProvider client={client}>
      <ContactTagSettingsSection contactId="c1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('ContactTagSettingsSection', () => {
  it('lists differing items with current, expected and decided by; marks conflicts; skips matches', async () => {
    getContactTagSettings.mockResolvedValue({
      status: 'differs',
      tags: [{ id: 't1', name: 'Dealer' }, { id: 't2', name: 'Retail' }],
      items: [
        item({ label: 'Stock checks', current: true, expected: false, decided_by: ['Retail'], differs: true }),
        item({
          label: 'Skip period questions', conflict: true, differs: true,
          conflict_tags: [{ name: 'Dealer', value: true }, { name: 'Retail', value: false }],
        }),
        item({ label: 'Escalation', current: true, expected: true, differs: false }),
      ],
    });
    render();
    expect(await screen.findByText('Stock checks')).toBeInTheDocument();
    expect(screen.getAllByText(/Retail/).length).toBeGreaterThan(0);
    expect(screen.getByText('Skip period questions')).toBeInTheDocument();
    expect(screen.getByText(/conflict/i)).toBeInTheDocument();
    expect(screen.queryByText('Escalation')).toBeNull();
  });

  it.each(['matches', 'none'])('shows an empty state heading when the status is %s', async (status) => {
    getContactTagSettings.mockResolvedValue({
      status, tags: [{ id: 't1', name: 'Dealer' }], items: [item({ label: 'Escalation' })],
    });
    render();
    expect(await screen.findByRole('heading', { name: /match|no differences|no tag settings/i })).toBeInTheDocument();
    expect(screen.queryByText('Escalation')).toBeNull();
  });

  it('shows a readable error when the read fails', async () => {
    getContactTagSettings.mockRejectedValue(new Error('Failed to load tag settings'));
    render();
    expect(await screen.findByText(/failed to load tag settings/i)).toBeInTheDocument();
  });
});
