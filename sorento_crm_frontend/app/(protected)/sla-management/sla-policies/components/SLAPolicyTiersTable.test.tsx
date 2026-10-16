/**
 * M5-06 - SLAPolicyTiersTable's tiers grid and its "Users in tier" sheet both
 * render on DataGrid (the sheet used to be a raw `<Table>`).
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

// The tier actions need `sla_management.sla_policies.edit` (ROUTE-PERM-GATES); this
// suite is about the grids, so the caller holds it.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

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
  {
    id: 't-2',
    policy_id: 'p-1',
    tier_level: 2,
    tier_name: 'Tier Two',
    response_hours: '8',
    resolution_hours: '24',
    created_at: new Date(),
    updated_at: new Date(),
  },
];

const noopMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };

vi.mock('../hooks/useSLAPolicies', () => ({
  useSLAPolicyTiers: () => ({ data: TIERS, isLoading: false, error: null }),
  useCreateSLAPolicyTier: () => noopMutation,
  useUpdateSLAPolicyTier: () => noopMutation,
  useDeleteSLAPolicyTier: () => noopMutation,
}));

const TIER_USERS = [
  { id: 'u-1', name: 'Alice Tan', email: 'alice@example.com' },
  { id: 'u-2', name: 'Bob Lee', email: 'bob@example.com' },
];

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(async () => ({
    ok: true,
    json: async () => ({ data: TIER_USERS }),
  })),
}));

import SLAPolicyTiersTable from './SLAPolicyTiersTable';

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SLAPolicyTiersTable policyId="p-1" />
    </QueryClientProvider>,
  );
}

describe('SLAPolicyTiersTable - tiers grid', () => {
  it('renders the tier level and name columns on DataGrid', () => {
    renderTable();

    expect(screen.getByText('Tier Level')).toBeInTheDocument();
    expect(screen.getByText('Tier Name')).toBeInTheDocument();
    expect(screen.getByText('Tier One')).toBeInTheDocument();
    expect(screen.getByText('Tier Two')).toBeInTheDocument();
  });

  it('opens the users sheet on DataGrid with Name/Email columns and a real cell value', async () => {
    renderTable();

    const row = screen.getByText('Tier One').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: /Users/ }));

    await waitFor(() => {
      expect(screen.getByText('Name')).toBeInTheDocument();
      expect(screen.getByText('Email')).toBeInTheDocument();
    });
    expect(await screen.findByText('Alice Tan')).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
  });
});
