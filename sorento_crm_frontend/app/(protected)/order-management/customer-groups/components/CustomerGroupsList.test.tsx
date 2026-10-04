/**
 * CustomerGroupsList (CUSTOMER-GROUP S3, AC-16, AC-17).
 *
 * Service boundary mocked (`../services/customerGroupService`); the real hooks and the real
 * DataGrid run. Expected service exports: getCustomerGroups, createCustomerGroup.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
Element.prototype.scrollIntoView = vi.fn();

const services = vi.hoisted(() => ({
  getCustomerGroups: vi.fn(),
  createCustomerGroup: vi.fn(),
}));
vi.mock('../services/customerGroupService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...services,
}));

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
  usePathname: () => '/order-management/customer-groups',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: 'k', config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));
const perms = vi.hoisted(() => ({ edit: true }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) =>
    slug === 'order_management.customers.edit' ? perms.edit : true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    // eslint-disable-next-line no-restricted-syntax -- test stub for SearchableSelect
    <select
      id={props.id}
      aria-label="Agent filter"
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="">All agents</option>
      {props.options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

import CustomerGroupsList from './CustomerGroupsList';

const ROWS = [
  {
    id: 'grp-1',
    name: 'HANLIM TRADING SDN BHD',
    ledger_count: 6,
    account_levels: [1, 2, 3, 4],
    updated_at: '2026-10-02T08:00:00',
  },
  {
    id: 'grp-2',
    name: 'CHIN CHUN HOMEMART SDN BHD',
    ledger_count: 5,
    account_levels: [1, 3, 4],
    updated_at: '2026-10-02T08:00:00',
  },
  {
    id: 'grp-3',
    name: 'JUBIN KEMUNING SDN BHD',
    ledger_count: 3,
    account_levels: [1],
    updated_at: '2026-10-02T08:00:00',
  },
];

function page(rows: typeof ROWS) {
  return { data: rows, pagination: { total: rows.length, page: 1, limit: 50 } };
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomerGroupsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.values(services).forEach((fn) => fn.mockReset());
  nav.push.mockReset();
  perms.edit = true;
});
afterEach(() => cleanup());

describe('CustomerGroupsList', () => {
  it('AC-16: renders the group name as a link to its detail page', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    renderList();

    const link = await screen.findByRole('link', { name: 'HANLIM TRADING SDN BHD' });
    expect(link).toHaveAttribute('href', '/order-management/customer-groups/grp-1');
  });

  it('AC-16: has Group, Ledgers, Accounts and Updated columns with the ledger count', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    renderList();

    await screen.findByText('HANLIM TRADING SDN BHD');
    for (const title of ['Group', 'Ledgers', 'Accounts', 'Updated']) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    const row = screen.getByText('HANLIM TRADING SDN BHD').closest('tr') as HTMLElement;
    expect(row.textContent).toContain('6');
  });

  it('AC-16: Accounts reads "A/C 1 to 4" for a contiguous run, "A/C 1, 3, 4" for a gap, "A/C 1" for one', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    renderList();

    await screen.findByText('HANLIM TRADING SDN BHD');
    expect(screen.getByText('A/C 1 to 4')).toBeInTheDocument();
    expect(screen.getByText('A/C 1, 3, 4')).toBeInTheDocument();
    expect(screen.getByText('A/C 1')).toBeInTheDocument();
  });

  it('AC-16: empty state heading "No customer groups" and no button in the empty block', async () => {
    services.getCustomerGroups.mockResolvedValue(page([]));
    renderList();

    const heading = await screen.findByText('No customer groups');
    const block = heading.parentElement as HTMLElement;
    expect(block.querySelector('button')).toBeNull();
  });

  it('AC-16: the grid uses the fixed table layout', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    const { container } = renderList();

    await screen.findByText('HANLIM TRADING SDN BHD');
    const table = container.querySelector('table') as HTMLElement;
    expect(table.className + table.style.tableLayout).toMatch(/table-fixed|fixed/);
  });

  it('AC-17: the dialog needs a name; submitting empty does not call create', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    renderList();

    fireEvent.click(await screen.findByRole('button', { name: /add group/i }));
    const dialog = await screen.findByRole('dialog');
    const submit = Array.from(dialog.querySelectorAll('button')).find((b) =>
      /^(create|add|save)/i.test(b.textContent ?? ''),
    ) as HTMLElement;
    fireEvent.click(submit);

    await waitFor(() => expect(screen.getByText(/name is required/i)).toBeInTheDocument());
    expect(services.createCustomerGroup).not.toHaveBeenCalled();
  });

  it('AC-17: a 409 shows "A group with this name already exists" inline and keeps the dialog open', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    services.createCustomerGroup.mockRejectedValue(
      new Error('A group with this name already exists'),
    );
    renderList();

    fireEvent.click(await screen.findByRole('button', { name: /add group/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: 'HANLIM TRADING SDN BHD' },
    });
    const submit = Array.from(dialog.querySelectorAll('button')).find((b) =>
      /^(create|add|save)/i.test(b.textContent ?? ''),
    ) as HTMLElement;
    fireEvent.click(submit);

    expect(await screen.findByText('A group with this name already exists')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
  });

  it('AC-17: success navigates to the new group detail page', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    services.createCustomerGroup.mockResolvedValue({
      id: 'grp-new',
      name: 'NEW GROUP SDN BHD',
      ledger_count: 0,
      account_levels: [],
    });
    renderList();

    fireEvent.click(await screen.findByRole('button', { name: /add group/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: 'NEW GROUP SDN BHD' } });
    const submit = Array.from(dialog.querySelectorAll('button')).find((b) =>
      /^(create|add|save)/i.test(b.textContent ?? ''),
    ) as HTMLElement;
    fireEvent.click(submit);

    await waitFor(() => expect(services.createCustomerGroup).toHaveBeenCalled());
    expect(services.createCustomerGroup.mock.calls[0][0]).toEqual({ name: 'NEW GROUP SDN BHD' });
    await waitFor(() =>
      expect(nav.push).toHaveBeenCalledWith('/order-management/customer-groups/grp-new'),
    );
  });

  it('Add group is rendered only with order_management.customers.edit', async () => {
    services.getCustomerGroups.mockResolvedValue(page(ROWS));
    perms.edit = false;
    const denied = renderList();
    await screen.findByText('HANLIM TRADING SDN BHD');
    expect(screen.queryByRole('button', { name: /add group/i })).toBeNull();
    denied.unmount();

    perms.edit = true;
    renderList();
    expect(await screen.findByRole('button', { name: /add group/i })).toBeInTheDocument();
  });
});

describe('CustomerGroupsList agent column and filter (CUSTOMER-SALES-AGENT, AC-8, AC-9)', () => {
  const AGENT_ROWS = [
    { ...ROWS[0], name: 'GRP-1 TRADING', sales_agent_label: 'AGENT-A', sales_agent_mixed: false },
    { ...ROWS[1], name: 'GRP-2 TRADING', sales_agent_label: null, sales_agent_mixed: true },
    { ...ROWS[2], name: 'GRP-3 TRADING', sales_agent_label: null, sales_agent_mixed: false },
  ];

  it('AC-8: shows the person label, a Mixed pill, or a dash', async () => {
    services.getCustomerGroups.mockResolvedValue(page(AGENT_ROWS));
    renderList();

    await screen.findByText('GRP-1 TRADING');
    expect(screen.getAllByText('Agent').length).toBeGreaterThan(0);
    const labelRow = screen.getByText('GRP-1 TRADING').closest('tr') as HTMLElement;
    expect(labelRow.textContent).toContain('AGENT-A');
    const mixedRow = screen.getByText('GRP-2 TRADING').closest('tr') as HTMLElement;
    expect(mixedRow.textContent).toContain('Mixed');
    const noneRow = screen.getByText('GRP-3 TRADING').closest('tr') as HTMLElement;
    expect(noneRow.textContent).not.toContain('Mixed');
    expect(noneRow.textContent).not.toContain('AGENT-A');
  });

  it('AC-9: choosing Mixed agents only calls the service with agent_mixed true', async () => {
    services.getCustomerGroups.mockResolvedValue(page(AGENT_ROWS));
    renderList();
    await screen.findByText('GRP-1 TRADING');
    expect(services.getCustomerGroups.mock.calls[0][0].agent_mixed).toBe(false);

    fireEvent.keyDown(screen.getByRole('button', { name: /filters/i }), { key: 'Enter' });
    const filters = await screen.findAllByLabelText('Agent filter');
    fireEvent.change(filters[filters.length - 1], { target: { value: 'mixed' } });

    await waitFor(() =>
      expect(
        services.getCustomerGroups.mock.calls.some(([params]) => params.agent_mixed === true),
      ).toBe(true),
    );
  });
});
