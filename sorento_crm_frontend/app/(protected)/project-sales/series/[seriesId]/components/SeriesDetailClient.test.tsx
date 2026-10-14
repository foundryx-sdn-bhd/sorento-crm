/**
 * SERIES-BRAND-SCOPE UAC A5 - picking a brand re-filters the category list, and a category
 * already picked must keep its NAME on its chip (never its id) even when the brand-filtered
 * list no longer carries it, or has not arrived yet.
 */
import React from 'react';
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
const categorySelect = vi.fn((brandId?: string) => {
  if (!brandId) return { data: ALL, error: null, refetch: vi.fn(), isLoading: false };
  if (state.loadingForBrand)
    return { data: undefined, error: null, refetch: vi.fn(), isLoading: true };
  return { data: [ALL[2]], error: null, refetch: vi.fn(), isLoading: false };
});

vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-product-category-select-query', () => ({
  useProductCategorySelectQuery: (brandId?: string) => categorySelect(brandId),
}));
vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query', () => ({
  useBrandSelectQuery: () => ({
    data: [{ id: BRAND_X, brand_name: 'Brand X', brand_code: 'BX', is_active: true }],
    error: null,
    refetch: vi.fn(),
  }),
}));
vi.mock('../../../_shared/hooks/useProjects', () => ({
  useProjectSeries: () => ({ data: [], isLoading: false }),
  useSeriesMutations: () => ({
    create: { isPending: false, mutateAsync: vi.fn() },
    update: { isPending: false, mutateAsync: vi.fn() },
    remove: { isPending: false, mutateAsync: vi.fn() },
  }),
}));
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
// The brand picker is incidental; a native select keeps the test about the category chips.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    id,
    value,
    onChange,
    options,
  }: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select data-testid={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Any brand</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

import { SeriesDetailClient } from './SeriesDetailClient';

const openMenu = () =>
  fireEvent.click(document.querySelector('[data-slot="searchable-multi-select-trigger"]')!);

async function pickCategory(name: string) {
  openMenu();
  const option = await screen.findByText(name);
  fireEvent.click(option);
  // close again so the next open is a clean toggle
  openMenu();
}

function chipsText() {
  return Array.from(document.querySelectorAll('[data-slot="searchable-multi-select-trigger"] *'))
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
});

describe('SeriesDetailClient category chips across a brand change (A5)', () => {
  it('keeps the names of picked categories after a brand drops them from the list', async () => {
    render(<SeriesDetailClient seriesId="new" />);
    await pickCategory('Basins');
    await pickCategory('Cisterns');
    await waitFor(() => expect(chipsText()).toContain('Basins'));

    fireEvent.change(screen.getByTestId('series-brand'), { target: { value: BRAND_X } });

    await waitFor(() => expect(categorySelect).toHaveBeenCalledWith(BRAND_X));
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
    fireEvent.change(screen.getByTestId('series-brand'), { target: { value: BRAND_X } });

    await waitFor(() => expect(categorySelect).toHaveBeenCalledWith(BRAND_X));
    expect(chipsText()).toContain('Basins');
    expect(chipsText()).toContain('Cisterns');
    noElementTextEquals(ID_A);
    noElementTextEquals(ID_C);
  });
});
