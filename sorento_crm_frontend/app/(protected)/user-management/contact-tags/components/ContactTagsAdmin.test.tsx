/**
 * CONTACT-TAGS S1, UAC T5.1: the Contact tags admin list. Rows come from the service
 * (name, contacts count, active); an empty list shows an empty-state heading.
 * Assumed paths: ./ContactTagsAdmin (default export), ../services/contactTagService.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contact-tags',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

const getContactTags = vi.fn();
vi.mock('../services/contactTagService', () => ({
  getContactTags: (...a: unknown[]) => getContactTags(...a),
  getContactTag: vi.fn(),
  createContactTag: vi.fn(),
  updateContactTag: vi.fn(),
  deleteContactTag: vi.fn(),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

import ContactTagsAdmin from './ContactTagsAdmin';

const ROWS = [
  {
    id: 'tag-1',
    name: 'Dealer',
    description: 'Our dealers',
    is_active: true,
    sort_order: 0,
    settings: {},
    contact_count: 7,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  },
  {
    id: 'tag-2',
    name: 'Retired',
    description: null,
    is_active: false,
    sort_order: 1,
    settings: {},
    contact_count: 0,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  },
];

function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <ContactTagsAdmin />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ContactTagsAdmin', () => {
  it('renders a row per tag with its name and contacts count', async () => {
    getContactTags.mockResolvedValue({
      data: ROWS,
      pagination: { total: 2, page: 1, limit: 50, pages: 1 },
    });
    render();
    expect(await screen.findByText('Dealer')).toBeInTheDocument();
    expect(screen.getByText('Retired')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('shows the empty-state heading when there are no tags', async () => {
    getContactTags.mockResolvedValue({
      data: [],
      pagination: { total: 0, page: 1, limit: 50, pages: 0 },
    });
    render();
    expect(await screen.findByRole('heading', { name: /no contact tags/i })).toBeInTheDocument();
  });

  it('shows a readable error, not a spinner, when the list fails', async () => {
    getContactTags.mockRejectedValue(new Error('Failed to fetch contact tags'));
    render();
    expect(await screen.findByText(/failed to fetch contact tags/i)).toBeInTheDocument();
  });
});
