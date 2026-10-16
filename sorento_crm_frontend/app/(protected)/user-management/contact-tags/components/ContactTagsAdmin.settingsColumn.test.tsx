/**
 * CONTACT-TAGS browser-pass (mock view B): the tag list has a "Settings" column: "None" when the tag
 * sets nothing, otherwise "n settings" (n = the number of set values: the leaves of the settings object).
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contact-tags',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
const base = vi.hoisted(() => ({ description: null, is_active: true, sort_order: 0, contact_count: 0, differs_count: 0, created_at: '', updated_at: '' }));
vi.mock('../services/contactTagService', () => ({
  getContactTags: vi.fn().mockResolvedValue({
    data: [
      { ...base, id: 't1', name: 'Empty tag', settings: {} },
      { ...base, id: 't2', name: 'Two set', settings: { portal_forms: { purchase_request: false, stock_inquiry: false } } },
    ],
    pagination: { total: 2, page: 1, limit: 50, pages: 1 },
  }),
  getContactTag: vi.fn(), createContactTag: vi.fn(), updateContactTag: vi.fn(), deleteContactTag: vi.fn(),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));

import ContactTagsAdmin from './ContactTagsAdmin';

it('shows None and "n settings" in a Settings column', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  rtlRender(
    <QueryClientProvider client={client}>
      <ContactTagsAdmin />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('Empty tag')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument();
  const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;
  expect(within(rowOf('Empty tag')).getByText('None')).toBeInTheDocument();
  expect(within(rowOf('Two set')).getByText('2 settings')).toBeInTheDocument();
});
