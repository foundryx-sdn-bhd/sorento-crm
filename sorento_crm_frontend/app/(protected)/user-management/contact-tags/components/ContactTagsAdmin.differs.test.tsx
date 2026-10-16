/**
 * CONTACT-TAGS S2, UAC T4.5 / T5.2: the Differs column of the tag list shows differs_count and links
 * to the contacts list filtered to that tag's differing holders (tag=<id>&tag_settings=differs).
 * Same mocks as ContactTagsAdmin.test.tsx; ContactTag gains `differs_count: number`.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
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
vi.mock('../services/contactTagService', () => ({
  getContactTags: vi.fn().mockResolvedValue({
    data: [
      {
        id: 'tag-1', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings: {},
        contact_count: 7, differs_count: 3, created_at: '', updated_at: '',
      },
    ],
    pagination: { total: 1, page: 1, limit: 50, pages: 1 },
  }),
  getContactTag: vi.fn(),
  createContactTag: vi.fn(),
  updateContactTag: vi.fn(),
  deleteContactTag: vi.fn(),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));

import ContactTagsAdmin from './ContactTagsAdmin';

describe('ContactTagsAdmin Differs column', () => {
  it('links the differs count to the contacts list filtered to the tag and tag_settings=differs', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={client}>
        <ContactTagsAdmin />
      </QueryClientProvider>,
    );
    const link = (await screen.findByText('3')).closest('a');
    expect(link).not.toBeNull();
    const url = new URL(link!.getAttribute('href')!, 'http://x');
    expect(url.pathname).toBe('/user-management/contacts');
    expect(url.searchParams.getAll('tag')).toEqual(['tag-1']);
    expect(url.searchParams.get('tag_settings')).toBe('differs');
  });
});
