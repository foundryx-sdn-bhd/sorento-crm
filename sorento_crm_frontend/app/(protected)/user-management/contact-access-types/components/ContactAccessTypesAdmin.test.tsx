/**
 * AC-M3 (PLAN-portal-forms-market-segment): the "Portal forms" column and
 * field are gone from Contact Access Types - the group source for a portal
 * form grant moved to Market Segments (D1). This is the direct replacement
 * for the retired `ContactAccessTypesAdmin.portalForms.test.tsx`, which
 * proved the opposite (the field existed here).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contact-access-types',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

// DataGrid persists column prefs via this hook (fires network) - stub it, or the
// grid renders skeletons forever and no row can be asserted.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

const getAllContactAccessTypes = vi.fn();
const updateContactAccessType = vi.fn();
const createContactAccessType = vi.fn();
vi.mock('../services/contactAccessTypeService', () => ({
  getAllContactAccessTypes: (...a: unknown[]) => getAllContactAccessTypes(...a),
  updateContactAccessType: (...a: unknown[]) => updateContactAccessType(...a),
  createContactAccessType: (...a: unknown[]) => createContactAccessType(...a),
  deleteContactAccessType: vi.fn(),
  getContactAccessTypes: vi.fn(),
  getContactAccessType: vi.fn(),
  CONTACT_ACCESS_TYPE_PERMS: { manage: 'user_management.reference_data.manage' },
}));

// The caller's grants. Writes need `reference_data.manage` (NS-SHARED-LOOKUPS).
let granted = new Set<string>(['user_management.reference_data.manage']);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
}));

import ContactAccessTypesAdmin from './ContactAccessTypesAdmin';

const ROWS = [
  {
    code: 'dealer',
    name: 'Dealer',
    description: 'Sorento dealers',
    is_active: true,
    sort_order: 1,
    keywords: [],
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  },
];

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ContactAccessTypesAdmin />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  granted = new Set(['user_management.reference_data.manage']);
  getAllContactAccessTypes.mockResolvedValue(ROWS);
  updateContactAccessType.mockResolvedValue(ROWS[0]);
  createContactAccessType.mockResolvedValue(ROWS[0]);
});

describe('ContactAccessTypesAdmin - no Portal forms column or field (AC-M3)', () => {
  it('renders no "Portal forms" column header', async () => {
    render();
    await screen.findByText('Dealer');

    expect(screen.queryByText('Portal forms')).not.toBeInTheDocument();
  });

  it('renders no "Portal forms" field in the edit dialog', async () => {
    render();
    await screen.findByText('Dealer');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await screen.findByText('Edit access type');

    expect(screen.queryByText('Portal forms')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Portal forms')).not.toBeInTheDocument();
  });

  it('the update payload carries no portal_form_types key', async () => {
    render();
    await screen.findByText('Dealer');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await screen.findByText('Edit access type');
    fireEvent.click(screen.getByRole('button', { name: 'Update' }));

    await waitFor(() => expect(updateContactAccessType).toHaveBeenCalled());
    expect(updateContactAccessType.mock.calls[0][1]).not.toHaveProperty('portal_form_types');
  });

  it('the create payload carries no portal_form_types key', async () => {
    render();
    await screen.findByText('Dealer');
    fireEvent.click(screen.getByRole('button', { name: /add type/i }));
    await screen.findByText('Add access type');

    fireEvent.change(screen.getByLabelText('Code'), { target: { value: 'zzt_new' } });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'ZZT New' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createContactAccessType).toHaveBeenCalled());
    expect(createContactAccessType.mock.calls[0][0]).not.toHaveProperty('portal_form_types');
  });
});

describe('ContactAccessTypesAdmin - writes need reference_data.manage (NS-SHARED-LOOKUPS)', () => {
  it('a reader without the write slug sees the list and no Add, Edit or Delete', async () => {
    granted = new Set();
    render();
    await screen.findByText('Dealer');

    expect(screen.queryByRole('button', { name: /add type/i })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });

  it('a holder of the write slug gets Add, Edit and Delete', async () => {
    render();
    await screen.findByText('Dealer');

    expect(screen.getByRole('button', { name: /add type/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });
});
