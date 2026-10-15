/**
 * SERIES-BRAND-SCOPE UAC A5 - picking a brand re-filters the category list, and a category
 * already picked must keep its NAME on its chip (never its id) even when the brand-filtered
 * list no longer carries it, or has not arrived yet.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
if (!(globalThis as { ResizeObserver?: unknown }).ResizeObserver) {
  (globalThis as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const ID_A = '11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const ID_B = '22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const ID_C = '33333333-cccc-4ccc-8ccc-cccccccccccc';
const BRAND_X = '99999999-xxxx-4xxx-8xxx-xxxxxxxxxxxx';

const cat = (id: string, name: string) => ({
  id,
  category_name: name,
  category_code: `code-${name}`,
  is_active: true,
});
const ALL = [cat(ID_A, 'Basins'), cat(ID_B, 'Bidets'), cat(ID_C, 'Cisterns')];

// Per-brand answers; `loading` makes the brand-filtered query come back with data undefined.
const state = { loadingForBrand: false };
const categorySelect = vi.fn((brandArg?: string | string[]) => {
  const brandId = Array.isArray(brandArg) ? brandArg[0] : brandArg;
  if (!brandId) return { data: ALL, error: null, refetch: vi.fn(), isLoading: false };
  if (state.loadingForBrand)
    return { data: undefined, error: null, refetch: vi.fn(), isLoading: true };
  return { data: [ALL[2]], error: null, refetch: vi.fn(), isLoading: false };
});

vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-product-category-select-query', () => ({
  useProductCategorySelectQuery: (brandArg?: string | string[]) => categorySelect(brandArg),
}));
vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query', () => ({
  useBrandSelectQuery: () => ({
    data: [{ id: BRAND_X, brand_name: 'Brand X', brand_code: 'BX', is_active: true }],
    error: null,
    refetch: vi.fn(),
  }),
}));
const seriesState: { rows: Record<string, unknown>[]; saved: Record<string, unknown> | null } = {
  rows: [],
  saved: null,
};
const updateMutate = vi.fn(async () => seriesState.saved);
vi.mock('../../../_shared/hooks/useProjects', () => ({
  useProjectSeries: () => ({ data: seriesState.rows, isLoading: false }),
  useSeriesMutations: () => ({
    create: { isPending: false, mutateAsync: vi.fn() },
    update: { isPending: false, mutateAsync: updateMutate },
    remove: { isPending: false, mutateAsync: vi.fn() },
  }),
}));
vi.mock('./SeriesProductsTable', () => ({ SeriesProductsTable: () => null }));
vi.mock('./SeriesSheetLoader', () => ({ SeriesSheetLoader: () => null }));
vi.mock('./SeriesSetsTable', () => ({ SeriesSetsTable: () => null }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => '/project-sales/series/new',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/components/common/BackToList', () => ({
  default: () => null,
  useBackToListHref: () => '/project-sales/series',
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/RecordNavigation', () => ({ default: () => null }));
vi.mock('@/components/common/DetailActions', () => ({ default: () => null }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
}));
import { SeriesDetailClient } from './SeriesDetailClient';

// Brand is the first multi-select on the page, Categories the second.
const openMenu = (index = 1) =>
  fireEvent.click(document.querySelectorAll('[data-slot="searchable-multi-select-trigger"]')[index]);

async function pickBrand(name: string) {
  openMenu(0);
  fireEvent.click(await screen.findByText(name));
  openMenu(0);
}

async function pickCategory(name: string) {
  openMenu();
  const option = await screen.findByText(name);
  fireEvent.click(option);
  // close again so the next open is a clean toggle
  openMenu();
}

function chipsText() {
  return Array.from(
    document.querySelectorAll('[data-slot="searchable-multi-select-trigger"]'),
  )
    .slice(1)
    .flatMap((t) => Array.from(t.querySelectorAll('*')))
    .map((el) => el.textContent ?? '')
    .join('|');
}

function noElementTextEquals(id: string) {
  const hit = Array.from(document.querySelectorAll('*')).find(
    (el) => el.children.length === 0 && (el.textContent ?? '').trim() === id,
  );
  expect(hit, `an element renders the raw id ${id}`).toBeUndefined();
}

beforeEach(() => {
  vi.clearAllMocks();
  state.loadingForBrand = false;
  seriesState.rows = [];
  seriesState.saved = null;
});

describe('SeriesDetailClient category chips across a brand change (A5)', () => {
  it('keeps the names of picked categories after a brand drops them from the list', async () => {
    render(<SeriesDetailClient seriesId="new" />);
    await pickCategory('Basins');
    await pickCategory('Cisterns');
    await waitFor(() => expect(chipsText()).toContain('Basins'));

    await pickBrand('Brand X');

    await waitFor(() => expect(categorySelect.mock.calls.some((c) => JSON.stringify(c).includes(BRAND_X))).toBe(true));
    expect(chipsText()).toContain('Basins');
    expect(chipsText()).toContain('Cisterns');
    noElementTextEquals(ID_A);
    noElementTextEquals(ID_C);
  });

  it('keeps names while the brand-filtered query is still loading', async () => {
    render(<SeriesDetailClient seriesId="new" />);
    await pickCategory('Basins');
    await pickCategory('Cisterns');
    await waitFor(() => expect(chipsText()).toContain('Basins'));

    state.loadingForBrand = true;
    await pickBrand('Brand X');

    await waitFor(() => expect(categorySelect.mock.calls.some((c) => JSON.stringify(c).includes(BRAND_X))).toBe(true));
    expect(chipsText()).toContain('Basins');
    expect(chipsText()).toContain('Cisterns');
    noElementTextEquals(ID_A);
    noElementTextEquals(ID_C);
  });
});

describe('SeriesDetailClient save on an existing series', () => {
  const base = {
    id: 'ser-1',
    name: 'Template',
    is_active: true,
    brand_ids: [] as string[],
    brand_names: [] as string[],
    description: null,
    product_count: 0,
    product_codes: [],
    quotation_count: 0,
  };
  const stale = {
    ...base,
    category_ids: [ID_A, ID_C],
    category_names: ['Basins', 'Cisterns'],
    covered_category_count: 2,
  };
  const fresh = {
    ...base,
    category_ids: [ID_C],
    category_names: ['Cisterns'],
    covered_category_count: 1,
  };

  it('does not resurrect a removed category from the stale cached row', async () => {
    seriesState.rows = [stale];
    seriesState.saved = fresh; // what updateSeries resolves with
    const qc = new QueryClient();
    const ui = (
      <QueryClientProvider client={qc}>
        <SeriesDetailClient seriesId="ser-1" />
      </QueryClientProvider>
    );
    const view = render(ui);
    await waitFor(() => expect(chipsText()).toContain('Basins'));

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Remove Basins' }));
    await waitFor(() => expect(chipsText()).not.toContain('Basins'));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));

    // The list query has NOT refetched yet: it still returns the old row.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled());
    expect(chipsText()).not.toContain('Basins');
    expect(chipsText()).toContain('Cisterns');

    // Later the refetch lands with the saved row; still no resurrection.
    seriesState.rows = [fresh];
    view.rerender(ui);
    await waitFor(() => expect(chipsText()).toContain('Cisterns'));
    expect(chipsText()).not.toContain('Basins');
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  });

  it('A1/A6: brand is a multi-select that reads "All brands" and "All categories" when empty', () => {
    render(<SeriesDetailClient seriesId="new" />);
    expect(document.querySelectorAll('[data-slot="searchable-multi-select-trigger"]')).toHaveLength(2);
    expect(screen.getByText('All brands')).toBeInTheDocument();
    expect(screen.getByText('All categories')).toBeInTheDocument();
  });

  it('A6: changing only the brands enables Save', async () => {
    seriesState.rows = [stale];
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SeriesDetailClient seriesId="ser-1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(chipsText()).toContain('Basins'));
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
    await pickBrand('Brand X');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled());
  });

  it('A2: saving sends brand_ids whole and no brand_id; clearing sends []', async () => {
    seriesState.rows = [{ ...stale, brand_ids: [BRAND_X], brand_names: ['Brand X'] }];
    seriesState.saved = { ...stale, brand_ids: [], brand_names: [] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SeriesDetailClient seriesId="ser-1" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText('Brand X')).toBeInTheDocument());
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Remove Brand X' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    const call = updateMutate.mock.calls[0] as unknown as [{ body?: Record<string, unknown> } & Record<string, unknown>];
    const body = (call[0].body ?? call[0]) as Record<string, unknown>;
    expect(body.brand_ids).toEqual([]);
    expect('brand_id' in body).toBe(false);
  });
});
