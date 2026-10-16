'use client';

/**
 * Portal Sales Opportunity detail (UAC S2-11; F6/F7).
 *
 * F6: one CTA (Edit, while the opportunity is open) plus a gear dropdown (`DetailActionsMenu`)
 * holding the stage moves from `available_transitions` - Lost opens a small dialog for the
 * required reason, every other move runs the moment it is clicked. The old "Move stage" button
 * row is gone.
 *
 * Edit is IN PLACE (Phase 3 fix2 should-fix 5) - the SAME cards in the SAME order, title,
 * customer or prospect, amount, close date and product lines each swap for an input where
 * they stand, exactly as the CRM's own `SalesOpportunityDetail` does.
 *
 * No `useRouter` - this component is unit-tested without a Next.js router context, so
 * navigation is plain `<Link>`s throughout, same as `SalesOpportunityPortalList`.
 */
import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, LoaderCircleIcon, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import type { RecordAction } from '@/components/common/recordActions';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { toast } from '@/lib/toast';
import { formatCurrency, formatDate } from '@/lib/helpers';
import { portalHomePath } from '../../lib/portal-paths';
import {
  getPortalOpportunityMeta,
  getPortalSalesOpportunity,
  updatePortalSalesOpportunity,
  type PortalSalesOpportunity,
} from '../../lib/sales-opportunity-service';
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
import { codeWithName, distinctName } from '@/lib/product-display';

export default function SalesOpportunityPortalDetail({ id, slug }: { id: string; slug?: string }) {
  const [opportunity, setOpportunity] = useState<PortalSalesOpportunity | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [lostReasonOptions, setLostReasonOptions] = useState<SearchableSelectOption[]>([]);
  // Won and Lost are both terminal (a closed opportunity cannot be edited again) - both
  // get a confirm dialog, titled with the transition's own label; only Lost also collects
  // a reason. Every other move (Qualify, ...) runs the instant it is clicked.
  const [pendingTransition, setPendingTransition] = useState<{
    toStatusId: string;
    key: string;
    label: string;
  } | null>(null);
  const [lostReason, setLostReason] = useState('');
  const [saving, setSaving] = useState(false);

  const [isEditing, setIsEditing] = useState(false);
  const [title, setTitle] = useState('');
  const [customerOrProspect, setCustomerOrProspect] = useState('');
  // S4: an async SearchableSelect only knows about whatever page it last fetched - without
  // keeping the picked option here, reopening the field after a different search (that
  // doesn't include it) shows blank instead of what was just chosen. Seeded from the
  // opportunity's own value on `beginEdit`, then follows every fresh pick.
  const [editCustomerOption, setEditCustomerOption] = useState<SearchableSelectOption | undefined>();
  const [expectedAmount, setExpectedAmount] = useState('');
  const [showNumberErrors, setShowNumberErrors] = useState(false);
  const [amountTouched, setAmountTouched] = useState(false);
  const [expectedCloseDate, setExpectedCloseDate] = useState('');
  const [lines, setLines] = useState<LineDraft[]>([]);

  const load = () => {
    setLoading(true);
    setLoadError(false);
    getPortalSalesOpportunity(id)
      .then(setOpportunity)
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // The reason picker just falls back to an empty list on failure - Lost still works,
    // it only offers nothing to pick until a retry succeeds.
    getPortalOpportunityMeta()
      .then((meta) =>
        setLostReasonOptions(meta.lost_reasons.map((r) => ({ value: r.value, label: r.label }))),
      )
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // F7: while the salesperson has not typed an amount, it tracks the lines total.
  useEffect(() => {
    if (amountTouched) return;
    const sum = sumLineDraftAmounts(lines);
    setExpectedAmount(sum > 0 ? sum.toFixed(2) : '');
  }, [lines, amountTouched]);

  const customerLabel = opportunity?.customer_name ?? opportunity?.prospect_name ?? undefined;
  const customerSelectedOption = useMemo(() => {
    if (!opportunity || !customerLabel) return undefined;
    if (opportunity.customer_id) return { value: opportunity.customer_id, label: customerLabel };
    if (opportunity.prospect_name) {
      return { value: `${PROSPECT_PREFIX}${opportunity.prospect_name}`, label: customerLabel };
    }
    return undefined;
  }, [opportunity, customerLabel]);

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-2xl space-y-3 px-3 pt-4">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="mx-auto w-full max-w-2xl space-y-3 px-3 pt-4 text-center text-sm">
        <p className="text-destructive">Failed to load this opportunity.</p>
        <Button variant="outline" size="sm" onClick={load}>
          Retry
        </Button>
      </div>
    );
  }

  if (!opportunity) {
    return (
      <div className="mx-auto w-full max-w-2xl px-3 pt-4 text-center text-sm text-muted-foreground">
        Sales opportunity not found.
      </div>
    );
  }

  const lines_ = opportunity.lines ?? [];
  const transitions = opportunity.available_transitions ?? [];
  // N7 (Phase 3): once closed, the server rejects every field edit with 422
  // OPPORTUNITY_CLOSED - Edit has no reason to appear once that door is shut.
  const canEditFields = opportunity.outcome === 'open';
  const viewTotal = lines_.reduce((acc, line) => acc + Number(line.line_amount ?? 0), 0);

  const beginEdit = () => {
    setTitle(opportunity.title);
    setCustomerOrProspect(
      opportunity.customer_id ?? (opportunity.prospect_name ? `${PROSPECT_PREFIX}${opportunity.prospect_name}` : ''),
    );
    setEditCustomerOption(customerSelectedOption);
    setExpectedCloseDate(opportunity.expected_close_date ?? '');
    const nextLines: LineDraft[] = lines_.map((line) => ({
      key: nextLineKey(),
      productId: line.product_id,
      productLabel: codeWithName(line.product_code ?? '', line.product_name),
      qty: String(line.qty),
      // B1: the portal routes send Decimals as JSON numbers (1000, not "1000.00") -
      // normalize before comparing/displaying, or a number-vs-string comparison never
      // matches and every edit looks "touched".
      unitPrice: line.unit_price == null ? '' : Number(line.unit_price).toFixed(2),
    }));
    setLines(nextLines);
    // F7: touched only when the stored amount is not what the stored lines add up to -
    // otherwise a plain re-save would silently drop a hand-typed amount the moment any
    // line changes.
    const storedSum = sumLineDraftAmounts(nextLines).toFixed(2);
    const storedAmount =
      opportunity.expected_amount == null ? '' : Number(opportunity.expected_amount).toFixed(2);
    setAmountTouched(storedAmount !== storedSum);
    setExpectedAmount(storedAmount);
    setIsEditing(true);
    setShowNumberErrors(false);
  };

  const isProspect = customerOrProspect.startsWith(PROSPECT_PREFIX);

  const addLine = () =>
    setLines((prev) => [
      ...prev,
      { key: nextLineKey(), productId: '', productLabel: '', qty: '1', unitPrice: '' },
    ]);
  const removeLine = (key: string) => setLines((prev) => prev.filter((l) => l.key !== key));
  const updateLine = (key: string, patch: Partial<LineDraft>) =>
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const canSaveEdit =
    customerOrProspect !== BLOCKED_VALUE &&
    title.trim().length > 0 &&
    !!expectedCloseDate &&
    !saving;

  const amountError = numberFieldError(expectedAmount, { min: 0 });

  const handleSaveEdit = async () => {
    if (!canSaveEdit) return;
    if (amountError || linesHaveNumberErrors(lines)) {
      setShowNumberErrors(true);
      return;
    }
    setSaving(true);
    try {
      await updatePortalSalesOpportunity(id, {
        title: title.trim(),
        expected_amount: expectedAmount,
        expected_close_date: expectedCloseDate,
        // B-new (Phase 3 fix2): the OTHER key goes along as an explicit null - the
        // service fills an absent key from the row already on the opportunity, so
        // switching customer <-> prospect with the other key merely missing re-sent the
        // old value and 422'd CUSTOMER_AND_PROSPECT_TOGETHER.
        ...(isProspect
          ? { prospect_name: customerOrProspect.slice(PROSPECT_PREFIX.length), customer_id: null }
          : { customer_id: customerOrProspect, prospect_name: null }),
        lines: lines
          .filter((l) => l.productId)
          .map((l) => ({
            product_id: l.productId,
            qty: Number(l.qty),
            unit_price: l.unitPrice === '' ? null : Number(l.unitPrice),
          })),
      });
      toast.success('Opportunity saved');
      setIsEditing(false);
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save the opportunity.');
    } finally {
      setSaving(false);
    }
  };

  /** Returns whether the PATCH succeeded, so a confirm dialog on top of it knows whether
   *  it is safe to close itself - a failed save has to keep the typed reason, not lose it. */
  const applyStatus = async (toStatusId: string, extra?: { lost_reason: string }): Promise<boolean> => {
    setSaving(true);
    try {
      await updatePortalSalesOpportunity(id, { status_id: toStatusId, ...extra });
      toast.success('Opportunity updated');
      load();
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update the opportunity.');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const TERMINAL_KEYS = new Set(['won', 'lost']);

  const cancelPending = () => {
    setPendingTransition(null);
    setLostReason('');
  };

  const confirmPending = async () => {
    if (!pendingTransition) return;
    if (pendingTransition.key === 'lost' && !lostReason) return;
    const ok = await applyStatus(
      pendingTransition.toStatusId,
      pendingTransition.key === 'lost' ? { lost_reason: lostReason } : undefined,
    );
    // Only clears the dialog on success - a failed save keeps it open with the reason
    // still typed, so a retry doesn't have to be picked again.
    if (ok) {
      setPendingTransition(null);
      setLostReason('');
    }
  };

  // F6: a terminal move (Won, Lost) opens the confirm dialog first - Won is terminal too
  // (a closed opportunity cannot be edited again), so it gets the same dialog Lost does,
  // just without the reason field. Every other move runs the instant it is clicked. Lost
  // stays a secondary action (not destructive) - it is a stage move, same as the CRM's own
  // gear, not a delete.
  const stageActions: RecordAction[] = transitions.map((t) => ({
    key: `stage-${t.to_status_id}`,
    label: t.label,
    kind: 'secondary',
    run: () => {
      if (TERMINAL_KEYS.has(t.key)) {
        setPendingTransition({ toStatusId: t.to_status_id, key: t.key, label: t.label });
        setLostReason('');
        return;
      }
      void applyStatus(t.to_status_id);
    },
  }));

  return (
    <div className="mx-auto w-full max-w-2xl space-y-4 px-3 pb-8 pt-4">
      <Button variant="ghost" size="sm" asChild>
        <Link href={portalHomePath({ slug, type: 'sales_opportunity' })}>
          <ArrowLeft className="mr-1 size-4" /> Back
        </Link>
      </Button>

      <Card>
        <CardContent className="flex flex-col gap-2 py-4">
          <div className="flex items-center justify-between gap-2">
            {isEditing ? (
              <div className="flex flex-1 flex-col gap-1.5">
                <Label htmlFor="portal-opportunity-edit-title">Title</Label>
                <Input
                  id="portal-opportunity-edit-title"
                  aria-label="Title"
                  value={title}
                  maxLength={200}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
            ) : (
              <span className="text-sm font-semibold">{opportunity.title}</span>
            )}
            <Badge status={opportunity.stage_key} appearance="light">
              {opportunity.stage_label}
            </Badge>
          </div>
          <span className="text-xs text-muted-foreground">{opportunity.opportunity_no}</span>
          {isEditing ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="portal-opportunity-edit-customer">Customer or prospect</Label>
              <SearchableSelect
                id="portal-opportunity-edit-customer"
                aria-label="Customer or prospect"
                value={customerOrProspect}
                onChange={setCustomerOrProspect}
                onOptionChange={(option) => setEditCustomerOption(option ?? undefined)}
                fetchOptions={fetchCustomerOrProspectOptions}
                renderTriggerLabel={customerTriggerLabel}
                selectedOption={editCustomerOption}
                placeholder="Search customer or prospect..."
                emptyMessage={NO_CUSTOMERS_MESSAGE}
                wrapOptions
              />
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">{customerLabel ?? '-'}</span>
          )}
          {isEditing ? (
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="portal-opportunity-edit-amount">Expected amount</Label>
                <NumberInput
                  id="portal-opportunity-edit-amount"
                  aria-label="Expected amount"
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
                <Label htmlFor="portal-opportunity-edit-close-date">Expected close date</Label>
                <Input
                  id="portal-opportunity-edit-close-date"
                  aria-label="Expected close date"
                  type="date"
                  value={expectedCloseDate}
                  onChange={(e) => setExpectedCloseDate(e.target.value)}
                />
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between text-sm">
              <span>{formatCurrency(opportunity.expected_amount)}</span>
              <span>{formatDate(opportunity.expected_close_date)}</span>
            </div>
          )}
          {/* Browser pass defect 1: the detail never surfaced WHY a Lost opportunity was
              lost, once it already was one - the reason only ever showed during the
              confirm step that set it. */}
          {opportunity.outcome === 'lost' && opportunity.lost_reason_label ? (
            <p className="text-sm text-muted-foreground">
              Lost reason: <span className="font-medium text-foreground">{opportunity.lost_reason_label}</span>
            </p>
          ) : null}
          {/* F6: one CTA (Edit) plus the gear holding the stage moves. */}
          <div className="flex items-center gap-2">
            {isEditing ? (
              <>
                <Button type="button" size="sm" onClick={handleSaveEdit} disabled={!canSaveEdit}>
                  {saving ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
                  Save
                </Button>
                <Button type="button" variant="outline" size="sm" onClick={() => setIsEditing(false)} disabled={saving}>
                  Cancel
                </Button>
              </>
            ) : (
              <>
                {canEditFields ? (
                  <Button type="button" size="sm" onClick={beginEdit}>
                    Edit
                  </Button>
                ) : null}
                <DetailActionsMenu actions={stageActions} ariaLabel="Actions" disabled={saving} />
              </>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-3 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-semibold">Products</span>
            {isEditing ? (
              <Button type="button" variant="outline" size="sm" onClick={addLine}>
                <Plus className="size-4" />
                Add product
              </Button>
            ) : null}
          </div>
          {isEditing ? (
            lines.length === 0 ? (
              <p className="text-sm text-muted-foreground">No products yet</p>
            ) : (
              <div className="flex flex-col gap-2">
                {lines.map((line) => (
                  <PortalOpportunityLineRow
                    key={line.key}
                    line={line}
                    onChange={(patch) => updateLine(line.key, patch)}
                    onRemove={() => removeLine(line.key)}
                    showErrors={showNumberErrors}
                  />
                ))}
              </div>
            )
          ) : lines_.length === 0 ? (
            <p className="text-sm text-muted-foreground">No products yet</p>
          ) : (
            <>
              <ul className="flex flex-col divide-y rounded-lg border">
                {lines_.map((line) => (
                  <li
                    key={line.id ?? line.product_id}
                    className="flex flex-col gap-1 px-3 py-2 text-sm"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 flex-1 truncate">
                        <span>{line.product_code}</span>
                        {distinctName(line.product_code, line.product_name) ? (
                          <>
                            {' - '}
                            <span>{line.product_name}</span>
                          </>
                        ) : null}
                      </span>
                      <span className="text-muted-foreground">{line.qty}</span>
                    </div>
                    <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>{formatCurrency(line.unit_price)}</span>
                      <span>{formatCurrency(line.line_amount)}</span>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="flex items-center justify-between text-sm font-semibold">
                <span>Total</span>
                <span>{formatCurrency(viewTotal)}</span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={pendingTransition !== null}
        onOpenChange={(open) => {
          if (!open) cancelPending();
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{pendingTransition?.label}</DialogTitle>
          </DialogHeader>
          {pendingTransition?.key === 'lost' ? (
            <DialogBody className="flex flex-col gap-1.5">
              <Label htmlFor="portal-opportunity-lost-reason">Lost reason</Label>
              <SearchableSelect
                id="portal-opportunity-lost-reason"
                aria-label="Lost reason"
                value={lostReason}
                onChange={setLostReason}
                options={lostReasonOptions}
                placeholder="Pick a reason"
                wrapOptions
              />
            </DialogBody>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" size="sm" onClick={cancelPending}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => void confirmPending()}
              disabled={(pendingTransition?.key === 'lost' && !lostReason) || saving}
            >
              {saving ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
