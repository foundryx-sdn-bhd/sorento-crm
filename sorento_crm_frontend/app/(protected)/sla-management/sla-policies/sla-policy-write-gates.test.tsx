/**
 * ROUTE-PERM-GATES: the SLA Policies screens offer a write only to a caller the
 * server will let make it.
 *
 * The six policy and tier writes used to check only "signed in". They now need
 * `sla_management.sla_policies.add` (create), `.edit` (update, and every tier
 * write: a tier is part of its policy) and `.delete` (delete). The list and the
 * detail page stay readable on `.view`; the buttons that would answer 403 are
 * not shown, and the create / edit pages answer "no access" in place.
 */
import React, { Suspense } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  });
}
Element.prototype.scrollIntoView = vi.fn();

const ADD = 'sla_management.sla_policies.add';
const EDIT = 'sla_management.sla_policies.edit';
const DELETE = 'sla_management.sla_policies.delete';
const VIEW = 'sla_management.sla_policies.view';

const granted = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
  usePermissions: () => ({
    permissions: [...granted],
    permissionSet: granted,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/sla-management/sla-policies',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({ data: [] }) })),
}));

// The create / edit form is not under test here, only whether the page lets you reach it.
vi.mock('./components/SLAPolicyForm', () => ({
  default: () => <div>sla-policy-form</div>,
}));

const POLICY = {
  id: 'p-1',
  code: 'STANDARD',
  name: 'Standard',
  description: null,
  is_active: true,
  tiers_count: 1,
  created_at: '2026-10-01T00:00:00',
  updated_at: '2026-10-01T00:00:00',
};
const TIERS = [
  {
    id: 't-1',
    policy_id: 'p-1',
    tier_level: 1,
    tier_name: 'Tier One',
    response_hours: '1',
    resolution_hours: '4',
    created_at: new Date(),
    updated_at: new Date(),
  },
];
const noopMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };

vi.mock('./hooks/useSLAPolicies', () => ({
  useSLAPolicies: () => ({
    data: { data: [POLICY], pagination: { total: 1, page: 1, limit: 10 } },
    isLoading: false,
    isPlaceholderData: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useSLAPolicy: () => ({ data: POLICY, isLoading: false }),
  useSLAPolicyTiers: () => ({ data: TIERS, isLoading: false, error: null }),
  useCreateSLAPolicy: () => noopMutation,
  useUpdateSLAPolicy: () => noopMutation,
  useDeleteSLAPolicy: () => noopMutation,
  useCreateSLAPolicyTier: () => noopMutation,
  useUpdateSLAPolicyTier: () => noopMutation,
  useDeleteSLAPolicyTier: () => noopMutation,
}));

import SLAPoliciesList from './components/SLAPoliciesList';
import SLAPolicyDetail from './components/SLAPolicyDetail';
import NewSLAPolicyPage from './new/page';
import EditSLAPolicyPage from './[id]/edit/page';

async function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // `act` lets a page's `use(params)` settle before the assertions run.
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback={<div>suspended</div>}>{node}</Suspense>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  granted.clear();
  granted.add(VIEW);
});
afterEach(() => cleanup());

describe('SLA Policies list', () => {
  it('offers Create only to the add holder', async () => {
    await wrap(<SLAPoliciesList />);
    expect(screen.getByText('STANDARD')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Create SLA Policy/ })).not.toBeInTheDocument();

    cleanup();
    granted.add(ADD);
    await wrap(<SLAPoliciesList />);
    expect(screen.getAllByRole('button', { name: /Create SLA Policy/ }).length).toBeGreaterThan(0);
  });
});

describe('SLA Policy detail', () => {
  it('shows no write action to a view-only caller', async () => {
    await wrap(<SLAPolicyDetail slaPolicyId="p-1" />);

    expect(screen.getByText('Tier One')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Delete$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Tier/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit tier' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete tier' })).not.toBeInTheDocument();
  });

  it('gives the edit holder Edit and every tier action, but not Delete', async () => {
    granted.add(EDIT);
    await wrap(<SLAPolicyDetail slaPolicyId="p-1" />);

    expect(screen.getByRole('button', { name: /^Edit$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Tier/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit tier' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete tier' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Delete$/ })).not.toBeInTheDocument();
  });

  it('gives the delete holder Delete only', async () => {
    granted.add(DELETE);
    await wrap(<SLAPolicyDetail slaPolicyId="p-1" />);

    expect(screen.getByRole('button', { name: /^Delete$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Tier/ })).not.toBeInTheDocument();
  });
});

describe('SLA Policy create and edit pages', () => {
  it('answers no access on the create page without add', async () => {
    await wrap(<NewSLAPolicyPage />);
    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
    expect(screen.queryByText('sla-policy-form')).not.toBeInTheDocument();
  });

  it('renders the create form for the add holder', async () => {
    granted.add(ADD);
    await wrap(<NewSLAPolicyPage />);
    expect(screen.getByText('sla-policy-form')).toBeInTheDocument();
  });

  it('answers no access on the edit page without edit', async () => {
    await wrap(<EditSLAPolicyPage params={Promise.resolve({ id: 'p-1' })} />);
    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
    expect(screen.queryByText('sla-policy-form')).not.toBeInTheDocument();
  });

  it('renders the edit form for the edit holder', async () => {
    granted.add(EDIT);
    await wrap(<EditSLAPolicyPage params={Promise.resolve({ id: 'p-1' })} />);
    expect(screen.getByText('sla-policy-form')).toBeInTheDocument();
  });
});
