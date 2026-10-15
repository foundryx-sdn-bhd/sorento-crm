'use client';

/**
 * One editable product line (UAC S2-16; F7) - shared by `SalesOpportunityPortalForm` (create)
 * and `SalesOpportunityPortalDetail` (edit in place, Phase 3 fix2 should-fix 5), the portal's
 * own mirror of the CRM's `app/(protected)/sales/opportunities/components/OpportunityLineRow.tsx`
 * over the system `SearchableSelect` in `fetchOptions` mode.
 *
 * At 375px the product field is its own full-width row; qty, unit price, amount and remove
 * sit on the row under it (F7).
 */
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { formatCurrency } from '@/lib/helpers';
import { getPortalProductOptions, type PortalProductOption } from '../../lib/sales-opportunity-service';
import { codeWithName } from '@/lib/product-display';

export interface LineDraft {
  key: string;
  productId: string;
  productLabel: string;
  qty: string;
  /** '' = not set yet - prefilled from the product's list price on pick, still editable. */
  unitPrice: string;
}

let lineKeySeq = 0;
export function nextLineKey(): string {
  lineKeySeq += 1;
  return `line-${lineKeySeq}`;
}

/** Null when qty or unit price is missing/invalid - the Amount cell shows "-" then. Rounded
 *  to 2dp here (not left to the caller's own display formatting) so the total sums exactly
 *  what each line itself shows, rather than drifting a cent from summing raw floats. */
export function lineDraftAmount(line: LineDraft): number | null {
  if (line.qty === '' || line.unitPrice === '') return null;
  const qty = Number(line.qty);
  const price = Number(line.unitPrice);
  if (!Number.isFinite(qty) || !Number.isFinite(price)) return null;
  return Math.round(qty * price * 100) / 100;
}

/** Sum of every line's own amount (a line with no price contributes nothing, not a blocker). */
export function sumLineDraftAmounts(lines: LineDraft[]): number {
  return lines.reduce((acc, line) => acc + (lineDraftAmount(line) ?? 0), 0);
}

interface ProductSelectOption extends SearchableSelectOption {
  listPrice: string | null;
}

async function fetchProductOptions(q: string): Promise<ProductSelectOption[]> {
  const items = await getPortalProductOptions(q);
  return items.map((item: PortalProductOption) => ({
    value: item.id,
    label: codeWithName(item.code, item.name),
    listPrice: item.listPrice,
  }));
}

export function PortalOpportunityLineRow({
  line,
  onChange,
  onRemove,
}: {
  line: LineDraft;
  onChange: (patch: Partial<LineDraft>) => void;
  onRemove: () => void;
}) {
  const amount = lineDraftAmount(line);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`product-${line.key}`}>Product</Label>
        <SearchableSelect
          id={`product-${line.key}`}
          value={line.productId}
          onChange={() => {
            /* the whole option carries the label + list price; see onOptionChange */
          }}
          onOptionChange={(option) => {
            const picked = option as ProductSelectOption | null;
            onChange({
              productId: picked?.value ?? '',
              productLabel: picked?.label ?? '',
              unitPrice: picked?.listPrice ?? '',
            });
          }}
          fetchOptions={fetchProductOptions}
          selectedOption={
            line.productId ? { value: line.productId, label: line.productLabel } : undefined
          }
          wrapOptions
          placeholder="Search products..."
          emptyMessage="No products match."
        />
      </div>
      <div className="flex items-end gap-2">
        <div className="w-16 flex flex-col gap-1.5">
          <Label htmlFor={`qty-${line.key}`}>Qty</Label>
          <Input
            id={`qty-${line.key}`}
            type="number"
            min="0.01"
            step="0.01"
            value={line.qty}
            onChange={(e) => onChange({ qty: e.target.value })}
          />
        </div>
        <div className="w-24 flex flex-col gap-1.5">
          <Label htmlFor={`unit-price-${line.key}`}>Unit price</Label>
          <Input
            id={`unit-price-${line.key}`}
            type="number"
            min="0"
            step="0.01"
            value={line.unitPrice}
            onChange={(e) => onChange({ unitPrice: e.target.value })}
          />
        </div>
        <div className="flex-1 flex flex-col gap-1.5">
          <span className="text-xs text-muted-foreground">Amount</span>
          <span className="text-sm font-medium">{formatCurrency(amount)}</span>
        </div>
        <Button type="button" variant="ghost" size="sm" aria-label="Remove product" onClick={onRemove}>
          <Trash2 className="size-4" />
          Remove
        </Button>
      </div>
    </div>
  );
}
