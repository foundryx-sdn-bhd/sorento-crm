'use client';

import { useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { LoaderCircleIcon, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useCreatePromotionType, useUpdatePromotionType } from '../hooks/usePromotionTypes';
import {
  PromotionTypeSchema,
  type PromotionTypeSchemaType,
} from '../forms/promotion-type-schema';
import type { PromotionType } from '../types/promotionType.types';

interface PromotionTypeFormModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  promotionType?: PromotionType | null;
  onSuccess?: () => void;
}

const EMPTY: PromotionTypeSchemaType = {
  type_code: '',
  type_name: '',
  description: '',
  show_expired: false,
  expired_valid_until_year_end: false,
  expired_max_age_days: null,
  markers_text: '',
  match_priority: 100,
  is_default: false,
  sort_order: 0,
};

export default function PromotionTypeFormModal({
  open,
  onOpenChange,
  promotionType,
  onSuccess,
}: PromotionTypeFormModalProps) {
  const isEditMode = !!promotionType;
  const createMutation = useCreatePromotionType();
  const updateMutation = useUpdatePromotionType();

  const form = useForm<PromotionTypeSchemaType>({
    resolver: zodResolver(PromotionTypeSchema),
    defaultValues: EMPTY,
    mode: 'onTouched',
  });

  useEffect(() => {
    if (!open) return;
    form.reset(
      promotionType
        ? {
            type_code: promotionType.type_code,
            type_name: promotionType.type_name,
            description: promotionType.description || '',
            show_expired: promotionType.show_expired,
            expired_valid_until_year_end: promotionType.expired_valid_until_year_end,
            expired_max_age_days: promotionType.expired_max_age_days ?? null,
            markers_text: (promotionType.match_markers || []).join(', '),
            match_priority: promotionType.match_priority,
            is_default: promotionType.is_default,
            sort_order: promotionType.sort_order,
          }
        : EMPTY,
    );
  }, [open, promotionType, form]);

  const showExpired = form.watch('show_expired');
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const onSubmit = async (values: PromotionTypeSchemaType) => {
    const payload = {
      type_code: values.type_code.trim(),
      type_name: values.type_name.trim(),
      description: values.description?.trim() || null,
      show_expired: values.show_expired,
      expired_valid_until_year_end: values.show_expired ? values.expired_valid_until_year_end : false,
      expired_max_age_days: values.show_expired ? values.expired_max_age_days ?? null : null,
      match_markers: (values.markers_text || '')
        .split(',')
        .map((marker) => marker.trim().toLowerCase())
        .filter(Boolean),
      match_priority: values.match_priority,
      is_default: values.is_default,
      sort_order: values.sort_order,
    };

    try {
      if (isEditMode && promotionType) {
        await updateMutation.mutateAsync({ id: promotionType.id, data: payload });
      } else {
        await createMutation.mutateAsync(payload);
      }
      onOpenChange(false);
      onSuccess?.();
    } catch {
      // The mutation hooks surface the extracted error as a toast.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEditMode ? 'Edit promotion type' : 'Add promotion type'}</DialogTitle>
          <DialogDescription className="sr-only">
            {isEditMode
              ? 'Change how promotions of this type are classified and served after expiry.'
              : 'Define a new promotion type with its classification markers and expiry rule.'}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
            <ScrollArea className="max-h-[65vh] pe-3">
              <div className="space-y-5">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="type_code"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Code</FormLabel>
                        <FormControl>
                          <Input placeholder="special" {...field} disabled={isSaving} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="type_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Name</FormLabel>
                        <FormControl>
                          <Input placeholder="Special Promo" {...field} disabled={isSaving} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <FormField
                  control={form.control}
                  name="description"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Description</FormLabel>
                      <FormControl>
                        <Textarea rows={2} {...field} value={field.value || ''} disabled={isSaving} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="show_expired"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between gap-4 rounded-md border p-3">
                      <div className="space-y-0.5">
                        <FormLabel>Show when expired</FormLabel>
                        <FormDescription>
                          Off: never served after its end date.
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch checked={field.value} onCheckedChange={field.onChange} disabled={isSaving} />
                      </FormControl>
                    </FormItem>
                  )}
                />

                {showExpired && (
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <FormField
                      control={form.control}
                      name="expired_valid_until_year_end"
                      render={({ field }) => (
                        <FormItem className="flex flex-row items-center justify-between gap-4 rounded-md border p-3">
                          <div className="space-y-0.5">
                            <FormLabel>Until end of year</FormLabel>
                            <FormDescription>Only within the year it ended.</FormDescription>
                          </div>
                          <FormControl>
                            <Switch
                              checked={field.value}
                              onCheckedChange={field.onChange}
                              disabled={isSaving}
                            />
                          </FormControl>
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="expired_max_age_days"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Max age (days)</FormLabel>
                          <FormControl>
                            <NumberInput
                              min={0}
                              placeholder="No limit"
                              value={field.value ?? ''}
                              onChange={(e) => field.onChange(e.target.value)}
                              disabled={isSaving}
                            />
                          </FormControl>
                          <FormDescription>Blank: no age limit.</FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                )}

                <FormField
                  control={form.control}
                  name="markers_text"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>File-name markers</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="special, clearance"
                          {...field}
                          value={field.value || ''}
                          disabled={isSaving}
                        />
                      </FormControl>
                      <FormDescription>
                        Comma separated. An uploaded file naming one is classified as this type.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <FormField
                    control={form.control}
                    name="match_priority"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Match priority</FormLabel>
                        <FormControl>
                          <NumberInput
                            min={0}
                            value={field.value}
                            onChange={(e) => field.onChange(e.target.value)}
                            disabled={isSaving}
                          />
                        </FormControl>
                        <FormDescription>Lowest wins when a name matches two types.</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="sort_order"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Sort order</FormLabel>
                        <FormControl>
                          <NumberInput
                            min={0}
                            value={field.value}
                            onChange={(e) => field.onChange(e.target.value)}
                            disabled={isSaving}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <FormField
                  control={form.control}
                  name="is_default"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between gap-4 rounded-md border p-3">
                      <div className="space-y-0.5">
                        <FormLabel>Default type</FormLabel>
                        <FormDescription>
                          Used for a promotion whose name carries no marker.
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch checked={field.value} onCheckedChange={field.onChange} disabled={isSaving} />
                      </FormControl>
                    </FormItem>
                  )}
                />
              </div>
            </ScrollArea>

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSaving}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isSaving}>
                {isSaving ? <LoaderCircleIcon className="animate-spin" /> : <Save />}
                {isEditMode ? 'Save changes' : 'Create'}
              </Button>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
