/**
 * CUSTOMER-BULK-OPS U3: bulk Unlink on the Contact Customers card.
 *
 * Contract: every row has a checkbox (not the picker's); with N ticked the header shows a
 * button `Unlink (N)`; clicking it parks ONE pending action per ticked link through
 * useDeferredBulkAction (key `contact_customer_link.unlink`, entity `contact_customer_link`,
 * entityId = the link row id) with no confirm dialog. The real hook runs; only the
 * pending-action service and the toast surface are mocked.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const services = vi.hoisted(() => ({
  getContactCustomers: vi.fn(),
  linkContactCustomers: vi.fn(),
}));
vi.mock('../services/contactCustomersService', () => services);

vi.mock('@/app/(protected)/order-management/customers/services/customerService', () => ({
  searchCustomersSelect: vi.fn().mockResolvedValue([]),
  CUSTOMER_SELECT_PAGE_SIZE: 50,
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => slug === 'user_management.contacts.edit',
}));

vi.mock('@/hooks/useDeferredRowAction', () => ({
  useDeferredRowAction: () => ({ run: vi.fn(), targetId: null, countdown: null, isPending: false }),
  useRowPending: () => () => false,
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), dismiss: vi.fn() },
}));
vi.mock('@/components/common/deferredToast', () => ({
  deferredToast: (input: { id?: string }) => input.id ?? 'toast',
  dismissDeferredToast: vi.fn(),
}));

const pending = vi.hoisted(() => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));
vi.mock('@/services/pendingActionService', () => pending);

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: () => <div>picker</div>,
}));

import ContactCustomersSection from './ContactCustomersSection';

const LINKS = ['a', 'b', 'c'].map((k, i) => ({
  id: `link-${k}`,
  customer_id: `cust-${k}`,
  customer_code: `C-${i + 1}00`,
  customer_name: `Hanlim ${k.toUpperCase()}`,
  is_active: true,
  source: 'manual',
  sales_agent_id: null,
  sales_agent_code: null,
  sales_agent_name: null,
  created_at: '2026-01-01T00:00:00',
}));

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactCustomersSection contactId="contact-1" />
    </QueryClientProvider>,
  );
}

function rowCheckboxes(): HTMLElement[] {
  return screen
    .getAllByRole('checkbox')
    .filter((c) => !/all/i.test(c.getAttribute('aria-label') ?? ''));
}

beforeEach(() => {
  vi.clearAllMocks();
  services.getContactCustomers.mockResolvedValue({ data: LINKS });
  pending.createPendingAction.mockImplementation(async (input: { entityId: string }) => ({
    id: `pa-${input.entityId}`,
    entity_id: input.entityId,
    commit_at: new Date(Date.now() + 10000).toISOString().replace(/\.\d+Z$/, ''),
    status: 'pending',
  }));
});
afterEach(() => cleanup());

describe('ContactCustomersSection - bulk Unlink', () => {
  it('every link row has a checkbox and ticking two shows "Unlink (2)"', async () => {
    renderSection();
    await screen.findByText('C-100 - Hanlim A');
    expect(rowCheckboxes()).toHaveLength(3);
    fireEvent.click(rowCheckboxes()[0]);
    fireEvent.click(rowCheckboxes()[1]);
    expect(await screen.findByRole('button', { name: 'Unlink (2)' })).toBeInTheDocument();
  });

  it('clicking it parks one contact_customer_link.unlink action per ticked link, no dialog', async () => {
    renderSection();
    await screen.findByText('C-100 - Hanlim A');
    fireEvent.click(rowCheckboxes()[0]);
    fireEvent.click(rowCheckboxes()[1]);
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink (2)' }));

    await waitFor(() => expect(pending.createPendingAction).toHaveBeenCalledTimes(2));
    const calls = pending.createPendingAction.mock.calls.map((c) => c[0]);
    expect(calls.map((c) => c.entityId).sort()).toEqual(['link-a', 'link-b']);
    for (const c of calls) {
      expect(c.actionKey).toBe('contact_customer_link.unlink');
      expect(c.entityType).toBe('contact_customer_link');
    }
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('R6: while the bulk countdown shows, the ticked rows per-row Unlink buttons are disabled', async () => {
    renderSection();
    await screen.findByText('C-100 - Hanlim A');
    fireEvent.click(rowCheckboxes()[0]);
    fireEvent.click(rowCheckboxes()[1]);
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink (2)' }));
    await waitFor(() => expect(pending.createPendingAction).toHaveBeenCalledTimes(2));

    const rowButtons = screen.getAllByRole('button', { name: /^Unlink$/ });
    expect(rowButtons.length).toBeGreaterThanOrEqual(2);
    // The ticked rows (A and B) are first in document order; their buttons must be disabled.
    expect(rowButtons[0]).toBeDisabled();
    expect(rowButtons[1]).toBeDisabled();
  });
});
