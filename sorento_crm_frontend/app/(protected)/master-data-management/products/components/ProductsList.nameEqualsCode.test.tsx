/**
 * ProductsList Product Name column (AC-4 Q1): blank when the name equals the code.
 * Same harness as ProductsList.variant.test.tsx: DataGrid stubbed to capture the table,
 * then the REAL `product_name` cell renderer is invoked against the captured column.
 */
import type { ReactElement } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Column } from '@tanstack/react-table';

import type { ProductListItem } from '../types/product.types';

const getProducts = vi.fn();
const grid = vi.hoisted(() => ({ table: null as unknown }));

vi.mock('@/components/ui/data-grid', () => ({
  DataGrid: ({ table }: { table: unknown }) => {
    grid.table = table;
    return null;
  },
}));

vi.mock('../services/productService', () => ({
  getProducts: (...a: unknown[]) => getProducts(...a),
  bulkImportProducts: vi.fn(),
  validateProductsImport: vi.fn(),
}));

const nav = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { replace: () => {}, push: () => {} },
}));
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  usePathname: () => '/master-data-management/products',
  useSearchParams: () => nav.params,
}));
vi.mock('../../shared/hooks/use-product-category-select-query', () => ({
  useProductCategorySelectQuery: () => ({ data: [] }),
}));
vi.mock('../../shared/hooks/use-brand-select-query', () => ({
  useBrandSelectQuery: () => ({ data: [] }),
}));
vi.mock('@/components/upload-activity', () => ({
  useImportJobDrawer: () => ({ notifyImportQueued: vi.fn() }),
}));
vi.mock('@/app/(protected)/system-management/import-jobs/autocount-pull/hooks/useAutocountPull', () => ({
  useAutocountPullAction: () => ({ visible: false, label: '', onSelect: () => {} }),
}));

import ProductsList from './ProductsList';

const row = (code: string, name: string): ProductListItem => ({
  id: `row-${code}`,
  product_code: code,
  product_name: name,
  list_price: 10,
  is_active: true,
  is_variant: false,
  variant_of: null,
  variant_child_count: 0,
  created_at: new Date('2026-01-01T00:00:00Z'),
});

function renderNameCell(r: ProductListItem) {
  const table = grid.table as { getAllColumns: () => Column<ProductListItem, unknown>[] };
  const column = table.getAllColumns().find((c) => c.id === 'product_name');
  if (!column) throw new Error('column product_name not found');
  const cell = column.columnDef.cell;
  if (typeof cell !== 'function') throw new Error('product_name has no cell fn');
  return render(cell({ row: { original: r } } as never) as ReactElement);
}

beforeEach(() => {
  grid.table = null;
  getProducts.mockReset();
  getProducts.mockResolvedValue({ data: [], pagination: { total: 0, page: 1, limit: 10 } });
});
afterEach(() => cleanup());

describe('ProductsList Product Name cell (AC-4)', () => {
  it('is blank when the name equals the code', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ProductsList />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(grid.table).not.toBeNull());

    const same = renderNameCell(row('1861', '1861'));
    expect(same.container.textContent).not.toContain('1861');
    same.unmount();

    const caseOnly = renderNameCell(row('11X11', '11x11'));
    expect(caseOnly.container.textContent?.trim()).toBe('');
    caseOnly.unmount();

    const differs = renderNameCell(row('SRT6641', 'FLOOR TRAP'));
    expect(differs.container.textContent).toContain('FLOOR TRAP');
  });
});
