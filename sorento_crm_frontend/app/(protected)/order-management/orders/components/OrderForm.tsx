'use client';

import { useEffect, useMemo } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import {
  Info,
  LoaderCircleIcon,
  Save,
  Settings,
  StickyNote,
  Truck,
} from 'lucide-react';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useCreateOrder, useUpdateOrder, useOrder } from '../hooks/useOrders';
import { OrderSchema, type OrderSchemaType } from '../forms/order-schema';
import type { OrderFormData } from '../types/order.types';
import { useOrderStatusSelectQuery } from '../../shared/hooks/use-order-status-select-query';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

/**
 * Normalize an API date to UTC midnight on the same calendar day (as shown by formatDate in view).
 * So edit input value (toISOString().slice(0,10)) matches the view and DB day.
 */
function dateOnlyUTC(value: string | Date | undefined | null): Date | undefined {
  if (value == null) return undefined;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return undefined;
  return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
}

/** Count weekdays (Mon - Fri) strictly between start and end, matching backend logic. */
function businessDaysBetween(start: Date, end: Date): number {
  const s = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const e = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  if (e.getTime() <= s.getTime()) return 0;
  let count = 0;
  const cur = new Date(s);
  cur.setDate(cur.getDate() + 1);
  while (cur.getTime() < e.getTime()) {
    const d = cur.getDay();
    if (d >= 1 && d <= 5) count += 1;
    cur.setDate(cur.getDate() + 1);
  }
  return count;
}

interface OrderFormProps {
  orderId?: string;
  onSuccess?: () => void;
}

export default function OrderForm({ orderId, onSuccess }: OrderFormProps) {
  const router = useRouter();
  const isEditMode = !!orderId;
  const { data: order, isLoading: isLoadingOrder } = useOrder(orderId || null);
  const { data: orderStatuses } = useOrderStatusSelectQuery();
  const createMutation = useCreateOrder();
  const updateMutation = useUpdateOrder();

  // Remarks CS is frozen once the DO is delivered AND linked to a complaint
  // (its fulfilment is historical). Backend-owned: `remarks_cs_locked` on the
  // single-order GET response. See PLAN-complaint-do-auto-fulfilment.md.
  const remarksCsLocked = isEditMode && !!order?.remarks_cs_locked;
  // An AutoCount-owned DO takes these fields only from AutoCount; the API rejects an edit.
  const autocountOwned = useMemo(
    () => new Set(isEditMode ? (order?.autocount_owned_fields ?? []) : []),
    [isEditMode, order?.autocount_owned_fields],
  );
  const fromAutoCount = (name: string) => autocountOwned.has(name);

  const defaultOrderDate = new Date();
  const defaultEstimatedDeliveryDate = (() => {
    const d = new Date(defaultOrderDate);
    d.setDate(d.getDate() + 2);
    return d;
  })();

  // The order fills the form through `values`, not a reset scheduled in an effect
  // behind a `formInitialized` flag (S7-03). The flag existed to stop the reset
  // running twice, and the price of it was that a refetched order never reached
  // the inputs at all. `values` is react-hook-form's own answer: undefined in
  // create mode, so the defaults below stand.
  const editValues = useMemo<OrderSchemaType | undefined>(() => {
    if (!order || !isEditMode) return undefined;
    return {
      order_number: order.order_number ?? '',
      order_date: order.order_date ? dateOnlyUTC(order.order_date) ?? new Date() : new Date(),
      estimated_delivery_date: dateOnlyUTC(order.estimated_delivery_date),
      actual_delivery_date: dateOnlyUTC(order.actual_delivery_date),
      customer_id: order.customer_id ? String(order.customer_id) : '',
      order_status_id: order.order_status_id ? String(order.order_status_id) : '',
      billing_address_id: order.billing_address_id || undefined,
      shipping_address_id: order.shipping_address_id || undefined,
      created_time: order.created_time ? new Date(order.created_time) : undefined,
      debtor_code: order.debtor_code ?? '',
      debtor_name: order.debtor_name ?? '',
      agent: order.agent ?? '',
      is_cancelled: order.is_cancelled ?? false,
      remarks_cs: order.remarks_cs ?? '',
      order_type: order.order_type ?? '',
      pickup_time: order.pickup_time ?? '',
      checker: order.checker ?? '',
      transporter: order.transporter ?? '',
      driver_name: order.driver_name ?? '',
      lorry_plate: order.lorry_plate ?? '',
      customer_ref: order.customer_ref ?? '',
      delivery_remarks_cs: order.delivery_remarks_cs ?? '',
      delivery_remarks: order.delivery_remarks ?? '',
      salesman: order.salesman ?? '',
      trips: order.trips ?? undefined,
      warehouse: order.warehouse ?? '',
      delivery_days: order.delivery_days ?? 2,
      kpi_warning: order.kpi_warning ?? false,
      subtotal_amount:
        typeof order.subtotal_amount === 'number'
          ? order.subtotal_amount
          : Number(order.subtotal_amount) || 0,
      discount_amount:
        typeof order.discount_amount === 'number'
          ? order.discount_amount
          : Number(order.discount_amount) || 0,
      tax_amount:
        typeof order.tax_amount === 'number' ? order.tax_amount : Number(order.tax_amount) || 0,
      total_amount:
        typeof order.total_amount === 'number'
          ? order.total_amount
          : Number(order.total_amount) || 0,
      remarks: order.remarks ?? '',
    };
  }, [order, isEditMode]);

  const form = useForm<OrderSchemaType>({
    resolver: zodResolver(OrderSchema),
    defaultValues: {
      order_number: '',
      order_date: defaultOrderDate,
      estimated_delivery_date: defaultEstimatedDeliveryDate,
      actual_delivery_date: undefined,
      customer_id: '',
      order_status_id: '',
      billing_address_id: undefined,
      shipping_address_id: undefined,
      created_time: undefined,
      debtor_code: '',
      debtor_name: '',
      agent: '',
      is_cancelled: false,
      remarks_cs: '',
      order_type: '',
      pickup_time: '',
      checker: '',
      transporter: '',
      driver_name: '',
      lorry_plate: '',
      customer_ref: '',
      delivery_remarks_cs: '',
      delivery_remarks: '',
      salesman: '',
      trips: undefined,
      warehouse: '',
      delivery_days: 2,
      kpi_warning: false,
      subtotal_amount: 0,
      discount_amount: 0,
      tax_amount: 0,
      total_amount: 0,
      remarks: '',
    },
    values: editValues,
    // A refetch arriving mid-edit updates the fields nobody has touched and
    // leaves the ones being typed in alone.
    resetOptions: { keepDirtyValues: true },
    // A field answers when the reader leaves it, not on submit.
    mode: 'onTouched',
  });

  // Watch financial fields to auto-calculate total
  const subtotal = form.watch('subtotal_amount');
  const discount = form.watch('discount_amount') || 0;
  const tax = form.watch('tax_amount') || 0;

  // Auto-calculate total when financial fields change
  useEffect(() => {
    const calculatedTotal = subtotal - discount + tax;
    if (calculatedTotal >= 0) {
      form.setValue('total_amount', calculatedTotal);
    }
  }, [subtotal, discount, tax, form]);

  // Recalculate Delivery Days and Over 2 days when order date or actual delivery date changes
  const orderDate = form.watch('order_date');
  const actualDeliveryDate = form.watch('actual_delivery_date');
  useEffect(() => {
    const orderDateVal = form.getValues('order_date');
    const actualDateVal = form.getValues('actual_delivery_date');
    if (!orderDateVal || !actualDateVal) {
      form.setValue('delivery_days', undefined);
      form.setValue('kpi_warning', false);
      return;
    }
    const start = orderDateVal instanceof Date ? orderDateVal : new Date(orderDateVal);
    const end = actualDateVal instanceof Date ? actualDateVal : new Date(actualDateVal);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return;
    const days = businessDaysBetween(start, end);
    form.setValue('delivery_days', days);
    form.setValue('kpi_warning', days > 2);
  }, [orderDate, actualDeliveryDate, form]);

  const onSubmit = async (data: OrderSchemaType) => {
    try {
      // Calculate total to ensure it's correct
      const calculatedTotal = data.subtotal_amount - (data.discount_amount || 0) + (data.tax_amount || 0);

      // Transform data to ensure proper format
      const formData: OrderFormData = {
        order_number: data.order_number,
        order_date: data.order_date,
        // Send explicit null (not undefined) for cleared dates so the BE actually
        // clears them - undefined is dropped by JSON.stringify and skipped by the
        // backend's exclude_unset, so clearing the field would otherwise be a no-op.
        estimated_delivery_date: data.estimated_delivery_date || null,
        actual_delivery_date: data.actual_delivery_date || null,
        customer_id: data.customer_id ?? undefined,
        order_status_id: data.order_status_id,
        billing_address_id: data.billing_address_id || undefined,
        shipping_address_id: data.shipping_address_id || undefined,
        created_time: data.created_time || undefined,
        debtor_code: data.debtor_code || undefined,
        debtor_name: data.debtor_name || undefined,
        agent: data.agent || undefined,
        is_cancelled: data.is_cancelled ?? false,
        remarks_cs: data.remarks_cs || undefined,
        order_type: data.order_type || undefined,
        pickup_time: data.pickup_time || undefined,
        checker: data.checker || undefined,
        transporter: data.transporter || undefined,
        driver_name: data.driver_name || undefined,
        lorry_plate: data.lorry_plate || undefined,
        customer_ref: data.customer_ref || undefined,
        delivery_remarks_cs: data.delivery_remarks_cs || undefined,
        delivery_remarks: data.delivery_remarks || undefined,
        salesman: data.salesman || undefined,
        trips: data.trips ?? undefined,
        warehouse: data.warehouse || undefined,
        delivery_days: data.delivery_days ?? undefined,
        kpi_warning: data.kpi_warning ?? false,
        subtotal_amount: typeof data.subtotal_amount === 'number' ? data.subtotal_amount : Number(data.subtotal_amount),
        discount_amount: data.discount_amount || 0,
        tax_amount: data.tax_amount || 0,
        total_amount: calculatedTotal,
        remarks: data.remarks || undefined,
      };

      if (isEditMode && orderId) {
        const editable = Object.fromEntries(
          Object.entries(formData).filter(([key]) => !autocountOwned.has(key)),
        ) as OrderFormData;
        await updateMutation.mutateAsync({ id: orderId, data: editable });
      } else {
        await createMutation.mutateAsync(formData);
      }

      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/order-management/orders');
      }
    } catch (error) {
      // Error is handled by the mutation hook
      console.error('Order form submission error:', error);
    }
  };

  if (isEditMode && isLoadingOrder) {
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
        {isEditMode && orderId && (
          <div className="flex justify-end items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="outline" size="icon" aria-label="Delivery order status">
                  <Settings className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {(() => {
                  const list = orderStatuses ?? [];
                  const newOrDelivered = list.filter((s) => {
                    const code = (s.status_code ?? '').toString().trim().toLowerCase();
                    return code === 'new' || code === 'delivered';
                  });
                  const options = newOrDelivered.length > 0 ? newOrDelivered : list;
                  return (
                    <>
                      {options.map((status) => (
                        <DropdownMenuItem
                          key={status.id}
                          onClick={() => form.setValue('order_status_id', status.id)}
                        >
                          {status.status_name}
                        </DropdownMenuItem>
                      ))}
                      {options.length === 0 && (
                        <DropdownMenuItem disabled>No statuses available</DropdownMenuItem>
                      )}
                    </>
                  );
                })()}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
        <Tabs defaultValue="basic" className="w-full">
          {/* Same tab set, same order and the same underline as the read view. */}
          <TabsList className="mb-5">
            <TabsTrigger value="basic">
              <Info />
              <span>Basic Information</span>
            </TabsTrigger>
            <TabsTrigger value="remarks">
              <StickyNote />
              <span>Remarks</span>
            </TabsTrigger>
            <TabsTrigger value="tracking">
              <Truck />
              <span>Delivery &amp; Tracking</span>
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: Basic Information */}
          <TabsContent value="basic">
            <Card>
              <CardHeader>
                <CardTitle>Basic Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                <FormField
                  control={form.control}
                  name="order_number"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Delivery Order Number *</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="ORD-001"
                          {...field}
                          disabled={isEditMode}
                        />
                      </FormControl>
                      <FormDescription>
                        {fromAutoCount('order_number') ? 'From AutoCount' : 'Unique delivery order number'}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField
                    control={form.control}
                    name="order_date"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Delivery Order Date *</FormLabel>
                        <FormControl>
                          <Input
                            type="date"
                            value={field.value ? field.value.toISOString().slice(0, 10) : ''}
                            onChange={(e) =>
                              field.onChange(e.target.value ? new Date(e.target.value) : undefined)
                            }
                            disabled={fromAutoCount('order_date')}
                          />
                        </FormControl>
                        {fromAutoCount('order_date') && <FormDescription>From AutoCount</FormDescription>}
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="estimated_delivery_date"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Estimated Delivery Date</FormLabel>
                        <FormControl>
                          <Input
                            type="date"
                            value={field.value ? field.value.toISOString().slice(0, 10) : ''}
                            onChange={(e) =>
                              field.onChange(e.target.value ? new Date(e.target.value) : undefined)
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField
                    control={form.control}
                    name="actual_delivery_date"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Actual Delivery Date</FormLabel>
                        <FormControl>
                          <Input
                            type="date"
                            value={field.value ? field.value.toISOString().slice(0, 10) : ''}
                            onChange={(e) =>
                              field.onChange(e.target.value ? new Date(e.target.value) : undefined)
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField
                    control={form.control}
                    name="order_status_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Delivery Order Status *</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            value={field.value || ''}
                            onChange={field.onChange}
                            options={[
                              ...(orderStatuses?.map((status) => ({
                                value: status.id,
                                label: `${status.status_code} - ${status.status_name}`,
                              })) ?? []),
                              ...(!orderStatuses || orderStatuses.length === 0
                                ? [{ value: '__no_statuses__', label: 'No delivery order statuses available', disabled: true }]
                                : []),
                            ]}
                            placeholder="Select delivery order status"
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="order_type"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Delivery Order Type</FormLabel>
                        <FormControl>
                          <Input placeholder="e.g. BEYOND, CG" {...field} value={field.value ?? ''} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="debtor_code"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Debtor Code</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g. 300-A007"
                            {...field}
                            value={field.value ?? ''}
                            disabled={fromAutoCount('debtor_code')}
                          />
                        </FormControl>
                        {fromAutoCount('debtor_code') && <FormDescription>From AutoCount</FormDescription>}
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="debtor_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Debtor Name</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="Company name"
                            {...field}
                            value={field.value ?? ''}
                            disabled={fromAutoCount('debtor_name')}
                          />
                        </FormControl>
                        {fromAutoCount('debtor_name') && <FormDescription>From AutoCount</FormDescription>}
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="agent"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Agent</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="Agent name"
                            {...field}
                            value={field.value ?? ''}
                            disabled={fromAutoCount('agent')}
                          />
                        </FormControl>
                        {fromAutoCount('agent') && <FormDescription>From AutoCount</FormDescription>}
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="remarks_cs"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Remarks CS</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g. PS25-2053"
                            {...field}
                            value={field.value ?? ''}
                            disabled={remarksCsLocked}
                          />
                        </FormControl>
                        {remarksCsLocked && (
                          <FormDescription>
                            Locked - this delivery order is delivered and linked to a complaint.
                          </FormDescription>
                        )}
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="is_cancelled"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center gap-2 space-y-0">
                        <FormControl>
                          <input
                            type="checkbox"
                            checked={!!field.value}
                            onChange={(e) => field.onChange(e.target.checked)}
                            className="h-4 w-4 rounded border-input"
                            disabled={fromAutoCount('is_cancelled')}
                          />
                        </FormControl>
                        <FormLabel className="font-normal">Cancelled</FormLabel>
                        {fromAutoCount('is_cancelled') && <FormDescription>From AutoCount</FormDescription>}
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="remarks">
            <Card>
              <CardHeader>
                <CardTitle>Remarks</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                <FormField
                  control={form.control}
                  name="remarks"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Remarks</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder="Enter any additional notes or remarks..."
                          {...field}
                          value={field.value || ''}
                          rows={8}
                          disabled={fromAutoCount('remarks')}
                        />
                      </FormControl>
                      {fromAutoCount('remarks') && <FormDescription>From AutoCount</FormDescription>}
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab 3: Delivery & Tracking */}
          <TabsContent value="tracking">
            <Card>
              <CardHeader>
                <CardTitle>Delivery & Tracking</CardTitle>
              </CardHeader>
              <CardContent className="space-y-8">
                <div className="space-y-4">
                  <h4 className="text-sm font-semibold text-muted-foreground">Master Information</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormField
                      control={form.control}
                      name="order_type"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Type</FormLabel>
                          <FormControl>
                            <Input placeholder="BEYOND" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="debtor_code"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Debtor Code</FormLabel>
                          <FormControl>
                            <Input
                              placeholder="300-A007"
                              {...field}
                              value={field.value || ''}
                              disabled={fromAutoCount('debtor_code')}
                            />
                          </FormControl>
                          {fromAutoCount('debtor_code') && <FormDescription>From AutoCount</FormDescription>}
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="debtor_name"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Debtor Name</FormLabel>
                          <FormControl>
                            <Input
                              placeholder="ASIAN PAC HOLDINGS BERHAD"
                              {...field}
                              value={field.value || ''}
                              disabled={fromAutoCount('debtor_name')}
                            />
                          </FormControl>
                          {fromAutoCount('debtor_name') && <FormDescription>From AutoCount</FormDescription>}
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="agent"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Agent</FormLabel>
                          <FormControl>
                            <Input
                              placeholder="ERIC"
                              {...field}
                              value={field.value || ''}
                              disabled={fromAutoCount('agent')}
                            />
                          </FormControl>
                          {fromAutoCount('agent') && <FormDescription>From AutoCount</FormDescription>}
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="is_cancelled"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Cancelled</FormLabel>
                          <FormControl>
                            <SearchableSelect
                              value={field.value ? 'true' : 'false'}
                              onChange={(value) => field.onChange(value === 'true')}
                              options={[
                                { value: 'false', label: 'No' },
                                { value: 'true', label: 'Yes' },
                              ]}
                              placeholder="Select"
                              disabled={fromAutoCount('is_cancelled')}
                            />
                          </FormControl>
                          {fromAutoCount('is_cancelled') && <FormDescription>From AutoCount</FormDescription>}
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="remarks_cs"
                      render={({ field }) => (
                        <FormItem className="md:col-span-2">
                          <FormLabel>Remarks CS</FormLabel>
                          <FormControl>
                            <Textarea
                              placeholder="Remarks from CS..."
                              {...field}
                              value={field.value || ''}
                              rows={3}
                              disabled={remarksCsLocked}
                            />
                          </FormControl>
                          {remarksCsLocked && (
                            <FormDescription>
                              Locked - this delivery order is delivered and linked to a complaint.
                            </FormDescription>
                          )}
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                </div>

                <div className="space-y-4">
                  <h4 className="text-sm font-semibold text-muted-foreground">Delivery Information</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormField
                      control={form.control}
                      name="pickup_time"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Delivery Time</FormLabel>
                          <FormControl>
                            <Input placeholder="8:47 AM" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="checker"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Checker</FormLabel>
                          <FormControl>
                            <Input placeholder="indra" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="transporter"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Transporter</FormLabel>
                          <FormControl>
                            <Input placeholder="SORENTO" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="driver_name"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Driver Name</FormLabel>
                          <FormControl>
                            <Input placeholder="AZLAN" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="lorry_plate"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Lorry Plate</FormLabel>
                          <FormControl>
                            <Input placeholder="ABC 1234" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="customer_ref"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Customer</FormLabel>
                          <FormControl>
                            <Input placeholder="CPI EMPOWER SDN BHD (PROJECT)" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                </div>

                <div className="space-y-4">
                  <h4 className="text-sm font-semibold text-muted-foreground">Delivery Details</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormField
                      control={form.control}
                      name="delivery_remarks_cs"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Remarks CS</FormLabel>
                          <FormControl>
                            <Textarea placeholder="CS remarks..." {...field} value={field.value || ''} rows={3} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="delivery_remarks"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Remarks</FormLabel>
                          <FormControl>
                            <Textarea placeholder="Delivery remarks..." {...field} value={field.value || ''} rows={3} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="salesman"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Salesman</FormLabel>
                          <FormControl>
                            <Input placeholder="ACT" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="trips"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Trips</FormLabel>
                          <FormControl>
                            <Input
                              type="number"
                              {...field}
                              value={field.value ?? ''}
                              onChange={(e) => field.onChange(e.target.value ? Number(e.target.value) : undefined)}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="warehouse"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Warehouse</FormLabel>
                          <FormControl>
                            <Input placeholder="BRW" {...field} value={field.value || ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                </div>

                <div className="space-y-4">
                  <h4 className="text-sm font-semibold text-muted-foreground">Delivery Days (2)</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <FormField
                      control={form.control}
                      name="delivery_days"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Delivery Days (2)</FormLabel>
                          <FormControl>
                            <Input value={field.value ?? ''} disabled />
                          </FormControl>
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="kpi_warning"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Over 2 days</FormLabel>
                          <FormControl>
                            <Input value={field.value ? 'Yes' : 'No'} disabled />
                          </FormControl>
                        </FormItem>
                      )}
                    />
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

        <div className="flex justify-end gap-4 pt-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              if (onSuccess) {
                onSuccess();
              } else {
                router.push('/order-management/orders');
              }
            }}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={isLoading}>
            {isLoading ? (
              <>
                <LoaderCircleIcon className="mr-2 size-4 animate-spin" />
                {isEditMode ? 'Updating...' : 'Creating...'}
              </>
            ) : (
              <>
                <Save className="mr-2 size-4" />
                {isEditMode ? 'Update Delivery Order' : 'Create Delivery Order'}
              </>
            )}
          </Button>
        </div>
      </form>
    </Form>
  );
}
