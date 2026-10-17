'use client';

import { useEffect, useMemo, useState } from 'react';
import { LoaderCircleIcon, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { useSaveSalesOpportunity } from '../hooks/useSalesOpportunities';
import { numberFieldError } from '@/lib/number-field';
import {
  OpportunityLineRow,
  lineHasNumberError,
  nextLineKey,
  sumLineAmounts,
  type LineDraft,
} from './OpportunityLineRow';
import {
  BLOCKED_VALUE,
  PROSPECT_PREFIX,
  fetchCustomerOrProspectOptions,
  customerTriggerLabel,
} from '../lib/customerOrProspect';

/**
 * The Log opportunity modal (UAC S2-12, S2-15, S2-16; plan 3.4, 3.5, section 16, N10, N11).
 *
 * ONE "Customer or prospect" search (N10): typing lists matching customers, plus, when
 * nothing matches the typed name exactly, an "Add ... as a new prospect" option; a name
 * that belongs to another agent's customer shows as a disabled, explained option instead
 * (S2-15). Products are optional (N11): a product and a quantity per row, the complaint
 * form's line pattern. Create only - editing is in place on the detail page.
 */
export default function SalesOpportunityModal({
  open,
  onOpenChange,
  presetCustomerId,
  presetCustomerLabel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Preset from a customer's own page (J8); the field still shows the picked customer. */
  presetCustomerId?: string;
  /** Reviewer should-fix 7: the async SearchableSelect has no page loaded that could
   *  contain this id, so without the name here the field reads as empty despite a real
   *  preset - the caller already has it (the customer page it was opened from). */
  presetCustomerLabel?: string;
}) {
  const save = useSaveSalesOpportunity();

  const [customerOrProspect, setCustomerOrProspect] = useState('');
  const [title, setTitle] = useState('');
  const [expectedAmount, setExpectedAmount] = useState('');
  // F7: Expected amount defaults to the live sum of the product lines until the field is
  // actually typed into - once it is, the typed value wins over any further line change.
  const [amountTouched, setAmountTouched] = useState(false);
  const [expectedCloseDate, setExpectedCloseDate] = useState('');
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [showErrors, setShowErrors] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCustomerOrProspect(presetCustomerId ?? '');
    setTitle('');
    setExpectedAmount('');
    setAmountTouched(false);
    setExpectedCloseDate('');
    setLines([]);
    setShowErrors(false);
  }, [open, presetCustomerId]);

  const customerOptions = useMemo(
    () => [] as SearchableSelectOption[],
    [],
  );

  const addLine = () =>
    setLines((prev) => [...prev, { key: nextLineKey(), productId: '', qty: '1', unitPrice: '' }]);
  const removeLine = (key: string) => setLines((prev) => prev.filter((l) => l.key !== key));
  const updateLine = (key: string, patch: Partial<LineDraft>) =>
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  // S1: untouched always means the lines' sum, 0.00 included - never a fallback to
  // whatever `expectedAmount` still holds once the sum drops to zero.
  const linesSum = sumLineAmounts(lines);
  const effectiveAmount = amountTouched ? expectedAmount : linesSum.toFixed(2);

  const isProspect = customerOrProspect.startsWith(PROSPECT_PREFIX);
  // Customer-or-prospect validity (including the exact-name-match rules, S2-15) stays
  // server-authoritative - the hook's onError toasts a 422 the same way any other save
  // failure does, so it is not duplicated here. Blocked is the one client-side rule
  // clearly enforceable without a round trip: it is never a submittable choice at all.
  const canSave =
    customerOrProspect !== BLOCKED_VALUE &&
    title.trim().length > 0 &&
    !!expectedCloseDate &&
    !save.isPending;

  const amountError = numberFieldError(effectiveAmount, { min: 0 });

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    // Only the final values are checked: a bad amount or product row stays on screen as typed.
    if (amountError || lines.some(lineHasNumberError)) {
      setShowErrors(true);
      return;
    }
    const payload = {
      title: title.trim(),
      expected_amount: effectiveAmount,
      expected_close_date: expectedCloseDate,
      ...(isProspect
        ? { prospect_name: customerOrProspect.slice(PROSPECT_PREFIX.length) }
        : { customer_id: customerOrProspect }),
      lines: lines
        .filter((l) => l.productId)
        .map((l) => ({
          product_id: l.productId,
          qty: Number(l.qty) || 0,
          unit_price: l.unitPrice ? l.unitPrice : null,
        })),
    };
    try {
      await save.mutateAsync(payload);
      onOpenChange(false);
    } catch {
      // The hook toasted the reason; the modal stays open with what was typed.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Log opportunity</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogBody className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="opportunity-customer">Customer or prospect</Label>
              <SearchableSelect
                id="opportunity-customer"
                aria-label="Customer or prospect"
                value={customerOrProspect}
                onChange={setCustomerOrProspect}
                options={customerOptions}
                fetchOptions={fetchCustomerOrProspectOptions}
                renderTriggerLabel={customerTriggerLabel}
                selectedOption={
                  presetCustomerId && presetCustomerLabel
                    ? { value: presetCustomerId, label: presetCustomerLabel }
                    : undefined
                }
                placeholder="Search customers..."
                wrapOptions
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="opportunity-title">Title</Label>
              <Input
                id="opportunity-title"
                value={title}
                maxLength={200}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="opportunity-amount">Expected amount</Label>
              <NumberInput
                id="opportunity-amount"
                min="0"
                step="0.01"
                value={effectiveAmount}
                error={showErrors ? amountError : undefined}
                onChange={(e) => {
                  setExpectedAmount(e.target.value);
                  setAmountTouched(true);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="opportunity-close-date">Expected close date</Label>
              <Input
                id="opportunity-close-date"
                type="date"
                value={expectedCloseDate}
                onChange={(e) => setExpectedCloseDate(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Label>Products</Label>
                <Button type="button" variant="outline" size="sm" onClick={addLine}>
                  <Plus className="size-3.5" />
                  Add product
                </Button>
              </div>
              {lines.length === 0 ? (
                <p className="text-sm text-muted-foreground">No products yet</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {lines.map((line) => (
                    <OpportunityLineRow
                      key={line.key}
                      line={line}
                      onChange={(patch) => updateLine(line.key, patch)}
                      onRemove={() => removeLine(line.key)}
                      showErrors={showErrors}
                    />
                  ))}
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSave}>
              {save.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
