'use client';

import { useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { LoaderCircleIcon } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  useCreateAttachmentType,
  useUpdateAttachmentType,
  useAttachmentType,
} from '../hooks/useAttachmentTypes';
import {
  AttachmentTypeSchema,
  type AttachmentTypeSchemaType,
} from '../forms/attachment-type-schema';
import { useDirectoryTree } from '../../attachments/hooks/useAttachments';
import { flattenDirectoryOptions } from '../../attachment-directories/components/drivePath';

interface AttachmentTypeFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  attachmentTypeId?: string | null;
}

export default function AttachmentTypeFormDialog({
  open,
  onOpenChange,
  attachmentTypeId,
}: AttachmentTypeFormDialogProps) {
  const isEditMode = !!attachmentTypeId;
  const { data: attachmentType, isLoading: isLoadingType } = useAttachmentType(attachmentTypeId || null);
  const createMutation = useCreateAttachmentType();
  const updateMutation = useUpdateAttachmentType();
  const { data: directoryTree = [] } = useDirectoryTree();
  const directoryOptions = flattenDirectoryOptions(directoryTree);

  const form = useForm<AttachmentTypeSchemaType>({
    resolver: zodResolver(AttachmentTypeSchema),
    defaultValues: {
      type_name: '',
      description: '',
      allowed_extensions: '',
      max_file_size_mb: 10,
      max_count_per_entity: null,
      supports_field_linkage: false,
      triggers_n8n_webhook: true,
      is_certificate: false,
      max_validity_months: null,
      is_shared: false,
      default_directory_id: null,
    },
    mode: 'onTouched',
  });

  // Reset form when dialog opens or attachment type changes
  useEffect(() => {
    if (open) {
      if (isEditMode && attachmentType) {
        form.reset({
          type_name: attachmentType.type_name,
          description: attachmentType.description || '',
          allowed_extensions: attachmentType.allowed_extensions,
          max_file_size_mb: attachmentType.max_file_size_mb,
          max_count_per_entity: attachmentType.max_count_per_entity ?? null,
          supports_field_linkage: attachmentType.supports_field_linkage ?? false,
          triggers_n8n_webhook: attachmentType.triggers_n8n_webhook ?? true,
          is_certificate: attachmentType.is_certificate ?? false,
          max_validity_months: attachmentType.max_validity_months ?? null,
          is_shared: attachmentType.is_shared ?? false,
          default_directory_id: attachmentType.default_directory_id ?? null,
        });
      } else {
        form.reset({
          type_name: '',
          description: '',
          allowed_extensions: '',
          max_file_size_mb: 10,
          max_count_per_entity: null,
          supports_field_linkage: false,
          triggers_n8n_webhook: true,
          is_certificate: false,
          max_validity_months: null,
          is_shared: false,
          default_directory_id: null,
        });
      }
    }
  }, [open, isEditMode, attachmentType, form]);

  const onSubmit = async (data: AttachmentTypeSchemaType) => {
    try {
      if (isEditMode && attachmentTypeId) {
        await updateMutation.mutateAsync({
          id: attachmentTypeId,
          data: {
            type_name: data.type_name,
            description: data.description || undefined,
            allowed_extensions: data.allowed_extensions,
            max_file_size_mb: data.max_file_size_mb,
            max_count_per_entity: data.max_count_per_entity ?? null,
            supports_field_linkage: data.supports_field_linkage ?? false,
            triggers_n8n_webhook: data.triggers_n8n_webhook ?? true,
            is_certificate: data.is_certificate ?? false,
            max_validity_months: data.max_validity_months ?? null,
            is_shared: data.is_shared ?? false,
            default_directory_id: data.default_directory_id ?? null,
          },
        });
      } else {
        await createMutation.mutateAsync({
          type_name: data.type_name,
          description: data.description || undefined,
          allowed_extensions: data.allowed_extensions,
          max_file_size_mb: data.max_file_size_mb,
          max_count_per_entity: data.max_count_per_entity ?? null,
          supports_field_linkage: data.supports_field_linkage ?? false,
          triggers_n8n_webhook: data.triggers_n8n_webhook ?? true,
          is_certificate: data.is_certificate ?? false,
          max_validity_months: data.max_validity_months ?? null,
          is_shared: data.is_shared ?? false,
          default_directory_id: data.default_directory_id ?? null,
        });
      }
      onOpenChange(false);
      form.reset();
    } catch {
      // Error handled by mutation
    }
  };

  const isLoading = createMutation.isPending || updateMutation.isPending || isLoadingType;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEditMode ? 'Edit Attachment Type' : 'Create Attachment Type'}</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="type_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Type Name *</FormLabel>
                  <FormControl>
                    <Input placeholder="e.g., Document, Image" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Enter description"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="allowed_extensions"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Allowed Extensions *</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="e.g., pdf,doc,docx,jpg,png (comma-separated)"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="max_file_size_mb"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Max File Size (MB) *</FormLabel>
                  <FormControl>
                    <NumberInput
                      placeholder="10"
                      name={field.name}
                      ref={field.ref}
                      onBlur={field.onBlur}
                      value={field.value}
                      onValueChange={field.onChange}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="max_count_per_entity"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Max attachments per record</FormLabel>
                  <FormControl>
                    <NumberInput
                      min={1}
                      placeholder="Leave blank for unlimited"
                      value={field.value ?? null}
                      // Blank clears the cap (NULL = unlimited); never 0, which would read as
                      // "no uploads allowed".
                      onValueChange={field.onChange}
                    />
                  </FormControl>
                  <p className="text-xs text-muted-foreground">
                    How many files of this type one record may hold. Blank = unlimited. Portal
                    uploads are rejected once a record reaches this count.
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="supports_field_linkage"
              render={({ field }) => (
                <FormItem className="flex flex-row items-start gap-2 rounded-md border p-3">
                  <FormControl>
                    <Checkbox
                      checked={!!field.value}
                      onCheckedChange={(v) => field.onChange(v === true)}
                    />
                  </FormControl>
                  <div className="space-y-0.5">
                    <FormLabel className="cursor-pointer">Allow linking to entity fields</FormLabel>
                    <p className="text-xs text-muted-foreground">
                      When on, uploads of this type show the “Linked to / Linked fields” section
                      (e.g. product photos) so files can be tied to a record field.
                    </p>
                  </div>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="triggers_n8n_webhook"
              render={({ field }) => (
                <FormItem className="flex flex-row items-start gap-2 rounded-md border p-3">
                  <FormControl>
                    <Checkbox
                      checked={field.value !== false}
                      onCheckedChange={(v) => field.onChange(v === true)}
                    />
                  </FormControl>
                  <div className="space-y-0.5">
                    <FormLabel className="cursor-pointer">
                      Send uploads to the automation (n8n)
                    </FormLabel>
                    <p className="text-xs text-muted-foreground">
                      On for document types the automation reads and links (product photos,
                      catalogues, certificates). Turn it OFF for types it never handles - a
                      background file like a stock list or an imported workbook otherwise waits
                      on a reply that never comes, and sits on &quot;Processing&quot; in Upload
                      activity forever.
                    </p>
                  </div>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="is_certificate"
              render={({ field }) => (
                <FormItem className="flex flex-row items-start gap-2 rounded-md border p-3">
                  <FormControl>
                    <Checkbox
                      checked={!!field.value}
                      onCheckedChange={(v) => field.onChange(v === true)}
                    />
                  </FormControl>
                  <div className="space-y-0.5">
                    <FormLabel className="cursor-pointer">
                      Files of this type are certificates
                    </FormLabel>
                    <p className="text-xs text-muted-foreground">
                      When on, an upload of this type that arrives with certificate details
                      (scheme, number, validity) also files a certificate in the register, so it
                      can be renewed and chased before it expires. Off = the file is linked to
                      products exactly as before.
                    </p>
                  </div>
                </FormItem>
              )}
            />

            {form.watch('is_certificate') && (
              <FormField
                control={form.control}
                name="max_validity_months"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Maximum validity (months)</FormLabel>
                    <FormControl>
                      <NumberInput
                        min={1}
                        placeholder="Leave blank for no limit"
                        value={field.value ?? null}
                        onValueChange={field.onChange}
                      />
                    </FormControl>
                    <p className="text-xs text-muted-foreground">
                      How long a certificate of this type can plausibly run. A document read as
                      valid for longer is flagged for review rather than trusted. Blank = no
                      limit.
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="is_shared"
              render={({ field }) => (
                <FormItem className="flex flex-row items-start gap-2 rounded-md border p-3">
                  <FormControl>
                    <Checkbox
                      checked={!!field.value}
                      onCheckedChange={(v) => field.onChange(v === true)}
                    />
                  </FormControl>
                  <FormLabel className="cursor-pointer">Shared across companies</FormLabel>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="default_directory_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Default folder</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      value={field.value ?? ''}
                      onChange={(value) => field.onChange(value || null)}
                      options={directoryOptions}
                      placeholder="No default folder"
                      clearable
                    />
                  </FormControl>
                  <p className="text-xs text-muted-foreground">
                    Where an upload of this type files by default - still changeable per upload.
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isLoading}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading || !form.formState.isDirty}>
                {isLoading && <LoaderCircleIcon className="animate-spin mr-2" />}
                {isEditMode ? 'Update' : 'Create'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
