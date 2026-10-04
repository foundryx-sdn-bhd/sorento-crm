/**
 * GRNList - "Pull from AutoCount" (lane GRN-PULL-CRM, AC-GP-60, review S8): the Actions menu
 * carries it only when the shared action says the caller holds
 * `procurement.grn.autocount_pull`; a click with no open pull opens the DocDate dialog, a
 * click with one reviews it. Same harness as `GRNList.test.tsx`, plus the action hook.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  });
}
Element.prototype.scrollIntoView = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => '/procurement-management/grn',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('@/components/upload-activity', () => ({
  useImportJobDrawer: () => ({ notifyImportQueued: vi.fn() }),
}));

const useGRNsMock = vi.fn();
// The row "..." menu resolves permissions; this test has no session or query
// client, and RBAC has its own tests.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('../hooks/useGRN', () => ({
  useGRNs: (...args: unknown[]) => useGRNsMock(...args),
  // The bulk-delete dialog renders with the list, so its hook has to exist.
  useBulkDeleteGRNs: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // The row "..." menu shares the record's action set, which sets status.
  useUpdateGRN: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useDeleteGRN: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const action = { visible: true, label: 'Pull from AutoCount', hasOpenPull: false, onSelect: vi.fn() };
const useAutocountPullAction = vi.fn(() => action);
vi.mock('@/app/(protected)/system-management/import-jobs/autocount-pull/hooks/useAutocountPull', () => ({
  useAutocountPullAction: (...a: unknown[]) => useAutocountPullAction(...(a as [])),
}));

import GRNList from './GRNList';

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GRNList />
    </QueryClientProvider>,
  );
}

function openActions() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /^Actions/i }), { button: 0 });
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  Object.assign(action, { visible: true, label: 'Pull from AutoCount', hasOpenPull: false });
  useGRNsMock.mockReturnValue({ data: { data: [], pagination: { total: 0, page: 1, limit: 50 } }, isLoading: false });
});

describe('GRNList - Pull from AutoCount', () => {
  it('asks the shared action for the goods_receive_notes entity', () => {
    renderList();
    expect(useAutocountPullAction).toHaveBeenCalledWith('goods_receive_notes');
  });

  it('is in the Actions menu when the caller may pull, and opens the DocDate dialog', async () => {
    renderList();
    openActions();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Pull from AutoCount/ }));
    expect(await screen.findByText('Pull goods receipt notes from AutoCount')).toBeInTheDocument();
    expect(action.onSelect).not.toHaveBeenCalled();
  });

  it('is absent without the permission', async () => {
    action.visible = false;
    renderList();
    openActions();
    await screen.findByRole('menuitem', { name: /Upload GRN Lines/ });
    expect(screen.queryByRole('menuitem', { name: /Pull from AutoCount/ })).not.toBeInTheDocument();
  });

  it('with an open pull reads Review pull and reviews it without the dialog', async () => {
    Object.assign(action, { label: 'Review pull', hasOpenPull: true });
    renderList();
    openActions();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Review pull/ }));
    expect(action.onSelect).toHaveBeenCalledWith();
    expect(screen.queryByText('Pull goods receipt notes from AutoCount')).not.toBeInTheDocument();
  });
});
