/**
 * NUMBER-FIELDS round 2b: clearing List Price shows no sentence while typing (before any Update
 * click); Update shows it, then it is live until fixed.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

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


beforeEach(() => {
  vi.clearAllMocks();
  useCategoryMock.mockReturnValue({ data: undefined });
  useProductMock.mockReturnValue({ data: undefined });
});


beforeEach(() => {
  vi.clearAllMocks();
  useCategoryMock.mockReturnValue({ data: undefined });
  useProductMock.mockReturnValue({ data: undefined });
});

describe('ProductForm - List Price message timing', () => {
  it('a visited List Price cleared while focused shows no sentence before Update; Update shows it; 52 clears it', async () => {
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
    fireEvent.mouseDown(screen.getByRole('tab', { name: /Pricing/i }));
    const input = screen.getByLabelText(/List Price/) as HTMLInputElement;
    // The box was visited once already (blurred with a valid value), then re-entered to clear it.
    input.focus();
    fireEvent.blur(input);
    input.focus();
    fireEvent.change(input, { target: { value: '' } });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/^Enter a (whole )?number/)).toBeNull();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');

    fireEvent.click(screen.getByRole('button', { name: /Update Product/i }));
    expect(await screen.findByText(/^Enter a (whole )?number/)).toBeInTheDocument();
    expect(updateMutateAsync).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: '52' } });
    await waitFor(() => expect(screen.queryByText(/^Enter a (whole )?number/)).toBeNull());
  });
});
