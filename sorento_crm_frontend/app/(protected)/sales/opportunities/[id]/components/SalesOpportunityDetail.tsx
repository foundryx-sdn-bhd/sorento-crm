'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { FileText, ListOrdered, LoaderCircleIcon, Plus, SquarePen } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardHeading, CardTitle } from '@/components/ui/card';
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
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DetailActions from '@/components/common/DetailActions';
import { RecordNavigation } from '@/components/common/RecordNavigation';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatCurrency, formatDate, formatDateTimeInMalaysia } from '@/lib/helpers';
import { getSalesOpportunitySalesOrderOptions } from '../../services/salesOpportunityService';
import {
  useSalesOpportunities,
  useSalesOpportunity,
  useSalesOpportunityAgentOptions,
  useSalesOpportunityMeta,
  useSaveSalesOpportunity,
} from '../../hooks/useSalesOpportunities';
import { useSalesOpportunityActions } from '../../actions';
import {
  OpportunityLineRow,
  lineHasNumberError,
  nextLineKey,
  sumLineAmounts,
  type LineDraft,
} from '../../components/OpportunityLineRow';
import {
  BLOCKED_VALUE,
  PROSPECT_PREFIX,
  fetchCustomerOrProspectOptions,
  customerTriggerLabel,
} from '../../lib/customerOrProspect';
import type { SalesOpportunityTransition } from '../../types/salesOpportunity.types';
import { codeWithName } from '@/lib/product-display';

/**
 * `/sales/opportunities/{id}` detail page (UAC S2-11, S2-12, S2-13; plan section 16; Phase 3
 * fix B2; fix round 2 F5-F7).
 *
 * Header card (title, stage `Badge`, `opportunity_no`/Created/Updated) then a `Tabs` strip -
 * `Details` (the Opportunity card: customer or prospect, amount, close date, agent, and,
 * once set, the linked sales order or the lost reason) and `Products`. THE SAME TABS IN VIEW
 * AND EDIT - editing swaps a value for an input in place, nothing moves. Stage moves
 * (`available_transitions`) live in the gear, before Delete; Lost and Won open a small dialog
 * (a required reason, an optional sales order), anything else runs on click. Every section
 * renders with an empty state.
 */
export default function SalesOpportunityDetail({ id }: { id: string }) {
  const router = useRouter();
  const canEdit = useHasPermission('sales.opportunities.edit');
  const { data: opportunity, isLoading, isError } = useSalesOpportunity(id);
  const { data: meta } = useSalesOpportunityMeta();
  const { data: agentOptions } = useSalesOpportunityAgentOptions();
  const save = useSaveSalesOpportunity();

  // Prev/next over the default (unfiltered) list, same simplification
  // SalesTeamDetail's own pager makes - capped at 200 rather than truly unbounded.
  const { data: list } = useSalesOpportunities({ pageIndex: 0, pageSize: 200, sorting: [], searchQuery: '' });
  const rows = list?.data ?? [];
  const index = rows.findIndex((r) => r.id === id);

  const [tab, setTab] = useState('details');

  // The lost/won dialog a stage move can open (F6). Any other transition runs straight away.
  const [pending, setPending] = useState<SalesOpportunityTransition | null>(null);
  const [lostReason, setLostReason] = useState('');
  const [salesOrderId, setSalesOrderId] = useState('');

  const [isEditing, setIsEditing] = useState(false);
  const [title, setTitle] = useState('');
  const [customerOrProspect, setCustomerOrProspect] = useState('');
  const [salesAgentId, setSalesAgentId] = useState('');
  // Should-fix 2 (Phase 3 fix2): the picker starts seeded from the record, so a save
  // with the field untouched must not resend the same id as though it had changed -
  // only an actual pick (SearchableSelect's onChange) sets this.
  const [salesAgentTouched, setSalesAgentTouched] = useState(false);
  const [expectedAmount, setExpectedAmount] = useState('');
  // F7: while untouched the field tracks the live line total; typing into it (or
  // starting an edit session where the stored amount already disagrees with the
  // stored lines) makes the typed value win instead.
  const [amountTouched, setAmountTouched] = useState(false);
  const [expectedCloseDate, setExpectedCloseDate] = useState('');
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [showErrors, setShowErrors] = useState(false);

  useEffect(() => {
    setPending(null);
    setLostReason('');
    setSalesOrderId('');
    setIsEditing(false);
  }, [id]);

  const transitions = canEdit ? (opportunity?.available_transitions ?? []) : [];

  const handleTransition = (transition: SalesOpportunityTransition) => {
    if (transition.key === 'lost' || transition.key === 'won') {
      setPending(transition);
      setLostReason('');
      setSalesOrderId('');
      return;
    }
    // Any other move (Qualify) is not a decision that needs a form - it runs on click.
    save.mutateAsync({ id, status_id: transition.to_status_id }).catch(() => {});
  };

  const { actions, pending: pendingDelete } = useSalesOpportunityActions(opportunity, {
    onDeleted: () => router.push('/sales/opportunities'),
    transitions,
    onTransition: handleTransition,
  });

  const lostReasonOptions: SearchableSelectOption[] = (meta?.lost_reasons ?? []).map((r) => ({
    value: r.value,
    label: r.label,
  }));
  const agentSelectOptions: SearchableSelectOption[] = (agentOptions ?? []).map((a) => ({
    value: a.id,
    label: a.label,
  }));

  // Reviewer should-fix 3: a live search over every sales order, not a fetch-once list -
  // the same reasoning every other id picker in this app follows for a catalog that can
  // outgrow one page (LESSONS 70).
  const fetchSalesOrderOptions = async (q: string): Promise<SearchableSelectOption[]> => {
    const rows = await getSalesOpportunitySalesOrderOptions(id, q);
    return rows.map((r) => ({
      value: r.id,
      label: r.customer_name ? `${r.so_number} - ${r.customer_name}` : r.so_number,
    }));
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
    );
  }

  if (isError || !opportunity) {
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <div className="text-sm font-semibold">Sales opportunity not found</div>
        <p className="max-w-md text-sm text-muted-foreground">
          This opportunity does not exist, or it was deleted after this link was made.
        </p>
      </Card>
    );
  }

  const beginEdit = () => {
    setTitle(opportunity.title);
    setCustomerOrProspect(
      opportunity.customer_id ?? (opportunity.prospect_name ? `${PROSPECT_PREFIX}${opportunity.prospect_name}` : ''),
    );
    setSalesAgentId(opportunity.sales_agent_id ?? '');
    setSalesAgentTouched(false);
    setExpectedAmount(String(opportunity.expected_amount ?? ''));
    setExpectedCloseDate(opportunity.expected_close_date ?? '');
    const seededLines = (opportunity.lines ?? []).map((line) => ({
      key: nextLineKey(),
      productId: line.product_id,
      qty: String(line.qty),
      unitPrice: line.unit_price ?? '',
      productLabel: codeWithName(line.product_code, line.product_name),
    }));
    setLines(seededLines);
    // F7: only start "touched" (the typed value wins) when the stored amount does not
    // already match what the stored lines add up to - otherwise editing a line for a
    // record that was never customised would silently freeze the old total in place.
    const storedAmount = Number(opportunity.expected_amount);
    const storedSum = sumLineAmounts(seededLines);
    setAmountTouched(!(Number.isFinite(storedAmount) && storedAmount.toFixed(2) === storedSum.toFixed(2)));
    setShowErrors(false);
    setIsEditing(true);
  };

  const addLine = () =>
    setLines((prev) => [...prev, { key: nextLineKey(), productId: '', qty: '1', unitPrice: '' }]);
  const removeLine = (key: string) => setLines((prev) => prev.filter((l) => l.key !== key));
  const updateLine = (key: string, patch: Partial<LineDraft>) =>
    setLines((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  // S1: untouched always means the lines' sum, 0.00 included - falling back to whatever
  // `expectedAmount` still holds (the seeded stored value) once the sum drops to zero is
  // exactly the stale-total bug this replaced.
  const linesSum = sumLineAmounts(lines);
  const effectiveAmount = amountTouched ? expectedAmount : linesSum.toFixed(2);

  const customerLabel = opportunity.customer_name ?? opportunity.prospect_name ?? 'No customer yet';
  const existingLines = opportunity.lines ?? [];
  const existingLinesTotal = existingLines.reduce((sum, l) => sum + (Number(l.line_amount) || 0), 0);
  const isProspect = customerOrProspect.startsWith(PROSPECT_PREFIX);
  const canSaveEdit =
    customerOrProspect !== BLOCKED_VALUE &&
    title.trim().length > 0 &&
    !!expectedCloseDate &&
    !save.isPending;

  const amountError = numberFieldError(effectiveAmount, { min: 0 });

  const handleSaveEdit = async () => {
    if (!canSaveEdit) return;
    // Only the final values are checked: a bad amount or product row stays as typed.
    if (amountError || lines.some(lineHasNumberError)) {
      setShowErrors(true);
      return;
    }
    try {
      await save.mutateAsync({
        id,
        title: title.trim(),
        expected_amount: effectiveAmount,
        expected_close_date: expectedCloseDate,
        // Should-fix 2 (Phase 3 fix2): only sent when the picker was actually touched -
        // resending the seeded value every save reads as a change to `update_opportunity`
        // (it re-validates the agent) for a field nothing happened to.
        ...(salesAgentTouched ? { sales_agent_id: salesAgentId || null } : {}),
        // B-new (Phase 3 fix2): the OTHER key goes along as an explicit null, not
        // omitted - `update_opportunity` fills an absent key from the row already on the
        // opportunity, so switching customer -> prospect (or back) with the other key
        // merely missing re-sent the old value and 422'd CUSTOMER_AND_PROSPECT_TOGETHER.
        ...(isProspect
          ? { prospect_name: customerOrProspect.slice(PROSPECT_PREFIX.length), customer_id: null }
          : { customer_id: customerOrProspect, prospect_name: null }),
        lines: lines
          .filter((l) => l.productId)
          .map((l) => ({
            product_id: l.productId,
            qty: Number(l.qty) || 0,
            unit_price: l.unitPrice ? l.unitPrice : null,
          })),
      });
      setIsEditing(false);
    } catch {
      // The hook toasted the reason; the session stays open so nothing typed is lost.
    }
  };

  const confirmMove = async () => {
    if (!pending) return;
    if (pending.key === 'lost' && !lostReason) return;
    try {
      await save.mutateAsync({
        id,
        status_id: pending.to_status_id,
        ...(pending.key === 'lost' ? { lost_reason: lostReason } : {}),
        ...(pending.key === 'won' && salesOrderId ? { sales_order_id: salesOrderId } : {}),
      });
      setPending(null);
      setLostReason('');
      setSalesOrderId('');
    } catch {
      // The hook toasted the reason; stay on the pending move so nothing typed is lost.
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              {isEditing ? (
                <div className="flex flex-col gap-1">
                  <Label htmlFor="opportunity-edit-title" className="text-xs text-muted-foreground">
                    Title
                  </Label>
                  <Input
                    id="opportunity-edit-title"
                    value={title}
                    maxLength={200}
                    onChange={(e) => setTitle(e.target.value)}
                    className="h-8 w-64 max-w-full"
                  />
                </div>
              ) : (
                <CardTitle className="truncate text-lg" title={opportunity.title}>
                  {opportunity.title}
                </CardTitle>
              )}
              <Badge status={opportunity.stage_key} appearance="light">
                {opportunity.stage_label}
              </Badge>
            </div>
            {isEditing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => setIsEditing(false)} disabled={save.isPending}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSaveEdit} disabled={!canSaveEdit}>
                  {save.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
                  Save
                </Button>
              </div>
            ) : (
              <DetailActions
                pagerNode={
                  <RecordNavigation
                    index={index >= 0 ? index + 1 : null}
                    total={rows.length}
                    hasPrevious={index > 0}
                    hasNext={index >= 0 && index < rows.length - 1}
                    onPrevious={() => router.push(`/sales/opportunities/${rows[index - 1].id}`)}
                    onNext={() => router.push(`/sales/opportunities/${rows[index + 1].id}`)}
                    ariaLabel="sales opportunity"
                  />
                }
                actions={actions}
                pendingAction={pendingDelete}
                primary={
                  canEdit && opportunity.outcome === 'open' ? (
                    <Button variant="primary" size="sm" className="gap-1.5" onClick={beginEdit}>
                      <SquarePen className="size-4" />
                      Edit
                    </Button>
                  ) : undefined
                }
              />
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>{opportunity.opportunity_no}</span>
            {opportunity.created_at ? (
              <span>Created {formatDateTimeInMalaysia(opportunity.created_at)}</span>
            ) : null}
            {opportunity.updated_at ? (
              <span>Updated {formatDateTimeInMalaysia(opportunity.updated_at)}</span>
            ) : null}
          </div>
        </CardHeader>
      </Card>

      {/* The tab set is the SAME in view and in edit - editing swaps a value for an input
          in place, nothing moves between tabs. */}
      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="details">
            <FileText />
            <span>Details</span>
          </TabsTrigger>
          <TabsTrigger value="products">
            <ListOrdered />
            <span>Products</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="details" className="mt-0 space-y-4 focus-visible:outline-none">
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Opportunity</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Opportunity" className="flex flex-col gap-4 p-4">
              {isEditing ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="opportunity-edit-customer">Customer or prospect</Label>
                    <SearchableSelect
                      id="opportunity-edit-customer"
                      aria-label="Customer or prospect"
                      value={customerOrProspect}
                      onChange={setCustomerOrProspect}
                      fetchOptions={fetchCustomerOrProspectOptions}
                      renderTriggerLabel={customerTriggerLabel}
                      selectedOption={
                        opportunity.customer_id
                          ? { value: opportunity.customer_id, label: customerLabel }
                          : opportunity.prospect_name
                            ? { value: `${PROSPECT_PREFIX}${opportunity.prospect_name}`, label: customerLabel }
                            : undefined
                      }
                      placeholder="Search customers..."
                      wrapOptions
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="opportunity-edit-amount">Expected amount</Label>
                    <NumberInput
                      id="opportunity-edit-amount"
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
                    <Label htmlFor="opportunity-edit-close-date">Expected close date</Label>
                    <Input
                      id="opportunity-edit-close-date"
                      type="date"
                      value={expectedCloseDate}
                      onChange={(e) => setExpectedCloseDate(e.target.value)}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="opportunity-edit-agent">Agent</Label>
                    <SearchableSelect
                      id="opportunity-edit-agent"
                      aria-label="Agent"
                      value={salesAgentId}
                      onChange={(value) => {
                        setSalesAgentId(value);
                        setSalesAgentTouched(true);
                      }}
                      options={agentSelectOptions}
                      placeholder="Unassigned"
                      clearable
                      wrapOptions
                    />
                  </div>
                </div>
              ) : (
                <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <dt className="text-xs text-muted-foreground">Customer or prospect</dt>
                    <dd className="truncate text-sm" title={customerLabel}>
                      {customerLabel}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Expected amount</dt>
                    <dd className="text-sm">{formatCurrency(opportunity.expected_amount)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Expected close date</dt>
                    <dd className="text-sm">{formatDate(opportunity.expected_close_date)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Agent</dt>
                    <dd className="truncate text-sm" title={opportunity.sales_agent_label ?? undefined}>
                      {opportunity.sales_agent_label ?? 'Unassigned'}
                    </dd>
                  </div>
                </dl>
              )}
              {/* Not editable, either view: both are facts a stage move set, not a value
                  somebody types - they used to sit in their own "Stage" card. */}
              {opportunity.sales_order_no || opportunity.lost_reason_label ? (
                <div className="grid grid-cols-1 gap-3 border-t border-border pt-3 sm:grid-cols-2">
                  {opportunity.sales_order_no ? (
                    <div>
                      <dt className="text-xs text-muted-foreground">Sales order</dt>
                      <dd className="text-sm font-medium">{opportunity.sales_order_no}</dd>
                    </div>
                  ) : null}
                  {opportunity.lost_reason_label ? (
                    <div>
                      <dt className="text-xs text-muted-foreground">Lost reason</dt>
                      <dd className="text-sm font-medium">{opportunity.lost_reason_label}</dd>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>
          </Card>
        </TabsContent>

        <TabsContent value="products" className="mt-0 space-y-4 focus-visible:outline-none">
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Products</CardTitle>
              </CardHeading>
              {isEditing ? (
                <Button type="button" variant="outline" size="sm" onClick={addLine}>
                  <Plus className="size-4" />
                  Add product
                </Button>
              ) : null}
            </CardHeader>
            <section aria-label="Products" className="flex flex-col gap-3 p-4">
              {isEditing ? (
                lines.length === 0 ? (
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
                )
              ) : existingLines.length === 0 ? (
                <p className="text-sm text-muted-foreground">No products yet</p>
              ) : (
                <ul className="flex flex-col divide-y rounded-lg border text-sm">
                  <li className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
                    <span className="min-w-0 flex-1">Product</span>
                    <span className="w-10 shrink-0 text-end">Qty</span>
                    <span className="w-20 shrink-0 text-end">Unit price</span>
                    <span className="w-20 shrink-0 text-end">Amount</span>
                  </li>
                  {existingLines.map((line) => (
                    <li key={line.id} className="flex items-center gap-2 px-3 py-2">
                      <span className="min-w-0 flex-1 truncate" title={codeWithName(line.product_code, line.product_name)}>
                        {codeWithName(line.product_code, line.product_name)}
                      </span>
                      <span className="w-10 shrink-0 text-end text-muted-foreground">{line.qty}</span>
                      <span className="w-20 shrink-0 text-end text-muted-foreground">
                        {formatCurrency(line.unit_price)}
                      </span>
                      <span className="w-20 shrink-0 text-end font-medium">
                        {formatCurrency(line.line_amount)}
                      </span>
                    </li>
                  ))}
                  <li className="flex items-center gap-2 px-3 py-2 font-semibold">
                    <span className="min-w-0 flex-1">Total</span>
                    <span className="w-10 shrink-0" />
                    <span className="w-20 shrink-0" />
                    <span className="w-20 shrink-0 text-end">{formatCurrency(existingLinesTotal)}</span>
                  </li>
                </ul>
              )}
            </section>
          </Card>
        </TabsContent>
      </Tabs>

      {/* The lost/won dialog a stage move can open (F6) - a form, not a delete confirm, so
          it does not carry the no-confirm-dialog rule. */}
      <Dialog
        open={!!pending}
        onOpenChange={(open) => {
          if (open) return;
          setPending(null);
          setLostReason('');
          setSalesOrderId('');
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pending?.label}</DialogTitle>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            {pending?.key === 'lost' ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="opportunity-lost-reason">Lost reason</Label>
                <SearchableSelect
                  id="opportunity-lost-reason"
                  aria-label="Lost reason"
                  value={lostReason}
                  onChange={setLostReason}
                  options={lostReasonOptions}
                  placeholder="Pick a reason"
                  wrapOptions
                />
              </div>
            ) : null}
            {pending?.key === 'won' ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="opportunity-sales-order">Sales order (optional)</Label>
                <SearchableSelect
                  id="opportunity-sales-order"
                  aria-label="Sales order"
                  value={salesOrderId}
                  onChange={setSalesOrderId}
                  fetchOptions={fetchSalesOrderOptions}
                  placeholder="Search sales orders..."
                  clearable
                  wrapOptions
                />
              </div>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={confirmMove}
              disabled={(pending?.key === 'lost' && !lostReason) || save.isPending}
            >
              {save.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
