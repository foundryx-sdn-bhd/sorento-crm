'use client';

/**
 * Staff new / edit new product application (NEW-PRODUCT-APP): one document card, the way New
 * Sample Request and New Purchase Request draw theirs. The detail page shows the same fields in
 * the same order.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { NewProductApplicationLines } from '@/components/common/NewProductApplicationLines';
import { AsyncCombobox } from '@/app/(auth)/portal/components/AsyncCombobox';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import {
  EMPTY_FORM,
  blankLine,
  numberErrors,
  payloadFromState,
  stateFromDetail,
  stateFromPrefill,
  type FormState,
} from '@/lib/new-product-application-form';
import { lookupCustomersForRequest } from '@/app/(protected)/procurement-management/purchase-requests/services/customerLookupService';
import type { CustomerLookupItem } from '@/app/(protected)/procurement-management/purchase-requests/types/purchaseRequest.types';
import {
  useNewProductApplicationLookups,
  useNewProductApplicationMutations,
  useNewProductApplicationPrefillQuery,
  useNewProductApplicationQuery,
} from '../hooks/useNewProductApplications';
import { searchStockInquiryOptions } from '../services/newProductApplicationService';

const BASE_PATH = '/procurement-management/new-product-applications';

function DocField({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('min-w-0', className)}>
      {htmlFor ? (
        <Label
          htmlFor={htmlFor}
          className="text-sm font-normal text-muted-foreground"
        >
          {label}
        </Label>
      ) : (
        <p className="text-sm text-muted-foreground">{label}</p>
      )}
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

const ReadValue = ({ children }: { children: ReactNode }) => (
  <p className="min-h-9 break-words py-2 text-sm">{children || '-'}</p>
);

export default function NewProductApplicationForm({
  applicationId,
  stockInquiryId,
}: {
  /** Edit mode: the application to change. */
  applicationId?: string;
  /** New mode: a responded stock inquiry to copy the text from. */
  stockInquiryId?: string;
}) {
  const router = useRouter();
  const { create, update, submit } = useNewProductApplicationMutations();
  const existing = useNewProductApplicationQuery(applicationId ?? null);
  const prefill = useNewProductApplicationPrefillQuery(
    applicationId ? null : (stockInquiryId ?? null),
  );
  const lookups = useNewProductApplicationLookups();
  const [state, setState] = useState<FormState>({
    ...EMPTY_FORM,
    lines: [blankLine()],
  });
  const [hydrated, setHydrated] = useState(!applicationId && !stockInquiryId);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // A Submit the server refuses leaves the draft saved; the next press updates it.
  const [savedId, setSavedId] = useState<string | null>(null);

  const set = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) =>
      setState((prev) => ({ ...prev, [key]: value })),
    [],
  );

  useEffect(() => {
    if (hydrated) return;
    if (applicationId && existing.data) {
      const loaded = stateFromDetail(existing.data);
      setState(
        loaded.lines.length ? loaded : { ...loaded, lines: [blankLine()] },
      );
      setHydrated(true);
    } else if (!applicationId && prefill.data) {
      setState(stateFromPrefill(prefill.data));
      setHydrated(true);
    } else if (!applicationId && prefill.isError) {
      // The inquiry could not be read: start from an empty form, the hook has toasted why.
      setHydrated(true);
    }
  }, [hydrated, applicationId, existing.data, prefill.data, prefill.isError]);

  const saving = create.isPending || update.isPending || submit.isPending;

  const save = async () => {
    const body = payloadFromState(state);
    const id = applicationId ?? savedId;
    if (id) return update.mutateAsync({ id, data: body });
    const created = await create.mutateAsync(body);
    setSavedId(created.id);
    return created;
  };

  const checkNumbers = () => {
    const found = numberErrors(state);
    setErrors(found);
    return Object.keys(found).length === 0;
  };

  const handleSaveDraft = async () => {
    if (!checkNumbers()) return;
    try {
      const saved = await save();
      router.push(`${BASE_PATH}/${saved.id}`);
    } catch {
      // the mutation's own onError toast names the refusal
    }
  };

  // Save, then submit. The server's refusal names the line; the toast shows it and the
  // user stays here with the draft kept.
  const handleSubmit = async () => {
    if (!checkNumbers()) return;
    try {
      const saved = await save();
      await submit.mutateAsync(saved.id);
      router.push(`${BASE_PATH}/${saved.id}`);
    } catch {
      // the mutation's own onError toast names the refusal
    }
  };

  if ((applicationId && existing.isLoading) || !hydrated) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-4">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const record = existing.data;
  const cancelHref = applicationId
    ? `${BASE_PATH}/${applicationId}`
    : BASE_PATH;

  return (
    <div className="mx-auto w-full max-w-5xl">
      <Card className="border-2 shadow-sm">
        <CardContent className="px-5 pb-8 pt-6 sm:px-10">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Badge variant="secondary">New Product Application</Badge>
            <span
              className={`${STATUS_PILL_BASE} ${statusPillClass(record?.status ?? 'draft')}`}
            >
              {record?.status_label ?? 'Draft'}
            </span>
          </div>
          <h2 className="mb-6 border-b border-border pb-4 text-center text-xl font-semibold tracking-tight">
            New Product Application
          </h2>

          <div className="grid grid-cols-1 gap-x-10 gap-y-5 sm:grid-cols-2">
            <DocField label="Applicant">
              <ReadValue>{record?.applicant_name ?? 'You'}</ReadValue>
            </DocField>
            <DocField label="Date">
              <ReadValue>
                {formatDateInMalaysia(record?.created_at ?? new Date()) || '-'}
              </ReadValue>
            </DocField>

            <DocField label="Stock inquiry" htmlFor="npa-inquiry">
              <SearchableSelect
                id="npa-inquiry"
                value={state.stock_inquiry_id}
                clearable
                placeholder="Search a stock inquiry"
                emptyMessage="No stock inquiry matches that."
                fetchOptions={(query) => searchStockInquiryOptions(query)}
                selectedOption={
                  state.stock_inquiry_id
                    ? {
                        value: state.stock_inquiry_id,
                        label: state.stock_inquiry_label,
                      }
                    : undefined
                }
                onChange={(v) => {
                  if (!v)
                    setState((prev) => ({
                      ...prev,
                      stock_inquiry_id: '',
                      stock_inquiry_label: '',
                    }));
                }}
                onOptionChange={(opt) =>
                  opt &&
                  setState((prev) => ({
                    ...prev,
                    stock_inquiry_id: opt.value,
                    stock_inquiry_label: opt.label,
                  }))
                }
              />
            </DocField>
            <DocField label="Needed by" htmlFor="npa-needed-by">
              <Input
                id="npa-needed-by"
                type="date"
                value={state.needed_by}
                onChange={(e) => set('needed_by', e.target.value)}
              />
            </DocField>

            <DocField
              label="Customer"
              htmlFor="npa-customer"
              className="sm:col-span-2"
            >
              <AsyncCombobox<CustomerLookupItem>
                id="npa-customer"
                value={state.customer_name}
                onChange={(v, item) =>
                  setState((prev) => ({
                    ...prev,
                    customer_id: item?.customer_id ?? '',
                    customer_name: v,
                  }))
                }
                fetchOptions={lookupCustomersForRequest}
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
            </DocField>

            <DocField
              label="Project name"
              htmlFor="npa-project"
              className="sm:col-span-2"
            >
              <Input
                id="npa-project"
                value={state.project_name}
                onChange={(e) => set('project_name', e.target.value)}
              />
            </DocField>

            <DocField
              label="Reason / remark"
              htmlFor="npa-remark"
              className="sm:col-span-2"
            >
              <Textarea
                id="npa-remark"
                rows={3}
                value={state.remark}
                onChange={(e) => set('remark', e.target.value)}
              />
            </DocField>
          </div>

          <div className="mb-3 mt-8 flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold">Line items</h3>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setState((prev) => ({
                  ...prev,
                  lines: [...prev.lines, blankLine()],
                }))
              }
            >
              <Plus className="size-4" />
              Add row
            </Button>
          </div>
          <NewProductApplicationLines
            lines={state.lines}
            onChange={(lines) => set('lines', lines)}
            lookups={lookups}
            errors={errors}
          />

          <div className="mt-8 flex flex-col gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push(cancelHref)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={handleSaveDraft}
              disabled={saving}
            >
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
