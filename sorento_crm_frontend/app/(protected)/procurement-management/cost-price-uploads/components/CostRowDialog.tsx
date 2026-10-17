'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DatePicker } from '@/components/ui/date-picker';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { toast } from '@/lib/toast';
import {
  createProductSupplierCost,
  updateProductSupplierCost,
} from '../services/costPriceService';
import type { ProductSupplierCostRow } from '../types/costPrice.types';
import { useDeferredAction } from '@/hooks/useDeferredAction';

const CURRENCY_OPTIONS = [
  { value: 'CNY', label: 'CNY' },
  { value: 'USD', label: 'USD' },
  { value: 'MYR', label: 'MYR' },
  { value: 'EUR', label: 'EUR' },
];

function toDateOnly(date: Date | undefined): string | null {
  if (!date) return null;
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function fromDateOnly(value: string | null): Date | undefined {
  if (!value) return undefined;
  const [y, m, d] = value.split('-').map(Number);
  return new Date(y, m - 1, d);
}

/**
 * Add or edit one cost list row by hand (section 2.3), shared by the supplier Prices tab
 * and the product Suppliers tab. Delete is the 10s deferred action, no dialog (D7).
 */
export function CostRowDialog({
  open,
  onOpenChange,
  link,
  cost,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  /** `packaging`: the packaging method a new row is for (round 8); absent means `standard`. */
  link: { id: string; product?: { product_code: string } | null; currency?: string | null; packaging?: string | null };
  cost: ProductSupplierCostRow | null;
  onSaved: () => void;
}) {
  const [unitCost, setUnitCost] = React.useState(cost ? String(cost.unit_cost) : '');
  const [currency, setCurrency] = React.useState(cost?.currency ?? link.currency ?? '');
  const [startDate, setStartDate] = React.useState<Date | undefined>(fromDateOnly(cost?.start_date ?? null));
  const [endDate, setEndDate] = React.useState<Date | undefined>(fromDateOnly(cost?.end_date ?? null));
  const [saving, setSaving] = React.useState(false);
  // The cost is checked when Save is pressed; afterwards the message follows the box.
  const [checked, setChecked] = React.useState(false);

  const deleteAction = useDeferredAction({
    actionKey: 'product_supplier_cost.delete',
    entityType: 'product_supplier_cost',
    entityId: cost?.id ?? null,
    verb: 'Deleting',
    subject: `the ${link.product?.product_code ?? ''} cost`.trim(),
    surface: 'inline',
    successMessage: 'Deleted.',
    onCommitted: () => {
      onSaved();
      onOpenChange(false);
    },
  });

  const endBeforeStart = Boolean(startDate && endDate && endDate < startDate);
  const canSave = Boolean(currency) && !endBeforeStart;
  const costError = checked ? numberFieldError(unitCost, { min: 0 }) : undefined;

  const onSave = async () => {
    if (!canSave) return;
    setChecked(true);
    if (numberFieldError(unitCost, { min: 0 })) return;
    setSaving(true);
    try {
      const input = {
        unit_cost: parseNumberDraft(unitCost) as number,
        currency,
        start_date: toDateOnly(startDate),
        end_date: toDateOnly(endDate),
      };
      if (cost) await updateProductSupplierCost(link.id, cost.id, input);
      else await createProductSupplierCost(link.id, link.packaging ? { ...input, packaging_method: link.packaging } : input);
      onSaved();
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save the cost');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            Cost for {link.product?.product_code ?? 'this product'}
            {(cost?.packaging_method ?? link.packaging) ? ` · ${cost?.packaging_method ?? link.packaging}` : ''}
          </DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="cost-row-price">Cost</Label>
              <NumberInput
                id="cost-row-price"
                min={0}
                step="0.01"
                value={unitCost}
                onChange={(e) => setUnitCost(e.target.value)}
                error={costError}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cost-row-currency">Currency</Label>
              <SearchableSelect
                id="cost-row-currency"
                value={currency}
                onChange={setCurrency}
                options={CURRENCY_OPTIONS}
                triggerClassName="w-full"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>
                Valid from <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <DatePicker value={startDate} onChange={setStartDate} ariaLabel="Valid from" />
            </div>
            <div className="space-y-1.5">
              <Label>
                Valid to <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <DatePicker value={endDate} onChange={setEndDate} ariaLabel="Valid to" />
            </div>
          </div>
          {endBeforeStart ? <p className="text-xs text-destructive">Valid to cannot be before Valid from.</p> : null}
        </DialogBody>
        <DialogFooter className="sm:justify-between">
          {cost ? (
            deleteAction.countdown ?? (
              <Button variant="ghost" className="text-destructive" onClick={() => deleteAction.start()}>
                Delete
              </Button>
            )
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={onSave} disabled={!canSave || saving}>
              Save
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default CostRowDialog;
