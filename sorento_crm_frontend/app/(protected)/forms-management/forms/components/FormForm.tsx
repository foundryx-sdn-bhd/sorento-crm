'use client';

import { useEffect, useMemo, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { LoaderCircleIcon, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Switch } from '@/components/ui/switch';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import LookupBoundField from '@/components/common/LookupBoundField';
import { useCreateForm, useUpdateForm, useForm as useFormQuery } from '../hooks/useForms';
import { FormSchema, type FormSchemaInput, type FormSchemaType } from '../forms/form-schema';
import type { FormFormData } from '../types/form.types';
import { useAttachments } from '@/app/(protected)/resource-management/attachments/hooks/useAttachments';
import { AccessLevelsMultiSelect } from '@/app/(protected)/resource-management/attachments/components/AccessLevelsMultiSelect';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import { getAttachmentPreviewUrl } from '@/app/(protected)/resource-management/attachments/services/attachmentService';
import { formatDate } from '@/lib/helpers';
import { toast } from '@/lib/toast';

interface FormFormProps {
  formId?: string;
  onSuccess?: () => void;
}

export default function FormForm({ formId, onSuccess }: FormFormProps) {
  const router = useRouter();
  const isEditMode = !!formId;
  const { data: form, isLoading: isLoadingForm } = useFormQuery(formId || null);
  const createMutation = useCreateForm();
  const updateMutation = useUpdateForm();
  const {
    data: accessTypeOptions = [],
    error: accessTypesError,
    refetch: refetchAccessTypes,
  } = useContactAccessTypes();
  const defaultAccessLevels = useMemo(() => accessTypeOptions.map((opt) => opt.code), [accessTypeOptions]);

  // The form record fills the fields through `values`, not a reset scheduled in
  // an effect behind a `formInitialized` flag (S7-03), which meant a refetched
  // record never reached the inputs. Undefined in create mode, so the defaults
  // (and the access-level seed below) stand.
  const editValues = useMemo<FormSchemaInput | undefined>(
    () =>
      form && isEditMode
        ? {
            code: form.code,
            name: form.name,
            form_type: form.form_type || 'marketing',
            purpose: form.purpose || null,
            language: form.language,
            is_active: form.is_active,
            attachment_id: form.attachment_id || null,
            access_levels: form.access_levels?.length ? form.access_levels : defaultAccessLevels,
          }
        : undefined,
    [form, isEditMode, defaultAccessLevels],
  );

  const formHook = useForm<FormSchemaInput, unknown, FormSchemaType>({
    resolver: zodResolver(FormSchema),
    defaultValues: {
      code: '',
      name: '',
      form_type: 'marketing',
      purpose: null,
      language: 'en',
      is_active: false,
      attachment_id: null,
      access_levels: [],
    },
    values: editValues,
    // A refetch arriving mid-edit updates the fields nobody has touched and
    // leaves the ones being typed in alone.
    resetOptions: { keepDirtyValues: true },
    // A field answers when the reader leaves it, not on submit.
    mode: 'onTouched',
  });

  // Create-mode only: has the access-level seed already been applied once?
  const [accessLevelsSeeded, setAccessLevelsSeeded] = useState(false);

  const handlePreview = async (attachmentId: string) => {
    try {
      const previewUrl = await getAttachmentPreviewUrl(attachmentId);
      if (previewUrl) {
        window.open(previewUrl, '_blank');
      }
    } catch {
      toast.error('Failed to open attachment preview');
    }
  };

  // Create-mode seed: fill default access levels exactly once, the first
  // time the catalog arrives. Do NOT re-seed when the field is later cleared
  // - that would block "Clear all" in the multi-select.
  useEffect(() => {
    if (
      !isEditMode &&
      !accessLevelsSeeded &&
      defaultAccessLevels.length > 0 &&
      formHook.getValues('access_levels').length === 0
    ) {
      formHook.setValue('access_levels', defaultAccessLevels);
      setAccessLevelsSeeded(true);
    }
  }, [defaultAccessLevels, formHook, isEditMode, accessLevelsSeeded]);

  // A different record is a different seed.
  useEffect(() => {
    setAccessLevelsSeeded(false);
  }, [formId]);

  // Fetch attachments for selection
  const { data: attachmentsData } = useAttachments({
    pageIndex: 0,
    pageSize: 100,
    sorting: [],
    searchQuery: '',
  });
  const attachmentsList = attachmentsData?.data || [];
  const attachments = (() => {
    if (!form?.attachment) return attachmentsList;
    if (attachmentsList.some((a) => a.id === form.attachment!.id)) return attachmentsList;
    const merged = {
      ...form.attachment,
      uploaded_at: form.created_at,
    } as unknown as (typeof attachmentsList)[number];
    return [merged, ...attachmentsList];
  })();

  const onSubmit = async (data: FormSchemaType) => {
    try {
      const formData: FormFormData = {
        code: data.code,
        name: data.name,
        form_type: data.form_type,
        purpose: data.purpose || undefined,
        language: data.language,
        is_active: data.is_active,
        attachment_id: data.attachment_id || null,
        access_levels: data.access_levels,
      };

      if (isEditMode && formId) {
        await updateMutation.mutateAsync({ id: formId, data: formData });
      } else {
        await createMutation.mutateAsync(formData);
      }

      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/forms-management/forms');
      }
    } catch (error) {
      // Error is handled by the mutation hook
      console.error('Form submission error:', error);
    }
  };

  if (isEditMode && isLoadingForm) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;
  const selectedAttachmentId = formHook.watch('attachment_id');
  const selectedAttachment = selectedAttachmentId
    ? attachments.find((a) => a.id === selectedAttachmentId)
    : null;

  return (
    <Form {...formHook}>
      <form onSubmit={formHook.handleSubmit(onSubmit)} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{isEditMode ? 'Edit Form' : 'Create Form'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={formHook.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Form Code *</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="FORM-001"
                        {...field}
                        disabled={isEditMode}
                      />
                    </FormControl>
                    <FormDescription>
                      Unique form identifier (alphanumeric, dashes, underscores only)
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={formHook.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Form Name *</FormLabel>
                    <FormControl>
                      <Input placeholder="Customer Feedback Form" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={formHook.control}
                name="form_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Form type</FormLabel>
                    <FormControl>
                      <LookupBoundField
                        table="forms"
                        column="form_type"
                        value={field.value}
                        onChange={field.onChange}
                        placeholder="Select form type"
                        renderFallback={() => (
                          <SearchableSelect
                            onChange={field.onChange}
                            value={field.value || 'marketing'}
                            options={[
                              { value: 'marketing', label: 'Marketing' },
                              { value: 'registration', label: 'Registration' },
                              { value: 'application', label: 'Application' },
                              { value: 'feedback', label: 'Feedback' },
                              { value: 'survey', label: 'Survey' },
                              { value: 'complaint', label: 'Complaint' },
                              { value: 'internal', label: 'Internal' },
                              { value: 'other', label: 'Other' },
                            ]}
                            placeholder="Select form type"
                          />
                        )}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={formHook.control}
                name="access_levels"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Access levels</FormLabel>
                    <FormControl>
                      <AccessLevelsMultiSelect
                        options={accessTypeOptions}
                        loadError={accessTypesError}
                        onRetry={() => void refetchAccessTypes()}
                        value={field.value ?? []}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={formHook.control}
              name="purpose"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Purpose</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Enter form purpose..."
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={formHook.control}
                name="language"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Language *</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        onChange={field.onChange}
                        value={field.value || 'en'}
                        options={[
                          { value: 'en', label: 'English' },
                          { value: 'es', label: 'Spanish' },
                          { value: 'fr', label: 'French' },
                          { value: 'de', label: 'German' },
                          { value: 'it', label: 'Italian' },
                          { value: 'pt', label: 'Portuguese' },
                          { value: 'zh', label: 'Chinese' },
                          { value: 'ja', label: 'Japanese' },
                          { value: 'ko', label: 'Korean' },
                          { value: 'ar', label: 'Arabic' },
                        ]}
                        placeholder="Select language"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={formHook.control}
                name="is_active"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                    <div className="space-y-0.5">
                      <FormLabel className="text-base">Active Status</FormLabel>
                      <FormDescription>
                        Enable or disable this form
                      </FormDescription>
                    </div>
                    <FormControl>
                      <Switch
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
            </div>

            {/* Attachment Selection */}
            <FormField
              control={formHook.control}
              name="attachment_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Attachment</FormLabel>
                  <div className="space-y-3">
                    <FormControl>
                      <SearchableSelect
                        value={field.value || '__none__'}
                        onChange={(value) => {
                          field.onChange(value === '__clear__' || value === '__none__' ? null : value);
                        }}
                        options={[
                          { value: '__none__', label: 'No attachment' },
                          ...(field.value ? [{ value: '__clear__', label: 'Clear attachment' }] : []),
                          ...attachments.map((attachment) => ({
                            value: attachment.id,
                            label: attachment.original_filename,
                          })),
                        ]}
                        placeholder="Select an attachment or leave empty"
                      />
                    </FormControl>
                    {selectedAttachment && (
                      <div className="rounded-lg border p-3 bg-muted/50">
                        <div className="flex items-center justify-between">
                          <div className="flex-1">
                            <p className="font-medium text-sm">{selectedAttachment.original_filename}</p>
                            <p className="text-xs text-muted-foreground">
                              {selectedAttachment.file_size_bytes
                                ? `${(selectedAttachment.file_size_bytes / 1024).toFixed(2)} KB`
                                : '-'} •{' '}
                              {formatDate(new Date(selectedAttachment.uploaded_at))}
                            </p>
                          </div>
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                handlePreview(selectedAttachment.id);
                              }}
                            >
                              Preview
                            </Button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                  <FormDescription>
                    Optionally link an attachment to this form. You can clear it by selecting "Clear attachment".
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex justify-end gap-4 pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (onSuccess) {
                    onSuccess();
                  } else {
                    router.push('/forms-management/forms');
                  }
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? (
                  <>
                    <LoaderCircleIcon className="size-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <Save className="size-4" />
                    {isEditMode ? 'Update Form' : 'Create Form'}
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </Form>
  );
}
