/**
 * CONTACT-TAGS review N5: the stock editor cannot set both the include and the exclude list
 * (the server rejects the pair), so a save never carries both non-empty.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contact-tags/t1',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] }) }));
const updateContactTag = vi.fn();
vi.mock('../services/contactTagService', () => ({
  updateContactTag: (...a: unknown[]) => updateContactTag(...a),
  getContactTag: vi.fn(),
  getContactTags: vi.fn().mockResolvedValue({ data: [], pagination: { total: 0 } }),
}));
vi.mock('../services/contactTagSettingsCatalogService', async (orig) => {
  const actual = await orig<typeof import('../services/contactTagSettingsCatalogService')>();
  return {
    ...actual,
    getSettingsCatalog: vi.fn().mockResolvedValue({
      ...actual.EMPTY_CATALOG,
      warehouses: [{ value: 'w1', label: 'WH One' }, { value: 'w2', label: 'WH Two' }],
    }),
  };
});
// One button per option, addressable by the list's placeholder (All = include list, None = exclude list).
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: { value: string[]; onChange: (v: string[]) => void;
    options?: { value: string; label: string }[]; placeholder?: string }) => (
    <div data-testid={`multi-${props.placeholder}`}>
      {(props.options ?? []).map((o) => (
        <button key={o.value} type="button" onClick={() => props.onChange([...props.value, o.value])}>{o.label}</button>
      ))}
    </div>
  ),
}));

import ContactTagSettings from './ContactTagSettings';

describe('ContactTagSettings stock editor', () => {
  it('never saves the include and the exclude list together', async () => {
    updateContactTag.mockResolvedValue({});
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={client}>
        <ContactTagSettings
          tag={{ id: 't1', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings: {},
            contact_count: 0, created_at: '', updated_at: '' } as never}
          editing
          onDone={vi.fn()}
        />
      </QueryClientProvider>,
    );
    const group = screen
      .getAllByTestId('settings-group')
      .find((g) => within(g).getByTestId('settings-group-title').textContent === 'Stock visibility')!;
    fireEvent.click(within(group).getByRole('switch', { name: /set on this tag/i }));
    fireEvent.click(await within(within(group).getByTestId('multi-All')).findByRole('button', { name: 'WH One' }));
    fireEvent.click(within(within(group).getByTestId('multi-None')).getByRole('button', { name: 'WH Two' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateContactTag).toHaveBeenCalled());
    const stock = updateContactTag.mock.calls[0][1].settings.stock_visibility ?? {};
    const incl = stock.warehouse_ids ?? [];
    const excl = stock.excluded_warehouse_ids ?? [];
    expect(incl.length > 0 && excl.length > 0).toBe(false);
  });
});
