/**
 * CustomerGroupLedgersCard (CUSTOMER-GROUP review round): a group bigger than one fetch must
 * not be silently capped at 50 rows. The card renders as a paged grid whose count is the
 * server total, and keeps its "Open group" link.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/order-management/customers/c-1',
  useSearchParams: () => new URLSearchParams(),
}));

const getCustomerGroupCustomers = vi.fn();
vi.mock('../../customer-groups/services/customerGroupService', () => ({
  getCustomerGroupCustomers: (...a: unknown[]) => getCustomerGroupCustomers(...a),
  getCustomerGroups: vi.fn(),
  getCustomerGroup: vi.fn(),
  searchCustomerGroupsSelect: vi.fn().mockResolvedValue([]),
}));

import CustomerGroupLedgersCard from './CustomerGroupLedgersCard';

const FIFTY = Array.from({ length: 50 }, (_, i) => ({
  id: `c-${i}`,
  customer_code: `ZZ-${String(i).padStart(3, '0')}`,
  customer_name: `LEDGER ${i}`,
  account_level: null,
  is_active: true,
}));

afterEach(() => cleanup());

describe('CustomerGroupLedgersCard', () => {
  it('a 60-ledger group shows the server total in a pager and links to the group', async () => {
    getCustomerGroupCustomers.mockResolvedValue({
      data: FIFTY,
      pagination: { total: 60, page: 1, limit: 50 },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CustomerGroupLedgersCard customerId="c-1" groupId="grp-1" />
      </QueryClientProvider>,
    );

    await screen.findByText('ZZ-000');
    expect(screen.getByText(/of 60\b/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open group' })).toHaveAttribute(
      'href',
      '/order-management/customer-groups/grp-1',
    );
  });
});
