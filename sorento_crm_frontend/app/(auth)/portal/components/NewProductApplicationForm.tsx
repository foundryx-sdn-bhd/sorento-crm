'use client';

/**
 * Portal new product application - create / edit a draft or rejected application / read-only
 * view (NEW-PRODUCT-APP). One document card like the portal Sample Request, then the line table
 * (no supplier, supplier model no or cost: purchasing fills those in). Approval and the opening
 * of codes are staff work; the portal only shows their result.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Loader2, Plus, XCircle } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { RejectionReasonBanner } from '@/components/common/RejectionReasonBanner';
import {
  NewProductApplicationLines,
  type LookupFetcher,
  type NewProductApplicationLookups,
} from '@/components/common/NewProductApplicationLines';
import {
  EMPTY_FORM,
  blankLine,
  numberErrors,
  payloadFromState,
  stateFromDetail,
  stateFromPrefill,
  stockInquiryLabel,
  type FormState,
} from '@/lib/new-product-application-form';
import { formatDateInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import type { NewProductApplicationDetail } from '@/types/newProductApplication';
import { portalBase } from '../lib/portal-paths';
import {
  cancelNewProductApplication,
  createNewProductApplication,
  getNewProductApplication,
  getNewProductApplicationPrefill,
  listMyRespondedInquiries,
  lookupNewProductApplication,
  lookupNewProductApplicationCustomers,
  reviseNewProductApplication,
  submitNewProductApplication,
  updateNewProductApplication,
  type PortalCustomerHit,
  type PortalInquiryHit,
  type PortalLookupKind,
} from '../lib/new-product-application-service';
import { AsyncCombobox } from './AsyncCombobox';

const LOCK_SENTENCE =
  'Locked: Revise to change it; it goes back to Draft and needs approval again.';

function FormField({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('min-w-0 space-y-1.5', className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

/** The select fetcher for one portal lookup list (the server filters by `q`). */
const portalLookup =
  (
    kind: PortalLookupKind,
    label: (hit: { code: string; name: string }) => string,
  ): LookupFetcher =>
  async (q) =>
    (await lookupNewProductApplication(kind, q)).map((hit) => ({
      value: hit.id,
      label: label(hit),
      code: hit.code,
    }));

const PORTAL_LOOKUPS: NewProductApplicationLookups = {
  itemGroups: portalLookup('item-groups', (h) => h.name),
  uoms: portalLookup('uoms', (h) => h.code),
  brands: portalLookup('brands', (h) => h.name),
  itemTypes: portalLookup('item-types', (h) => h.name),
};

function ReasonDialog({
  title,
  description,
  label,
  confirmLabel,
  required,
  busy,
  onCancel,
  onConfirm,
}: {
  title: string;
  description?: string;
  label: string;
  confirmLabel: string;
  required: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}) {
  const [value, setValue] = useState('');
  const ready = !required || value.trim().length > 0;
  return (
    <Dialog open onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="w-full max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className={description ? undefined : 'sr-only'}>
            {description ?? label}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-1.5">
          <Label htmlFor="npa-portal-reason">
            {required ? `${label} *` : label}
          </Label>
          <Textarea
            id="npa-portal-reason"
            rows={3}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </DialogBody>
        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onCancel}>
            Back
          </Button>
          <Button
            disabled={!ready || busy}
            onClick={() => onConfirm(value.trim())}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function NewProductApplicationForm({
  slug,
  applicationId,
}: {
  slug?: string;
  applicationId?: string;
}) {
  const router = useRouter();
  const [application, setApplication] =
    useState<NewProductApplicationDetail | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(applicationId));
  const [state, setState] = useState<FormState>({
    ...EMPTY_FORM,
    lines: [blankLine()],
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState<'revise' | 'cancel' | null>(null);

  const backPath = `${portalBase(slug)}?type=new_product_application`;
  const lookups = useMemo(() => PORTAL_LOOKUPS, []);
  const set = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) =>
      setState((prev) => ({ ...prev, [key]: value })),
    [],
  );

  const load = useCallback((d: NewProductApplicationDetail) => {
    setApplication(d);
    const loaded = stateFromDetail(d);
    setState(
      loaded.lines.length ? loaded : { ...loaded, lines: [blankLine()] },
    );
  }, []);

  useEffect(() => {
    if (!applicationId) return;
    let cancelled = false;
    getNewProductApplication(applicationId)
      .then((d) => {
        if (!cancelled) load(d);
      })
      .catch((e) => {
        if (!cancelled)
          toast.error(
            e instanceof Error ? e.message : 'Failed to load the application',
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applicationId, load]);

  const status = application?.status ?? 'draft';
  const editable = !application || status === 'draft' || status === 'rejected';
  const readOnly = !editable;
  const locked = status === 'submitted' || status === 'approved';
  const anyResolved = Boolean(
    application?.lines.some((l) => l.opened_product_id || l.closed_reason),
  );
  const canRevise = locked && !anyResolved;
  const canCancel =
    Boolean(application) && !anyResolved && (locked || status === 'draft');

  const save = async (): Promise<{ id: string }> => {
    const body = payloadFromState(state);
    const id = application?.id ?? savedId;
    const saved = id
      ? await updateNewProductApplication(id, body)
      : await createNewProductApplication(body);
    setSavedId(saved.id);
    return saved;
  };

  const explain = (e: unknown, fallback: string) => {
    const message = e instanceof Error ? e.message : fallback;
    setServerMessage(message);
    toast.error(message);
  };

  const checkNumbers = () => {
    const found = numberErrors(state);
    setErrors(found);
    return Object.keys(found).length === 0;
  };

  const handleSaveDraft = async () => {
    setServerMessage(null);
    if (!checkNumbers()) return;
    setSaving(true);
    try {
      const saved = await save();
      toast.success('Draft saved');
      if (!applicationId) {
        router.replace(
          `${portalBase(slug)}/new_product_application/${saved.id}`,
        );
      }
    } catch (e) {
      explain(e, 'Failed to save the application');
    } finally {
      setSaving(false);
    }
  };

  // Save, then submit. A refusal names the line; the user stays here with the draft kept.
  const handleSubmit = async () => {
    setServerMessage(null);
    if (!checkNumbers()) return;
    setSaving(true);
    try {
      const saved = await save();
      await submitNewProductApplication(saved.id);
      toast.success('Application submitted');
      router.push(backPath);
    } catch (e) {
      explain(e, 'Failed to submit the application');
    } finally {
      setSaving(false);
    }
  };

  const confirmAsk = async (value: string) => {
    if (!application || !asking) return;
    setSaving(true);
    try {
      if (asking === 'revise') {
        load(await reviseNewProductApplication(application.id, value));
        toast.success('Back to draft');
      } else {
        load(
          await cancelNewProductApplication(application.id, value || undefined),
        );
        toast.success('Application cancelled');
      }
      setAsking(null);
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : 'Failed to update the application',
      );
    } finally {
      setSaving(false);
    }
  };

  // Picking an inquiry copies its text into the form; the debtor is never picked for the user.
  const startFromInquiry = async (inquiryId: string) => {
    try {
      setState(
        stateFromPrefill(await getNewProductApplicationPrefill(inquiryId)),
      );
      setErrors({});
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : 'Failed to load the stock inquiry',
      );
    }
  };

  const fetchInquiries = async (q: string) => {
    const needle = q.trim().toLowerCase();
    return (await listMyRespondedInquiries()).filter(
      (i) => !needle || inquiryText(i).toLowerCase().includes(needle),
    );
  };

  if (loading) {
    return (
      <div className="min-h-dvh max-w-7xl mx-auto px-4 py-6 space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="min-h-dvh max-w-7xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0"
          onClick={() => router.push(backPath)}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back
        </Button>
        {application && (
          <div className="flex min-w-0 items-center gap-3">
            <span className="min-w-0 truncate text-sm text-muted-foreground">
              {application.application_no}
            </span>
            {canRevise && (
              <Button
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={() => setAsking('revise')}
              >
                Revise
              </Button>
            )}
            {canCancel && (
              <DetailActionsMenu ariaLabel="Application actions">
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => setAsking('cancel')}
                >
                  <XCircle className="size-4" />
                  <span>Cancel application</span>
                </DropdownMenuItem>
              </DetailActionsMenu>
            )}
          </div>
        )}
      </div>

      {!application && (
        <h1 className="text-lg font-semibold">New Product Application</h1>
      )}
      {application?.created_at && (
        <p className="text-xs text-muted-foreground">
          Created {formatDateInMalaysia(application.created_at)}
        </p>
      )}

      {locked && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {LOCK_SENTENCE}
        </p>
      )}
      {status === 'rejected' && (
        <RejectionReasonBanner reason={application?.reject_reason} />
      )}
      {application?.revise_reason && (
        <p className="text-sm text-muted-foreground">
          Revised: {application.revise_reason}
        </p>
      )}
      {application?.cancel_reason && (
        <p className="text-sm text-muted-foreground">
          Cancelled: {application.cancel_reason}
        </p>
      )}

      <Card className="border-2 shadow-sm">
        <CardContent className="pt-6 pb-8 px-5 sm:px-10">
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <Badge variant="secondary">New Product Application</Badge>
            <Badge status={status}>
              {application?.status_label ?? 'Draft'}
            </Badge>
          </div>
          <h2 className="text-center text-xl font-semibold tracking-tight border-b border-border pb-4 mb-6">
            New Product Application
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-5">
            {!application ? (
              <FormField
                label="Start from my stock inquiry"
                htmlFor="npa-inquiry"
                className="sm:col-span-2"
              >
                <AsyncCombobox<PortalInquiryHit>
                  id="npa-inquiry"
                  ariaLabel="Start from my stock inquiry"
                  value={state.stock_inquiry_id}
                  displayValue={state.stock_inquiry_label}
                  allowFreeText={false}
                  fetchOptions={fetchInquiries}
                  optionValue={(o) => o.id}
                  optionLabel={inquiryText}
                  optionMeta={(o) => o.project_name ?? ''}
                  placeholder="Search your responded stock inquiries"
                  onChange={(v, item) => {
                    if (item) {
                      set('stock_inquiry_label', inquiryText(item));
                      void startFromInquiry(item.id);
                    } else if (!v) {
                      setState((prev) => ({
                        ...prev,
                        stock_inquiry_id: '',
                        stock_inquiry_label: '',
                      }));
                    }
                  }}
                />
              </FormField>
            ) : (
              <FormField label="Stock inquiry" htmlFor="npa-inquiry-read">
                <p id="npa-inquiry-read" className="min-h-9 py-2 text-sm">
                  {application.stock_inquiry_number || '-'}
                </p>
              </FormField>
            )}

            <FormField
              label="Customer"
              htmlFor="npa-customer"
              className="sm:col-span-2"
            >
              <AsyncCombobox<PortalCustomerHit>
                id="npa-customer"
                disabled={readOnly}
                value={state.customer_name}
                onChange={(v, item) =>
                  setState((prev) => ({
                    ...prev,
                    customer_id: item?.customer_id ?? '',
                    customer_name: v,
                  }))
                }
                fetchOptions={(q) => lookupNewProductApplicationCustomers(q)}
                optionValue={(o) => o.debtor_name}
                optionLabel={(o) => o.debtor_name}
                optionMeta={(o) => o.customer_code}
                placeholder="Customer name"
              />
              {!state.customer_id && state.customer_name.trim() && (
                <Badge variant="secondary" className="mt-1.5">
                  No debtor code
                </Badge>
              )}
            </FormField>

            <FormField label="Project name" htmlFor="npa-project">
              <Input
                id="npa-project"
                disabled={readOnly}
                value={state.project_name}
                onChange={(e) => set('project_name', e.target.value)}
              />
            </FormField>
            <FormField label="Needed by" htmlFor="npa-needed-by">
              <Input
                id="npa-needed-by"
                type="date"
                disabled={readOnly}
                value={state.needed_by}
                onChange={(e) => set('needed_by', e.target.value)}
              />
            </FormField>

            <FormField
              label="Reason / remark"
              htmlFor="npa-remark"
              className="sm:col-span-2"
            >
              <Textarea
                id="npa-remark"
                rows={3}
                disabled={readOnly}
                value={state.remark}
                onChange={(e) => set('remark', e.target.value)}
              />
            </FormField>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Items</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <NewProductApplicationLines
            lines={state.lines}
            onChange={(lines) => set('lines', lines)}
            lookups={lookups}
            hideSupplierAndCost
            errors={errors}
            disabled={readOnly}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={readOnly}
            onClick={() =>
              setState((prev) => ({
                ...prev,
                lines: [...prev.lines, blankLine()],
              }))
            }
          >
            <Plus className="h-4 w-4 mr-2" />
            Add item
          </Button>
        </CardContent>
      </Card>

      {application && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Opened as</CardTitle>
          </CardHeader>
          <CardContent>
            {anyResolved ? (
              <ul className="space-y-1.5 text-sm">
                {application.lines.map((l) => (
                  <li key={l.id} className="flex flex-wrap items-center gap-2">
                    <span className="text-muted-foreground">
                      Line {l.line_no}
                    </span>
                    {l.opened_product_id ? (
                      <>
                        <Badge status="completed">Opened</Badge>
                        <span className="font-medium">
                          {l.opened_product_code}
                        </span>
                      </>
                    ) : l.closed_reason ? (
                      <>
                        <Badge status="cancelled">Not opening</Badge>
                        <span>{l.closed_reason}</span>
                      </>
                    ) : (
                      <span className="text-muted-foreground">Not yet</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                Nothing opened yet
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {serverMessage && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {serverMessage}
        </p>
      )}

      {editable && (
        <div className="flex flex-wrap gap-2 justify-end pt-2">
          <Button
            variant="ghost"
            onClick={() => router.push(backPath)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button variant="outline" onClick={handleSaveDraft} disabled={saving}>
            {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
            Save as draft
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving && <Loader2 className="size-4 mr-1 animate-spin" />}
            Submit
          </Button>
        </div>
      )}

      {asking === 'revise' && (
        <ReasonDialog
          title="Revise this application?"
          description="It goes back to Draft and needs approval again."
          label="What changed, and why?"
          confirmLabel="Revise"
          required
          busy={saving}
          onCancel={() => setAsking(null)}
          onConfirm={(v) => void confirmAsk(v)}
        />
      )}
      {asking === 'cancel' && (
        <ReasonDialog
          title="Cancel this application"
          label="Reason (optional)"
          confirmLabel="Cancel application"
          required={false}
          busy={saving}
          onCancel={() => setAsking(null)}
          onConfirm={(v) => void confirmAsk(v)}
        />
      )}
    </div>
  );
}

/** "SI26-0412 - XY-1234-MB": how the picker and the form name an inquiry. */
function inquiryText(i: PortalInquiryHit): string {
  return stockInquiryLabel(i.inquiry_number, i.product_code);
}
