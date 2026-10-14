'use client';

import { useEffect, useRef } from 'react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SEARCH_DEBOUNCE_MS } from '@/hooks/useDebouncedSearch';
import { codeWithName } from '@/lib/product-display';

interface ProductOption {
  id: string;
  product_code: string;
  product_name?: string;
}

interface ProductComboboxProps {
  value: string;
  onChange: (value: string) => void;
  products: ProductOption[];
  productFallback?: { id: string; product_code: string; product_name?: string } | null;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** Server-side search callback, debounced 300ms, so the parent can refetch from
   *  `/products?query=...` rather than relying on the local slice. */
  onSearch?: (query: string) => void;
}

/**
 * Thin domain wrapper over the standard SearchableSelect: owns the product label format,
 * the saved-value fallback, and the debounce in front of the parent's server refetch.
 */
export function ProductCombobox({
  value,
  onChange,
  products = [],
  productFallback,
  placeholder = 'Select product',
  disabled,
  className,
  onSearch,
}: ProductComboboxProps) {
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  // The standard component reports every keystroke, so the debounce lives here -
  // on the one window every search in the app waits (S7-02).
  const handleSearch = (query: string) => {
    if (!onSearch) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => onSearch(query), SEARCH_DEBOUNCE_MS);
  };

  const options = [
    ...products,
    // Keep the saved product selectable even when it is absent from the current page.
    ...(productFallback && value && !products.some((p) => p.id === productFallback.id)
      ? [productFallback]
      : []),
  ];

  return (
    <SearchableSelect
      value={value}
      onChange={onChange}
      disabled={disabled}
      placeholder={placeholder}
      emptyMessage="No product found."
      triggerClassName={className}
      onSearchChange={onSearch ? handleSearch : undefined}
      options={options.map((p) => ({
        value: p.id,
        label: codeWithName(p.product_code, p.product_name),
        searchText: `${p.product_code} ${p.product_name ?? ''}`,
      }))}
      renderTriggerLabel={(opt) => {
        const p = options.find((o) => o.id === opt.value);
        if (!p) return opt.label;
        return codeWithName(p.product_code, p.product_name);
      }}
    />
  );
}
