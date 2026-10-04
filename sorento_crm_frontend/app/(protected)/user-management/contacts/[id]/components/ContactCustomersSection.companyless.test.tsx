/**
 * CONTACT-COMPANYLESS AC3 + AC4 + AC7 (FE half). The contact card's "Add customers" picker
 * asks for every GRANTED company and reads `<Company> · <code> - <name>`; linked rows show
 * their company too. Asserted at the `apiFetch` seam (real customerService + picker hook),
 * so it holds however the flag is threaded through.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

const services = vi.hoisted(() => ({
  getContactCustomers: vi.fn(),
  linkContactCustomers: vi.fn(),
}));
vi.mock('../services/contactCustomersService', () => services);

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));
vi.mock('@/hooks/useDeferredRowAction', () => ({
  useDeferredRowAction: () => ({ run: vi.fn(), targetId: null, countdown: null, isPending: false }),
  useRowPending: () => () => false,
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    fetchOptions?: (q: string) => Promise<{ value: string; label: string }[]>;
  }) => {
    const [opts, setOpts] = React.useState<{ value: string; label: string }[]>([]);
    return (
      <div>
        <button type="button" onClick={() => void props.fetchOptions?.('').then(setOpts)}>
          open picker
        </button>
        {opts.map((o) => (
          <span key={o.value} data-testid="option">
            {o.label}
          </span>
        ))}
      </div>
    );
  },
}));

import ContactCustomersSection from './ContactCustomersSection';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

beforeEach(() => {
  apiFetch.mockReset();
  Object.values(services).forEach((fn) => fn.mockReset());
  services.getContactCustomers.mockResolvedValue({
    data: [
      {
        id: 'link-1',
        customer_id: 'cust-1',
        customer_code: 'C-300',
        customer_name: 'Hanlim Gamma',
        company_name: 'Mocha',
        is_active: true,
        source: 'manual',
        sales_agent_id: null,
        sales_agent_code: null,
        sales_agent_name: null,
        created_at: '2026-01-01T00:00:00',
      },
    ],
  });
  apiFetch.mockResolvedValue(
    jsonResponse({
      data: [
        {
          id: 'cust-9',
          customer_code: 'C-900',
          customer_name: 'Mocha Trading',
          company_id: 'co-2',
          company_name: 'Mocha',
        },
      ],
    }),
  );
});

afterEach(() => cleanup());

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactCustomersSection contactId="contact-1" />
    </QueryClientProvider>,
  );
}

describe('ContactCustomersSection - every granted company', () => {
  it('AC3: the Add customers search sends company_scope=grants', async () => {
    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'open picker' }));
    await screen.findByTestId('option');

    const url = new URL(apiFetch.mock.calls[apiFetch.mock.calls.length - 1][0], 'http://localhost');
    expect(url.pathname).toBe('/api/v1/order-management/customers/select');
    expect(url.searchParams.get('company_scope')).toBe('grants');
  });

  it('AC7: each option reads "<Company> · <code> - <name>"', async () => {
    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'open picker' }));

    expect((await screen.findByTestId('option')).textContent).toBe('Mocha · C-900 - Mocha Trading');
  });

  it('AC4/AC7: a linked row shows its company name', async () => {
    renderSection();

    expect(await screen.findByText(/Mocha · C-300 - Hanlim Gamma/)).toBeInTheDocument();
  });
});
