/**
 * CONTACT-TAGS review N6 (owner ruling Q3): the tag detail settings area shows the greyed
 * "Not on a tag" block from the approved mock, with its eight items.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/x', useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] }) }));
vi.mock('../services/contactTagService', () => ({ updateContactTag: vi.fn(), getContactTag: vi.fn(), getContactTags: vi.fn() }));

import ContactTagSettings from './ContactTagSettings';

const ITEMS = [
  'Linked customers', 'Linked staff user', 'Linked sales agent', 'CS routing',
  'Memory facts and language', 'Default ledgers', 'Agent grant dates', 'Media usage counters',
];

describe('ContactTagSettings Not on a tag block', () => {
  it.each([false, true])('is shown with all eight items (editing=%s)', (editing) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={client}>
        <ContactTagSettings
          tag={{ id: 't1', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings: {},
            contact_count: 0, created_at: '', updated_at: '' } as never}
          editing={editing}
          onDone={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByText('Not on a tag')).toBeInTheDocument();
    for (const item of ITEMS) expect(screen.getByText(item)).toBeInTheDocument();
  });
});
