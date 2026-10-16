'use client';

/**
 * Staff new / edit sample request: a full page, like New Purchase Request, with the same
 * sections in the same order as the detail card (SR-CORE).
 */

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { FormSection } from '@/components/common/FormSection';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { codeWithName } from '@/lib/product-display';
import { cn } from '@/lib/utils';
import {
  DELIVERY_MODE_LABELS,
  GOODS_FROM_LABELS,
  SampleRequestError,
  type SampleRequestDeliveryMode,
  type SampleRequestGoodsFrom,
  type SampleRequestProductHit,
} from '@/types/sampleRequest';
import {
  EMPTY_FORM,
  FIELD_SECTION,
  nextKey,
  payloadFromState,
  stateFromDetail,
  validate,
  type FormState,
  type SectionKey,
} from '@/lib/sample-request-form';
import { searchCustomersSelect } from '@/app/(protected)/order-management/customers/services/customerService';
import { getProject, listProjects, listQuotations } from '../../_shared/services/projectService';
import {
  useSampleRequestMutations,
  useSampleRequestQuery,
  useSampleRequestSwitches,
} from '../hooks/useSampleRequests';
import { searchSampleRequestProducts } from '../services/sampleRequestService';

const BASE_PATH = '/project-sales/sample-requests';

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
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex flex-wrap items-center rounded-md border p-0.5"
    >
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

const productOptions = (hits: SampleRequestProductHit[]): SearchableSelectOption[] =>
  hits.map((h) => ({
    value: h.id,
    label: codeWithName(h.product_code, h.product_name),
    description: h.brand_name ?? undefined,
    code: h.product_code,
  }));

export default function SampleRequestForm({
  requestId,
  projectId,
}: {
  /** Edit mode: the draft to change. */
  requestId?: string;
  /** New mode: a project to start from (the project tab's Create). */
  projectId?: string;
}) {
  const router = useRouter();
  const { create, update, submit } = useSampleRequestMutations();
  const existing = useSampleRequestQuery(requestId ?? null);
  const { data: switches } = useSampleRequestSwitches();
  const [state, setState] = useState<FormState>(EMPTY_FORM);
  const [hydrated, setHydrated] = useState(!requestId);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [missed, setMissed] = useState('');
  const [quoteOptions, setQuoteOptions] = useState<SearchableSelectOption[]>([]);
  const [open, setOpen] = useState<Record<SectionKey, boolean>>({
    customer: true,
    lines: true,
    delivery: true,
    additional: true,
  });

  const set = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) =>
      setState((prev) => ({ ...prev, [key]: value })),
    [],
  );

  useEffect(() => {
    if (requestId && existing.data && !hydrated) {
      setState(stateFromDetail(existing.data));
      setHydrated(true);
    }
  }, [requestId, existing.data, hydrated]);

  // A project passed in the URL starts the request on that project.
  useEffect(() => {
    if (!projectId || requestId) return;
    let cancelled = false;
    getProject(projectId)
      .then((p) => {
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          project_id: p.id,
          project_label: [p.project_code, p.title].filter(Boolean).join(' - '),
          // The project's buyer is its originating lead's customer; none when registered directly.
          ...(p.customer_id && !prev.customer_id
            ? { customer_id: p.customer_id, customer_label: p.customer_name ?? '' }
            : {}),
        }));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId, requestId]);

  // The quotation picker offers the picked project's quotations, current version each.
  useEffect(() => {
    if (!state.project_id) {
      setQuoteOptions([]);
      return;
    }
    let cancelled = false;
    listQuotations(state.project_id)
      .then((rows) => {
        if (cancelled) return;
        setQuoteOptions(
          rows
            .filter((q) => q.current_version_id)
            .map((q) => ({
              value: q.current_version_id as string,
              label: `${q.scope_label} v${q.current_version_no ?? 1}`,
            })),
        );
      })
      .catch(() => {
        if (!cancelled) setQuoteOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [state.project_id]);

  const fetchProducts = useCallback(async (q: string) => {
    const hits = await searchSampleRequestProducts(q);
    setMissed(hits.length === 0 ? q.trim() : '');
    return productOptions(hits);
  }, []);

  const fetchCustomers = useCallback(
    (q: string, page: number) => searchCustomersSelect(q, page),
    [],
  );

  const fetchProjects = useCallback(async (q: string) => {
    const body = await listProjects({ query: q || undefined, limit: 30 });
    return body.data.map((p) => ({
      value: p.id,
      label: [p.project_code, p.title].filter(Boolean).join(' - '),
      description: p.developer_name ?? undefined,
    }));
  }, []);

  const showErrors = (found: Record<string, string>) => {
    setErrors(found);
    const keys = Object.keys(found)
      .map((f) => FIELD_SECTION[f])
      .filter((k): k is SectionKey => Boolean(k));
    setOpen((prev) => ({ ...prev, ...Object.fromEntries(keys.map((k) => [k, true])) }));
  };

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

  const updateLine = (key: string, patch: Partial<FormState['lines'][number]>) =>
    setState((prev) => ({
      ...prev,
      lines: prev.lines.map((l) => (l.key === key ? { ...l, ...patch } : l)),
    }));

  const explain = (e: unknown) => {
    const message = e instanceof Error ? e.message : 'Failed to save the sample request';
    if (e instanceof SampleRequestError && e.field) showErrors({ [e.field]: message });
    setServerMessage(message);
  };

  const saving = create.isPending || update.isPending || submit.isPending;

  const save = async () => {
    const body = payloadFromState(state);
    return requestId
      ? update.mutateAsync({ id: requestId, data: body })
      : create.mutateAsync(body);
  };

  const handleSaveDraft = async () => {
    setServerMessage(null);
    try {
      const saved = await save();
      toast.success('Draft saved');
      router.push(`${BASE_PATH}/${saved.id}`);
    } catch (e) {
      explain(e);
    }
  };

  const handleSubmit = async () => {
    setServerMessage(null);
    const found = validate(state, {
      requireProject: switches?.requireProject,
      requireQuotation: switches?.requireQuotation,
    });
    showErrors(found);
    if (Object.keys(found).length > 0) return;
    try {
      const saved = await save();
      await submit.mutateAsync(saved.id);
      router.push(`${BASE_PATH}/${saved.id}`);
    } catch (e) {
      explain(e);
    }
  };

  if (requestId && (existing.isLoading || !hydrated)) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const problemCount = Object.values(errors).filter(Boolean).length;
  const requireProject = Boolean(switches?.requireProject);
  const section = (key: SectionKey) => ({
    open: open[key],
    onOpenChange: (v: boolean) => setOpen((p) => ({ ...p, [key]: v })),
  });

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4">
      <FormSection title="Customer & project" {...section('customer')}>
        <div className="space-y-1.5">
          <Label htmlFor="sr-customer">Customer</Label>
          <SearchableSelect
            id="sr-customer"
            value={state.customer_id}
            onChange={(v) => {
              set('customer_id', v);
              if (!v) set('customer_label', '');
            }}
            onOptionChange={(opt) => opt && set('customer_label', opt.label)}
            fetchOptions={fetchCustomers}
            paginated
            pageSize={50}
            selectedOption={
              state.customer_id
                ? { value: state.customer_id, label: state.customer_label }
                : undefined
            }
            placeholder="Select a customer..."
            clearable
          />
          <FieldError message={errors.customer_id} />
        </div>
        {!state.customer_id && (
          <div className="space-y-1.5">
            <Label htmlFor="sr-customer-name">Customer name (no debtor code)</Label>
            <Input
              id="sr-customer-name"
              value={state.customer_name}
              onChange={(e) => set('customer_name', e.target.value)}
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="sr-project">Registered project{requireProject ? ' *' : ''}</Label>
          <SearchableSelect
            id="sr-project"
            value={state.project_id}
            onChange={(v) => {
              set('project_id', v);
              if (!v) {
                set('project_label', '');
                set('quotation_version_id', '');
                set('quotation_label', '');
              }
            }}
            onOptionChange={(opt) => {
              if (opt) {
                set('project_label', opt.label);
                set('quotation_version_id', '');
                set('quotation_label', '');
              }
            }}
            fetchOptions={fetchProjects}
            selectedOption={
              state.project_id
                ? { value: state.project_id, label: state.project_label }
                : undefined
            }
            placeholder="Search your registered projects..."
            clearable
          />
          <FieldError message={errors.project_id} />
        </div>
        {!requireProject && !state.project_id && (
          <div className="space-y-1.5">
            <Label htmlFor="sr-project-title">Project title (if not registered)</Label>
            <Input
              id="sr-project-title"
              value={state.project_title}
              onChange={(e) => set('project_title', e.target.value)}
            />
          </div>
        )}
        {state.project_id && (
          <div className="space-y-1.5">
            <Label htmlFor="sr-quotation">Quotation{switches?.requireQuotation ? ' *' : ''}</Label>
            <SearchableSelect
              id="sr-quotation"
              value={state.quotation_version_id}
              onChange={(v) => set('quotation_version_id', v)}
              onOptionChange={(opt) => set('quotation_label', opt?.label ?? '')}
              options={quoteOptions}
              selectedOption={
                state.quotation_version_id
                  ? { value: state.quotation_version_id, label: state.quotation_label }
                  : undefined
              }
              placeholder="Select a quotation..."
              clearable
            />
            <FieldError message={errors.quotation_version_id} />
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

      <FormSection title="Lines" {...section('lines')}>
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
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[640px] table-fixed text-sm">
              <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="w-14 px-2 py-2 text-left">#</th>
                  <th className="w-[32%] px-2 py-2 text-left">Item</th>
                  <th className="w-[26%] px-2 py-2 text-left">Or (alternative)</th>
                  <th className="w-20 px-2 py-2 text-left">Qty</th>
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
                      <span className="block truncate" title={line.label}>
                        {line.label}
                      </span>
                      {line.inactive && (
                        <Badge variant="secondary" className="mt-1">
                          Inactive
                        </Badge>
                      )}
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
                        onClick={() =>
                          setState((prev) => ({
                            ...prev,
                            lines: prev.lines.filter((l) => l.key !== line.key),
                          }))
                        }
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

        <div className="space-y-1.5">
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

      <FormSection title="Delivery" {...section('delivery')}>
        <div className="space-y-1.5">
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
        <div className="space-y-1.5">
          <Label>Delivery mode *</Label>
          <div>
            <Segmented
              label="Delivery mode"
              value={state.delivery_mode}
              options={(Object.keys(DELIVERY_MODE_LABELS) as SampleRequestDeliveryMode[]).map(
                (k) => ({ value: k, label: DELIVERY_MODE_LABELS[k] }),
              )}
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

      <FormSection title="Additional Information" {...section('additional')}>
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
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
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
        <Button variant="outline" onClick={handleSaveDraft} disabled={saving}>
          {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
          Save Draft
        </Button>
        <Button onClick={handleSubmit} disabled={saving}>
          {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
          Submit
        </Button>
      </div>
    </div>
  );
}
