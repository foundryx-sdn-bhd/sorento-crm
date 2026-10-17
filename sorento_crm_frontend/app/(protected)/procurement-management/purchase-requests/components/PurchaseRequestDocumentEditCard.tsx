'use client';

import { useMemo } from 'react';
import type { UseFormReturn, FieldArrayWithId } from 'react-hook-form';
import type { ColumnDef } from '@tanstack/react-table';
import { useReactTable, getCoreRowModel } from '@tanstack/react-table';
import { withRevisionSuffix } from '@/lib/document-number';
import { formatDate, formatCurrency } from '@/lib/helpers';
import { useCurrencyFormat } from '@/hooks/useCurrencyFormat';
import { Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import LookupBoundField from '@/components/common/LookupBoundField';
import { RequestorContactSelect } from '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import type { PurchaseRequestSchemaType } from '../forms/purchase-request-schema';
import type { PurchaseRequest } from '../types/purchaseRequest.types';
import { RequestCustomerField } from './RequestCustomerField';
import { PurchaseRequestSignoffFooter } from './PurchaseRequestSignoffFooter';
import { purchaseRequestNumberFieldLabel } from '../lib/purchase-request-field-labels';

const REQUEST_TYPE_LABELS: Record<string, string> = {
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
};

function DocField({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <p className="text-sm text-muted-foreground">{label}</p>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

type Props = {
  form: UseFormReturn<PurchaseRequestSchemaType>;
  request: PurchaseRequest;
  isSponsorship: boolean;
  showTypeSelect: boolean;
  fields: FieldArrayWithId<PurchaseRequestSchemaType, 'products', 'id'>[];
  append: (v: {
    item_code: null;
    quantity: null;
    remark: null;
    unit_price: null;
    total: null;
  }) => void;
  remove: (index: number) => void;
  sponsorshipLineGrandTotal: number;
};

type ProductLineField = FieldArrayWithId<PurchaseRequestSchemaType, 'products', 'id'>;

export function PurchaseRequestDocumentEditCard({
  form,
  request,
  isSponsorship,
  showTypeSelect,
  fields,
  append,
  remove,
  sponsorshipLineGrandTotal,
}: Props) {
  const { control } = form;
  const currencyFormat = useCurrencyFormat();
  const typeLabel =
    REQUEST_TYPE_LABELS[request.request_type ?? ''] ?? request.request_type ?? 'Request';

  const lineColumns = useMemo<ColumnDef<ProductLineField>[]>(() => {
    const base: ColumnDef<ProductLineField>[] = [
      {
        id: 'index',
        header: ({ column }) => (
          <DataGridColumnHeader title={isSponsorship ? 'NO.' : '#'} column={column} />
        ),
        cell: ({ row }) => (
          <span className="text-muted-foreground text-sm">{row.index + 1}</span>
        ),
        size: 56,
        enableResizing: false,
        meta: { headerTitle: isSponsorship ? 'NO.' : '#' },
      },
      {
        id: 'item_code',
        header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
        cell: ({ row }) => (
          <FormField
            control={control}
            name={`products.${row.index}.item_code`}
            render={({ field: f }) => (
              <FormItem>
                <FormControl>
                  <Input placeholder="Item code" {...f} value={f.value ?? ''} className="h-8" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        ),
        size: 180,
        meta: { headerTitle: 'Item Code' },
      },
      {
        id: 'quantity',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        cell: ({ row }) => (
          <FormField
            control={control}
            name={`products.${row.index}.quantity`}
            render={({ field: f }) => (
              <FormItem>
                <FormControl>
                  <NumberInput
                    step="any"
                    placeholder="0"
                    name={f.name}
                    ref={f.ref}
                    onBlur={f.onBlur}
                    value={f.value ?? null}
                    onValueChange={(v) => {
                      f.onChange(v);
                      // Re-check a flagged box as it is fixed (the form validates on blur otherwise).
                      if (form.getFieldState(f.name, form.formState).error) void form.trigger(f.name);
                      if (isSponsorship) {
                        const up = form.getValues(`products.${row.index}.unit_price`);
                        if (v !== null && !Number.isNaN(v) && up != null && up !== '') {
                          form.setValue(`products.${row.index}.total`, (v ?? 0) * Number(up));
                        }
                        if (v === null || Number.isNaN(v)) form.setValue(`products.${row.index}.total`, null);
                      }
                    }}
                    className="h-8 w-24"
                  />
                </FormControl>
                <FormMessage className="whitespace-normal break-words" />
              </FormItem>
            )}
          />
        ),
        size: isSponsorship ? 110 : 130,
        meta: { headerTitle: 'Qty' },
      },
    ];

    const pricing: ColumnDef<ProductLineField>[] = isSponsorship
      ? [
          {
            id: 'unit_price',
            header: ({ column }) => <DataGridColumnHeader title="U/P *" column={column} />,
            cell: ({ row }) => (
              <FormField
                control={control}
                name={`products.${row.index}.unit_price`}
                render={({ field: f }) => (
                  <FormItem>
                    <FormControl>
                      <NumberInput
                        step="any"
                        placeholder="0"
                        name={f.name}
                        ref={f.ref}
                        onBlur={f.onBlur}
                        value={f.value ?? null}
                        onValueChange={(v) => {
                          f.onChange(v);
                          const qty = form.getValues(`products.${row.index}.quantity`);
                          if (v !== null && !Number.isNaN(v) && qty != null && qty !== '') {
                            form.setValue(`products.${row.index}.total`, (v ?? 0) * Number(qty));
                          }
                          if (v === null || Number.isNaN(v)) form.setValue(`products.${row.index}.total`, null);
                        }}
                        className="h-8 w-24"
                      />
                    </FormControl>
                    <FormMessage className="whitespace-normal" />
                  </FormItem>
                )}
              />
            ),
            size: 120,
            meta: { headerTitle: 'U/P' },
          },
          {
            id: 'total',
            header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
            cell: ({ row }) => (
              <FormField
                control={control}
                name={`products.${row.index}.total`}
                render={({ field: f }) => (
                  <FormItem>
                    <FormControl>
                      <NumberInput
                        step="any"
                        placeholder="0"
                        name={f.name}
                        ref={f.ref}
                        onBlur={f.onBlur}
                        value={f.value ?? null}
                        onValueChange={(v) => {
                          f.onChange(v);
                        }}
                        className="h-8 w-28"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ),
            size: 130,
            meta: { headerTitle: 'Total' },
          },
        ]
      : [];

    const tail: ColumnDef<ProductLineField>[] = [
      {
        id: 'remark',
        header: ({ column }) => <DataGridColumnHeader title="Remark" column={column} />,
        cell: ({ row }) => (
          <FormField
            control={control}
            name={`products.${row.index}.remark`}
            render={({ field: f }) => (
              <FormItem>
                <FormControl>
                  <Input placeholder="Remark" {...f} value={f.value ?? ''} className="h-8" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        ),
        size: 200,
        meta: { headerTitle: 'Remark' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Delete</span>,
        // SF-5 (M5 run 3 review): `fields.length` read off the TABLE, not the
        // closure - `fields.length` in the memo's own deps recreated every
        // cell type (and remounted every input) on every append/remove. The
        // row count is read fresh per render regardless, since `table` is
        // part of `CellContext` and always current.
        cell: ({ row, table }) => (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => remove(row.index)}
            disabled={table.getRowModel().rows.length <= 1}
            aria-label="Delete line"
          >
            <Trash2 className="size-4 text-destructive" />
          </Button>
        ),
        size: 56,
        enableResizing: false,
        meta: { headerTitle: 'Delete' },
      },
    ];

    return [...base, ...pricing, ...tail];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [control, isSponsorship, remove]);

  const linesTable = useReactTable({
    columns: lineColumns,
    data: fields,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <div className="max-w-5xl mx-auto w-full">
      <Card className="border-2 shadow-sm">
        <CardContent className="pt-6 pb-8 px-5 sm:px-10">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">{typeLabel}</Badge>
              <Badge
                variant={
                  request.approval_status === 'approved'
                    ? 'primary'
                    : request.approval_status === 'rejected'
                      ? 'destructive'
                      : 'secondary'
                }
              >
                {request.approval_status === 'pending'
                  ? 'Pending approval'
                  : request.approval_status === 'approved'
                    ? 'Approved'
                    : request.approval_status === 'rejected'
                      ? 'Rejected'
                      : (request.status ?? '').toLowerCase() === 'submitted'
                        ? 'Submitted'
                        : (request.status ?? '').toLowerCase() === 'draft'
                          ? 'Draft'
                          : (request.approval_status || request.status || 'Draft')}
              </Badge>
            </div>
          </div>

          <h2 className="text-center text-xl font-semibold tracking-tight border-b border-border pb-4 mb-6">
            {isSponsorship ? 'Project Sales Sponsorship Form' : 'Purchase Request'}
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-5">
            {showTypeSelect && (
              <DocField label="Type" className="sm:col-span-2">
                <FormField
                  control={control}
                  name="request_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormControl>
                        <SearchableSelect
                          onChange={field.onChange}
                          value={field.value ?? ''}
                          disabled
                          placeholder="Select type"
                          options={[
                            { value: 'purchase_request', label: 'Purchase Request' },
                            { value: 'sponsorship_form', label: 'Sponsorship Form' },
                          ]}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </DocField>
            )}

            <DocField label={purchaseRequestNumberFieldLabel(request.request_type)}>
              <FormField
                control={control}
                name="request_number"
                render={({ field }) => (
                  <FormItem>
                    <FormControl>
                      {/*
                        Display suffixed, submit bare (UAC N1 against N2).

                        `request_number` is user-assignable and this form posts it
                        back, so the derived `-R{n}` must never enter the form
                        state: it would be written into the very column it was
                        derived from, and every lookup-by-number, index and
                        integration would then miss the row. The field stays bound
                        to the bare value - only what is painted carries the
                        suffix, and the input is read-only so no keystroke can
                        promote the painted value into the stored one.
                      */}
                      <Input
                        placeholder="e.g. PR26-0303"
                        {...field}
                        value={withRevisionSuffix(field.value, request.revision_no) ?? ''}
                        readOnly
                        className="font-medium tabular-nums"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </DocField>

            <DocField label="Submitted date">
              {/* Auto-stamped on submit; read-only. */}
              <p className="font-medium py-2">
                {request.submitted_at ? formatDate(new Date(request.submitted_at)) : '-'}
              </p>
            </DocField>

            <DocField label="Customer Name" className="sm:col-span-2">
              <RequestCustomerField form={form} />
            </DocField>

            <DocField label="PIC" className="sm:col-span-2">
              <FormField
                control={control}
                name="pic"
                render={({ field }) => (
                  <FormItem>
                    <FormControl>
                      <Input
                        placeholder="Name and contact number"
                        {...field}
                        value={field.value ?? ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </DocField>

            <DocField label="Project Title" className="sm:col-span-2">
              <FormField
                control={control}
                name="project_title"
                render={({ field }) => (
                  <FormItem>
                    <FormControl>
                      <Input placeholder="Project title" {...field} value={field.value ?? ''} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </DocField>

            {isSponsorship && (
              <DocField label="Delivery Address" className="sm:col-span-2">
                <FormField
                  control={control}
                  name="delivery_address"
                  render={({ field }) => (
                    <FormItem>
                      <FormControl>
                        <Textarea
                          placeholder="Delivery address"
                          rows={4}
                          className="resize-y min-h-[80px]"
                          {...field}
                          value={field.value ?? ''}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </DocField>
            )}

            {!isSponsorship && (
              <DocField label="Purpose" className="sm:col-span-2">
                <FormField
                  control={control}
                  name="purpose"
                  render={({ field }) => (
                    <FormItem>
                      <FormControl>
                        <Input
                          placeholder="e.g. Showroom, Mock up, Others"
                          {...field}
                          value={field.value ?? ''}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </DocField>
            )}

            {isSponsorship && (
              <>
                <DocField label="Total Project Value" className="sm:col-span-2">
                  <FormField
                    control={control}
                    name="total_project_value"
                    render={({ field }) => (
                      <FormItem>
                        <FormControl>
                          <NumberInput
                            inputMode="decimal"
                            step="0.01"
                            placeholder="e.g. 1234.00"
                            {...field}
                            value={field.value ?? ''}
                            onChange={(e) =>
                              field.onChange(e.target.value === '' ? null : e.target.value)
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </DocField>
                <DocField label="Sponsor Subject" className="sm:col-span-2">
                  <FormField
                    control={control}
                    name="sponsor_subject"
                    render={({ field }) => (
                      <FormItem>
                        <FormControl>
                          <LookupBoundField
                            table="purchase_requests"
                            column="sponsor_subject"
                            value={field.value}
                            onChange={field.onChange}
                            placeholder="Select sponsor subject"
                            renderFallback={() => (
                              <SearchableSelect
                                key={field.value || 'empty'}
                                onChange={field.onChange}
                                value={field.value || ''}
                                placeholder="Select sponsor subject"
                                options={[
                                  { value: 'showroom', label: 'Showroom' },
                                  { value: 'mockup', label: 'Mockup' },
                                  { value: 'others', label: 'Others' },
                                ]}
                              />
                            )}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </DocField>
                {form.watch('sponsor_subject') === 'others' && (
                  <DocField label="Please specify" className="sm:col-span-2">
                    <FormField
                      control={control}
                      name="sponsor_subject_other"
                      render={({ field }) => (
                        <FormItem>
                          <FormControl>
                            <Input
                              placeholder="Specify the sponsor subject"
                              {...field}
                              value={field.value ?? ''}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </DocField>
                )}
              </>
            )}

            {/* Required on a PR (routes CS), optional on an SF (SF-SALES-TYPE): after Purpose on a PR,
                after Sponsor Subject on an SF, as on the detail page. */}
            <DocField label="Sales Type" className="sm:col-span-2">
              <FormField
                control={control}
                name="sales_type"
                render={({ field }) => (
                  <FormItem>
                    <FormControl>
                      <LookupBoundField
                        table="purchase_requests"
                        column="sales_type"
                        value={field.value}
                        onChange={field.onChange}
                        placeholder="Select sales type"
                        clearable={isSponsorship}
                        // An existing SF with no sales type stays blank: the edit card mounts
                        // before the form reset, so the new-form default would otherwise fill it.
                        applyDefault={!isSponsorship}
                        renderFallback={() => (
                          <SearchableSelect
                            key={field.value || 'empty'}
                            onChange={field.onChange}
                            value={field.value || ''}
                            placeholder="Select sales type"
                            clearable={isSponsorship}
                            options={[
                              { value: 'project', label: 'Project' },
                              { value: 'cash_sales', label: 'Cash Sales' },
                            ]}
                          />
                        )}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </DocField>

            <DocField label={isSponsorship ? 'Date of Delivery' : 'Expected date of delivery'}>
              <FormField
                control={control}
                name="expected_delivery_date"
                render={({ field }) => (
                  <FormItem>
                    <FormControl>
                      <Input type="date" {...field} value={field.value ?? ''} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </DocField>

            {!isSponsorship && (
              <DocField label="Expected date to receive PO">
                <div className="space-y-2">
                  <FormField
                    control={control}
                    name="expected_po_date"
                    render={({ field }) => (
                      <FormItem>
                        <FormControl>
                          <Input type="date" {...field} value={field.value ?? ''} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={control}
                    name="expected_po_date_text"
                    render={({ field }) => (
                      <FormItem>
                        <FormControl>
                          <Input
                            placeholder="Free text (e.g. PROPOSED STAGE)"
                            {...field}
                            value={field.value ?? ''}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </DocField>
            )}

            {(request.approver_email || request.approver_user_id) && !request.approved_at && (
              <div className="sm:col-span-2">
                <p className="text-sm text-muted-foreground">Approver</p>
                <p className="font-medium mt-1.5">
                  {request.approver_display_name
                    ? `${request.approver_display_name} (${request.approver_email})`
                    : request.approver_email}
                </p>
              </div>
            )}

            {request.respond_inbox_url && (
              <div className="sm:col-span-2">
                <p className="text-sm text-muted-foreground">Respond conversation</p>
                <a
                  href={request.respond_inbox_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline text-sm break-all font-medium mt-1.5 inline-block"
                >
                  {request.respond_inbox_url}
                </a>
              </div>
            )}
          </div>

          <div className="mt-8">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <p className="text-sm font-medium">Line items</p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() =>
                  append({
                    item_code: null,
                    quantity: null,
                    remark: null,
                    unit_price: null,
                    total: null,
                  })
                }
              >
                <Plus className="size-4" />
                Add row
              </Button>
            </div>
            <DataGrid
              table={linesTable}
              recordCount={fields.length}
              listingKey="procurement.purchase_requests.view::edit-lines"
              tableLayout={{ width: 'fixed', columnsResizable: true }}
            >
              <DataGridTable />
            </DataGrid>
            {isSponsorship && (
              <div className="mt-3 flex justify-end border-t border-border pt-3">
                <p className="text-sm font-semibold tabular-nums">
                  Grand Total: {formatCurrency(sponsorshipLineGrandTotal, currencyFormat)}
                </p>
              </div>
            )}
          </div>

          <PurchaseRequestSignoffFooter
            request={request}
            renderRequestedColumn={() => (
              <>
                <div>
                  <p className="text-sm text-muted-foreground">Requested by</p>
                  <div className="mt-1.5">
                    <FormField
                      control={control}
                      name="requested_by_contact_id"
                      render={({ field }) => (
                        <FormItem>
                          <FormControl>
                            <RequestorContactSelect
                              value={field.value}
                              onChange={field.onChange}
                              submitterContactId={request.contact_id}
                              savedContactId={request.requested_by_contact_id}
                              savedContactName={request.requested_by_contact_name ?? request.requested_by}
                              placeholder="Select requestor"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                </div>
              </>
            )}
          />
        </CardContent>
      </Card>
    </div>
  );
}
