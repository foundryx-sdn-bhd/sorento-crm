'use client';

/**
 * Portal sample request form - create / edit a draft / read-only view (SR-CORE).
 *
 * Copies the price tag form's structure: plain page shell, collapsible sections,
 * a line table with a product picker, a segmented control, Save Draft / Submit.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Download, Loader2, Trash2, XCircle } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { FormSection } from '@/components/common/FormSection';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { codeWithName } from '@/lib/product-display';
import {
  EMPTY_FORM,
  type DraftLine,
  FIELD_SECTION,
  nextKey,
  payloadFromState,
  stateFromDetail,
  validate,
  type FormState,
  type SectionKey,
} from '@/lib/sample-request-form';
import { cn } from '@/lib/utils';
import {
  DELIVERY_MODE_LABELS,
  GOODS_FROM_LABELS,
  SampleRequestError,
  type SampleRequestDeliveryMode,
  type SampleRequestDetail,
  type SampleRequestGoodsFrom,
  type SampleRequestProductHit,
} from '@/types/sampleRequest';
import { portalBase } from '../lib/portal-paths';
import {
  lookupDebtors,
  lookupProjects,
  type DebtorLookupItem,
  type ProjectLookupItem,
} from '../lib/portal-client';
import {
  cancelSampleRequest,
  createSampleRequest,
  downloadSampleRequestPdf,
  getSampleRequest,
  searchSampleRequestProducts,
  submitSampleRequest,
  updateSampleRequest,
} from '../lib/sample-request-service';
import { AsyncCombobox } from './AsyncCombobox';

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T | '';
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap items-center rounded-md border p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={cn(
            'rounded px-3 py-1.5 text-sm transition-colors',
            value === o.value
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-muted',
          )}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function FieldError({ message }: { message?: string }) {
  return message ? <p className="text-xs text-destructive">{message}</p> : null;
}

function ReadRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="text-sm">{children}</div>
    </div>
  );
}

const productOptions = (hits: SampleRequestProductHit[]): SearchableSelectOption[] =>
  hits.map((h) => ({
    value: h.id,
    label: codeWithName(h.product_code, h.product_name),
    description: h.brand_name ?? undefined,
    code: h.product_code,
  }));

export default function SampleRequestForm({
  slug,
  requestId,
}: {
  slug?: string;
  requestId?: string;
}) {
  const router = useRouter();
  const [request, setRequest] = useState<SampleRequestDetail | null>(null);
  const [loading, setLoading] = useState(Boolean(requestId));
  const [state, setState] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  // The search text the server last answered with nothing for; the empty message names it.
  const [missed, setMissed] = useState('');
  const [open, setOpen] = useState<Record<SectionKey, boolean>>({
    customer: true,
    lines: true,
    delivery: false,
    additional: false,
  });

  const backPath = `${portalBase(slug)}?type=sample_request`;
  const set = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) =>
      setState((prev) => ({ ...prev, [key]: value })),
    [],
  );

  useEffect(() => {
    if (!requestId) return;
    let cancelled = false;
    getSampleRequest(requestId)
      .then((d) => {
        if (cancelled) return;
        setRequest(d);
        setState(stateFromDetail(d));
      })
      .catch((e) => {
        if (!cancelled) toast.error(e instanceof Error ? e.message : 'Failed to load the sample request');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  const openSections = (keys: SectionKey[]) =>
    setOpen((prev) => ({ ...prev, ...Object.fromEntries(keys.map((k) => [k, true])) }));

  const showErrors = (found: Record<string, string>) => {
    setErrors(found);
    openSections(
      Object.keys(found)
        .map((f) => FIELD_SECTION[f])
        .filter((k): k is SectionKey => Boolean(k)),
    );
  };

  const fetchProducts = useCallback(async (q: string) => {
    const hits = await searchSampleRequestProducts(q);
    setMissed(hits.length === 0 ? q.trim() : '');
    return productOptions(hits);
  }, []);

  const addLine = (opt: SearchableSelectOption | null) => {
    if (!opt) return;
    setState((prev) => ({
      ...prev,
      lines: [
        ...prev.lines,
        {
          key: nextKey(),
          product_id: opt.value,
          label: opt.label,
          alt_product_id: null,
          alt_label: null,
          qty: '1',
          remark: '',
        },
      ],
    }));
    setErrors((prev) => ({ ...prev, lines: '' }));
  };

  const updateLine = (key: string, patch: Partial<DraftLine>) =>
    setState((prev) => ({
      ...prev,
      lines: prev.lines.map((l) => (l.key === key ? { ...l, ...patch } : l)),
    }));

  const removeLine = (key: string) =>
    setState((prev) => ({ ...prev, lines: prev.lines.filter((l) => l.key !== key) }));

  const isDraft = !request || request.status === 'draft';

  const save = async (): Promise<SampleRequestDetail> => {
    const body = payloadFromState(state);
    const saved = request
      ? await updateSampleRequest(request.id, body)
      : await createSampleRequest(body);
    setRequest(saved);
    return saved;
  };

  const explain = (e: unknown, fallback: string) => {
    const message = e instanceof Error ? e.message : fallback;
    if (e instanceof SampleRequestError && e.field) {
      showErrors({ [e.field]: message });
    }
    setServerMessage(message);
    toast.error(message);
  };

  const handleSaveDraft = async () => {
    setSaving(true);
    setServerMessage(null);
    try {
      const saved = await save();
      toast.success('Draft saved');
      if (!requestId) router.replace(`${portalBase(slug)}/sample_request/${saved.id}`);
    } catch (e) {
      explain(e, 'Failed to save the sample request');
    } finally {
      setSaving(false);
    }
  };

  const handleSubmit = async () => {
    setServerMessage(null);
    const found = validate(state);
    showErrors(found);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    try {
      const saved = await save();
      await submitSampleRequest(saved.id);
      toast.success('Sample request submitted');
      router.push(backPath);
    } catch (e) {
      explain(e, 'Failed to submit the sample request');
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!request) return;
    try {
      setRequest(await cancelSampleRequest(request.id));
      toast.success('Sample request cancelled');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to cancel the sample request');
    }
  };

  const problemCount = Object.values(errors).filter(Boolean).length;
  const deliverySummary = useMemo(
    () =>
      [
        state.immediate ? 'Immediate' : state.delivery_date,
        state.delivery_mode ? DELIVERY_MODE_LABELS[state.delivery_mode] : '',
        state.ship_to_name,
      ]
        .filter(Boolean)
        .join(' · '),
    [state.immediate, state.delivery_date, state.delivery_mode, state.ship_to_name],
  );
  const summaries: Record<SectionKey, string> = {
    customer: [state.customer_label, state.project_label || state.project_title]
      .filter(Boolean)
      .join(' · '),
    lines: [
      state.lines.length ? `${state.lines.length} line${state.lines.length === 1 ? '' : 's'}` : '',
      state.board_mounting && state.board_label ? `board: ${state.board_label}` : '',
    ]
      .filter(Boolean)
      .join(' · '),
    delivery: deliverySummary,
    additional: `Loan ${state.loan_value} ${state.loan_unit} · ${GOODS_FROM_LABELS[state.goods_from]}`,
  };

  if (loading) {
    return (
      <div className="w-full max-w-5xl mx-auto px-3 pt-4 pb-8 space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const backButton = (
    <Button variant="ghost" size="sm" onClick={() => router.push(backPath)}>
      <ArrowLeft className="size-4 mr-1" /> Back
    </Button>
  );

  // ---- Read-only view: the same sections, each input replaced by its value ----
  if (request && !isDraft) {
    const r = request;
    const canCancel = r.status === 'submitted';
    return (
      <div className="w-full max-w-5xl mx-auto px-3 pt-4 pb-8 space-y-4">
        <div className="flex items-center justify-between gap-2">
          {backButton}
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 truncate text-sm text-muted-foreground">{r.request_no}</span>
            <DetailActionsMenu ariaLabel="Sample request actions">
              <DropdownMenuItem
                onSelect={() => {
                  void downloadSampleRequestPdf(r.id, r.request_no).catch((e) =>
                    toast.error(e instanceof Error ? e.message : 'Failed to download the PDF'),
                  );
                }}
              >
                <Download className="size-4" />
                <span>Print</span>
              </DropdownMenuItem>
              {canCancel && (
                <DropdownMenuItem variant="destructive" onSelect={() => void handleCancel()}>
                  <XCircle className="size-4" />
                  <span>Cancel request</span>
                </DropdownMenuItem>
              )}
            </DetailActionsMenu>
          </div>
        </div>

        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Status:</span>
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(r.status)}`}>
              {r.status_label}
            </span>
          </div>
          {r.created_at && (
            <p className="text-xs text-muted-foreground">
              Created {new Date(r.created_at).toLocaleDateString()}
            </p>
          )}
        </div>

        <FormSection
          title="Customer & project"
          summary={summaries.customer}
          open={open.customer}
          onOpenChange={(v) => setOpen((p) => ({ ...p, customer: v }))}
        >
          <ReadRow label="Customer">{r.customer_name || '-'}</ReadRow>
          <ReadRow label="Project">
            {r.project_id ? [r.project_code, r.project_name].filter(Boolean).join(' - ') : r.project_name || '-'}
          </ReadRow>
          {r.quotation_label && <ReadRow label="Quotation">{r.quotation_label}</ReadRow>}
          {r.scope_note && <ReadRow label="For (scope)">{r.scope_note}</ReadRow>}
        </FormSection>

        <FormSection
          title="Lines"
          summary={summaries.lines}
          open={open.lines}
          onOpenChange={(v) => setOpen((p) => ({ ...p, lines: v }))}
        >
          {r.board_mounting && <ReadRow label="Board label">{r.board_label || '-'}</ReadRow>}
          {r.lines.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">No items.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] table-fixed text-sm">
                <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="w-8 px-2 py-2 text-left">#</th>
                    <th className="w-[44%] px-2 py-2 text-left">Item</th>
                    <th className="w-12 px-2 py-2 text-left">Qty</th>
                    <th className="px-2 py-2 text-left">Remarks</th>
                  </tr>
                </thead>
                <tbody>
                  {r.lines.map((l) => (
                    <tr key={l.id} data-testid="sample-request-line" className="border-t">
                      <td className="px-2 py-2 text-muted-foreground">{l.line_no}</td>
                      <td className="px-2 py-2">
                        <span className="block truncate" title={codeWithName(l.product_code, l.product_name)}>
                          {codeWithName(l.product_code, l.product_name)}
                        </span>
                        {l.alt_product_code && (
                          <span className="block truncate text-xs text-muted-foreground">
                            OR {l.alt_product_code}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2">{l.qty}</td>
                      <td className="px-2 py-2">
                        <span className="block truncate" title={l.remark ?? ''}>
                          {l.remark}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </FormSection>

        <FormSection
          title="Delivery"
          summary={summaries.delivery}
          open={open.delivery}
          onOpenChange={(v) => setOpen((p) => ({ ...p, delivery: v }))}
        >
          <ReadRow label="Delivery">
            {r.deliver_immediately ? 'Immediate' : r.delivery_date || '-'}
          </ReadRow>
          <ReadRow label="Delivery mode">
            {r.delivery_mode ? DELIVERY_MODE_LABELS[r.delivery_mode] : '-'}
          </ReadRow>
          <ReadRow label="Ship to">{r.ship_to_name || '-'}</ReadRow>
          <ReadRow label="Shipping address">{r.ship_to_address || '-'}</ReadRow>
          <ReadRow label="Site contact">
            {[r.site_contact_name, r.site_contact_phone].filter(Boolean).join(' · ') || '-'}
          </ReadRow>
          {r.site_contact_note && <ReadRow label="Contact note">{r.site_contact_note}</ReadRow>}
        </FormSection>

        <FormSection
          title="Additional Information"
          summary={summaries.additional}
          open={open.additional}
          onOpenChange={(v) => setOpen((p) => ({ ...p, additional: v }))}
        >
          <ReadRow label="Loan period">
            {r.loan_value} {r.loan_unit}
          </ReadRow>
          <ReadRow label="Goods from">{r.goods_from ? GOODS_FROM_LABELS[r.goods_from] : '-'}</ReadRow>
          {r.purpose && <ReadRow label="Purpose">{r.purpose}</ReadRow>}
        </FormSection>
      </div>
    );
  }

  // ---- New / draft edit ----
  const projectChosen = Boolean(state.project_id);
  return (
    <div className="w-full max-w-5xl mx-auto px-3 pt-4 pb-8 space-y-4">
      {backButton}
      <h1 className="text-lg font-semibold">
        {request ? `Edit ${request.request_no}` : 'New Sample Request'}
      </h1>

      <FormSection
        title="Customer & project"
        summary={summaries.customer}
        open={open.customer}
        onOpenChange={(v) => setOpen((p) => ({ ...p, customer: v }))}
      >
        <div className="space-y-1.5">
          <Label htmlFor="sr-customer">Customer *</Label>
          <AsyncCombobox<DebtorLookupItem>
            id="sr-customer"
            value={state.customer_id}
            onChange={(v, item) => {
              set('customer_id', v);
              set('customer_label', item?.debtor_name ?? '');
            }}
            fetchOptions={(q) => lookupDebtors(q)}
            optionValue={(o) => o.customer_id}
            optionLabel={(o) => o.debtor_name}
            optionMeta={(o) => o.customer_code}
            allowFreeText={false}
            displayValue={state.customer_label}
            placeholder="Search customers..."
          />
          <FieldError message={errors.customer_id} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sr-project">Registered project</Label>
          <AsyncCombobox<ProjectLookupItem>
            id="sr-project"
            value={state.project_id}
            onChange={(v, item) => {
              set('project_id', v);
              set('project_label', item ? `${item.project_code} - ${item.title}` : '');
            }}
            fetchOptions={(q) => lookupProjects(q)}
            optionValue={(o) => o.id}
            optionLabel={(o) => `${o.project_code} - ${o.title}`}
            optionMeta={(o) => o.company_name ?? ''}
            allowFreeText={false}
            displayValue={state.project_label}
            placeholder="Search your registered projects..."
          />
          <FieldError message={errors.project_id} />
        </div>
        {!projectChosen && (
          <div className="space-y-1.5">
            <Label htmlFor="sr-project-title">Project title (if not registered)</Label>
            <Input
              id="sr-project-title"
              value={state.project_title}
              onChange={(e) => set('project_title', e.target.value)}
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="sr-scope">For (scope)</Label>
          <Input
            id="sr-scope"
            value={state.scope_note}
            placeholder="e.g. Tower A & B"
            onChange={(e) => set('scope_note', e.target.value)}
          />
        </div>
      </FormSection>

      <FormSection
        title="Lines"
        summary={summaries.lines}
        open={open.lines}
        onOpenChange={(v) => setOpen((p) => ({ ...p, lines: v }))}
      >
        <div className="space-y-1.5">
          <Label>Mount &amp; label sample board</Label>
          <div>
            <Segmented
              label="Mount and label sample board"
              value={state.board_mounting ? 'yes' : 'no'}
              options={[
                { value: 'no', label: 'No' },
                { value: 'yes', label: 'Yes' },
              ]}
              onChange={(v) => set('board_mounting', v === 'yes')}
            />
          </div>
        </div>
        {state.board_mounting && (
          <div className="space-y-1.5">
            <Label htmlFor="sr-board-label">Board label *</Label>
            <Input
              id="sr-board-label"
              value={state.board_label}
              onChange={(e) => set('board_label', e.target.value)}
            />
            <FieldError message={errors.board_label} />
          </div>
        )}

        {state.lines.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] table-fixed text-sm">
              <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="w-8 px-2 py-2 text-left">#</th>
                  <th className="w-[34%] px-2 py-2 text-left">Item</th>
                  <th className="w-[26%] px-2 py-2 text-left">Or (alternative)</th>
                  <th className="w-16 min-w-[3.5rem] px-2 py-2 text-left">Qty</th>
                  <th className="px-2 py-2 text-left">Remarks</th>
                  <th className="w-10 px-2 py-2">
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.lines.map((line, index) => (
                  <tr key={line.key} data-testid="sample-request-line" className="border-t align-top">
                    <td className="px-2 py-2 text-muted-foreground">{index + 1}</td>
                    <td className="px-2 py-2">
                      <span className="block truncate text-sm" title={line.label}>
                        {line.label}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <SearchableSelect
                        aria-label="Alternative"
                        value={line.alt_product_id ?? ''}
                        onChange={(v) => {
                          if (!v) updateLine(line.key, { alt_product_id: null, alt_label: null });
                        }}
                        onOptionChange={(opt) =>
                          updateLine(line.key, {
                            alt_product_id: opt ? opt.value : null,
                            alt_label: opt ? opt.label : null,
                          })
                        }
                        fetchOptions={fetchProducts}
                        selectedOption={
                          line.alt_product_id
                            ? { value: line.alt_product_id, label: line.alt_label ?? '' }
                            : undefined
                        }
                        placeholder="Optional"
                        clearable
                        truncateTriggerLabel
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Input
                        type="number"
                        inputMode="numeric"
                        min={1}
                        aria-label="Quantity"
                        value={line.qty}
                        onChange={(e) => updateLine(line.key, { qty: e.target.value })}
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Input
                        aria-label="Remarks"
                        value={line.remark}
                        onChange={(e) => updateLine(line.key, { remark: e.target.value })}
                      />
                    </td>
                    <td className="px-2 py-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label="Remove line"
                        onClick={() => removeLine(line.key)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="space-y-1.5" {...(errors.lines ? { 'data-error-anchor': 'lines' } : {})}>
          <Label>Add item</Label>
          <SearchableSelect
            aria-label="Product"
            value=""
            onChange={() => undefined}
            onOptionChange={addLine}
            fetchOptions={fetchProducts}
            placeholder="Search a product code or name..."
            emptyMessage={
              missed
                ? `${missed} is not in the product list. Pick the product you mean, or ask purchasing to add it as a new product before you request it.`
                : 'Type a product code or name.'
            }
          />
          <FieldError message={errors.lines} />
        </div>
      </FormSection>

      <FormSection
        title="Delivery"
        summary={summaries.delivery}
        open={open.delivery}
        onOpenChange={(v) => setOpen((p) => ({ ...p, delivery: v }))}
      >
        <div className="space-y-1.5" {...(errors.delivery_date ? { 'data-error-anchor': 'delivery_date' } : {})}>
          <Label>Delivery *</Label>
          <div>
            <Segmented
              label="Delivery timing"
              value={state.immediate ? 'immediate' : 'date'}
              options={[
                { value: 'date', label: 'On a date' },
                { value: 'immediate', label: 'Immediate arrangement' },
              ]}
              onChange={(v) => set('immediate', v === 'immediate')}
            />
          </div>
          {!state.immediate && (
            <Input
              type="date"
              aria-label="Delivery date"
              value={state.delivery_date}
              onChange={(e) => set('delivery_date', e.target.value)}
            />
          )}
          <FieldError message={errors.delivery_date} />
        </div>
        <div className="space-y-1.5" {...(errors.delivery_mode ? { 'data-error-anchor': 'delivery_mode' } : {})}>
          <Label>Delivery mode *</Label>
          <div>
            <Segmented
              label="Delivery mode"
              value={state.delivery_mode}
              options={(Object.keys(DELIVERY_MODE_LABELS) as SampleRequestDeliveryMode[]).map((k) => ({
                value: k,
                label: DELIVERY_MODE_LABELS[k],
              }))}
              onChange={(v) => set('delivery_mode', v)}
            />
          </div>
          <FieldError message={errors.delivery_mode} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sr-ship-to">Ship to *</Label>
          <Input
            id="sr-ship-to"
            value={state.ship_to_name}
            onChange={(e) => set('ship_to_name', e.target.value)}
          />
          <FieldError message={errors.ship_to_name} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sr-ship-address">Shipping address</Label>
          <Textarea
            id="sr-ship-address"
            rows={2}
            value={state.ship_to_address}
            onChange={(e) => set('ship_to_address', e.target.value)}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="sr-site-contact">Site contact</Label>
            <Input
              id="sr-site-contact"
              value={state.site_contact_name}
              onChange={(e) => set('site_contact_name', e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sr-site-phone">Contact phone</Label>
            <Input
              id="sr-site-phone"
              inputMode="tel"
              value={state.site_contact_phone}
              onChange={(e) => set('site_contact_phone', e.target.value)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sr-site-note">Contact note</Label>
          <Input
            id="sr-site-note"
            value={state.site_contact_note}
            onChange={(e) => set('site_contact_note', e.target.value)}
          />
        </div>
      </FormSection>

      <FormSection
        title="Additional Information"
        summary={summaries.additional}
        open={open.additional}
        onOpenChange={(v) => setOpen((p) => ({ ...p, additional: v }))}
      >
        <div className="space-y-1.5">
          <Label htmlFor="sr-loan">Loan period</Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="sr-loan"
              type="number"
              inputMode="numeric"
              min={1}
              className="w-24"
              value={state.loan_value}
              onChange={(e) => set('loan_value', e.target.value)}
            />
            <Segmented
              label="Loan unit"
              value={state.loan_unit}
              options={[
                { value: 'days', label: 'Days' },
                { value: 'months', label: 'Months' },
              ]}
              onChange={(v) => set('loan_unit', v)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Goods from</Label>
          <div>
            <Segmented
              label="Goods from"
              value={state.goods_from}
              options={(Object.keys(GOODS_FROM_LABELS) as SampleRequestGoodsFrom[]).map((k) => ({
                value: k,
                label: GOODS_FROM_LABELS[k],
              }))}
              onChange={(v) => set('goods_from', v)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sr-purpose">Purpose</Label>
          <Textarea
            id="sr-purpose"
            rows={3}
            value={state.purpose}
            onChange={(e) => set('purpose', e.target.value)}
          />
        </div>
      </FormSection>

      {(problemCount > 0 || serverMessage) && (
        <div
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          data-testid="submit-problem-summary"
        >
          {problemCount > 0 && (
            <p>
              {problemCount} thing{problemCount === 1 ? '' : 's'} need
              {problemCount === 1 ? 's' : ''} attention.
            </p>
          )}
          {serverMessage && <p className="mt-0.5">{serverMessage}</p>}
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button variant="outline" onClick={handleSaveDraft} disabled={saving || busy}>
          {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
          Save Draft
        </Button>
        <Button onClick={handleSubmit} disabled={saving || busy}>
          {busy && <Loader2 className="size-4 mr-1 animate-spin" />}
          Submit
        </Button>
      </div>
    </div>
  );
}
