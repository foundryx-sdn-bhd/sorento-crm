'use client';

import type { FieldErrors, UseFormRegister, UseFormSetValue, UseFormWatch } from 'react-hook-form';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useWarehouseOptions } from '@/app/(protected)/scm/hooks/useScmOptions';
import { searchProductOptions } from '@/app/(protected)/scm/services/scmOptionsService';
import { DEMAND_OPTIONS } from '@/app/(protected)/scm/reorder/components/RunPlanningModal';
import type { FormValues } from './ScheduledTaskForm';
import { useCallback, useMemo, useState } from 'react';

interface ScmReorderRunScopeFieldsProps {
  watch: UseFormWatch<FormValues>;
  setValue: UseFormSetValue<FormValues>;
  register: UseFormRegister<FormValues>;
  errors: FieldErrors<FormValues>;
}

/**
 * The scm_reorder_run task's configurable scope (#1340): the same fields Start Plan
 * offers, reused here so an unattended scheduled run can be narrowed the same way a
 * person narrows a manual one. Every field is optional and clearing it sends `null` for
 * its key (`mapFormToUpdateBody`) - absent means the run's own today default.
 */
export function ScmReorderRunScopeFields({
  watch,
  setValue,
  register,
  errors,
}: ScmReorderRunScopeFieldsProps) {
  const {
    data: warehouseOptions,
    isLoading: warehousesLoading,
    isError: warehousesError,
  } = useWarehouseOptions();

  const [productLabels, setProductLabels] = useState<Record<string, string>>({});
  const fetchProductOptions = useCallback(async (query: string) => {
    const options = await searchProductOptions(query);
    setProductLabels((prev) => {
      const next = { ...prev };
      for (const opt of options) next[opt.value] = opt.label;
      return next;
    });
    return options;
  }, []);

  // `?? []` would allocate a fresh array every render, which then re-triggers the
  // useMemo below on every render regardless of whether the watched value changed -
  // memoize the fallback away.
  const watchedProductCodes = watch('product_codes');
  const productCodes = useMemo(() => watchedProductCodes ?? [], [watchedProductCodes]);
  const selectedProductOptions = useMemo(
    () => productCodes.map((code) => ({ value: code, label: productLabels[code] ?? code })),
    [productCodes, productLabels],
  );

  return (
    <div className="space-y-4 rounded-md border p-4">
      <p className="text-sm font-medium">Reorder run scope</p>

      <div>
        <Label htmlFor="scm-demand" className="mb-1 block">Demand</Label>
        <SearchableSelect
          id="scm-demand"
          value={watch('demand_class') ?? ''}
          onChange={(v) => setValue('demand_class', v as '' | 'project' | 'retail', { shouldDirty: true })}
          options={DEMAND_OPTIONS}
          clearable
          placeholder="All"
        />
      </div>

      <div>
        <Label className="mb-1 block">Sales orders needed</Label>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="scm-horizon-start" className="mb-1 block text-2xs text-muted-foreground">
              From (days from run day)
            </Label>
            <NumberInput
              id="scm-horizon-start"
              {...register('horizon_start_days', {
                setValueAs: (v) => (v === '' || v === null ? '' : Number(v)),
              })}
            />
          </div>
          <div>
            <Label htmlFor="scm-horizon-end" className="mb-1 block text-2xs text-muted-foreground">
              To (days from run day)
            </Label>
            <NumberInput
              id="scm-horizon-end"
              {...register('horizon_end_days', {
                setValueAs: (v) => (v === '' || v === null ? '' : Number(v)),
              })}
            />
          </div>
        </div>
        {errors.horizon_start_days && (
          <p className="mt-1 text-sm text-destructive">{errors.horizon_start_days.message}</p>
        )}
        {errors.horizon_end_days && (
          <p className="mt-1 text-sm text-destructive">{errors.horizon_end_days.message}</p>
        )}
        <p className="mt-1 text-2xs text-muted-foreground">
          Days from the run day. Empty = every open order counts.
        </p>
      </div>

      <div>
        <Label htmlFor="scm-warehouses" className="mb-1 block">Warehouses</Label>
        <SearchableMultiSelect
          id="scm-warehouses"
          value={watch('warehouse_codes') ?? []}
          onChange={(v) => setValue('warehouse_codes', v, { shouldDirty: true })}
          options={warehouseOptions ?? []}
          disabled={warehousesLoading}
          placeholder={warehousesLoading ? 'Loading warehouses...' : 'All warehouses'}
          emptyMessage={warehousesError ? 'Could not load warehouses.' : 'No warehouses found.'}
        />
      </div>

      <div>
        <Label htmlFor="scm-products" className="mb-1 block">Products</Label>
        <SearchableMultiSelect
          id="scm-products"
          value={productCodes}
          onChange={(v) => setValue('product_codes', v, { shouldDirty: true })}
          fetchOptions={fetchProductOptions}
          selectedOptions={selectedProductOptions}
          placeholder="All products"
          emptyMessage="No products found."
        />
      </div>

      <div>
        <Label htmlFor="scm-budget" className="mb-1 block">Budget</Label>
        <NumberInput
          id="scm-budget"
          min={0}
          placeholder="Full budget"
          {...register('budget', {
            setValueAs: (v) => (v === '' || v === null ? '' : Number(v)),
          })}
        />
        {errors.budget && <p className="mt-1 text-sm text-destructive">{errors.budget.message}</p>}
        <p className="mt-1 text-2xs text-muted-foreground">Leave blank to fund every buy.</p>
      </div>

      <div className="flex items-center space-x-2">
        <Switch
          id="scm-include-market"
          checked={watch('include_market') ?? false}
          onCheckedChange={(v) => setValue('include_market', v, { shouldDirty: true })}
        />
        <Label htmlFor="scm-include-market">Market insight</Label>
      </div>
    </div>
  );
}
