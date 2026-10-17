'use client';

/**
 * One product line's own picker (plan section 16, N11) - shared by `SalesOpportunityModal`
 * (create) and `SalesOpportunityDetail` (edit in place, Phase 3 fix B2), so the two never
 * carry two copies of the same product-row behaviour.
 */
import { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatCurrency } from '@/lib/helpers';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import {
  getSalesOpportunityProductOptions,
  type SalesOpportunityProductOption,
} from '../services/salesOpportunityService';

let lineKeySeq = 0;
export function nextLineKey(): string {
  lineKeySeq += 1;
  return `line-${lineKeySeq}`;
}

export interface LineDraft {
  key: string;
  productId: string;
  qty: string;
  /** String all the way through, same reasoning `InlineLineTable` gives: the API wants a
   *  decimal string, and a number input round-trips `392.85` as `392.85000000000002`. */
  unitPrice: string;
  /** An existing line's own label (edit in place, Phase 3 fix B2) - seeds the row's
   *  "keep the picked option" state so an already-saved line shows its product
   *  immediately, rather than only once a fetch happens to include it. */
  productLabel?: string;
}

/** `qty x unitPrice`, or null when the row has no usable price (fix round 2 F7).
 *  Rounded to 2dp before it goes anywhere else - the server rounds each line the same
 *  way, and summing un-rounded floats first is how the screen's total stops matching it. */
export function lineAmount(line: Pick<LineDraft, 'qty' | 'unitPrice'>): number | null {
  if (line.unitPrice.trim() === '') return null;
  const price = Number(line.unitPrice);
  // A blank or invalid qty is pending (null), never 0: Number('') is 0.
  const qty = parseNumberDraft(line.qty);
  if (!Number.isFinite(price) || qty === null) return null;
  return Math.round(qty * price * 100) / 100;
}

/** The rules a picked product row's numbers are held to when the form is saved. */
export function lineQtyError(line: Pick<LineDraft, 'qty'>): string | undefined {
  return numberFieldError(line.qty, { min: 0.01 });
}
export function linePriceError(line: Pick<LineDraft, 'unitPrice'>): string | undefined {
  return numberFieldError(line.unitPrice, { min: 0, optional: true });
}
/** True when a row that will be sent (it has a product) holds a number that cannot be. */
export function lineHasNumberError(line: LineDraft): boolean {
  return Boolean(line.productId) && Boolean(lineQtyError(line) || linePriceError(line));
}

/** The live total across every line - what Expected amount defaults to while untouched.
 *  A line whose amount is pending (blank qty) is skipped, never added as 0; the row's own
 *  qty error blocks the save. */
export function sumLineAmounts(lines: Pick<LineDraft, 'qty' | 'unitPrice'>[]): number {
  return lines.reduce((sum, line) => sum + (lineAmount(line) ?? 0), 0);
}

/**
 * `options` (not `fetchOptions`) so this row's search box is one control, not two -
 * `onSearchChange` still hits the server on every keystroke (LESSONS-LEARNT: never a capped
 * static list over the ~22,000-product catalog).
 */
export function OpportunityLineRow({
  line,
  onChange,
  onRemove,
  showErrors = false,
}: {
  line: LineDraft;
  /** Set once a save was attempted: the row's number messages then follow the fields live. */
  showErrors?: boolean;
  onChange: (patch: Partial<LineDraft>) => void;
  onRemove: () => void;
}) {
  const [options, setOptions] = useState<SalesOpportunityProductOption[]>([]);
  // Reviewer should-fix 7: a later search REPLACES `options` wholesale, and a picked
  // product from an earlier search is gone from that replacement the moment the reader
  // types again - the trigger would then find no match for `line.productId` and fall
  // back to the placeholder, reading as if the pick had been lost. Keeping the chosen
  // option itself, merged back in, means it survives regardless of what the next search
  // returns (the static-mode equivalent of `selectedOption`, which only applies in
  // `fetchOptions` mode).
  const [selected, setSelected] = useState<SalesOpportunityProductOption | null>(
    line.productId && line.productLabel ? { value: line.productId, label: line.productLabel } : null,
  );

  useEffect(() => {
    let active = true;
    getSalesOpportunityProductOptions('').then((opts) => {
      if (active) setOptions(opts);
    });
    return () => {
      active = false;
    };
  }, []);

  const mergedOptions = useMemo(() => {
    if (!selected || options.some((o) => o.value === selected.value)) return options;
    return [selected, ...options];
  }, [options, selected]);

  // A fresh pick prefills Unit price from the product's own list price (the price the
  // dealer flyer prints) - the product picked is the fact; a hand-typed edit afterward
  // simply overwrites it, same as any other field.
  const handleOptionChange = (option: SalesOpportunityProductOption | null) => {
    setSelected(option);
    if (option?.listPrice) onChange({ unitPrice: option.listPrice });
  };

  const amount = lineAmount(line);

  return (
    <div data-testid="opportunity-line-row" className="flex flex-wrap items-end gap-2">
      <div className="flex w-full flex-col gap-1.5 sm:w-auto sm:flex-1">
        <Label htmlFor={`Product-${line.key}`}>Product</Label>
        <SearchableSelect
          id={`Product-${line.key}`}
          value={line.productId}
          onChange={(v) => onChange({ productId: v })}
          onOptionChange={handleOptionChange}
          options={mergedOptions}
          onSearchChange={(q) => getSalesOpportunityProductOptions(q).then(setOptions)}
          placeholder="Search products..."
          wrapOptions
        />
      </div>
      <div className="flex w-20 flex-col gap-1.5">
        <Label htmlFor={`Qty-${line.key}`}>Qty</Label>
        <NumberInput
          id={`Qty-${line.key}`}
          min="0.01"
          step="0.01"
          value={line.qty}
          error={showErrors && line.productId ? lineQtyError(line) : undefined}
          onChange={(e) => onChange({ qty: e.target.value })}
        />
      </div>
      <div className="flex w-28 flex-col gap-1.5">
        <Label htmlFor={`UnitPrice-${line.key}`}>Unit price</Label>
        <NumberInput
          id={`UnitPrice-${line.key}`}
          min="0"
          step="0.01"
          value={line.unitPrice}
          error={showErrors && line.productId ? linePriceError(line) : undefined}
          onChange={(e) => onChange({ unitPrice: e.target.value })}
        />
      </div>
      <div className="flex w-28 flex-col gap-1.5">
        <Label htmlFor={`Amount-${line.key}`}>Amount</Label>
        <Input
          id={`Amount-${line.key}`}
          readOnly
          tabIndex={-1}
          value={formatCurrency(amount)}
        />
      </div>
      <Button type="button" variant="ghost" size="sm" aria-label="Remove line" onClick={onRemove}>
        <X className="size-4" />
        Remove
      </Button>
    </div>
  );
}
