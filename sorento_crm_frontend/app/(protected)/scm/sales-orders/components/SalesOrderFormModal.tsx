'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { LoaderCircleIcon, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useCustomerOptions, useOrderTypeOptions } from '../../hooks/useScmOptions';
import { SELECT_PAGE_SIZE, searchProductOptions } from '../../services/scmOptionsService';
import type { SalesOrderFormData, SalesOrderPriority } from '../../types/scm.types';

/**
 * CREATE only. Editing a sales order happens in place on the detail page (A5) - the same
 * shape as the project sales order screen - so this modal no longer takes an `editing` row
 * or writes a PUT; the list's Pencil action navigates to `/scm/sales-orders/{id}?edit=1`
 * instead of opening this dialog.
 */

const PRIORITY_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'normal', label: 'Normal' },
  { value: 'high', label: 'High' },
  { value: 'urgent', label: 'Urgent' },
];

// `uom` is display-only (product base UOM, stamped by the BE) - carried through the draft
// but never sent on write.
type LineDraft = { sku: string; qty_ordered: string; uom: string };

/** A picked product needs a quantity above zero. */
const qtyError = (raw: string) =>
  numberFieldError(raw, { min: 0, minExclusive: true });

const emptyLine = (): LineDraft => ({ sku: '', qty_ordered: '', uom: '' });

export function SalesOrderFormModal({
  open,
  onOpenChange,
  onSubmit,
  isPending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: SalesOrderFormData) => Promise<void>;
  isPending: boolean;
}) {
  const [orderType, setOrderType] = useState('');
  const [customer, setCustomer] = useState('');
  const [priority, setPriority] = useState<SalesOrderPriority>('normal');
  const [requestedDate, setRequestedDate] = useState('');
  const [lines, setLines] = useState<LineDraft[]>([emptyLine()]);
  const [error, setError] = useState<string | null>(null);
  /** Labels of every product this modal has seen come back from the server, so a line whose
   *  product is not on the page currently loaded still reads as `CODE · Name`. */
  const [productLabels, setProductLabels] = useState<Record<string, string>>({});
  const [showErrors, setShowErrors] = useState(false);

  const orderTypeOptions = useOrderTypeOptions();
  const customerOptions = useCustomerOptions();

  /** Products are SEARCHED ON THE SERVER, never a static list: `products/select` answers with
   *  its own default of 100 rows against ~22,000 active products, so the list this field used
   *  to hold covered 0.5% of the catalogue and said "no product found" for the rest. Same
   *  helper the sales-order detail line picker uses. */
  const fetchProductOptions = useCallback(async (query: string, pageIndex: number) => {
    const options = await searchProductOptions(query, pageIndex);
    setProductLabels((prev) => {
      const next = { ...prev };
      for (const opt of options) next[opt.value] = opt.label;
      return next;
    });
    return options;
  }, []);

  const selectedProductOptions = useMemo(
    () =>
      lines.map((l) =>
        l.sku ? { value: l.sku, label: productLabels[l.sku] ?? l.sku } : undefined,
      ),
    [lines, productLabels],
  );

  useEffect(() => {
    if (!open) return;
    setOrderType('');
    setCustomer('');
    setPriority('normal');
    setRequestedDate('');
    setLines([emptyLine()]);
    setError(null);
    setShowErrors(false);
    setProductLabels({});
  }, [open]);

  const segment = customerOptions.data?.find((c) => c.value === customer)?.description ?? null;

  const updateLine = (idx: number, patch: Partial<LineDraft>) => {
    setLines((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!orderType) return setError('Select an order type.');
    if (!customer) return setError('Select a customer.');
    // uom is display-only and dropped by the service before the write payload.
    const picked = lines.filter((l) => l.sku);
    // A line with a product but no usable quantity is told, never dropped.
    if (picked.some((l) => qtyError(l.qty_ordered))) {
      setShowErrors(true);
      return;
    }
    const cleanedLines = picked
      .map((l) => ({ sku: l.sku, qty_ordered: Number(l.qty_ordered), uom: l.uom }));
    if (cleanedLines.length === 0) {
      return setError('Add at least one line with a product and quantity.');
    }
    await onSubmit({
      order_type: orderType,
      customer_code: customer,
      priority,
      requested_delivery_date: requestedDate || null,
      lines: cleanedLines,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add sales order</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label className="mb-1 block">Order type</Label>
              <SearchableSelect
                value={orderType}
                onChange={setOrderType}
                options={orderTypeOptions.data ?? []}
                placeholder="Select type"
              />
            </div>
            <div>
              <Label className="mb-1 block">Customer</Label>
              <SearchableSelect
                value={customer}
                onChange={setCustomer}
                options={customerOptions.data ?? []}
                placeholder="Select customer"
              />
              {segment ? (
                <p className="mt-1 text-2xs text-muted-foreground">Segment: {segment}</p>
              ) : null}
            </div>
            <div>
              <Label className="mb-1 block">Priority</Label>
              <SearchableSelect
                value={priority}
                onChange={(v) => setPriority((v || 'normal') as SalesOrderPriority)}
                options={PRIORITY_OPTIONS}
                placeholder="Select priority"
              />
            </div>
            <div>
              <Label className="mb-1 block">Requested delivery date</Label>
              <Input
                type="date"
                value={requestedDate}
                onChange={(e) => setRequestedDate(e.target.value)}
              />
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <Label>Lines</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setLines((prev) => [...prev, emptyLine()])}
              >
                <Plus className="size-4" />
                Add line
              </Button>
            </div>
            <div className="space-y-2">
              {lines.map((line, idx) => (
                <div key={idx} className="flex items-end gap-2">
                  <div className="flex-1">
                    {idx === 0 ? (
                      <Label className="mb-1 block text-2xs text-muted-foreground">Product</Label>
                    ) : null}
                    <SearchableSelect
                      value={line.sku}
                      onChange={(v) => updateLine(idx, { sku: v })}
                      paginated
                      pageSize={SELECT_PAGE_SIZE}
                      fetchOptions={fetchProductOptions}
                      selectedOption={selectedProductOptions[idx]}
                      placeholder="Select product"
                      emptyMessage="No product found."
                    />
                  </div>
                  <div className="w-24">
                    {idx === 0 ? (
                      <Label className="mb-1 block text-2xs text-muted-foreground">Qty</Label>
                    ) : null}
                    <NumberInput
                      min={0}
                      value={line.qty_ordered}
                      error={showErrors && line.sku ? qtyError(line.qty_ordered) : undefined}
                      onChange={(e) => updateLine(idx, { qty_ordered: e.target.value })}
                      className="text-right tabular-nums"
                    />
                  </div>
                  <div className="w-24">
                    {idx === 0 ? (
                      <Label className="mb-1 block text-2xs text-muted-foreground">UoM</Label>
                    ) : null}
                    {/* Display-only: the BE stamps UoM from the product's base UOM. */}
                    <div
                      className="flex h-9 items-center rounded-md border border-input bg-muted/40 px-3 text-sm text-muted-foreground"
                      title="Set automatically from the product's base unit of measure"
                    >
                      {line.uom || '-'}
                    </div>
                  </div>
                  <Button
                    type="button"
                    mode="icon"
                    variant="ghost"
                    size="sm"
                    className="text-destructive"
                    disabled={lines.length === 1}
                    onClick={() => setLines((prev) => prev.filter((_, i) => i !== idx))}
                    aria-label="Remove line"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? <LoaderCircleIcon className="me-2 size-4 animate-spin" /> : null}
              Create sales order
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
