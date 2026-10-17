'use client';

/**
 * Staff new / edit sample request (SR-CORE): the Consignment Requisition Form as one document
 * card, the way New Purchase Request / Sponsorship Form draws its paper form. The detail page
 * shows the same fields in the same order.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, Plus } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SampleRequestLines } from '@/components/common/SampleRequestLines';
import { SampleRequestFooterFields } from '@/components/common/SampleRequestFooterFields';
import { AsyncCombobox } from '@/app/(auth)/portal/components/AsyncCombobox';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import { SampleRequestError } from '@/types/sampleRequest';
import {
  EMPTY_FORM,
  blankLine,
  payloadFromState,
  stateFromDetail,
  numberErrors,
  validate,
  type FormState,
} from '@/lib/sample-request-form';
import { lookupCustomersForRequest } from '@/app/(protected)/procurement-management/purchase-requests/services/customerLookupService';
import type { CustomerLookupItem } from '@/app/(protected)/procurement-management/purchase-requests/types/purchaseRequest.types';
import { searchProjectsForLink } from '@/app/(protected)/complaint-management/complaints/services/complaintService';
import { getProject } from '../../_shared/services/projectService';
import {
  useSampleRequestMutations,
  useSampleRequestQuery,
  useSampleRequestSwitches,
} from '../hooks/useSampleRequests';
import { searchSampleRequestProducts } from '../services/sampleRequestService';

const BASE_PATH = '/project-sales/sample-requests';

function DocField({
  label,
  htmlFor,
  className,
  error,
  children,
}: {
  label: string;
  htmlFor?: string;
  className?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('min-w-0', className)}>
      {htmlFor ? (
        <Label htmlFor={htmlFor} className="text-sm font-normal text-muted-foreground">
          {label}
        </Label>
      ) : (
        <p className="text-sm text-muted-foreground">{label}</p>
      )}
      <div className="mt-1.5">{children}</div>
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
    </div>
  );
}

const ReadValue = ({ children }: { children: ReactNode }) => (
  <p className="min-h-9 break-words py-2 text-sm">{children || '-'}</p>
);

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
  const [state, setState] = useState<FormState>({ ...EMPTY_FORM, lines: [blankLine()] });
  const [hydrated, setHydrated] = useState(!requestId);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverMessage, setServerMessage] = useState<string | null>(null);

  const set = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) =>
      setState((prev) => ({ ...prev, [key]: value })),
    [],
  );
  const patch = useCallback(
    (next: Partial<FormState>) => setState((prev) => ({ ...prev, ...next })),
    [],
  );

  useEffect(() => {
    if (requestId && existing.data && !hydrated) {
      const loaded = stateFromDetail(existing.data);
      setState(loaded.lines.length ? loaded : { ...loaded, lines: [blankLine()] });
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

  const explain = (e: unknown) => {
    const message = e instanceof Error ? e.message : 'Failed to save the sample request';
    if (e instanceof SampleRequestError && e.field) {
      setErrors((prev) => ({ ...prev, [e.field as string]: message }));
    }
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
    const numberProblems = numberErrors(state);
    setErrors(numberProblems);
    if (Object.keys(numberProblems).length > 0) return;
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
    const found = validate(state, { requireProject: switches?.requireProject });
    setErrors(found);
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
      <div className="mx-auto w-full max-w-5xl space-y-4">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const record = existing.data;
  const requireProject = Boolean(switches?.requireProject);
  const projectTitle = state.project_id
    ? state.project_label.split(' - ').slice(1).join(' - ') || state.project_label
    : state.project_title;

  return (
    <div className="mx-auto w-full max-w-5xl">
      <Card className="border-2 shadow-sm">
        <CardContent className="px-5 pb-8 pt-6 sm:px-10">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Sample Request</Badge>
            <span className={`${STATUS_PILL_BASE} ${statusPillClass('draft')}`}>
              {record?.status_label ?? 'Draft'}
            </span>
          </div>
          <h2 className="mb-6 border-b border-border pb-4 text-center text-xl font-semibold tracking-tight">
            Consignment Requisition Form
          </h2>

          <div className="grid grid-cols-1 gap-x-10 gap-y-5 sm:grid-cols-2">
            <DocField label="Sales Person">
              <ReadValue>{record?.salesperson_name ?? 'You'}</ReadValue>
            </DocField>
            <DocField label="Date">
              <ReadValue>
                {formatDateInMalaysia(record?.request_date ?? new Date()) || '-'}
              </ReadValue>
            </DocField>

            <DocField
              label="Customer Name *"
              htmlFor="sr-customer"
              className="sm:col-span-2"
              error={errors.customer_id}
            >
              <AsyncCombobox<CustomerLookupItem>
                id="sr-customer"
                value={state.customer_id ? state.customer_label : state.customer_name}
                onChange={(v, item) => {
                  if (item && item.customer_id) {
                    patch({
                      customer_id: item.customer_id,
                      customer_label: item.debtor_name,
                      customer_name: '',
                    });
                  } else {
                    patch({ customer_id: '', customer_label: '', customer_name: v });
                  }
                }}
                fetchOptions={lookupCustomersForRequest}
                optionValue={(o) => o.debtor_name}
                optionLabel={(o) => o.debtor_name}
                optionMeta={(o) => o.customer_code}
                placeholder="Customer name"
              />
            </DocField>

            <DocField label="Project Title" htmlFor="sr-project-title">
              <Input
                id="sr-project-title"
                value={projectTitle}
                disabled={Boolean(state.project_id)}
                onChange={(e) => set('project_title', e.target.value)}
              />
            </DocField>
            <DocField
              label={requireProject ? 'Registered project *' : 'Registered project (optional)'}
              htmlFor="sr-project"
              error={errors.project_id}
            >
              <SearchableSelect
                id="sr-project"
                value={state.project_id}
                onChange={(v) => {
                  if (!v) patch({ project_id: '', project_label: '' });
                }}
                onOptionChange={(opt) =>
                  opt && patch({ project_id: opt.value, project_label: opt.label })
                }
                selectedOption={
                  state.project_id
                    ? { value: state.project_id, label: state.project_label }
                    : undefined
                }
                fetchOptions={(query, pageIndex) => searchProjectsForLink(query, pageIndex + 1)}
                paginated
                pageSize={25}
                clearable
                placeholder="Search a project by code or title"
                emptyMessage="No project matches that."
              />
            </DocField>

            <DocField label="For" htmlFor="sr-scope">
              <Input
                id="sr-scope"
                value={state.scope_note}
                onChange={(e) => set('scope_note', e.target.value)}
              />
            </DocField>
            <DocField
              label="Request Delivery Date *"
              htmlFor="sr-delivery-date"
              error={errors.delivery_date}
            >
              <Input
                id="sr-delivery-date"
                type="date"
                value={state.delivery_date}
                onChange={(e) => set('delivery_date', e.target.value)}
              />
            </DocField>

            <DocField label="Ship To *" htmlFor="sr-ship-to" error={errors.ship_to_name}>
              <Input
                id="sr-ship-to"
                value={state.ship_to_name}
                onChange={(e) => set('ship_to_name', e.target.value)}
              />
            </DocField>

            <DocField label="Shipping Address" htmlFor="sr-address" className="sm:col-span-2">
              <Textarea
                id="sr-address"
                rows={3}
                value={state.ship_to_address}
                onChange={(e) => set('ship_to_address', e.target.value)}
              />
            </DocField>

            <DocField label="Contact Person" htmlFor="sr-contact-person">
              <Input
                id="sr-contact-person"
                value={state.site_contact_name}
                onChange={(e) => set('site_contact_name', e.target.value)}
              />
            </DocField>

            <DocField label="Contact Phone / Note" htmlFor="sr-contact-phone">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Input
                  id="sr-contact-phone"
                  inputMode="tel"
                  placeholder="Phone"
                  value={state.site_contact_phone}
                  onChange={(e) => set('site_contact_phone', e.target.value)}
                />
                <Input
                  aria-label="Contact note"
                  placeholder="Note"
                  value={state.site_contact_note}
                  onChange={(e) => set('site_contact_note', e.target.value)}
                />
              </div>
            </DocField>

            <DocField label="Consignment No">
              <ReadValue>{record?.consignment_no}</ReadValue>
            </DocField>
            <DocField label="PO No">
              <ReadValue>{record?.po_no}</ReadValue>
            </DocField>
          </div>

          <div className="mb-3 mt-8 flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold">Line items</h3>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => patch({ lines: [...state.lines, blankLine()] })}
            >
              <Plus className="size-4" />
              Add row
            </Button>
          </div>
          <SampleRequestLines
            value={state}
            onChange={patch}
            search={searchSampleRequestProducts}
            captions={{ no: 'No.', item: 'Item Code', qty: 'Qty' }}
            errors={errors}
          />

          <div className="mt-8">
            <SampleRequestFooterFields state={state} set={set} errors={errors} />
          </div>

          {serverMessage && (
            <p className="mt-6 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {serverMessage}
            </p>
          )}

          <div className="mt-8 flex flex-col gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push(requestId ? `${BASE_PATH}/${requestId}` : BASE_PATH)}
            >
              Cancel
            </Button>
            <Button type="button" variant="outline" onClick={handleSaveDraft} disabled={saving}>
              {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              Save as draft
            </Button>
            <Button type="button" onClick={handleSubmit} disabled={saving}>
              {saving && <Loader2 className="mr-1 size-4 animate-spin" />}
              Submit
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
