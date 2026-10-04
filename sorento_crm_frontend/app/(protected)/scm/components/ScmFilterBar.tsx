'use client';

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import {
  useCategoryOptions,
  useSupplierOptions,
  useWarehouseOptions,
} from '../hooks/useScmOptions';
import {
  ABC_DISPLAY,
  UNKNOWN_CLASS_LABEL,
  XYZ_DISPLAY,
} from '../lib/health';
import { EMPTY_SCM_FILTERS, type ScmFilters } from '../services/scmDashboardService';
import { isFocusedScope } from '../lib/scope';
import type {
  AbcFilterValue,
  ActiveStatusFilter,
  LifecycleFilter,
  XyzFilterValue,
} from '../types/scm.types';

/** The "Value" (ABC) + "Demand" (XYZ) scales are a tiny fixed vocabulary →
 *  static-filter SearchableSelect (not async). Option labels use the same
 *  plain-language display maps as the grid chips; the stored filter values stay
 *  A/B/C · X/Y/Z (+ `unknown` for SKUs the analytics job couldn't classify). */
const VALUE_OPTIONS = [
  { value: 'A', label: ABC_DISPLAY.A },
  { value: 'B', label: ABC_DISPLAY.B },
  { value: 'C', label: ABC_DISPLAY.C },
  { value: 'unknown', label: UNKNOWN_CLASS_LABEL },
];
const DEMAND_OPTIONS = [
  { value: 'X', label: XYZ_DISPLAY.X },
  { value: 'Y', label: XYZ_DISPLAY.Y },
  { value: 'Z', label: XYZ_DISPLAY.Z },
  { value: 'unknown', label: UNKNOWN_CLASS_LABEL },
];

/** Product-lifecycle scope. Small fixed vocabularies → static SearchableSelect.
 *  Defaults (Active / Ongoing) are the focused view; "All" widens the scope. */
const PRODUCT_STATUS_OPTIONS = [
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'all', label: 'All' },
];
const LIFECYCLE_OPTIONS = [
  { value: 'ongoing', label: 'Ongoing' },
  { value: 'discontinued', label: 'Discontinued' },
  { value: 'all', label: 'All' },
];

export function ScmFilterBar({
  filters,
  onChange,
  isFetching,
}: {
  filters: ScmFilters;
  onChange: (next: ScmFilters) => void;
  /** The dashboard's own fetch state, so the box can spin while a debounced
   * search is in flight - this bar owns the debounce, not the caller. */
  isFetching?: boolean;
}) {
  const warehouseOptions = useWarehouseOptions();
  const categoryOptions = useCategoryOptions();
  const supplierOptions = useSupplierOptions();
  const {
    value: productSearchInput,
    setValue: setProductSearchInput,
    debouncedValue: productSearch,
    isSettling: productSearchSettling,
    reset: resetProductSearch,
  } = useDebouncedSearch(filters.productSearch);

  // The box owns its own live value and debounces before telling the parent -
  // `lastPushed` tells the two effects below apart: a change that came FROM
  // this component (push it up) from one that arrived from OUTSIDE it (the
  // Clear button below, or the scope chip resetting the whole filter set),
  // which the box has to catch up to instead.
  const lastPushed = useRef(filters.productSearch);
  useEffect(() => {
    if (filters.productSearch !== lastPushed.current) {
      resetProductSearch(filters.productSearch);
      lastPushed.current = filters.productSearch;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.productSearch]);
  useEffect(() => {
    if (productSearch !== lastPushed.current) {
      lastPushed.current = productSearch;
      onChange({ ...filters, productSearch });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productSearch]);
  // "Clear" resets to the FOCUSED default (not to `all`), so a widened lifecycle
  // scope also counts as an active filter worth clearing.
  const hasActive =
    filters.warehouses.length > 0 ||
    !!filters.categoryId ||
    !!filters.supplier ||
    !!filters.healthStatus ||
    !!filters.abcClass ||
    !!filters.xyzClass ||
    !!filters.productSearch ||
    !isFocusedScope(filters);

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
      <div className="min-w-52 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Search product</Label>
        <ListSearchInput
          value={productSearchInput}
          onChange={setProductSearchInput}
          isSettling={isSearchInFlight(productSearchSettling, isFetching ?? false, productSearch)}
          placeholder="Search product…"
          className="w-full"
        />
      </div>
      <div className="min-w-52 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Warehouses</Label>
        <SearchableMultiSelect
          value={filters.warehouses}
          onChange={(v) => onChange({ ...filters, warehouses: v })}
          options={warehouseOptions.data ?? []}
          placeholder="All warehouses"
        />
      </div>
      <div className="min-w-52 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Product Category</Label>
        <SearchableSelect
          value={filters.categoryId ?? ''}
          onChange={(v) => onChange({ ...filters, categoryId: v || null })}
          loadError={categoryOptions.error}
          onRetry={() => void categoryOptions.refetch()}
          options={categoryOptions.data ?? []}
          placeholder="All categories"
        />
      </div>
      <div className="min-w-52 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Supplier</Label>
        <SearchableSelect
          value={filters.supplier ?? ''}
          onChange={(v) => onChange({ ...filters, supplier: v || null })}
          options={supplierOptions.data ?? []}
          placeholder="All suppliers"
        />
      </div>
      <div className="min-w-36 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Value</Label>
        <SearchableSelect
          value={filters.abcClass ?? ''}
          onChange={(v) => onChange({ ...filters, abcClass: (v || null) as AbcFilterValue | null })}
          options={VALUE_OPTIONS}
          placeholder="All values"
        />
      </div>
      <div className="min-w-36 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Demand</Label>
        <SearchableSelect
          value={filters.xyzClass ?? ''}
          onChange={(v) => onChange({ ...filters, xyzClass: (v || null) as XyzFilterValue | null })}
          options={DEMAND_OPTIONS}
          placeholder="All demand"
        />
      </div>
      <div className="min-w-36 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Product status</Label>
        <SearchableSelect
          value={filters.activeStatus}
          onChange={(v) =>
            onChange({ ...filters, activeStatus: (v || 'active') as ActiveStatusFilter })
          }
          options={PRODUCT_STATUS_OPTIONS}
          placeholder="Active"
        />
      </div>
      <div className="min-w-36 flex-1">
        <Label className="mb-1 block text-xs text-muted-foreground">Lifecycle</Label>
        <SearchableSelect
          value={filters.lifecycle}
          onChange={(v) =>
            onChange({ ...filters, lifecycle: (v || 'ongoing') as LifecycleFilter })
          }
          options={LIFECYCLE_OPTIONS}
          placeholder="Ongoing"
        />
      </div>
      {hasActive ? (
        <Button
          variant="ghost"
          size="sm"
          className="self-end"
          onClick={() => onChange({ ...EMPTY_SCM_FILTERS })}
        >
          <X className="size-4" />
          Clear
        </Button>
      ) : null}
    </div>
  );
}
