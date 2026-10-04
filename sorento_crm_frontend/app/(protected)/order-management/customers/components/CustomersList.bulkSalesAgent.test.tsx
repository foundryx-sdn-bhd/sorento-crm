/**
 * CUSTOMER-BULK-OPS U2: bulk "Set sales agent" on the customers list.
 *
 * Contract the implementation must match:
 * - bulk strip button labelled `Set sales agent (N)`, only with `master_data.sales_agents.edit`
 * - opens a dialog (role=dialog) holding a SearchableSelect fed by `options` from
 *   useCustomerSalesAgentOptions, and an `Apply` button disabled until an agent is chosen
 * - Apply calls assignSalesAgentCustomers(agentId, customerIds) once; success toast and the
 *   selection clears; failure shows toast.error(message) and keeps the selection
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Customer } from '../types/customer.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/order-management/customers',
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: toastMock }));

vi.mock('@/components/upload-activity', () => ({
  useImportJobDrawer: () => ({ notifyImportQueued: vi.fn() }),
}));

const perms = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('../services/customerImportService', () => ({
  importCustomers: vi.fn(),
  validateCustomerImport: vi.fn(),
}));

const svc = vi.hoisted(() => ({ assignSalesAgentCustomers: vi.fn() }));
vi.mock(
  '@/app/(protected)/master-data-management/sales-agents/services/salesAgentService',
  async (importOriginal) => ({
    ...(await importOriginal<
      typeof import('@/app/(protected)/master-data-management/sales-agents/services/salesAgentService')
    >()),
    assignSalesAgentCustomers: svc.assignSalesAgentCustomers,
  }),
);

vi.mock('../hooks/useCustomerSalesAgentOptions', () => ({
  useCustomerSalesAgentOptions: () => ({
    options: [
      { value: 'agent-1', label: 'SEAN I - Sean Tan', code: 'SEAN I' },
      { value: 'agent-2', label: 'MAY K - May Koh', code: 'MAY K' },
    ],
    isLoading: false,
  }),
}));

// Native stand-in for the Radix select so a test can choose an option by label.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={props.placeholder ?? 'select'}
      value={props.value ?? ''}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="" />
      {(props.options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const ROWS: Customer[] = [
  ['cust-a', 'C-001', 'Alpha Trading'],
  ['cust-b', 'C-002', 'Beta Supplies'],
  ['cust-c', 'C-003', 'Gamma Foods'],
].map(([id, code, name], i) => ({
  id,
  customer_code: code,
  customer_name: name,
  email: null,
  phone_number: null,
  is_active: true,
  created_at: new Date(`2026-01-0${i + 1}T00:00:00Z`),
  sales_agent_id: null,
  sales_agent_code: null,
  sales_agent_name: null,
})) as Customer[];

vi.mock('../hooks/useCustomers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../hooks/useCustomers')>()),
  useCustomers: () => ({
    data: { data: ROWS, pagination: { page: 1, limit: 50, total: ROWS.length } },
    isLoading: false,
    isPlaceholderData: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
}));

import CustomersList from './CustomersList';

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomersList />
    </QueryClientProvider>,
  );
}

function tickRow(code: string) {
  const row = screen.getByText(code).closest('tr') as HTMLElement;
  fireEvent.click(within(row).getByRole('checkbox'));
}

async function openDialogWithTwoSelected() {
  renderList();
  tickRow('C-001');
  tickRow('C-002');
  fireEvent.click(await screen.findByRole('button', { name: /Set sales agent \(2\)/ }));
  return await screen.findByRole('dialog');
}

beforeEach(() => {
  vi.clearAllMocks();
  perms.granted = new Set(['master_data.sales_agents.edit']);
  svc.assignSalesAgentCustomers.mockResolvedValue([]);
});

describe('CustomersList - bulk Set sales agent', () => {
  it('shows "Set sales agent (2)" once two rows are selected', async () => {
    renderList();
    expect(screen.queryByRole('button', { name: /Set sales agent/ })).toBeNull();
    tickRow('C-001');
    tickRow('C-002');
    expect(await screen.findByRole('button', { name: 'Set sales agent (2)' })).toBeInTheDocument();
  });

  it('opens a dialog whose Apply is disabled until an agent is chosen', async () => {
    const dialog = await openDialogWithTwoSelected();
    const apply = within(dialog).getByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'agent-1' } });
    expect(apply).toBeEnabled();
  });

  it('Apply calls assignSalesAgentCustomers once with the agent and both ids, toasts, clears selection', async () => {
    const dialog = await openDialogWithTwoSelected();
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'agent-1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(svc.assignSalesAgentCustomers).toHaveBeenCalledTimes(1));
    const [agentId, ids] = svc.assignSalesAgentCustomers.mock.calls[0];
    expect(agentId).toBe('agent-1');
    expect([...ids].sort()).toEqual(['cust-a', 'cust-b']);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Set sales agent/ })).toBeNull(),
    );
  });

  it('a failed assign shows the error toast and keeps the selection', async () => {
    svc.assignSalesAgentCustomers.mockRejectedValue(new Error('Customer not in your company'));
    const dialog = await openDialogWithTwoSelected();
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'agent-2' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('Customer not in your company'),
      ),
    );
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Set sales agent \(2\)/ })).toBeInTheDocument();
  });

  it('without master_data.sales_agents.edit the bulk action is absent', async () => {
    perms.granted = new Set();
    renderList();
    tickRow('C-001');
    tickRow('C-002');
    await screen.findByText(/2 selected/i);
    expect(screen.queryByRole('button', { name: /Set sales agent/ })).toBeNull();
  });
});
