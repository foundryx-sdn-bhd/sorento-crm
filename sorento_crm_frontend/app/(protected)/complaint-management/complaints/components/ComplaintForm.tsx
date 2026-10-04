"use client";

import { useEffect, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useFieldArray } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { LoaderCircleIcon, Save, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SearchableSelect } from "@/components/common/SearchableSelect";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import LookupBoundField from "@/components/common/LookupBoundField";
import {
  useCreateComplaint,
  useUpdateComplaint,
  useUpdateComplaintAndReply,
  useComplaint,
} from "../hooks/useComplaints";
import { useComplaintRootCausesSelect } from "@/app/(protected)/complaint-management/complaint-root-causes/hooks/useComplaintRootCauses";
import { useComplaintResolutionsSelect } from "@/app/(protected)/complaint-management/complaint-resolutions/hooks/useComplaintResolutions";
import {
  getOrCreateComplaintViewLink,
  displayComplaintTechnicalResponse,
  searchProjectsForLink,
} from "../services/complaintService";
import { toast } from '@/lib/toast';
import {
  ComplaintSchema,
  type ComplaintSchemaType,
} from "../forms/complaint-schema";
import type {
  ComplaintFormData,
  ComplaintAttachment,
} from "../types/complaint.types";
import ListPager from '@/components/common/ListPager';
import { complaintsPagerQuery } from '../hooks/useComplaints';
import ComplaintManualAttachmentsSection from "./ComplaintManualAttachmentsSection";
import { usePublicViewLinksEnabled } from "@/hooks/usePublicViewLinksEnabled";

interface ComplaintFormProps {
  complaintId?: string;
  onSuccess?: () => void;
}

type ProductLineForm = {
  product_code: string;
  product_type?: string | null;
  quantity?: string | null;
};

/** Prefer structured product_lines; fall back to splitting the legacy
 * product_code CSV (pairing type/quantity by position) for older rows. */
function deriveProductLines(complaint: {
  product_lines?:
    | {
        product_code: string;
        product_type?: string | null;
        quantity?: string | null;
      }[]
    | null;
  product_code?: string | null;
  product_type?: string | null;
  quantity?: string | null;
}): ProductLineForm[] {
  if (complaint.product_lines && complaint.product_lines.length > 0) {
    return complaint.product_lines.map((l) => ({
      product_code: l.product_code,
      product_type: l.product_type ?? "",
      quantity: l.quantity ?? "",
    }));
  }
  const codes = (complaint.product_code || "").split(",").map((s) => s.trim());
  const types = (complaint.product_type || "").split(",").map((s) => s.trim());
  const qtys = (complaint.quantity || "").split(",").map((s) => s.trim());
  return codes
    .map((code, i) => ({
      product_code: code,
      product_type: types[i] ?? "",
      quantity: qtys[i] ?? "",
    }))
    .filter((l) => l.product_code);
}

/** Drop blank rows; trim. Returns undefined when no valid rows. */
function cleanProductLines(
  lines: ProductLineForm[] | undefined,
):
  | { product_code: string; product_type?: string; quantity?: string }[]
  | undefined {
  const cleaned = (lines || [])
    .map((l) => ({
      product_code: (l.product_code || "").trim(),
      product_type: (l.product_type ?? "").trim() || undefined,
      quantity: (l.quantity ?? "").trim() || undefined,
    }))
    .filter((l) => l.product_code);
  return cleaned.length > 0 ? cleaned : undefined;
}

export default function ComplaintForm({
  complaintId,
  onSuccess,
}: ComplaintFormProps) {
  const router = useRouter();
  const isEditMode = !!complaintId;
  const { data: complaint, isLoading: isLoadingComplaint } = useComplaint(
    complaintId || null,
  );
  const createMutation = useCreateComplaint();
  const updateMutation = useUpdateComplaint();

  const form = useForm<ComplaintSchemaType>({
    resolver: zodResolver(ComplaintSchema),
    defaultValues: {
      delivery_order_number: null,
      complaint_date: null,
      customer_type: null,
      customer_type_others: null,
      within_warranty: null,
      product_type: null,
      defects_discovered: null,
      complaint_type: null,
      defect_description: null,
      product_code: null,
      quantity: null,
      product_lines: [],
      salesperson: null,
      customer_name: null,
      contact_person: null,
      contact_number: null,
      customer_address: null,
      project_title: null,
      project_id: null,
      contact_id: null,
      space_id: null,
      technical_team_response: null,
      root_cause_id: null,
      resolution_id: null,
      required_on_site_support: false,
      attachments: [],
    },
    mode: 'onTouched',
  });

  const productLines = useFieldArray({
    control: form.control,
    name: "product_lines",
  });

  const [formInitialized, setFormInitialized] = useState(false);
  const updateAndReplyMutation = useUpdateComplaintAndReply();
  const publicViewLinksEnabled = usePublicViewLinksEnabled();
  const {
    data: rootCauseOptions = [],
    error: rootCauseError,
    refetch: refetchRootCauses,
  } = useComplaintRootCausesSelect();
  const {
    data: resolutionOptions = [],
    error: resolutionError,
    refetch: refetchResolutions,
  } = useComplaintResolutionsSelect();

  // Load complaint data when editing (normalize dates so schema validation passes)
  useEffect(() => {
    if (complaint && isEditMode && !formInitialized) {
      const toDate = (v: unknown): Date | undefined => {
        if (!v) return undefined;
        if (v instanceof Date) return v;
        const d = new Date(v as string);
        return Number.isNaN(d.getTime()) ? undefined : d;
      };
      const toNum = (v: unknown): number | null | undefined => {
        if (v === null || v === undefined || v === "")
          return v as null | undefined;
        const n = typeof v === "number" ? v : Number(v);
        return Number.isFinite(n) ? n : null;
      };
      const attachments = (complaint.attachments || []).map((att) => ({
        ...att,
        file_size_bytes: toNum(att.file_size_bytes) ?? null,
        uploaded_at: toDate(att.uploaded_at),
        created_at: toDate((att as { created_at?: unknown }).created_at),
      }));
      form.reset({
        delivery_order_number: complaint.delivery_order_number || null,
        complaint_date: complaint.complaint_date
          ? complaint.complaint_date instanceof Date
            ? complaint.complaint_date
            : new Date(complaint.complaint_date as unknown as string)
          : null,
        customer_type: complaint.customer_type || null,
        customer_type_others: complaint.customer_type_others || null,
        within_warranty: complaint.within_warranty || null,
        product_type: complaint.product_type || null,
        defects_discovered: complaint.defects_discovered || null,
        complaint_type: complaint.complaint_type || null,
        defect_description: complaint.defect_description || null,
        product_code: complaint.product_code || null,
        quantity: complaint.quantity || null,
        product_lines: deriveProductLines(complaint),
        salesperson: complaint.salesperson || null,
        customer_name: complaint.customer_name || null,
        contact_person: complaint.contact_person || null,
        contact_number: complaint.contact_number || null,
        customer_address: complaint.customer_address || null,
        project_title: complaint.project_title || null,
        project_id: complaint.project_id || null,
        contact_id: complaint.contact_id ?? null,
        space_id: complaint.space_id ?? null,
        technical_team_response:
          displayComplaintTechnicalResponse(
            complaint.technical_team_response,
          ) || null,
        root_cause_id: complaint.root_cause_id ?? null,
        resolution_id: complaint.resolution_id ?? null,
        required_on_site_support: complaint.required_on_site_support ?? false,
        attachments,
      });
      setFormInitialized(true);
    }
  }, [complaint, isEditMode, form, formInitialized]);

  useEffect(() => {
    setFormInitialized(false);
  }, [complaintId]);

  const onSubmit = async (data: ComplaintSchemaType) => {
    try {
      // Transform attachments to match ComplaintAttachment type
      // Filter out attachments that don't have required fields (id, complaint_id, uploaded_at)
      const validAttachments = (data.attachments || [])
        .filter(
          (att): att is ComplaintAttachment =>
            !!att.id && !!att.complaint_id && !!att.uploaded_at,
        )
        .map((att) => ({
          id: att.id!,
          complaint_id: att.complaint_id!,
          file_name: att.file_name ?? null,
          file_url: att.file_url ?? null,
          file_size_bytes: att.file_size_bytes ?? null,
          uploaded_at: att.uploaded_at!,
        }));

      const formData: ComplaintFormData = {
        delivery_order_number: data.delivery_order_number || undefined,
        complaint_date: data.complaint_date || undefined,
        customer_type: data.customer_type || undefined,
        customer_type_others: data.customer_type_others || undefined,
        within_warranty: data.within_warranty || undefined,
        defects_discovered: data.defects_discovered || undefined,
        complaint_type: data.complaint_type || undefined,
        defect_description: data.defect_description || undefined,
        product_lines: cleanProductLines(data.product_lines),
        salesperson: data.salesperson || undefined,
        customer_name: data.customer_name || undefined,
        contact_person: data.contact_person || undefined,
        contact_number: data.contact_number || undefined,
        customer_address: data.customer_address || undefined,
        project_title: data.project_title || undefined,
        project_id: data.project_id || null,
        ...(isEditMode
          ? {}
          : {
              contact_id: data.contact_id || undefined,
              space_id: data.space_id || undefined,
            }),
        technical_team_response: data.technical_team_response || undefined,
        root_cause_id: data.root_cause_id || null,
        resolution_id: data.resolution_id || null,
        required_on_site_support: data.required_on_site_support ?? false,
        attachments: validAttachments.length > 0 ? validAttachments : undefined,
      };

      if (isEditMode && complaintId) {
        await updateMutation.mutateAsync({ id: complaintId, data: formData });
      } else {
        const created = await createMutation.mutateAsync(formData);
        if (created?.id && publicViewLinksEnabled) {
          try {
            const baseUrl =
              typeof window !== "undefined"
                ? window.location.origin
                : undefined;
            const { view_url } = await getOrCreateComplaintViewLink(
              created.id,
              baseUrl,
            );
            await navigator.clipboard.writeText(view_url);
            toast.success("Complaint created. View link copied to clipboard.");
          } catch {
            // view link optional
          }
        }
      }

      onSuccess?.();
    } catch (error) {
      // Error is handled by the mutation
      console.error("Error submitting form:", error);
    }
  };

  const handleUpdateAndReply = async () => {
    if (!complaintId) return;
    const valid = await form.trigger();
    if (!valid) {
      const firstError = Object.values(form.formState.errors)[0];
      toast.error(
        firstError?.message ??
          "Please fix the errors in the form before updating.",
      );
      return;
    }
    const data = form.getValues();
    const technicalResponse = displayComplaintTechnicalResponse(
      (data.technical_team_response ?? "").trim(),
    ).trim();
    if (!technicalResponse) {
      toast.error("Enter a technical team response before sending.");
      return;
    }
    const validAttachments = (data.attachments || [])
      .filter(
        (att): att is ComplaintAttachment =>
          !!att.id && !!att.complaint_id && !!att.uploaded_at,
      )
      .map((att) => ({
        id: att.id!,
        complaint_id: att.complaint_id!,
        file_name: att.file_name ?? null,
        file_url: att.file_url ?? null,
        file_size_bytes: att.file_size_bytes ?? null,
        uploaded_at: att.uploaded_at!,
      }));
    const formData: ComplaintFormData = {
      delivery_order_number: data.delivery_order_number || undefined,
      complaint_date: data.complaint_date || undefined,
      customer_type: data.customer_type || undefined,
      customer_type_others: data.customer_type_others || undefined,
      within_warranty: data.within_warranty || undefined,
      defects_discovered: data.defects_discovered || undefined,
      complaint_type: data.complaint_type || undefined,
      defect_description: data.defect_description || undefined,
      product_lines: cleanProductLines(data.product_lines),
      salesperson: data.salesperson || undefined,
      customer_name: data.customer_name || undefined,
      contact_person: data.contact_person || undefined,
      contact_number: data.contact_number || undefined,
      customer_address: data.customer_address || undefined,
      project_title: data.project_title || undefined,
      project_id: data.project_id || null,
      technical_team_response: technicalResponse,
      required_on_site_support: data.required_on_site_support ?? true,
      attachments: validAttachments.length > 0 ? validAttachments : undefined,
    };
    try {
      await updateAndReplyMutation.mutateAsync({
        id: complaintId,
        data: formData,
      });
      onSuccess?.();
    } catch {
      // Error toast from mutation
    }
  };

  if (isEditMode && isLoadingComplaint) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading =
    createMutation.isPending ||
    updateMutation.isPending ||
    updateAndReplyMutation.isPending;

  const onFormSubmit = form.handleSubmit(onSubmit, (errors) => {
    // eslint-disable-next-line no-console
    console.error("[ComplaintForm] validation errors", errors);
    const flat: string[] = [];
    const walk = (node: unknown, path: string) => {
      if (!node || typeof node !== "object") return;
      const obj = node as Record<string, unknown>;
      const msg = obj.message;
      if (typeof msg === "string" && msg.trim()) {
        flat.push(`${path || "form"}: ${msg}`);
        return;
      }
      for (const [k, v] of Object.entries(obj)) {
        if (k === "ref" || k === "type") continue;
        walk(v, path ? `${path}.${k}` : k);
      }
    };
    walk(errors, "");
    const summary = flat.length
      ? flat.slice(0, 5).join("; ")
      : "See console for details";
    toast.error(`Please fix: ${summary}`);
    const firstField = Object.keys(errors)[0];
    if (firstField && typeof window !== "undefined") {
      const el = document.querySelector(
        `[name="${firstField}"]`,
      ) as HTMLElement | null;
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.focus?.();
    }
  });

  return (
    <Form {...form}>
      <form onSubmit={onFormSubmit} className="space-y-6">
        {isEditMode && complaintId && (
          <div className="flex justify-end">
            <ListPager
              {...complaintsPagerQuery}
              detailPath="/complaint-management/complaints"
              currentId={complaintId}
              ariaLabel="complaint"
              hrefFor={(id, search) =>
                `/complaint-management/complaints/${id}/edit${search ? `?${search}` : ''}`
              }
            />
          </div>
        )}
        <Card>
          <CardHeader>
            <CardTitle>Complaint Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <FormField
              control={form.control}
              name="delivery_order_number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Delivery Order Number</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter DO number"
                      {...field}
                      value={field.value || ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="customer_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Customer Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter customer name"
                      {...field}
                      value={field.value || ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="contact_person"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Contact Person</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter contact person name"
                      {...field}
                      value={field.value || ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="contact_number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Contact Number</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter contact number"
                      {...field}
                      value={field.value || ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="customer_address"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Delivery Address</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Enter delivery address"
                      {...field}
                      value={field.value || ""}
                      rows={3}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="customer_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Customer Type</FormLabel>
                  <FormControl>
                    <LookupBoundField
                      table="complaints"
                      column="customer_type"
                      value={field.value}
                      onChange={field.onChange}
                      placeholder="Select customer type"
                      renderFallback={() => (
                        <SearchableSelect
                          key={field.value || "empty"}
                          onChange={field.onChange}
                          value={field.value || ""}
                          options={[
                            { value: "Individual", label: "Individual" },
                            { value: "Dealer", label: "Dealer" },
                            { value: "Corporate", label: "Corporate" },
                            { value: "Government", label: "Government" },
                            { value: "Other", label: "Other" },
                          ]}
                          placeholder="Select customer type"
                        />
                      )}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {form.watch("customer_type")?.toLowerCase() === "other" && (
              <FormField
                control={form.control}
                name="customer_type_others"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Customer Type (Other)</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Specify customer type"
                        {...field}
                        value={field.value || ""}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="complaint_date"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Complaint Date</FormLabel>
                  <FormControl>
                    <Input
                      type="date"
                      {...field}
                      value={
                        field.value
                          ? new Date(field.value).toISOString().split("T")[0]
                          : ""
                      }
                      onChange={(e) => {
                        const date = e.target.value
                          ? new Date(e.target.value)
                          : null;
                        field.onChange(date);
                      }}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormItem className="md:col-span-2">
              <FormLabel>Products</FormLabel>
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="w-10 px-2 py-2 text-left">#</th>
                      <th className="min-w-[200px] px-2 py-2 text-left">
                        Product code
                      </th>
                      <th className="min-w-[160px] px-2 py-2 text-left">
                        Product type
                      </th>
                      <th className="w-24 px-2 py-2 text-left">Qty</th>
                      <th className="w-10 px-2 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {productLines.fields.length === 0 && (
                      <tr>
                        <td
                          colSpan={5}
                          className="px-3 py-4 text-center text-muted-foreground"
                        >
                          No products yet. Click “Add product”.
                        </td>
                      </tr>
                    )}
                    {productLines.fields.map((row, index) => (
                      <tr key={row.id} className="border-t align-top">
                        <td className="px-2 py-2 text-muted-foreground">
                          {index + 1}
                        </td>
                        <td className="px-2 py-2">
                          <FormField
                            control={form.control}
                            name={`product_lines.${index}.product_code`}
                            render={({ field }) => (
                              <FormItem>
                                <FormControl>
                                  <Input
                                    placeholder="Product code"
                                    {...field}
                                    value={field.value || ""}
                                  />
                                </FormControl>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        </td>
                        <td className="px-2 py-2">
                          <FormField
                            control={form.control}
                            name={`product_lines.${index}.product_type`}
                            render={({ field }) => (
                              <FormItem>
                                <FormControl>
                                  <Input
                                    placeholder="Type"
                                    {...field}
                                    value={field.value || ""}
                                  />
                                </FormControl>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        </td>
                        <td className="px-2 py-2">
                          <FormField
                            control={form.control}
                            name={`product_lines.${index}.quantity`}
                            render={({ field }) => (
                              <FormItem>
                                <FormControl>
                                  <Input
                                    type="number"
                                    inputMode="numeric"
                                    min="0"
                                    placeholder="Qty"
                                    {...field}
                                    value={field.value || ""}
                                  />
                                </FormControl>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        </td>
                        <td className="px-2 py-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={() => productLines.remove(index)}
                            aria-label="Remove product"
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() =>
                  productLines.append({
                    product_code: "",
                    product_type: "",
                    quantity: "",
                  })
                }
              >
                <Plus className="size-4 mr-1" />
                Add product
              </Button>
            </FormItem>

            <FormField
              control={form.control}
              name="within_warranty"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Within Warranty</FormLabel>
                  <FormControl>
                    <LookupBoundField
                      table="complaints"
                      column="within_warranty"
                      value={field.value}
                      onChange={field.onChange}
                      placeholder="Select warranty status"
                      renderFallback={() => (
                        <SearchableSelect
                          key={field.value || "empty"}
                          onChange={field.onChange}
                          value={field.value || ""}
                          options={[
                            { value: "Yes", label: "Yes" },
                            { value: "No", label: "No" },
                            { value: "Unknown", label: "Unknown" },
                          ]}
                          placeholder="Select warranty status"
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
              name="defects_discovered"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Defects Discovered</FormLabel>
                  <FormControl>
                    <LookupBoundField
                      table="complaints"
                      column="defects_discovered"
                      value={field.value}
                      onChange={field.onChange}
                      placeholder="Select defect"
                      renderFallback={() => (
                        <Input
                          placeholder="Enter defects discovered"
                          {...field}
                          value={field.value || ""}
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
              name="complaint_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Complaint Type</FormLabel>
                  <FormControl>
                    <LookupBoundField
                      table="complaints"
                      column="complaint_type"
                      value={field.value}
                      onChange={field.onChange}
                      placeholder="Select complaint type"
                      renderFallback={() => (
                        <Input
                          placeholder="Enter complaint type"
                          {...field}
                          value={field.value || ""}
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
              name="defect_description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Defect Description</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Enter detailed defect description"
                      {...field}
                      value={field.value || ""}
                      rows={4}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="salesperson"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Salesperson</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter salesperson name"
                      {...field}
                      value={field.value || ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="project_title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Project Title</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter project title"
                      {...field}
                      value={field.value || ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* AC-L3. The link sits next to the free-text title rather than replacing it: the
                text is what thousands of historical complaints have, and typing a development
                name is still faster than picking when the project was never registered. */}
            <FormField
              control={form.control}
              name="project_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Registered project (optional)</FormLabel>
                  {/* No FormControl wrapper: it is a Radix Slot and needs exactly one element
                      child, while SearchableSelect renders a trigger plus its popover. Found in
                      the browser -- "React.Children.only expected to receive a single React
                      element child" took the whole card down, so the field never appeared. */}
                  <SearchableSelect
                    value={field.value || ""}
                    onChange={(next) => field.onChange(next || null)}
                    fetchOptions={(query, pageIndex) =>
                      searchProjectsForLink(query, pageIndex + 1)
                    }
                    paginated
                    pageSize={25}
                    clearable
                    selectedOption={
                      field.value && complaint?.project_code
                        ? {
                            value: field.value,
                            label: `${complaint.project_code} - ${complaint.project_name ?? ""}`,
                          }
                        : undefined
                    }
                    placeholder="Search a project by code or title"
                    emptyMessage="No project matches that. Leave it empty if it was never registered."
                  />
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tecnical Team</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!isEditMode && (
              <>
                <FormField
                  control={form.control}
                  name="contact_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Contact ID</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Respond.io contact ID"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="space_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Space ID</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Respond.io space ID"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </>
            )}
            <FormField
              control={form.control}
              name="root_cause_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Root Cause</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      key={`root-${field.value ?? "unset"}-${rootCauseOptions.length}`}
                      value={field.value ?? "__unset__"}
                      onChange={(v) =>
                        field.onChange(v === "__unset__" ? null : v)
                      }
                      loadError={rootCauseError}
                      onRetry={() => void refetchRootCauses()}
                      options={[
                        { value: "__unset__", label: " - None - " },
                        ...rootCauseOptions.map((opt) => ({
                          value: opt.id,
                          label: opt.name,
                        })),
                      ]}
                      placeholder="Select root cause (optional)"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="resolution_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Resolution</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      key={`res-${field.value ?? "unset"}-${resolutionOptions.length}`}
                      value={field.value ?? "__unset__"}
                      onChange={(v) =>
                        field.onChange(v === "__unset__" ? null : v)
                      }
                      loadError={resolutionError}
                      onRetry={() => void refetchResolutions()}
                      options={[
                        { value: "__unset__", label: " - None - " },
                        ...resolutionOptions.map((opt) => ({
                          value: opt.id,
                          label: opt.name,
                        })),
                      ]}
                      placeholder="Select resolution (optional)"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="technical_team_response"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Technical Team Response</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Enter technical team response to send to customer"
                      {...field}
                      value={field.value ?? ""}
                      rows={4}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        {isEditMode && complaintId && (
          <ComplaintManualAttachmentsSection
            complaintId={complaintId}
            attachments={complaint?.attachments ?? []}
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
              onClick={() => void handleUpdateAndReply()}
              disabled={isLoading}
            >
              {updateAndReplyMutation.isPending ? (
                <>
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  Sending...
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
                {isEditMode ? "Update Complaint" : "Create Complaint"}
              </>
            )}
          </Button>
        </div>
      </form>
    </Form>
  );
}
