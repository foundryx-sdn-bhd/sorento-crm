/**
 * The group detail page in its loading and error states shows exactly ONE
 * "Back to customer groups" link (the page header's), never a second from the component.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
Element.prototype.scrollIntoView = vi.fn();

const services = vi.hoisted(() => ({
  getCustomerGroup: vi.fn(),
  updateCustomerGroup: vi.fn(),
  getCustomerGroupCustomers: vi.fn(),
  addCustomerGroupCustomers: vi.fn(),
}));
vi.mock('../services/customerGroupService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...services,
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/order-management/customer-groups/grp-1',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
// The header's own chrome needs app-level providers; only its title and actions slot matter here.
vi.mock('@/components/common/PageHeader', () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <header>
      <h1>{title}</h1>
      {actions}
    </header>
  ),
}));

import CustomerGroupDetailPage from './page';

async function renderPage() {
  const ui = await CustomerGroupDetailPage({ params: Promise.resolve({ id: 'grp-1' }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  Object.values(services).forEach((fn) => fn.mockReset());
  services.getCustomerGroupCustomers.mockResolvedValue({
    data: [],
    pagination: { total: 0, page: 1, limit: 50 },
  });
});
afterEach(() => cleanup());

describe('CustomerGroupDetail page back link', () => {
  it('loading: exactly one "Back to customer groups" link', async () => {
    services.getCustomerGroup.mockReturnValue(new Promise(() => {}));
    await renderPage();

    expect(screen.getAllByRole('link', { name: /back to customer groups/i })).toHaveLength(1);
  });

  it('error: exactly one "Back to customer groups" link', async () => {
    services.getCustomerGroup.mockRejectedValue(new Error('boom'));
    await renderPage();

    await screen.findByText(/boom|not found|could not|failed/i);
    expect(screen.getAllByRole('link', { name: /back to customer groups/i })).toHaveLength(1);
  });
});
