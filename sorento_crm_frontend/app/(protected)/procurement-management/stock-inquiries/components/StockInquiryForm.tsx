'use client';

import { useEffect, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { LoaderCircleIcon, Save, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Textarea } from '@/components/ui/textarea';
import { format } from 'date-fns';
import {
  ProductInquiryFormLayout,
  InquiryFormTableRow,
} from './ProductInquiryFormLayout';
import { PRODUCT_INQUIRY_REMARK_HINT } from '../productInquiryFormConstants';
import {
  useCreateStockInquiry,
  useUpdateStockInquiry,
  useUpdateStockInquiryAndReply,
  useStockInquiry,
} from '../hooks/useStockInquiries';
import { getOrCreateStockInquiryViewLink } from '../services/stockInquiryService';
import { toast } from '@/lib/toast';
import {
  StockInquirySchema,
  type StockInquirySchemaType,
} from '../forms/stock-inquiry-schema';
import type { StockInquiryFormData } from '../types/stockInquiry.types';
import StockInquiryAttachmentsSection from './StockInquiryAttachmentsSection';
import { usePublicViewLinksEnabled } from '@/hooks/usePublicViewLinksEnabled';
import { RequestorContactSelect } from '@/app/(protected)/master-data-management/shared/components/RequestorContactSelect';

interface StockInquiryFormProps {
  inquiryId?: string;
  onSuccess?: () => void;
}

export default function StockInquiryForm({
  inquiryId,
  onSuccess,
}: StockInquiryFormProps) {
  const router = useRouter();
  const isEditMode = !!inquiryId;
  const { data: inquiry, isLoading: isLoadingInquiry } = useStockInquiry(
    inquiryId || null,
  );
  const createMutation = useCreateStockInquiry();
  const updateMutation = useUpdateStockInquiry();
  const updateAndReplyMutation = useUpdateStockInquiryAndReply();
  const [updateAndReplyDialogOpen, setUpdateAndReplyDialogOpen] = useState(false);
  const [replyMessage, setReplyMessage] = useState('');
  const [previewPreparing, setPreviewPreparing] = useState(false);
  const publicViewLinksEnabled = usePublicViewLinksEnabled();

  const form = useForm<StockInquirySchemaType>({
    resolver: zodResolver(StockInquirySchema),
    defaultValues: {
      salesperson: null,
      salesperson_contact_id: null,
      product_code: null,
      item_description: null,
      project_customer: null,
      project_name: null,
      quantity: null,
      delivery_date: null,
      remark: null,
      additional_remark: null,
      purchasing_response: null,
      contact_id: null,
      space_id: null,
    },
    mode: 'onTouched',
  });

  const [formInitialized, setFormInitialized] = useState(false);

  // Load inquiry data when editing
  useEffect(() => {
    if (inquiry && isEditMode && !formInitialized) {
      form.reset({
        salesperson: inquiry.salesperson || null,
        salesperson_contact_id: inquiry.salesperson_contact_id || null,
        product_code: inquiry.product_code || null,
        item_description: inquiry.item_description || null,
        project_customer: inquiry.project_customer || null,
        project_name: inquiry.project_name || null,
        quantity: inquiry.quantity ?? null,
        delivery_date: inquiry.delivery_date ?? null,
        remark: inquiry.remark ?? null,
        additional_remark: inquiry.additional_remark || null,
        purchasing_response: inquiry.purchasing_response || null,
        contact_id: inquiry.contact_id || null,
        space_id: inquiry.space_id || null,
      });
      setFormInitialized(true);
    }
  }, [inquiry, isEditMode, form, formInitialized]);

  useEffect(() => {
    setFormInitialized(false);
  }, [inquiryId]);

  /**
   * The dialog edits the purchasing wording only. The preamble and the contact's
   * portal link are composed server-side and appended when the message is sent, so
   * there is no link to fetch here - see
   * `StockInquiryService.compose_stock_inquiry_reply_message`.
   */
  const handleUpdateAndReplyClick = async () => {
    const valid = await form.trigger();
    if (!valid || !inquiryId) return;
    setPreviewPreparing(true);
    try {
      setReplyMessage((form.getValues().purchasing_response ?? '').trim());
      setUpdateAndReplyDialogOpen(true);
    } finally {
      setPreviewPreparing(false);
    }
  };

  const handleUpdateAndReplyConfirm = async () => {
    if (!inquiryId) return;
    const data = form.getValues();
    const formData: StockInquiryFormData = {
      salesperson: data.salesperson || undefined,
      salesperson_contact_id: data.salesperson_contact_id || null,
      product_code: data.product_code || undefined,
      item_description: data.item_description || undefined,
      project_customer: data.project_customer || undefined,
      project_name: data.project_name || undefined,
      quantity: data.quantity ?? undefined,
      delivery_date: data.delivery_date ?? undefined,
      remark: data.remark ?? undefined,
      additional_remark: data.additional_remark || undefined,
      purchasing_response: replyMessage.trim() || undefined,
      contact_id: data.contact_id || undefined,
      space_id: data.space_id || undefined,
    };
    try {
      await updateAndReplyMutation.mutateAsync({ id: inquiryId, data: formData });
      setUpdateAndReplyDialogOpen(false);
      setReplyMessage('');
      onSuccess?.();
    } catch {
      // Error toast from mutation
    }
  };

  const onSubmit = async (data: StockInquirySchemaType) => {
    try {
      const formData: StockInquiryFormData = {
        salesperson: data.salesperson || undefined,
        salesperson_contact_id: data.salesperson_contact_id || null,
        product_code: data.product_code || undefined,
        item_description: data.item_description || undefined,
        project_customer: data.project_customer || undefined,
        project_name: data.project_name || undefined,
        quantity: data.quantity ?? undefined,
        delivery_date: data.delivery_date ?? undefined,
        remark: data.remark ?? undefined,
        additional_remark: data.additional_remark || undefined,
        purchasing_response: data.purchasing_response || undefined,
        contact_id: data.contact_id || undefined,
        space_id: data.space_id || undefined,
      };

      if (isEditMode && inquiryId) {
        await updateMutation.mutateAsync({ id: inquiryId, data: formData });
      } else {
        const created = await createMutation.mutateAsync(formData);
        if (created?.id && publicViewLinksEnabled) {
          try {
            const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
            const { view_url } = await getOrCreateStockInquiryViewLink(created.id, baseUrl);
            await navigator.clipboard.writeText(view_url);
            toast.success('Stock inquiry created. View link copied to clipboard.');
          } catch {
            // view link optional
          }
        }
      }

      onSuccess?.();
    } catch (error) {
      console.error('Error submitting form:', error);
    }
  };

  if (isEditMode && isLoadingInquiry) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;

  const inquiryDateStr =
    isEditMode && inquiry?.created_at
      ? format(new Date(inquiry.created_at), 'dd/MM/yy')
      : format(new Date(), 'dd/MM/yy');

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <ProductInquiryFormLayout>
          <InquiryFormTableRow label="Date">
            <p className="text-sm font-medium tabular-nums py-1">{inquiryDateStr}</p>
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Stock inquiry number">
            <p className="text-sm font-medium tabular-nums py-1">
              {isEditMode && inquiry?.inquiry_number ? (
                inquiry.inquiry_number
              ) : (
                <span className="text-muted-foreground font-normal">
                  Assigned when you save
                </span>
              )}
            </p>
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Sales person">
            <FormField
              control={form.control}
              name="salesperson_contact_id"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <RequestorContactSelect
                      value={field.value}
                      onChange={field.onChange}
                      submitterContactId={inquiry?.contact_id}
                      savedContactId={inquiry?.salesperson_contact_id}
                      savedContactName={inquiry?.salesperson_contact_name ?? inquiry?.salesperson}
                      placeholder="Select sales person"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Product code">
            <FormField
              control={form.control}
              name="product_code"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Input
                      className="h-9"
                      placeholder="Code"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Item description" labelClassName="items-start pt-3">
            <FormField
              control={form.control}
              name="item_description"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Textarea
                      placeholder="Description"
                      {...field}
                      value={field.value || ''}
                      rows={3}
                      className="min-h-[4.5rem] resize-y"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Project customer">
            <FormField
              control={form.control}
              name="project_customer"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Input
                      className="h-9"
                      placeholder="Customer / site"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Project name">
            <FormField
              control={form.control}
              name="project_name"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Input
                      className="h-9"
                      placeholder="Project name"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Qty">
            <FormField
              control={form.control}
              name="quantity"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <NumberInput
                      className="h-9"
                      min={0}
                      step="any"
                      inputMode="decimal"
                      placeholder="Quantity"
                      {...field}
                      value={field.value ?? ''}
                      onChange={(e) => field.onChange(e.target.value)}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Delivery date">
            <FormField
              control={form.control}
              name="delivery_date"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Input
                      className="h-9"
                      placeholder="e.g. MOCK UP - MAY 2026, BALANCE BY AUG/SEPT 2026"
                      {...field}
                      value={field.value ?? ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Remark" labelClassName="items-start pt-3">
            <div className="space-y-2">
              <FormField
                control={form.control}
                name="remark"
                render={({ field }) => (
                  <FormItem className="space-y-1">
                    <FormControl>
                      <Textarea
                        placeholder="Remarks"
                        {...field}
                        value={field.value ?? ''}
                        rows={6}
                        className="min-h-[8rem] resize-y font-normal"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <p className="text-xs text-muted-foreground leading-snug">
                {PRODUCT_INQUIRY_REMARK_HINT}
              </p>
            </div>
          </InquiryFormTableRow>

          <InquiryFormTableRow label="Additional remark" labelClassName="items-start pt-3">
            <FormField
              control={form.control}
              name="additional_remark"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Textarea
                      placeholder="Additional notes"
                      {...field}
                      value={field.value || ''}
                      rows={4}
                      className="min-h-[6rem] resize-y"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>

          {inquiry?.respond_inbox_url && (
            <InquiryFormTableRow label="Respond inbox">
              <a
                href={inquiry.respond_inbox_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline text-sm break-all font-medium py-1 inline-block"
              >
                {inquiry.respond_inbox_url}
              </a>
            </InquiryFormTableRow>
          )}

          <InquiryFormTableRow
            label="Comment / reply by purchasing"
            labelClassName="items-start pt-3 sm:whitespace-normal"
          >
            <FormField
              control={form.control}
              name="purchasing_response"
              render={({ field }) => (
                <FormItem className="space-y-1">
                  <FormControl>
                    <Textarea
                      placeholder="For purchasing use"
                      {...field}
                      value={field.value || ''}
                      rows={5}
                      className="min-h-[6rem] resize-y"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </InquiryFormTableRow>
        </ProductInquiryFormLayout>

        {isEditMode && inquiryId && (
          <StockInquiryAttachmentsSection
            inquiryId={inquiryId}
            attachments={inquiry?.attachments ?? []}
          />
        )}

        <div className="flex justify-end gap-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
            disabled={isLoading || updateAndReplyMutation.isPending}
          >
            Cancel
          </Button>
          {isEditMode && (
            <Button
              type="button"
              variant="secondary"
              onClick={handleUpdateAndReplyClick}
              disabled={isLoading || previewPreparing || updateAndReplyMutation.isPending}
            >
              {previewPreparing ? (
                <>
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  Preparing...
                </>
              ) : updateAndReplyMutation.isPending ? (
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
          <Button
            type="submit"
            disabled={isLoading || updateAndReplyMutation.isPending}
          >
            {isLoading ? (
              <>
                <LoaderCircleIcon className="size-4 animate-spin" />
                {isEditMode ? 'Updating...' : 'Creating...'}
              </>
            ) : (
              <>
                <Save className="size-4" />
                {isEditMode ? 'Update Stock Inquiry' : 'Create Stock Inquiry'}
              </>
            )}
          </Button>
        </div>
      </form>

      <Dialog open={updateAndReplyDialogOpen} onOpenChange={setUpdateAndReplyDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Update & Reply</DialogTitle>
            <DialogDescription>
              Edit the message below. It will be saved as the purchasing response and sent to the customer via Respond.io. The conversation will be marked as responded.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="stock-inquiry-reply-message">Message to send</Label>
              <Textarea
                id="stock-inquiry-reply-message"
                value={replyMessage}
                onChange={(e) => setReplyMessage(e.target.value)}
                placeholder="Purchasing response..."
                rows={5}
                className="resize-none"
              />
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
              disabled={updateAndReplyMutation.isPending || !replyMessage.trim()}
              onClick={(e) => {
                e.preventDefault();
                handleUpdateAndReplyConfirm();
              }}
            >
              {updateAndReplyMutation.isPending ? 'Sending...' : 'Update & Reply'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Form>
  );
}
