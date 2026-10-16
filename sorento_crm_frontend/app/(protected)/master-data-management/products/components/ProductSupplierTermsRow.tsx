'use client';

import { useEffect, useMemo, useState } from 'react';
import { LoaderCircleIcon, Save, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { numberFieldError } from '@/lib/number-field';
import type {
  ProductSupplier,
  ProductSupplierSourcingTerms,
} from '../../../procurement-management/product-suppliers/types/productSupplier.types';

/**
 * One supplier's terms for a product: lead time, price, the currency the price is in, and
 * the quantities the supplier will accept. The reorder plan reads every one of them, and
 * until this row existed none of them could be entered - which is why the plan's "No price
 * yet" section is as large as it is.
 *
 * The whole row is one draft with one Save, rather than a save per field: a price and its
 * currency have to move together (see `dirty` + the caller's validation), and saving them
 * in two requests would leave a price briefly attached to the wrong money.
 */

export type SupplierTermsDraft = {
  standard_lead_time_days: string;
  unit_cost: string;
  currency: string;
  moq: string;
  order_multiple: string;
};

function toDraft(ps: ProductSupplier): SupplierTermsDraft {
  const lead = ps.standard_lead_time_days ?? ps.lead_time_days;
  return {
    standard_lead_time_days: lead === undefined || lead === null ? '' : String(lead),
    unit_cost: ps.unit_cost === undefined || ps.unit_cost === null ? '' : String(ps.unit_cost),
    currency: ps.currency ?? '',
    moq: ps.moq === undefined || ps.moq === null ? '' : String(ps.moq),
    order_multiple:
      ps.order_multiple === undefined || ps.order_multiple === null
        ? ''
        : String(ps.order_multiple),
  };
}

/** Blank means "nothing on file", which is a different fact from zero and is preserved. */
function numOrNull(raw: string): number | null {
  const t = raw.trim();
  if (t === '') return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

export function draftToPatch(d: SupplierTermsDraft): Partial<ProductSupplierSourcingTerms> & {
  standard_lead_time_days?: number;
} {
  const lead = numOrNull(d.standard_lead_time_days);
  return {
    ...(lead === null ? {} : { standard_lead_time_days: lead }),
    unit_cost: numOrNull(d.unit_cost),
    currency: d.currency.trim() || null,
    moq: numOrNull(d.moq),
    order_multiple: numOrNull(d.order_multiple),
  };
}

/** The one rule the backend also enforces: a price has to say what money it is in. */
export function termsError(d: SupplierTermsDraft): string | null {
  const lead = numOrNull(d.standard_lead_time_days);
  if (lead === null || lead < 0) return 'Enter a lead time in days.';
  // A typed figure that cannot be read (bad input) is never taken as "nothing on file".
  for (const raw of [d.unit_cost, d.moq, d.order_multiple]) {
    if (raw.trim() !== '' && numOrNull(raw) === null) return numberFieldError(raw, { min: 0, optional: true }) ?? null;
  }
  const cost = numOrNull(d.unit_cost);
  if (cost !== null && cost < 0) return 'A price cannot be negative.';
  if (cost !== null && !d.currency.trim()) return 'Choose the currency this price is in.';
  const multiple = numOrNull(d.order_multiple);
  if (multiple !== null && multiple < 1) return 'An order multiple must be at least 1.';
  const moq = numOrNull(d.moq);
  if (moq !== null && moq < 0) return 'A minimum order cannot be negative.';
  return null;
}

export function ProductSupplierTermsRow({
  ps,
  currencyOptions,
  onSave,
  onRemove,
  isSaving,
  isDeleting,
}: {
  ps: ProductSupplier;
  /** Currencies we hold an exchange rate for, so a saved price stays comparable. */
  currencyOptions: { value: string; label: string }[];
  onSave: (draft: SupplierTermsDraft) => void;
  /** Parks the detach on the server for its grace window (D7). Asks nothing first. */
  onRemove: () => void;
  isSaving: boolean;
  /** True while THIS row's removal is counting down, so its control stays quiet. */
  isDeleting: boolean;
}) {
  const saved = useMemo(() => toDraft(ps), [ps]);
  const [draft, setDraft] = useState<SupplierTermsDraft>(saved);

  // Re-sync when the server row changes (a save landing, or a refetch), so the inputs show
  // what was stored rather than what was typed.
  useEffect(() => setDraft(saved), [saved]);

  const dirty = (Object.keys(saved) as (keyof SupplierTermsDraft)[]).some(
    (k) => draft[k] !== saved[k],
  );
  const error = dirty ? termsError(draft) : null;
  const set = (k: keyof SupplierTermsDraft, v: string) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const supplierName = ps.supplier?.supplier_name || 'Unknown supplier';
  // The currency already on the row stays selectable even when no rate is on file for it,
  // so opening this form can never silently drop a value somebody saved.
  const options = currencyOptions.some((o) => o.value === draft.currency) || !draft.currency
    ? currencyOptions
    : [...currencyOptions, { value: draft.currency, label: draft.currency }];

  return (
    <div className="rounded-lg border p-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant="secondary">{ps.supplier?.supplier_code || 'N/A'}</Badge>
          <span className="truncate text-sm font-medium" title={supplierName}>
            {supplierName}
          </span>
          {ps.is_primary_supplier ? (
            <Badge variant="primary" appearance="light" size="sm">
              primary
            </Badge>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {dirty ? (
            <Button
              type="button"
              size="sm"
              onClick={() => onSave(draft)}
              disabled={isSaving || !!error}
            >
              {isSaving ? (
                <LoaderCircleIcon className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
              Save terms
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onRemove}
            disabled={isDeleting}
            aria-label={`Remove ${supplierName}`}
          >
            {isDeleting ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <Trash2 className="size-4 text-destructive" />
            )}
          </Button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <div className="space-y-1">
          <Label htmlFor={`lead-${ps.id}`} className="text-xs text-muted-foreground">
            Lead time (days)
          </Label>
          <NumberInput
            id={`lead-${ps.id}`}
            min={0}
            className="h-8"
            value={draft.standard_lead_time_days}
            onChange={(e) => set('standard_lead_time_days', e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`cost-${ps.id}`} className="text-xs text-muted-foreground">
            Unit cost
          </Label>
          <NumberInput
            id={`cost-${ps.id}`}
            min={0}
            step="0.01"
            className="h-8"
            value={draft.unit_cost}
            onChange={(e) => set('unit_cost', e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`ccy-${ps.id}`} className="text-xs text-muted-foreground">
            Currency
          </Label>
          <SearchableSelect
            id={`ccy-${ps.id}`}
            size="sm"
            clearable
            value={draft.currency}
            onChange={(v) => set('currency', v)}
            options={options}
            placeholder="Select"
            emptyMessage="Add the exchange rate first, under SCM."
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`moq-${ps.id}`} className="text-xs text-muted-foreground">
            Minimum order
          </Label>
          <NumberInput
            id={`moq-${ps.id}`}
            min={0}
            className="h-8"
            value={draft.moq}
            onChange={(e) => set('moq', e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`mult-${ps.id}`} className="text-xs text-muted-foreground">
            Order multiple
          </Label>
          <NumberInput
            id={`mult-${ps.id}`}
            min={1}
            className="h-8"
            value={draft.order_multiple}
            onChange={(e) => set('order_multiple', e.target.value)}
          />
        </div>
      </div>

      {error ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
