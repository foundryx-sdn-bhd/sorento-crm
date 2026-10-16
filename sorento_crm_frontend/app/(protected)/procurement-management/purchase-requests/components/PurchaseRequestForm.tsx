"use client";

import { useEffect, useMemo, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useFieldArray } from "react-hook-form";
import type { FieldArrayWithId } from "react-hook-form";
import { useRouter } from "next/navigation";
import {
  LoaderCircleIcon,
  Save,
  Send,
  Plus,
  Trash2,
  Link2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NumberInput } from "@/components/ui/number-input";
import { Textarea } from "@/components/ui/textarea";
import { SearchableSelect } from "@/components/common/SearchableSelect";
import { searchProjectsForLink } from "../services/purchaseRequestService";
import { RequestCustomerField } from "./RequestCustomerField";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ColumnDef } from "@tanstack/react-table";
import { useReactTable, getCoreRowModel } from "@tanstack/react-table";
import { DataGrid } from "@/components/ui/data-grid";
import { DataGridColumnHeader } from "@/components/ui/data-grid-column-header";
import { DataGridTable } from "@/components/ui/data-grid-table";
import {
  useCreatePurchaseRequest,
  useUpdatePurchaseRequest,
  useUpdatePurchaseRequestAndReply,
  usePurchaseRequest,
} from "../hooks/usePurchaseRequests";
import { getOrCreateViewLink } from "../services/purchaseRequestService";
import { toast } from '@/lib/toast';
import {
  PurchaseRequestFormSchema,
  lineHasContent,
  type PurchaseRequestSchemaType,
} from '../forms/purchase-request-schema';
import type { PurchaseRequestFormData } from '../types/purchaseRequest.types';
import PurchaseRequestAttachmentsSection from './PurchaseRequestAttachmentsSection';
import { PurchaseRequestDocumentEditCard } from './PurchaseRequestDocumentEditCard';
import { usePublicViewLinksEnabled } from '@/hooks/usePublicViewLinksEnabled';
import { purchaseRequestNumberFieldLabel, purchaseRequestNumberReplyPhrase } from '../lib/purchase-request-field-labels';
import LookupBoundField from '@/components/common/LookupBoundField';
import { RequestorContactSelect } from '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect';
import { cn } from '@/lib/utils';

const PURCHASE_REQUESTS_EDIT = "/procurement-management/purchase-requests";
const SPONSORSHIP_FORMS_EDIT = "/procurement-management/sponsorship-forms";

const REQUEST_TYPE_LABELS: Record<string, string> = {
  purchase_request: "Purchase Request",
  sponsorship_form: "Sponsorship Form",
};

interface PurchaseRequestFormProps {
  requestId?: string;
  /** When set (e.g. on sponsorship-forms/new), form defaults to this type and type field is hidden. */
  defaultRequestType?: "purchase_request" | "sponsorship_form";
  /** On edit: if the loaded record's type doesn't match, redirect to the correct section's edit page. */
  expectedRequestType?: "purchase_request" | "sponsorship_form";
  /** URL to redirect to after successful create/update (serializable; use this from Server Component pages). */
  successRedirectUrl?: string;
}

export default function PurchaseRequestForm({
  requestId,
  defaultRequestType = "purchase_request",
  expectedRequestType,
  successRedirectUrl,
}: PurchaseRequestFormProps) {
  const router = useRouter();
  const isEditMode = !!requestId;
  const { data: request, isLoading: isLoadingRequest } = usePurchaseRequest(
    requestId || null,
  );
  const createMutation = useCreatePurchaseRequest();
  const updateMutation = useUpdatePurchaseRequest();
  const updateAndReplyMutation = useUpdatePurchaseRequestAndReply();
  const [updateAndReplyDialogOpen, setUpdateAndReplyDialogOpen] =
    useState(false);
  const [replyMessage, setReplyMessage] = useState("");
  const [replyViewLinkLoading, setReplyViewLinkLoading] = useState(false);
  const publicViewLinksEnabled = usePublicViewLinksEnabled();

  // Redirect to the correct edit page if record type doesn't match (e.g. opened purchase-request edit but record is sponsorship_form)
  useEffect(() => {
    if (
      requestId &&
      request &&
      expectedRequestType &&
      request.request_type &&
      request.request_type !== expectedRequestType
    ) {
      const correctPath =
        request.request_type === "sponsorship_form"
          ? `${SPONSORSHIP_FORMS_EDIT}/${requestId}/edit`
          : `${PURCHASE_REQUESTS_EDIT}/${requestId}/edit`;
      router.replace(correctPath);
    }
  }, [requestId, request, expectedRequestType, router]);

  const form = useForm<PurchaseRequestSchemaType>({
    resolver: zodResolver(PurchaseRequestFormSchema),
    defaultValues: {
      request_type: defaultRequestType,
      request_number: null,
      request_date: null,
      customer_name: null,
      customer_id: null,
      pic: null,
      project_title: null,
      project_id: null,
      purpose: null,
      delivery_address: null,
      total_project_value: null,
      total_project_value_text: null,
      sales_type: null,
      sponsor_subject: null,
      sponsor_subject_other: null,
      expected_delivery_date: null,
      expected_po_date: null,
      expected_po_date_text: null,
      requested_by: null,
      requested_by_contact_id: null,
      requested_at: null,
      products: [
        {
          item_code: null,
          quantity: null,
          remark: null,
          unit_price: null,
          total: null,
        },
      ],
    },
    mode: 'onTouched',
  });

  const requestType =
    form.watch("request_type") ?? defaultRequestType ?? "purchase_request";
  const isSponsorship = requestType === "sponsorship_form";
  const watchedProducts = form.watch("products");
  const sponsorshipLineGrandTotal = useMemo(() => {
    if (!isSponsorship || !watchedProducts?.length) return 0;
    let sum = 0;
    for (const p of watchedProducts) {
      const tot = p.total != null && p.total !== "" ? Number(p.total) : null;
      if (tot != null && !Number.isNaN(tot)) {
        sum += tot;
        continue;
      }
      const q =
        p.quantity != null && p.quantity !== "" ? Number(p.quantity) : 0;
      const up =
        p.unit_price != null && p.unit_price !== "" ? Number(p.unit_price) : 0;
      sum += q * up;
    }
    return sum;
  }, [isSponsorship, watchedProducts]);

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "products",
  });

  const createModeLineColumns = useMemo<
    ColumnDef<FieldArrayWithId<PurchaseRequestSchemaType, "products", "id">>[]
  >(() => {
    const base: ColumnDef<FieldArrayWithId<PurchaseRequestSchemaType, "products", "id">>[] = [
      {
        id: "index",
        header: ({ column }) => (
          <DataGridColumnHeader title={isSponsorship ? "NO." : "#"} column={column} />
        ),
        cell: ({ row }) => (
          <span className="text-muted-foreground text-sm">{row.index + 1}</span>
        ),
        size: 56,
        enableResizing: false,
        meta: { headerTitle: isSponsorship ? "NO." : "#" },
      },
      {
        id: "item_code",
        header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
        cell: ({ row }) => (
          <FormField
            control={form.control}
            name={`products.${row.index}.item_code`}
            render={({ field: f }) => (
              <FormItem>
                <FormControl>
                  <Input placeholder="Item code" {...f} value={f.value ?? ""} className="h-8" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        ),
        size: 180,
        meta: { headerTitle: "Item Code" },
      },
      {
        id: "quantity",
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        cell: ({ row }) => (
          <FormField
            control={form.control}
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
                        if (v !== null && !Number.isNaN(v) && up != null && up !== "") {
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
        meta: { headerTitle: "Qty" },
      },
    ];

    const pricing: ColumnDef<FieldArrayWithId<PurchaseRequestSchemaType, "products", "id">>[] =
      isSponsorship
        ? [
            {
              id: "unit_price",
              header: ({ column }) => (
                <DataGridColumnHeader
                  title="U/P *"
                  column={column}
                />
              ),
              cell: ({ row }) => (
                <FormField
                  control={form.control}
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
                            if (v !== null && !Number.isNaN(v) && qty != null && qty !== "") {
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
              meta: { headerTitle: "U/P" },
            },
            {
              id: "total",
              header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
              cell: ({ row }) => (
                <FormField
                  control={form.control}
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
              meta: { headerTitle: "Total" },
            },
          ]
        : [];

    const tail: ColumnDef<FieldArrayWithId<PurchaseRequestSchemaType, "products", "id">>[] = [
      {
        id: "remark",
        header: ({ column }) => <DataGridColumnHeader title="Remark" column={column} />,
        cell: ({ row }) => (
          <FormField
            control={form.control}
            name={`products.${row.index}.remark`}
            render={({ field: f }) => (
              <FormItem>
                <FormControl>
                  <Input placeholder="Remark" {...f} value={f.value ?? ""} className="h-8" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        ),
        size: 200,
        meta: { headerTitle: "Remark" },
      },
      {
        id: "actions",
        header: () => <span className="sr-only">Delete</span>,
        // SF-6 (M5 run 3 review): `fields.length` read off the TABLE, not the
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
            aria-label="Delete"
          >
            <Trash2 className="size-4 text-destructive" />
          </Button>
        ),
        size: 56,
        enableResizing: false,
        meta: { headerTitle: "Delete" },
      },
    ];

    return [...base, ...pricing, ...tail];
  }, [form, isSponsorship, remove]);

  const createModeLinesTable = useReactTable({
    columns: createModeLineColumns,
    data: fields,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: "onChange",
  });

  const [formInitialized, setFormInitialized] = useState(false);

  useEffect(() => {
    if (request && isEditMode && !formInitialized) {
      const products =
        (request.lines?.length ?? 0) > 0
          ? request.lines!.map((l) => ({
              item_code: l.item_code ?? null,
              quantity: l.quantity != null ? Number(l.quantity) : null,
              remark: l.remark ?? null,
              unit_price: l.unit_price != null ? Number(l.unit_price) : null,
              total: l.total != null ? Number(l.total) : null,
            }))
          : [
              {
                item_code: null,
                quantity: null,
                remark: null,
                unit_price: null,
                total: null,
              },
            ];
      form.reset({
        request_type: (request.request_type ?? "purchase_request") as
          | "purchase_request"
          | "sponsorship_form",
        request_number: request.request_number ?? null,
        request_date: request.request_date
          ? new Date(request.request_date).toISOString().split("T")[0]
          : null,
        customer_name: request.customer_name ?? null,
        customer_id: request.customer_id ?? null,
        pic: request.pic ?? null,
        project_title: request.project_title ?? null,
        project_id: request.project_id ?? null,
        purpose: request.purpose ?? null,
        delivery_address: request.delivery_address ?? null,
        total_project_value:
          request.total_project_value != null
            ? Number(request.total_project_value)
            : null,
        total_project_value_text: request.total_project_value_text ?? null,
        sales_type: request.sales_type ?? null,
        sponsor_subject: request.sponsor_subject ?? null,
        sponsor_subject_other: request.sponsor_subject_other ?? null,
        expected_delivery_date: request.expected_delivery_date
          ? new Date(request.expected_delivery_date).toISOString().split("T")[0]
          : null,
        expected_po_date: request.expected_po_date
          ? new Date(request.expected_po_date).toISOString().split("T")[0]
          : null,
        expected_po_date_text: request.expected_po_date_text ?? null,
        requested_by: request.requested_by ?? null,
        requested_by_contact_id: request.requested_by_contact_id ?? null,
        requested_at: request.requested_at
          ? new Date(request.requested_at).toISOString().split("T")[0]
          : null,
        products,
      });
      setFormInitialized(true);
      // An intake line can arrive with a NULL quantity: flag it now, not only on Update.
      const blank = products
        .map((l, i) => (l.quantity == null ? i : -1))
        .filter((i) => i >= 0)
        .map((i) => `products.${i}.quantity` as const);
      if (blank.length) void form.trigger(blank);
    }
  }, [request, isEditMode, form, formInitialized]);

  useEffect(() => {
    setFormInitialized(false);
  }, [requestId]);

  // The ONE body Save and Update & Reply both send, so neither can drop a field.
  const buildFormData = (data: PurchaseRequestSchemaType): PurchaseRequestFormData => {
    return {
        request_type: data.request_type,
        request_number: data.request_number ?? undefined,
        request_date: data.request_date || undefined,
        customer_name: data.customer_name || undefined,
        customer_id: data.customer_id || null,
        // Empty string must become undefined, not "" - "not given" is one value.
        pic: data.pic || undefined,
        project_title: data.project_title || undefined,
        project_id: data.project_id || null,
        purpose: data.purpose ?? undefined,
        delivery_address: data.delivery_address ?? undefined,
        total_project_value:
          data.total_project_value != null && data.total_project_value !== ""
            ? Number(data.total_project_value)
            : undefined,
        total_project_value_text: data.total_project_value_text?.trim()
          ? data.total_project_value_text.trim()
          : undefined,
        // A cleared (optional, SF) select gives ''; send none rather than an empty code.
        sales_type: data.sales_type || undefined,
        sponsor_subject: data.sponsor_subject ?? undefined,
        // Only carry the "Others" detail when the sponsor subject is 'others';
        // sending '' otherwise clears any stale parked text on the backend.
        sponsor_subject_other:
          isSponsorship && data.sponsor_subject === "others"
            ? data.sponsor_subject_other?.trim() || undefined
            : isSponsorship
              ? ""
              : undefined,
        expected_delivery_date: data.expected_delivery_date || undefined,
        expected_po_date: data.expected_po_date || undefined,
        expected_po_date_text: data.expected_po_date_text || undefined,
        requested_by: data.requested_by || undefined,
        requested_by_contact_id: data.requested_by_contact_id ?? null,
        requested_at: data.requested_at || undefined,
        products: data.products
          .filter((p) =>
            lineHasContent(
              isSponsorship ? p : { item_code: p.item_code, quantity: p.quantity },
            ),
          )
          .map((p) => ({
            item_code: p.item_code ?? undefined,
            quantity:
              p.quantity != null && p.quantity !== ""
                ? Number(p.quantity)
                : undefined,
            remark: p.remark ?? undefined,
            unit_price:
              p.unit_price != null && p.unit_price !== ""
                ? Number(p.unit_price)
                : undefined,
            total:
              p.total != null && p.total !== "" ? Number(p.total) : undefined,
          })),
      };
  };

  const onSubmit = async (data: PurchaseRequestSchemaType) => {
    try {
      const formData = buildFormData(data);

      if (isEditMode && requestId) {
        await updateMutation.mutateAsync({ id: requestId, data: formData });
      } else {
        await createMutation.mutateAsync(formData);
      }
      if (successRedirectUrl) {
        router.push(successRedirectUrl);
      }
    } catch (error) {
      console.error("Error submitting form:", error);
    }
  };

  if (isEditMode && isLoadingRequest) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        {isEditMode && request ? (
          <PurchaseRequestDocumentEditCard
            form={form}
            request={request}
            isSponsorship={isSponsorship}
            showTypeSelect
            fields={fields}
            append={append}
            remove={remove}
            sponsorshipLineGrandTotal={sponsorshipLineGrandTotal}
          />
        ) : (
          <div className="space-y-6">
            <Card className={cn(isSponsorship && "border-2 shadow-sm")}>
              <CardHeader>
                {isSponsorship ? (
                  <CardTitle className="text-center text-xl font-semibold border-b border-border pb-4">
                    Project Sales Sponsorship Form
                  </CardTitle>
                ) : (
                  <CardTitle>Header</CardTitle>
                )}
              </CardHeader>
              <CardContent className="space-y-4">
                <FormField
                  control={form.control}
                  name="request_number"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {purchaseRequestNumberFieldLabel(requestType)}
                      </FormLabel>
                      <FormControl>
                        <Input
                          placeholder="e.g. PR26-0303"
                          {...field}
                          value={field.value ?? ""}
                          onChange={(e) =>
                            field.onChange(e.target.value || null)
                          }
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {defaultRequestType !== "sponsorship_form" && (
                  <FormField
                    control={form.control}
                    name="request_type"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Type</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            onChange={field.onChange}
                            value={field.value ?? ""}
                            placeholder="Select type"
                            options={[
                              {
                                value: "purchase_request",
                                label: "Purchase Request",
                              },
                              {
                                value: "sponsorship_form",
                                label: "Sponsorship Form",
                              },
                            ]}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
                <RequestCustomerField form={form} label="Customer Name" />
                <FormField
                  control={form.control}
                  name="pic"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>PIC</FormLabel>
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
                {isSponsorship && (
                  <FormField
                    control={form.control}
                    name="delivery_address"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Delivery Address</FormLabel>
                        <FormControl>
                          <Textarea
                            placeholder="Delivery address"
                            rows={4}
                            className="resize-y min-h-[80px]"
                            {...field}
                            value={field.value ?? ""}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
                <FormField
                  control={form.control}
                  name="project_title"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Project Title</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Project title"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {/* AC-L3: the office-side project picker. The portal has offered contacts
                    the same link since S4; without it here a CS-entered form could never be
                    linked, which is most of the historical backlog. */}
                <FormField
                  control={form.control}
                  name="project_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Registered project (optional)</FormLabel>
                      <SearchableSelect
                        value={field.value || ""}
                        onChange={(next) => field.onChange(next || null)}
                        fetchOptions={(query, pageIndex) =>
                          searchProjectsForLink(query, pageIndex + 1)
                        }
                        paginated
                        pageSize={25}
                        clearable
                        placeholder="Search a project by code or title"
                        emptyMessage="No project matches that. Leave it empty if it was never registered."
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {!isSponsorship && (
                  <FormField
                    control={form.control}
                    name="purpose"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Purpose (showroom/mock up/others)</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g. Showroom, Mock up, Others"
                            {...field}
                            value={field.value ?? ""}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
                {isSponsorship && (
                  <>
                    <FormField
                      control={form.control}
                      name="total_project_value"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Total Project Value</FormLabel>
                          <FormControl>
                            <NumberInput
                              inputMode="decimal"
                              step="0.01"
                              placeholder="e.g. 1234.00"
                              {...field}
                              value={field.value ?? ""}
                              onChange={(e) =>
                                field.onChange(
                                  e.target.value === "" ? null : e.target.value,
                                )
                              }
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="sponsor_subject"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Sponsor Subject</FormLabel>
                          <FormControl>
                            <LookupBoundField
                              table="purchase_requests"
                              column="sponsor_subject"
                              value={field.value}
                              onChange={field.onChange}
                              placeholder="Select sponsor subject"
                              renderFallback={() => (
                                <SearchableSelect
                                  key={field.value || "empty"}
                                  onChange={field.onChange}
                                  value={field.value || ""}
                                  placeholder="Select sponsor subject"
                                  options={[
                                    { value: "showroom", label: "Showroom" },
                                    { value: "mockup", label: "Mockup" },
                                    { value: "others", label: "Others" },
                                  ]}
                                />
                              )}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    {form.watch("sponsor_subject") === "others" && (
                      <FormField
                        control={form.control}
                        name="sponsor_subject_other"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Please specify</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Specify the sponsor subject"
                                {...field}
                                value={field.value ?? ""}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    )}
                  </>
                )}
                {/* Required on a purchase request (it routes CS); kept, optional, on a sponsorship
                    form (SF-SALES-TYPE). After Purpose on a PR, after Sponsor Subject on an SF. */}
                <FormField
                  control={form.control}
                  name="sales_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        Sales Type{" "}
                        {!isSponsorship && <span className="text-destructive">*</span>}
                      </FormLabel>
                      <FormControl>
                        <LookupBoundField
                          table="purchase_requests"
                          column="sales_type"
                          value={field.value}
                          onChange={field.onChange}
                          placeholder="Select sales type"
                          clearable={isSponsorship}
                          renderFallback={() => (
                            <SearchableSelect
                              key={field.value || "empty"}
                              onChange={field.onChange}
                              value={field.value || ""}
                              placeholder="Select sales type"
                              clearable={isSponsorship}
                              options={[
                                { value: "project", label: "Project" },
                                { value: "cash_sales", label: "Cash Sales" },
                              ]}
                            />
                          )}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="expected_delivery_date"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {isSponsorship
                          ? "Date of Delivery"
                          : "Expected date of delivery"}
                      </FormLabel>
                      <FormControl>
                        <Input
                          type="date"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {!isSponsorship && (
                  <FormField
                    control={form.control}
                    name="expected_po_date"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Expected date to receive PO</FormLabel>
                        <FormControl>
                          <Input
                            type="date"
                            {...field}
                            value={field.value ?? ""}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
                {!isSponsorship && (
                  <FormField
                    control={form.control}
                    name="expected_po_date_text"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          Expected date to receive PO (free text)
                        </FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g. PROPOSED STAGE (IMMEDIATE ORDER)"
                            {...field}
                            value={field.value ?? ""}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}
                <FormField
                  control={form.control}
                  name="requested_by_contact_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Requested By</FormLabel>
                      <FormControl>
                        <RequestorContactSelect
                          value={field.value}
                          onChange={field.onChange}
                          submitterContactId={request?.contact_id}
                          savedContactId={request?.requested_by_contact_id}
                          savedContactName={request?.requested_by_contact_name ?? request?.requested_by}
                          placeholder="Select requestor"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className={cn(isSponsorship && "border-2 shadow-sm")}>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>
                  {isSponsorship ? "Line items" : "Line Items"}
                </CardTitle>
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
              </CardHeader>
              <CardContent>
                <DataGrid
                  table={createModeLinesTable}
                  recordCount={fields.length}
                  listingKey="procurement.purchase_requests.view::create-lines"
                  tableLayout={{ width: "fixed", columnsResizable: true }}
                >
                  <DataGridTable />
                </DataGrid>
                {isSponsorship && (
                  <div className="mt-3 flex justify-end border-t border-border pt-3">
                    <p className="text-sm font-semibold tabular-nums">
                      Grand Total: {sponsorshipLineGrandTotal.toLocaleString()}
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {isEditMode && requestId && (
          <PurchaseRequestAttachmentsSection
            requestId={requestId}
            attachments={request?.attachments}
          />
        )}

        <div className="flex justify-end gap-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
            disabled={isLoading}
          >
            Cancel
          </Button>
          {isEditMode && (
            <Button
              type="button"
              variant="secondary"
              onClick={async () => {
                const valid = await form.trigger();
                if (!valid || !requestId) return;
                const values = form.getValues();
                const typeLabel =
                  REQUEST_TYPE_LABELS[values.request_type ?? ""] ??
                  values.request_type ??
                  "Request";
                // Form state holds the bare number (a suffix there would be
                // submitted back); the message quotes it with the revision
                // suffix the screens show.
                const idPhrase = purchaseRequestNumberReplyPhrase(
                  values.request_type,
                  values.request_number,
                  request?.revision_no,
                );
                let defaultReply = `This is the ${idPhrase} for ${typeLabel} for project title ${values.project_title ?? ""}.`;
                if (publicViewLinksEnabled) {
                  try {
                    const baseUrl =
                      typeof window !== "undefined"
                        ? window.location.origin
                        : undefined;
                    const { view_url } = await getOrCreateViewLink(
                      requestId,
                      baseUrl,
                    );
                    if (view_url)
                      defaultReply += `\n\nView full details: ${view_url}`;
                  } catch {
                    // leave message as-is, user can add link
                  }
                }
                setReplyMessage(defaultReply);
                setUpdateAndReplyDialogOpen(true);
              }}
              disabled={isLoading || updateAndReplyMutation.isPending}
            >
              {updateAndReplyMutation.isPending ? (
                <>
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  Sending…
                </>
              ) : (
                <>
                  <Send className="size-4" />
                  Update & Reply
                </>
              )}
            </Button>
          )}
          <Button type="submit" disabled={isLoading}>
            {isLoading ? (
              <>
                <LoaderCircleIcon className="size-4 animate-spin" />
                {isEditMode ? "Updating..." : "Creating..."}
              </>
            ) : (
              <>
                <Save className="size-4" />
                {isEditMode ? "Update" : "Create"}
              </>
            )}
          </Button>
        </div>
      </form>

      {isEditMode && (
        <Dialog
          open={updateAndReplyDialogOpen}
          onOpenChange={setUpdateAndReplyDialogOpen}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Update & Reply</DialogTitle>
              <DialogDescription>
                This message will be sent to the conversation in Respond. It is
                pre-filled for this{" "}
                {form.watch("request_type") === "sponsorship_form"
                  ? "Sponsorship Form"
                  : "Purchase Request"}
                . You can edit it below. A shareable view link is included only
                when the Public view links module is enabled in App Store.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="pr-reply_message">Message to send</Label>
                <Textarea
                  id="pr-reply_message"
                  value={replyMessage}
                  onChange={(e) => setReplyMessage(e.target.value)}
                  placeholder="This is the form number ... for Purchase Request / Sponsorship Form for project title ..."
                  rows={4}
                  className="resize-none"
                />
                {publicViewLinksEnabled && requestId && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-1"
                    disabled={
                      replyViewLinkLoading || updateAndReplyMutation.isPending
                    }
                    onClick={async () => {
                      setReplyViewLinkLoading(true);
                      try {
                        const baseUrl =
                          typeof window !== "undefined"
                            ? window.location.origin
                            : undefined;
                        const { view_url } = await getOrCreateViewLink(
                          requestId,
                          baseUrl,
                        );
                        if (view_url) {
                          const line = `View full details: ${view_url}`;
                          setReplyMessage((prev) =>
                            prev.trim() ? `${prev.trim()}\n\n${line}` : line,
                          );
                        }
                      } catch {
                        toast.error("Could not get view link");
                      } finally {
                        setReplyViewLinkLoading(false);
                      }
                    }}
                  >
                    <Link2 className="size-4 mr-1" />
                    {replyViewLinkLoading
                      ? "Getting link…"
                      : "Attach view link"}
                  </Button>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setUpdateAndReplyDialogOpen(false)}
                disabled={updateAndReplyMutation.isPending}
              >
                Cancel
              </Button>
              <Button
                disabled={
                  updateAndReplyMutation.isPending || !replyMessage.trim()
                }
                onClick={async () => {
                  if (!requestId) return;
                  try {
                    const values = form.getValues();
                    await updateAndReplyMutation.mutateAsync({
                      id: requestId,
                      data: {
                        formData: buildFormData(values),
                        reply_message: replyMessage.trim(),
                      },
                    });
                    setUpdateAndReplyDialogOpen(false);
                    setReplyMessage("");
                    toast.success("Updated and reply sent");
                    if (successRedirectUrl) router.push(successRedirectUrl);
                  } catch {
                    // toast from mutation
                  }
                }}
              >
                {updateAndReplyMutation.isPending
                  ? "Sending…"
                  : "Update & Reply"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Form>
  );
}
