'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  EM_DASH,
  fmtInt,
  fmtMoney,
  fmtMoneyIn,
  fmtSupplierCost,
  isBaseCurrency,
} from '../../lib/format';
import type { AdjustPayload } from '../types/decisions.types';
import type { ReorderRecommendation, SupplierChoice } from '../types/reorder.types';
import { distinctName } from '@/lib/product-display';

/**
 * M4-D7 - Adjust a recommendation before it drafts a PO: override the buy qty
 * and/or switch supplier (from the run's ranked alternatives). Switching
 * supplier recomputes a live preview of the resulting lead time + cash impact
 * off the chosen supplier's cost. A reason is REQUIRED (captured raw here;
 * LLM-classified into a code in Slice C). Mobile-scrollable (DialogBody).
 */
export function AdjustRecommendationModal({
  rec,
  open,
  onOpenChange,
  onSubmit,
  isSubmitting,
}: {
  rec: ReorderRecommendation | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: AdjustPayload) => void;
  isSubmitting: boolean;
}) {
  const [qty, setQty] = useState('');
  const [supplierCode, setSupplierCode] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  // Seed the form each time a new rec is opened.
  useEffect(() => {
    if (!open || !rec) return;
    setQty(String(rec.order_qty ?? 0));
    setSupplierCode(rec.supplier?.supplier_code ?? '');
    setReason('');
    setError(null);
    setShowErrors(false);
  }, [open, rec]);

  const supplierOptions = useMemo(() => {
    if (!rec) return [];
    // De-dupe alternatives by code (the primary is usually also listed).
    const seen = new Set<string>();
    const opts: { value: string; label: string; description?: string; searchText?: string }[] = [];
    for (const s of rec.alternatives) {
      if (seen.has(s.supplier_code)) continue;
      seen.add(s.supplier_code);
      // The supplier's own price, in the supplier's own money. `fmtMoney` hard-codes RM,
      // so a USD 8.00 alternative was offered as "RM 8" - a quarter of the ringgit price
      // instead of roughly four times it.
      const cost = s.unit_cost !== null ? fmtSupplierCost(s.unit_cost, s.currency) : 'no cost';
      const lead = s.lead_time_days !== null ? `${s.lead_time_days}d lead` : 'lead n/a';
      opts.push({
        value: s.supplier_code,
        label: s.supplier_name,
        description: `${cost} · ${lead}${s.is_primary ? ' · proposed' : ''}`,
        searchText: `${s.supplier_name} ${s.supplier_code}`,
      });
    }
    return opts;
  }, [rec]);

  const chosenSupplier = useMemo<SupplierChoice | null>(() => {
    if (!rec) return null;
    return rec.alternatives.find((s) => s.supplier_code === supplierCode) ?? rec.supplier;
  }, [rec, supplierCode]);

  const qtyNum = Number(qty);
  const qtyError = numberFieldError(qty, { min: 1, integer: true });
  const qtyValid = !qtyError;

  /**
   * Live recompute preview off the chosen supplier.
   *
   * It sits beside `rec.cash_impact`, which is in the BASE currency, so the preview has to
   * be comparable to it: quantity times the restated price when the choice carries one.
   * When the currency has no rate on file there is nothing to restate, and the honest
   * answer is the amount in the supplier's own money, labelled as that money - never a
   * foreign total wearing an RM.
   */
  const previewCash = useMemo(() => {
    if (!chosenSupplier || !qtyValid) return null;
    if (chosenSupplier.unit_cost_base != null) {
      return { text: fmtMoney(qtyNum * chosenSupplier.unit_cost_base), inBase: true };
    }
    if (chosenSupplier.unit_cost == null) return null;
    return {
      text: fmtMoneyIn(qtyNum * chosenSupplier.unit_cost, chosenSupplier.currency),
      inBase: isBaseCurrency(chosenSupplier.currency),
    };
  }, [chosenSupplier, qtyNum, qtyValid]);
  const supplierChanged = !!rec && chosenSupplier?.supplier_code !== rec.supplier?.supplier_code;
  const qtyChanged = !!rec && qtyValid && qtyNum !== (rec.order_qty ?? 0);

  const submit = () => {
    setError(null);
    if (!qtyValid) {
      setShowErrors(true);
      return;
    }
    if (!reason.trim()) {
      setError('A reason is required so this adjustment can feed policy suggestions.');
      return;
    }
    onSubmit({
      override_qty: qtyNum,
      override_supplier_code:
        rec && chosenSupplier?.supplier_code !== rec.supplier?.supplier_code
          ? chosenSupplier?.supplier_code ?? null
          : null,
      reason_text: reason.trim(),
    });
  };

  if (!rec) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Adjust {rec.sku}</DialogTitle>
          <DialogDescription>
            Override the buy quantity or switch supplier. This stages the change - no PO is
            drafted until you Confirm decisions.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-5">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <div className="rounded-lg border border-border bg-muted/30 p-3 text-sm">
            {distinctName(rec.sku, rec.product_name) ? (
              <div className="font-medium">{rec.product_name}</div>
            ) : null}
            <div className="mt-0.5 text-xs text-muted-foreground">
              Proposed: {fmtInt(rec.order_qty)} units · {rec.supplier?.supplier_name ?? EM_DASH} ·{' '}
              {fmtMoney(rec.cash_impact)}
            </div>
          </div>

          <div>
            <Label className="mb-1 block">Buy quantity</Label>
            <NumberInput
              min={1}
              value={qty}
              error={showErrors ? qtyError : undefined}
              onChange={(e) => setQty(e.target.value)}
              className="w-40 tabular-nums"
            />
          </div>

          <div>
            <Label className="mb-1 block">Supplier</Label>
            <SearchableSelect
              value={supplierCode}
              onChange={setSupplierCode}
              options={supplierOptions}
              placeholder="Select a supplier"
              emptyMessage="No alternative suppliers on file."
            />
            <p className="mt-1 text-2xs text-muted-foreground">
              Switch to a ranked alternative to recompute lead time and cash impact.
            </p>
          </div>

          {/* Live recompute preview (M4-D7). */}
          {chosenSupplier ? (
            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 text-xs font-medium text-muted-foreground">Recomputed off {chosenSupplier.supplier_name}</div>
              <div className="grid grid-cols-3 gap-2 text-sm">
                <PreviewCell
                  label="Buy qty"
                  from={fmtInt(rec.order_qty)}
                  to={fmtInt(qtyValid ? Math.round(qtyNum) : null)}
                  changed={qtyChanged}
                />
                <PreviewCell
                  label="Lead time"
                  from={rec.lead_time_days != null ? `${rec.lead_time_days}d` : EM_DASH}
                  to={chosenSupplier.lead_time_days != null ? `${chosenSupplier.lead_time_days}d` : EM_DASH}
                  changed={supplierChanged}
                />
                <PreviewCell
                  label="Cash impact"
                  from={fmtMoney(rec.cash_impact)}
                  to={previewCash?.text ?? EM_DASH}
                  changed={qtyChanged || supplierChanged}
                />
              </div>
              {chosenSupplier.unit_cost == null ? (
                <p className="mt-2 text-2xs text-scm-overstock">
                  This supplier has no cost on file - the buy can&apos;t be cash-ranked until a cost is added.
                </p>
              ) : null}
            </div>
          ) : null}

          <div>
            <Label className="mb-1 block">
              Reason <span className="text-destructive">*</span>
            </Label>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why are you adjusting this? e.g. supplier MOQ changed, cheaper alternative available…"
              rows={3}
            />
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={isSubmitting}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            Save adjustment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PreviewCell({
  label,
  from,
  to,
  changed,
}: {
  label: string;
  from: string;
  to: string;
  changed: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-2xs text-muted-foreground">{label}</div>
      {changed ? (
        <div className="flex items-center gap-1 text-sm">
          <span className="text-muted-foreground line-through">{from}</span>
          <ArrowRight className="size-3 shrink-0 text-muted-foreground" />
          <span className="font-medium tabular-nums text-scm-incoming">{to}</span>
        </div>
      ) : (
        <div className="text-sm font-medium tabular-nums">{to}</div>
      )}
    </div>
  );
}
