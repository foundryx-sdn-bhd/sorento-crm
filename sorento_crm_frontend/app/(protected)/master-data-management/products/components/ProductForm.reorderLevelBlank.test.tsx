/**
 * NUMBER-FIELDS (S3 SNAP screen): Reorder Level is a required whole number >= 0. Clearing it
 * stays empty (was snapped to 0 by `parseInt(...) || 0`); saving empty sends nothing and the
 * field says why.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  });
}

const CAT_ID = '11111111-1111-1111-1111-111111111111';
const UOM_ID = '22222222-2222-2222-2222-222222222222';
const PRODUCT_ID = '33333333-3333-3333-3333-333333333333';

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="" />
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  ),
}));

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, back: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

const createMutateAsync = vi.fn().mockResolvedValue({});
const updateMutateAsync = vi.fn().mockResolvedValue({});
const useProductMock = vi.fn();
vi.mock('../hooks/useProducts', () => ({
  useCreateProduct: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useUpdateProduct: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useProduct: (id: string | null) => useProductMock(id),
}));

vi.mock('../../shared/hooks/use-product-category-select-query', () => ({
  useProductCategorySelectQuery: () => ({
    data: [{ id: CAT_ID, category_code: 'CAT1', category_name: 'Category 1' }],
  }),
}));
vi.mock('../../shared/hooks/use-brand-select-query', () => ({
  useBrandSelectQuery: () => ({ data: [] }),
}));
vi.mock('../../shared/hooks/use-uom-select-query', () => ({
  useUOMSelectQuery: () => ({ data: [{ id: UOM_ID, uom_code: 'PCS' }] }),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

const useCategoryMock = vi.fn();
vi.mock('../../product-categories/hooks/useProductCategories', () => ({
  useCategory: (id: string | null) => useCategoryMock(id),
}));

vi.mock('@/app/(protected)/project-sales/_shared/components/PriceFloorPanel', () => ({
  PriceFloorPanel: () => <div data-testid="price-floor-stub" />,
}));
vi.mock('./ProductSuppliersSection', () => ({
  default: () => <div data-testid="suppliers-stub" />,
}));
vi.mock('./ProductAttachmentsTab', () => ({
  default: () => <div data-testid="attachments-stub" />,
}));
vi.mock('@/components/common/ListPager', () => ({
  default: () => <div data-testid="list-pager-stub" />,
}));

import ProductForm from './ProductForm';

function goToSpecificationsTab() {
  fireEvent.mouseDown(screen.getByRole('tab', { name: /Specifications/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  useCategoryMock.mockReturnValue({ data: undefined });
  useProductMock.mockReturnValue({ data: undefined });
});

describe('ProductForm - Reorder Level blank', () => {
  function existing() {
    useProductMock.mockReturnValue({
      data: {
        id: PRODUCT_ID, product_code: 'PROD-9', product_name: 'Product Nine',
        category_id: CAT_ID, base_uom_id: UOM_ID, list_price: 10,
        is_active: true, is_searchable: true, exclude_from_planning: false,
        has_serial_tracking: false, has_batch_tracking: false,
        reorder_level: 10, reorder_quantity: 50,
      },
    });
    render(<ProductForm productId={PRODUCT_ID} />);
    goToSpecificationsTab();
  }

  it('clearing Reorder Level leaves it empty (not 0)', () => {
    existing();
    const input = screen.getByLabelText('Reorder Level') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    expect(input.value).toBe('');
  });

  it('saving with Reorder Level empty sends nothing and shows a message', async () => {
    existing();
    const input = screen.getByLabelText('Reorder Level') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Update Product/i }));

    expect(await screen.findByText(/^Enter a (whole )?number/)).toBeInTheDocument();
    expect(updateMutateAsync).not.toHaveBeenCalled();
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });
});
