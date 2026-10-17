'use client';

/**
 * Portal sample request form - create / edit a draft / read-only view (SR-CORE).
 *
 * The portal Purchase Request / Sponsorship Form layout (SubmissionForm.tsx): a Back button, the
 * paper form as one document card, an Items card with the product table, the footer fields in a
 * card, then Cancel / Save as draft / Submit. After submit the same cards render disabled.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Download, Loader2, Plus, XCircle } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { SampleRequestLines } from '@/components/common/SampleRequestLines';
import { SampleRequestFooterFields } from '@/components/common/SampleRequestFooterFields';
import {
  EMPTY_FORM,
  blankLine,
  payloadFromState,
  stateFromDetail,
  numberErrors,
  validate,
  type FormState,
} from '@/lib/sample-request-form';
import { cn } from '@/lib/utils';
import { SampleRequestError, type SampleRequestDetail } from '@/types/sampleRequest';
import { portalBase } from '../lib/portal-paths';
import { lookupProjects, type ProjectLookupItem } from '../lib/portal-client';
import {
  cancelSampleRequest,
  lookupSampleRequestCustomers,
  type SampleRequestCustomerHit,
  createSampleRequest,
  downloadSampleRequestPdf,
  getSampleRequest,
  searchSampleRequestProducts,
  submitSampleRequest,
  updateSampleRequest,
} from '../lib/sample-request-service';
import { formatDateInMalaysia } from '@/lib/helpers';
import { AsyncCombobox } from './AsyncCombobox';

function FormField({
  label,
  htmlFor,
  className,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('min-w-0 space-y-1.5', className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

export default function SampleRequestForm({
  slug,
  requestId,
}: {
  slug?: string;
  requestId?: string;
}) {
  const router = useRouter();
  const [request, setRequest] = useState<SampleRequestDetail | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(requestId));
  const [state, setState] = useState<FormState>({ ...EMPTY_FORM, lines: [blankLine()] });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const backPath = `${portalBase(slug)}?type=sample_request`;
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
    if (!requestId) return;
    let cancelled = false;
    getSampleRequest(requestId)
      .then((d) => {
        if (cancelled) return;
        setRequest(d);
        const loaded = stateFromDetail(d);
        setState(loaded.lines.length ? loaded : { ...loaded, lines: [blankLine()] });
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

  const isDraft = !request || request.status === 'draft';

  const save = async (): Promise<{ id: string }> => {
    const body = payloadFromState(state);
    const id = request?.id ?? savedId;
    const saved = id ? await updateSampleRequest(id, body) : await createSampleRequest(body);
    setSavedId(saved.id);
    return saved;
  };

  const explain = (e: unknown, fallback: string) => {
    const message = e instanceof Error ? e.message : fallback;
    if (e instanceof SampleRequestError && e.field) {
      setErrors((prev) => ({ ...prev, [e.field as string]: message }));
    }
    setServerMessage(message);
    toast.error(message);
  };

  const handleSaveDraft = async () => {
    setServerMessage(null);
    const numberProblems = numberErrors(state);
    setErrors(numberProblems);
    if (Object.keys(numberProblems).length > 0) return;
    setSaving(true);
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
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setSaving(true);
    try {
      const saved = await save();
      await submitSampleRequest(saved.id);
      toast.success('Sample request submitted');
      router.push(backPath);
    } catch (e) {
      explain(e, 'Failed to submit the sample request');
    } finally {
      setSaving(false);
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

  if (loading) {
    return (
      <div className="min-h-dvh max-w-7xl mx-auto px-4 py-6 space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const readOnly = Boolean(request && !isDraft);
  const canCancel = request?.status === 'submitted';
  const projectTitle = state.project_id
    ? state.project_label.split(' - ').slice(1).join(' - ') || state.project_label
    : state.project_title;

  return (
    <div className="min-h-dvh max-w-7xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <Button variant="ghost" size="sm" className="shrink-0" onClick={() => router.push(backPath)}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back
        </Button>
        {request && (
          <div className="flex min-w-0 items-center gap-3">
            <span className="min-w-0 truncate text-sm text-muted-foreground">
              {request.request_no}
            </span>
            {readOnly && (
              <DetailActionsMenu ariaLabel="Sample request actions">
                <DropdownMenuItem
                  onSelect={() => {
                    void downloadSampleRequestPdf(request.id, request.request_no).catch((e) =>
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
            )}
          </div>
        )}
      </div>

      {!request && <h1 className="text-lg font-semibold">New Sample Request</h1>}
      {request?.created_at && (
        <p className="text-xs text-muted-foreground">
          Created {formatDateInMalaysia(request.created_at)}
        </p>
      )}

      <div className="w-full">
        <Card className="border-2 shadow-sm">
          <CardContent className="pt-6 pb-8 px-5 sm:px-10">
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <Badge variant="secondary">Sample Request</Badge>
              <Badge status={request?.status ?? 'draft'}>{request?.status_label ?? 'Draft'}</Badge>
            </div>
            <h2 className="text-center text-xl font-semibold tracking-tight border-b border-border pb-4 mb-6">
              Consignment Requisition Form
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-5">
              <FormField
                label="Customer Name *"
                htmlFor="sr-customer"
                className="sm:col-span-2"
                error={errors.customer_id}
              >
                <AsyncCombobox<SampleRequestCustomerHit>
                  id="sr-customer"
                  disabled={readOnly}
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
                  fetchOptions={(q) => lookupSampleRequestCustomers(q)}
                  optionValue={(o) => o.debtor_name}
                  optionLabel={(o) => o.debtor_name}
                  optionMeta={(o) => o.customer_code}
                  placeholder="Customer name"
                />
              </FormField>

              <FormField label="Project Title" htmlFor="sr-project-title">
                <Input
                  id="sr-project-title"
                  disabled={readOnly || Boolean(state.project_id)}
                  value={projectTitle}
                  onChange={(e) => set('project_title', e.target.value)}
                />
              </FormField>
              <FormField
                label="Registered project (optional)"
                htmlFor="sr-project"
                error={errors.project_id}
              >
                <AsyncCombobox<ProjectLookupItem>
                  id="sr-project"
                  disabled={readOnly}
                  value={state.project_id}
                  displayValue={state.project_label}
                  allowFreeText={false}
                  onChange={(v, item) =>
                    patch({
                      project_id: v,
                      project_label: item ? `${item.project_code} - ${item.title}` : '',
                    })
                  }
                  fetchOptions={(q) => lookupProjects(q)}
                  optionValue={(o) => o.id}
                  optionLabel={(o) => `${o.project_code} - ${o.title}`}
                  optionMeta={(o) => o.company_name ?? ''}
                  placeholder="Search your registered projects"
                />
              </FormField>

              <FormField label="For" htmlFor="sr-scope">
                <Input
                  id="sr-scope"
                  disabled={readOnly}
                  value={state.scope_note}
                  onChange={(e) => set('scope_note', e.target.value)}
                />
              </FormField>
              <FormField
                label="Request Delivery Date *"
                htmlFor="sr-delivery-date"
                error={errors.delivery_date}
              >
                <Input
                  id="sr-delivery-date"
                  type="date"
                  disabled={readOnly}
                  value={state.delivery_date}
                  onChange={(e) => set('delivery_date', e.target.value)}
                />
              </FormField>

              <FormField label="Ship To *" htmlFor="sr-ship-to" error={errors.ship_to_name}>
                <Input
                  id="sr-ship-to"
                  disabled={readOnly}
                  value={state.ship_to_name}
                  onChange={(e) => set('ship_to_name', e.target.value)}
                />
              </FormField>
              <FormField label="Contact Person" htmlFor="sr-contact-person">
                <Input
                  id="sr-contact-person"
                  disabled={readOnly}
                  value={state.site_contact_name}
                  onChange={(e) => set('site_contact_name', e.target.value)}
                />
              </FormField>

              <FormField label="Shipping Address" htmlFor="sr-address" className="sm:col-span-2">
                <Textarea
                  id="sr-address"
                  rows={3}
                  disabled={readOnly}
                  value={state.ship_to_address}
                  onChange={(e) => set('ship_to_address', e.target.value)}
                />
              </FormField>

              <FormField
                label="Contact Phone / Note"
                htmlFor="sr-contact-phone"
                className="sm:col-span-2"
              >
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Input
                    id="sr-contact-phone"
                    inputMode="tel"
                    placeholder="Phone"
                    disabled={readOnly}
                    value={state.site_contact_phone}
                    onChange={(e) => set('site_contact_phone', e.target.value)}
                  />
                  <Input
                    aria-label="Contact note"
                    placeholder="Note"
                    disabled={readOnly}
                    value={state.site_contact_note}
                    onChange={(e) => set('site_contact_note', e.target.value)}
                  />
                </div>
              </FormField>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Items</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <SampleRequestLines
            value={state}
            onChange={patch}
            search={searchSampleRequestProducts}
            captions={{ no: '#', item: 'Item code', qty: 'Quantity' }}
            errors={errors}
            disabled={readOnly}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={readOnly}
            onClick={() => patch({ lines: [...state.lines, blankLine()] })}
          >
            <Plus className="h-4 w-4 mr-2" />
            Add item
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <SampleRequestFooterFields
            state={state}
            set={set}
            errors={errors}
            disabled={readOnly}
          />
        </CardContent>
      </Card>

      {serverMessage && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {serverMessage}
        </p>
      )}

      {!readOnly && (
        <div className="flex flex-wrap gap-2 justify-end pt-2">
          <Button variant="ghost" onClick={() => router.push(backPath)} disabled={saving}>
            Cancel
          </Button>
          <Button variant="outline" onClick={handleSaveDraft} disabled={saving}>
            {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
            Save as draft
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
            Submit sample request
          </Button>
        </div>
      )}
    </div>
  );
}
