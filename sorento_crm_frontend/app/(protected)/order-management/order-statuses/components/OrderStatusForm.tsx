'use client';

import { useMemo } from 'react';
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
import { NumberInput } from '@/components/ui/number-input';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useCreateOrderStatus, useUpdateOrderStatus, useOrderStatus } from '../hooks/useOrderStatuses';
import { OrderStatusSchema, type OrderStatusSchemaType } from '../forms/order-status-schema';
import type { OrderStatusFormData } from '../types/orderStatus.types';

interface OrderStatusFormProps {
  orderStatusId?: string;
  onSuccess?: () => void;
}

export default function OrderStatusForm({ orderStatusId, onSuccess }: OrderStatusFormProps) {
  const router = useRouter();
  const isEditMode = !!orderStatusId;
  const { data: orderStatus, isLoading: isLoadingOrderStatus } = useOrderStatus(orderStatusId || null);
  const createMutation = useCreateOrderStatus();
  const updateMutation = useUpdateOrderStatus();

  // The record fills the form through `values`, not a reset scheduled in an effect
  // (S7-03). The old shape waited a tick for "the fields to be ready" and guarded
  // itself with a `formInitialized` flag, which meant a refetched record never
  // reached the inputs. `values` is react-hook-form's own answer: undefined in
  // create mode, so the defaults stand.
  const editValues = useMemo<OrderStatusSchemaType | undefined>(
    () =>
      orderStatus && isEditMode
        ? {
            status_code: orderStatus.status_code,
            status_name: orderStatus.status_name,
            description: orderStatus.description || '',
            sequence: orderStatus.sequence,
            is_final_status: orderStatus.is_final_status,
          }
        : undefined,
    [orderStatus, isEditMode],
  );

  const form = useForm<OrderStatusSchemaType>({
    resolver: zodResolver(OrderStatusSchema),
    defaultValues: {
      status_code: '',
      status_name: '',
      description: '',
      sequence: 0,
      is_final_status: false,
    },
    values: editValues,
    // A refetch arriving mid-edit updates the fields nobody has touched and
    // leaves the ones being typed in alone.
    resetOptions: { keepDirtyValues: true },
    // A field answers when the reader leaves it, not on submit and not on every
    // keystroke: onTouched is the one that does not shout at a half-typed word.
    mode: 'onTouched',
  });

  const onSubmit = async (data: OrderStatusSchemaType) => {
    try {
      // Transform data to ensure proper format
      const formData: OrderStatusFormData = {
        status_code: data.status_code,
        status_name: data.status_name,
        description: data.description || undefined,
        sequence: data.sequence,
        is_final_status: data.is_final_status,
      };

      if (isEditMode && orderStatusId) {
        await updateMutation.mutateAsync({ id: orderStatusId, data: formData });
      } else {
        await createMutation.mutateAsync(formData);
      }

      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/order-management/order-statuses');
      }
    } catch (error) {
      // Error is handled by the mutation hook
      console.error('Order status form submission error:', error);
    }
  };

  if (isEditMode && isLoadingOrderStatus) {
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
        <Card>
          <CardHeader>
            <CardTitle>{isEditMode ? 'Edit Delivery Order Status' : 'Create Delivery Order Status'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FormField
                control={form.control}
                name="status_code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Status Code *</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="PENDING"
                        {...field}
                        disabled={isEditMode}
                      />
                    </FormControl>
                    <FormDescription>
                      Unique status identifier (alphanumeric, dashes, underscores only)
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="status_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Status Name *</FormLabel>
                    <FormControl>
                      <Input placeholder="Pending Approval" {...field} />
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
                    <Textarea
                      placeholder="Enter status description..."
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
                control={form.control}
                name="sequence"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Sequence *</FormLabel>
                    <FormControl>
                      <NumberInput
                        {...field}
                        value={field.value ?? ''}
                        onChange={(e) => field.onChange(e.target.value)}
                      />
                    </FormControl>
                    <FormDescription>
                      Sequence in the list (lower numbers appear first)
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="is_final_status"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                    <div className="space-y-0.5">
                      <FormLabel className="text-base">Final Status</FormLabel>
                      <FormDescription>
                        Mark this status as a final/terminal status
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

            <div className="flex justify-end gap-4 pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (onSuccess) {
                    onSuccess();
                  } else {
                    router.push('/order-management/order-statuses');
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
                    {isEditMode ? 'Update Delivery Order Status' : 'Create Delivery Order Status'}
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
