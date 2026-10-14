'use client';

import { useRef } from 'react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { codeWithName } from '@/lib/product-display';

const PAGE_SIZE = 50;

export interface ProductOption {
  id: string;
  product_code: string;
  product_name?: string;
}

export interface ProductComboboxSearchableProps {
  value: string;
  onChange: (value: string) => void;
  /** Fetch products from API: (searchQuery, pageIndex) => Promise<{ data: ProductOption[] }> */
  fetchProducts: (query: string, pageIndex: number) => Promise<{ data: ProductOption[] }>;
  /**
   * The FULL product just picked (or `null` on clear) - not only its id.
   *
   * A caller that keeps its own copy of the code/name beside the id (a line-table draft
   * that renders those columns separately, rather than re-deriving them from `value` on
   * every render) has to update all three together. Without this the id changed but the
   * code/name it was fed as `productFallback` stayed the PREVIOUS product's, and
   * `renderTriggerLabel` below - which trusts `productFallback` once its `id` matches the
   * new `value` - printed the old product's code under the new selection.
   */
  onOptionChange?: (option: ProductOption | null) => void;
  productFallback?: ProductOption | null;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** One-line ellipsis trigger for fixed-width table cells. */
  truncateTriggerLabel?: boolean;
}

const itemLabel = (p: ProductOption) => codeWithName(p.product_code, p.product_name);

/**
 * Server-searched, paginated product picker. Now a thin wrapper over the standard
 * SearchableSelect's async + paginated mode - the debounce, stale-response dropping and
 * page accumulation it used to hand-roll all live in the component.
 */
export function ProductComboboxSearchable({
  value,
  onChange,
  onOptionChange,
  fetchProducts,
  productFallback,
  placeholder = 'Search or select product',
  disabled,
  className,
  truncateTriggerLabel,
}: ProductComboboxSearchableProps) {
  // Every product the last search returned, by id - so a pick can hand the caller the
  // FULL product (code + name), not only the id `onChange` carries. A `ref`, not state:
  // it is read once, at select time, and never drives a render itself.
  const fetchedRef = useRef(new Map<string, ProductOption>());

  return (
    <SearchableSelect
      value={value}
      onChange={onChange}
      onOptionChange={
        onOptionChange
          ? (opt) => onOptionChange(opt ? (fetchedRef.current.get(opt.value) ?? null) : null)
          : undefined
      }
      disabled={disabled}
      placeholder={placeholder}
      emptyMessage="No product found."
      triggerClassName={className}
      truncateTriggerLabel={truncateTriggerLabel}
      paginated
      pageSize={PAGE_SIZE}
      fetchOptions={async (query, pageIndex) => {
        const { data } = await fetchProducts(query, pageIndex);
        for (const p of data) fetchedRef.current.set(p.id, p);
        return data.map((p) => ({
          value: p.id,
          label: itemLabel(p),
          searchText: `${p.product_code} ${p.product_name ?? ''}`,
        }));
      }}
      // The saved product is rarely in the first page, so without this the trigger
      // would show nothing when editing an existing row.
      selectedOption={
        productFallback && value === productFallback.id
          ? { value: productFallback.id, label: itemLabel(productFallback) }
          : undefined
      }
      renderTriggerLabel={(opt) => {
        const p = productFallback && productFallback.id === opt.value ? productFallback : null;
        if (!p) return opt.label;
        return `${p.product_code}${p.product_name && p.product_name !== p.product_code ? ` - ${p.product_name}` : ''}`;
      }}
    />
  );
}
