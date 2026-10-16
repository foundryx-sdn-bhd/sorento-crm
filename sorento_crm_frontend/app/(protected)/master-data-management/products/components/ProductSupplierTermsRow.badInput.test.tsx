/** NUMBER-FIELDS round 3 (S8): bad input in a term is never saved as 'nothing on file'. */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ProductSupplier } from '../../../procurement-management/product-suppliers/types/productSupplier.types';
import {
  ProductSupplierTermsRow,
  termsError,
  type SupplierTermsDraft,
} from './ProductSupplierTermsRow';

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
Element.prototype.hasPointerCapture = Element.prototype.hasPointerCapture ?? (() => false);

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() } }));

const CURRENCIES = [
  { value: 'MYR', label: 'MYR' },
  { value: 'CNY', label: 'CNY' },
];

function link(over: Partial<ProductSupplier> = {}): ProductSupplier {
  return {
    id: 'ps-1',
    product_id: 'prod-1',
    supplier_id: 'sup-1',
    standard_lead_time_days: 45,
    created_at: new Date(),
    supplier: { id: 'sup-1', supplier_code: 'SUP-1', supplier_name: 'Acme Tiles' },
    ...over,
  } as ProductSupplier;
}

function renderRow(
  ps: ProductSupplier,
  onSave = vi.fn(),
  onRemove = vi.fn(),
  { isDeleting = false }: { isDeleting?: boolean } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(ProductSupplierTermsRow, {
        ps,
        currencyOptions: CURRENCIES,
        onSave,
        onRemove,
        isSaving: false,
        isDeleting,
      }),
    ),
  );
  return { onSave, onRemove };
}

import { typeBadInput } from '@/test-utils/badInput';

beforeEach(() => vi.clearAllMocks());

describe('ProductSupplierTermsRow bad number input (S8)', () => {
  it.each(['Unit cost', 'Minimum order', 'Order multiple'])(
    '%s: bad input is an error, Save terms sends nothing',
    (label) => {
      const { onSave } = renderRow(
        link({ unit_cost: 12.5, currency: 'CNY', moq: 100, order_multiple: 25 }),
      );
      typeBadInput(screen.getByLabelText(label));
      expect(screen.getByRole('alert')).toBeInTheDocument();
      const save = screen.queryByRole('button', { name: /Save terms/i });
      if (save) fireEvent.click(save);
      expect(onSave).not.toHaveBeenCalled();
    },
  );

  it('termsError reads NaN text as an error, not as no value', () => {
    const draft: SupplierTermsDraft = {
      standard_lead_time_days: '45',
      unit_cost: 'NaN',
      currency: 'CNY',
      moq: '',
      order_multiple: '',
    };
    expect(termsError(draft)).not.toBeNull();
  });
});
