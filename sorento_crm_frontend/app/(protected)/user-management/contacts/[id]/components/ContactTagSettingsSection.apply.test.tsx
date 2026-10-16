/**
 * CONTACT-TAGS S3, UAC T5.6: the contact detail Tags section carries an "Apply tag settings" button
 * that opens ApplyTagSettingsDialog for that one contact.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../services/contactTagSettingsService', () => ({
  getContactTagSettings: vi.fn().mockResolvedValue({
    status: 'differs',
    tags: [{ id: 't1', name: 'Dealer' }],
    items: [{ section: 'chatbot', key: 'k', label: 'Stock checks', current: true, expected: false,
      decided_by: ['Dealer'], conflict: false, conflict_tags: [], differs: true }],
  }),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('../../components/ApplyTagSettingsDialog', () => ({
  default: (props: { open: boolean; contactIds: string[] }) =>
    props.open ? <div data-testid="apply-dialog">{props.contactIds.join(',')}</div> : null,
}));

import ContactTagSettingsSection from './ContactTagSettingsSection';

describe('ContactTagSettingsSection apply button', () => {
  it('opens the apply dialog for this contact only', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={client}>
        <ContactTagSettingsSection contactId="c1" />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId('apply-dialog')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Apply tag settings' }));
    expect(screen.getByTestId('apply-dialog')).toHaveTextContent('c1');
  });
});
