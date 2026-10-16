'use client';

import { useEffect, useState } from 'react';
import { LoaderCircleIcon, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { toast } from '@/lib/toast';
import { createPortalSalesOpportunity } from '../../lib/sales-opportunity-service';
import {
  BLOCKED_VALUE,
  NO_CUSTOMERS_MESSAGE,
  PROSPECT_PREFIX,
  fetchCustomerOrProspectOptions,
  customerTriggerLabel,
} from '../lib/customerOrProspect';
import {
  PortalOpportunityLineRow,
  linesHaveNumberErrors,
  nextLineKey,
  sumLineDraftAmounts,
  type LineDraft,
} from './PortalOpportunityLineRow';

/**
 * The portal Sales Opportunity CREATE form (UAC S2-10, S2-15, S2-16; plan 3.5, N10, N11; F4/F7).
 *
 * The same "Customer or prospect" search and Products table contract as the CRM modal, over
 * the portal's own service and the system `SearchableSelect` (F4) - no agent field anywhere,
 * the agent comes from the token, never the form.
 *
 * Edit lives on `SalesOpportunityPortalDetail` itself, in place (Phase 3 fix2 should-fix 5) -
 * this component is create-only; an earlier round routed Edit through a second instance of it
 * with `initial`/`onSaved` props, which swapped the whole read view for the create form
 * instead of turning its own fields into inputs. Reverted.
 */
export default function SalesOpportunityPortalForm({
  onCreated,
}: {
  /** Called with the new opportunity's id once the server confirms it - the caller (the
   *  `new` page) owns navigating away, so this component stays router-agnostic and testable
   *  without mocking `next/navigation`. */
  onCreated?: (id: string) => void;
}) {
  const [customerOrProspect, setCustomerOrProspect] = useState('');
  // S4: an async SearchableSelect only knows about whatever page it last fetched - without
  // keeping the picked option here, reopening the field after a different search (which
  // doesn't happen to include it) shows blank instead of what was just chosen.
  const [customerSelectedOption, setCustomerSelectedOption] = useState<
    SearchableSelectOption | undefined
  >();
  const [title, setTitle] = useState('');
  const [expectedAmount, setExpectedAmount] = useState('');
  const [amountTouched, setAmountTouched] = useState(false);
  const [expectedCloseDate, setExpectedCloseDate] = useState('');
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [saving, setSaving] = useState(false);
  const [showNumberErrors, setShowNumberErrors] = useState(false);

  // F7: the expected amount tracks the lines total until the salesperson types one in.
  useEffect(() => {
    if (amountTouched) return;
    const sum = sumLineDraftAmounts(lines);
    setExpectedAmount(sum > 0 ? sum.toFixed(2) : '');
  }, [lines, amountTouched]);

  const isProspect = customerOrProspect.startsWith(PROSPECT_PREFIX);

  const addLine = () =>
    setLines((prev) => [
      ...prev,
      { key: nextLineKey(), productId: '', productLabel: '', qty: '1', unitPrice: '' },
    ]);
  const removeLine = (key: string) => setLines((prev) => prev.filter((l) => l.key !== key));
  const updateLine = (key: string, patch: Partial<LineDraft>) =>
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const canSave =
    customerOrProspect !== BLOCKED_VALUE &&
    title.trim().length > 0 &&
    !!expectedCloseDate &&
    !saving;

  const amountError = numberFieldError(expectedAmount, { min: 0 });

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    if (amountError || linesHaveNumberErrors(lines)) {
      setShowNumberErrors(true);
      return;
    }
    setSaving(true);
    const payload = {
      title: title.trim(),
      expected_amount: expectedAmount,
      expected_close_date: expectedCloseDate,
      ...(isProspect
        ? { prospect_name: customerOrProspect.slice(PROSPECT_PREFIX.length) }
        : { customer_id: customerOrProspect }),
      lines: lines
        .filter((l) => l.productId)
        .map((l) => ({
          product_id: l.productId,
          qty: Number(l.qty),
          unit_price: l.unitPrice === '' ? null : Number(l.unitPrice),
        })),
    };
    try {
      const created = await createPortalSalesOpportunity(payload);
      toast.success('Opportunity logged');
      onCreated?.(created.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save the opportunity.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="mx-auto flex w-full max-w-lg flex-col gap-4 px-3 py-4">
      <Card>
        <CardHeader>
          <CardTitle>Log a sales opportunity</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="customer-or-prospect">Customer or prospect</Label>
            <SearchableSelect
              id="customer-or-prospect"
              aria-label="Customer or prospect"
              value={customerOrProspect}
              onChange={setCustomerOrProspect}
              onOptionChange={(option) => setCustomerSelectedOption(option ?? undefined)}
              selectedOption={customerSelectedOption}
              fetchOptions={fetchCustomerOrProspectOptions}
              renderTriggerLabel={customerTriggerLabel}
              placeholder="Search customer or prospect..."
              emptyMessage={NO_CUSTOMERS_MESSAGE}
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
              value={expectedAmount}
              onChange={(e) => {
                setExpectedAmount(e.target.value);
                setAmountTouched(true);
              }}
              error={showNumberErrors ? amountError : undefined}
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Products</CardTitle>
          <Button type="button" variant="outline" size="sm" onClick={addLine}>
            <Plus className="size-4" />
            Add product
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {lines.length === 0 ? (
            <p className="text-sm text-muted-foreground">No products yet</p>
          ) : (
            lines.map((line) => (
              <PortalOpportunityLineRow
                key={line.key}
                line={line}
                onChange={(patch) => updateLine(line.key, patch)}
                onRemove={() => removeLine(line.key)}
                showErrors={showNumberErrors}
              />
            ))
          )}
        </CardContent>
      </Card>

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={!canSave}>
          {saving ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
          Save
        </Button>
      </div>
    </form>
  );
}
