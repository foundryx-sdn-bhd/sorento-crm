/**
 * ACCESS-MODEL final review: the legacy per-contact field-access dialog is retired. It wrote
 * `agent_field_access` rows that the access tree no longer reads, so an admin deny there did
 * nothing. The agents table must not offer it.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactAccessAgentsTable from './ContactAccessAgentsTable';

const dialogRendered = vi.hoisted(() => ({ count: 0 }));

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contacts/c1',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api', () => ({
  apiFetch: async (url: string) => {
    const body = url.includes('/access-agents?page=1&limit=1000')
      ? { data: [] }
      : url.includes('/contacts/c1/access-agents')
        ? {
            data: [
              {
                id: 'a1',
                respond_contact_phone: '+60000000000',
                respond_contact_name: 'CONTACT O',
                agent_id: 'ag1',
                agent_code: 'incoming_stock_enquiries',
                agent_name: 'Incoming Stock Agent',
                is_allowed: true,
                valid_from: null,
                valid_to: null,
                created_at: '2026-09-01T00:00:00',
                synced_to_excel: false,
              },
            ],
            pagination: { total: 1, page: 1, limit: 50 },
          }
        : { id: 'c1', phone_number: '+60000000000', name: 'CONTACT O' };
    return { ok: true, json: async () => body };
  },
}));
vi.mock('../../../access-agents/components/ContactAgentAccessDialog', () => ({ default: () => null }));
vi.mock('../../../access-agents/components/contact-agent-access-delete-dialog', () => ({ default: () => null }));
vi.mock('./CopyAccessAgentsFromContactDialog', () => ({ default: () => null }));
vi.mock('../../../access-agents/components/ContactFieldAccessDialog', () => ({
  default: () => {
    dialogRendered.count += 1;
    return <div data-testid="field-access-dialog" />;
  },
}));

afterEach(() => {
  cleanup();
  dialogRendered.count = 0;
});

describe('ContactAccessAgentsTable', () => {
  it('has no field access button and never opens the field access dialog', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContactAccessAgentsTable contactId="c1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText('Incoming Stock Agent')).toBeTruthy());
    expect(screen.queryByTitle('Fields this contact may be told')).toBeNull();
    expect(screen.queryByTestId('field-access-dialog')).toBeNull();
    expect(dialogRendered.count).toBe(0);
  });
});
