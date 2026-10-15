/**
 * #1335: one CTA per page. The toolbar's "Add series" is the only offer this listing
 * makes; the empty grid does not repeat it.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const push = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/series',
  useRouter: () => ({ push, replace: vi.fn() }),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

const listState: { data: Record<string, unknown>[] } = { data: [] };
vi.mock('../../_shared/hooks/useProjects', () => ({
  useProjectSeries: () => ({
    data: listState.data,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
}));

import { SeriesListClient } from './SeriesListClient';

beforeEach(() => {
  vi.clearAllMocks();
  listState.data = [];
});

describe('SeriesListClient', () => {
  it('offers Add series once, in the toolbar, and not again in the empty state', () => {
    render(<SeriesListClient />);

    const buttons = screen.getAllByRole('button', { name: /Add series/i });
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest('[data-slot="card-header"]')).not.toBeNull();

    fireEvent.click(buttons[0]);
    expect(push).toHaveBeenCalledWith('/project-sales/series/new');
  });

  const series = (id: string, name: string, brand_names: string[]) => ({
    id,
    name,
    is_active: true,
    brand_ids: brand_names.map((n) => `id-${n}`),
    brand_names,
    category_ids: [],
    category_names: [],
    covered_category_count: 0,
    product_count: 3,
    product_codes: [],
    quotation_count: 0,
    description: null,
  });

  it('A5: shows every brand name comma-separated, and "All brands" when none', () => {
    listState.data = [
      series('1', 'Alpha set', ['Aqua', 'Sorento']),
      series('2', 'Beta set', []),
    ];
    render(<SeriesListClient />);
    expect(screen.getByText('Aqua, Sorento')).toBeInTheDocument();
    expect(screen.getByText('All brands')).toBeInTheDocument();
  });

  it('A5: search matches the SECOND brand of a two-brand row, and hides a non-match', async () => {
    // rows carry brand_names only: no legacy brand_name field to fall back on
    listState.data = [
      series('1', 'Alpha set', ['Aqua', 'Sorento']),
      series('2', 'Beta set', ['Zeta']),
    ];
    render(<SeriesListClient />);
    const box = screen.getByLabelText('Search series');
    fireEvent.change(box, { target: { value: 'sorento' } });
    await vi.waitFor(() => expect(screen.queryByText('Beta set')).not.toBeInTheDocument());
    expect(screen.getByText('Alpha set')).toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'nomatch' } });
    await vi.waitFor(() => expect(screen.queryByText('Alpha set')).not.toBeInTheDocument());
  });
});
