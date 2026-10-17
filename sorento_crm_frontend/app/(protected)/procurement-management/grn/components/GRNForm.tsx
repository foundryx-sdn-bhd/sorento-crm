'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, useFieldArray, type Resolver } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { LoaderCircleIcon, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
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
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ProductComboboxSearchable } from '@/app/(protected)/procurement-management/packing-lists/components/ProductComboboxSearchable';
import { WarehouseCombobox } from '@/app/(protected)/procurement-management/spo-allocations/components/WarehouseCombobox';
import { useCreateGRN, useUpdateGRN, useGRN } from '../hooks/useGRN';
import { grnSchema, type GRNSchemaType } from '../forms/grn-schema';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import { getWarehouses } from '@/app/(protected)/inventory-management/warehouses/services/warehouseService';
import type { GRNDetail, GRNFormData } from '../types/grn.types';
import type { Warehouse } from '@/app/(protected)/inventory-management/warehouses/types/warehouse.types';

interface GRNFormProps {
  grnId?: string;
  onSuccess?: () => void;
}

type RemoveConfirmState =
  | { type: 'idle' }
  | { type: 'single'; index: number }
  | { type: 'bulk'; indices: number[] };

type PickingStatusForm = 'draft' | 'approved' | 'rejected';
const VALID_PICKING_STATUSES: PickingStatusForm[] = ['draft', 'approved', 'rejected'];

function normalizePickingStatus(g: { picking_status?: string; pickingStatus?: string; status?: string }): PickingStatusForm {
  const raw =
    g.picking_status ?? g.pickingStatus ?? g.status;
  const normalized = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  if (normalized === 'approved') return 'approved';
  if (normalized === 'rejected') return 'rejected';
  return 'draft';
}

export default function GRNForm({ grnId, onSuccess }: GRNFormProps) {
  const router = useRouter();
  const isEditMode = !!grnId;
  const { data: grn, isLoading: isLoadingGRN } = useGRN(grnId ?? null);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [removeConfirm, setRemoveConfirm] = useState<RemoveConfirmState>({ type: 'idle' });
  const [selectedLineIndices, setSelectedLineIndices] = useState<Set<number>>(new Set());

  const createMutation = useCreateGRN();
  const updateMutation = useUpdateGRN();

  const form = useForm<GRNSchemaType>({
    resolver: zodResolver(grnSchema) as Resolver<GRNSchemaType>,
    mode: 'onTouched',
    defaultValues: {
      picking_number: '',
      spo_number: '',
      picking_date: new Date().toISOString().slice(0, 10),
      picking_status: 'draft',
      notes: '',
      picking_lines: [
        { product_id: '', quantity_expected: 0, quantity_picked: 0, source_warehouse_id: '' },
      ],
    },
  });

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: 'picking_lines',
  });

  useEffect(() => {
    getWarehouses({
      pageIndex: 0,
      pageSize: 100,
      sorting: [],
      searchQuery: '',
      is_active: true,
    }).then((res) => {
      setWarehouses(res.data ?? []);
    });
  }, []);

  const fetchProducts = useCallback(
    async (query: string, pageIndex: number) => {
      return getProducts({
        pageIndex,
        pageSize: 50,
        sorting: [{ id: 'product_code', desc: false }],
        searchQuery: query,
        status: 'active',
      });
    },
    [],
  );

  const lastInitializedIdRef = useRef<string | null>(null);

  useLayoutEffect(() => {
    if (!grn || !isEditMode) return;
    const g = grn as GRNDetail;
    const pickingStatus = normalizePickingStatus(g);
    const payload = {
      picking_number: g.picking_number ?? '',
      spo_number: g.spo_number ?? '',
      picking_date: g.picking_date
        ? new Date(g.picking_date).toISOString().slice(0, 10)
        : new Date().toISOString().slice(0, 10),
      picking_status: pickingStatus,
      notes: g.notes ?? '',
      picking_lines:
        g.picking_lines && g.picking_lines.length > 0
          ? g.picking_lines.map((l) => ({
              product_id: l.product_id,
              quantity_expected: l.quantity_expected ?? 0,
              quantity_picked: l.quantity_picked ?? 0,
              source_warehouse_id: l.source_warehouse_id ?? undefined,
              spo_allocation_id: l.spo_allocation_id ?? undefined,
              spo_number_raw: l.spo_number_raw ?? undefined,
              picked_condition: l.picked_condition ?? undefined,
            }))
          : [{ product_id: '', quantity_expected: 0, quantity_picked: 0, source_warehouse_id: '' }],
    };
    form.reset(payload);
    lastInitializedIdRef.current = grn.id;
  }, [grn, isEditMode, form]);

  useEffect(() => {
    if (!isEditMode || !grn) return;
    const wanted = normalizePickingStatus(grn);
    const current = form.getValues('picking_status');
    if (current !== wanted && VALID_PICKING_STATUSES.includes(wanted)) {
      form.setValue('picking_status', wanted);
    }
  }, [grn, isEditMode, form]);

  const handleRemoveSingle = (index: number) => {
    setRemoveConfirm({ type: 'single', index });
  };

  const handleBulkRemove = () => {
    const indices = Array.from(selectedLineIndices).sort((a, b) => a - b);
    if (indices.length === 0) return;
    setRemoveConfirm({ type: 'bulk', indices });
  };

  const toggleLineSelection = (index: number) => {
    setSelectedLineIndices((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const selectAllLines = () => {
    if (selectedLineIndices.size === fields.length) {
      setSelectedLineIndices(new Set());
    } else {
      setSelectedLineIndices(new Set(fields.map((_, i) => i)));
    }
  };

  const confirmRemove = () => {
    if (removeConfirm.type === 'single') {
      remove(removeConfirm.index);
      setSelectedLineIndices((prev) => {
        const next = new Set<number>();
        prev.forEach((i) => {
          if (i < removeConfirm.index) next.add(i);
          else if (i > removeConfirm.index) next.add(i - 1);
        });
        return next;
      });
    } else if (removeConfirm.type === 'bulk') {
      removeConfirm.indices
        .sort((a, b) => b - a)
        .forEach((i) => remove(i));
      setSelectedLineIndices(new Set());
    }
    setRemoveConfirm({ type: 'idle' });
  };

  const grnDetail = grn as GRNDetail | undefined;
  const serverApproved =
    isEditMode && !!grnDetail && normalizePickingStatus(grnDetail) === 'approved';

  const onSubmit = async (data: GRNSchemaType) => {
    try {
      const lines = data.picking_lines
        ?.filter((l) => l.product_id && (l.quantity_expected > 0 || l.quantity_picked > 0))
        .map((l) => ({
          product_id: l.product_id,
          quantity_expected: l.quantity_expected,
          quantity_picked: l.quantity_picked,
          source_warehouse_id: l.source_warehouse_id || undefined,
          spo_allocation_id: l.spo_allocation_id || undefined,
          spo_number_raw: l.spo_number_raw ?? undefined,
          picked_condition: l.picked_condition || 'good',
        }));

      if (isEditMode && grnId) {
        const spoTrimmed = (data.spo_number ?? '').trim();
        const editPayload: Partial<GRNFormData> = {
          // null serializes so the API clears spo_number; undefined is omitted and leaves DB unchanged
          spo_number: spoTrimmed ? spoTrimmed : null,
          picking_date: data.picking_date,
          picking_status: data.picking_status,
          notes: data.notes || undefined,
        };
        if (!serverApproved) {
          editPayload.picking_lines = lines?.length ? lines : [];
        }
        await updateMutation.mutateAsync({
          id: grnId,
          data: editPayload,
        });
      } else {
        await createMutation.mutateAsync({
          picking_number: data.picking_number,
          spo_number: data.spo_number || undefined,
          picking_type: 'goods_received',
          picking_date: data.picking_date,
          picking_status: data.picking_status,
          inspection_status: 'pending',
          notes: data.notes || undefined,
          picking_lines: lines?.length ? lines : [],
        } as any);
      }
      if (onSuccess) {
        onSuccess();
      } else {
        router.push(
          isEditMode
            ? `/procurement-management/grn/${grnId}`
            : '/procurement-management/grn',
        );
      }
    } catch (error) {
      console.error('GRN form submission error:', error);
    }
  };

  if (isEditMode && isLoadingGRN) {
    return (
      <div className="flex justify-center p-8">
        <LoaderCircleIcon className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;
  const pickingLines = grnDetail?.picking_lines ?? [];

  return (
    <>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>
                {isEditMode ? (serverApproved ? 'View GRN' : 'Edit GRN') : 'Create GRN'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <FormField
                  control={form.control}
                  name="picking_number"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>GRN Number *</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="e.g. GRN-001"
                          {...field}
                          disabled={isEditMode}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="spo_number"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>SPO Number</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="e.g. SPO-2026/01-0001"
                          {...field}
                          disabled={serverApproved}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="picking_date"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Picking Date *</FormLabel>
                      <FormControl>
                        <Input type="date" {...field} disabled={serverApproved} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="picking_status"
                  render={({ field }) => {
                    const fromForm = field.value as string | undefined;
                    const validFormValue = fromForm && VALID_PICKING_STATUSES.includes(fromForm as PickingStatusForm);
                    const resolvedStatus: PickingStatusForm = validFormValue
                      ? (fromForm as PickingStatusForm)
                      : (isEditMode && grn ? normalizePickingStatus(grn) : 'draft');
                    return (
                      <FormItem>
                        <FormLabel>Status</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            value={resolvedStatus}
                            onChange={(v) => {
                              if (!VALID_PICKING_STATUSES.includes(v as PickingStatusForm)) {
                                return;
                              }
                              field.onChange(v as PickingStatusForm);
                            }}
                            disabled={serverApproved}
                            placeholder="Status"
                            options={[
                              { value: 'draft', label: 'Draft' },
                              { value: 'approved', label: 'Approved' },
                              { value: 'rejected', label: 'Rejected' },
                            ]}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    );
                  }}
                />
                <FormField
                  control={form.control}
                  name="notes"
                  render={({ field }) => (
                    <FormItem className="sm:col-span-2">
                      <FormLabel>Notes</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Optional notes"
                          {...field}
                          disabled={serverApproved}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Picking Lines</CardTitle>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={serverApproved}
                    onClick={() =>
                      append({
                        product_id: '',
                        quantity_expected: 0,
                        quantity_picked: 0,
                        source_warehouse_id: '',
                      })
                    }
                  >
                    <Plus className="size-4" />
                    Add Line
                  </Button>
                  {fields.length > 0 && (
                    <>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={selectAllLines}
                        disabled={serverApproved}
                      >
                        {selectedLineIndices.size === fields.length
                          ? 'Deselect All'
                          : 'Select All'}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="text-destructive"
                        onClick={handleBulkRemove}
                        disabled={serverApproved || selectedLineIndices.size === 0}
                      >
                        <Trash2 className="size-4" />
                        Bulk Remove ({selectedLineIndices.size})
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {fields.map((field, index) => (
                  <div
                    key={field.id}
                    className="flex flex-wrap items-end gap-2 rounded border p-3"
                  >
                    <FormItem className="flex items-end pb-2">
                      <FormControl>
                        <Checkbox
                          checked={selectedLineIndices.has(index)}
                          onCheckedChange={() => toggleLineSelection(index)}
                          disabled={serverApproved}
                        />
                      </FormControl>
                    </FormItem>
                    <FormField
                      control={form.control}
                      name={`picking_lines.${index}.product_id`}
                      render={({ field: f }) => (
                        <FormItem className="min-w-[200px] flex-1">
                          <FormLabel>Product *</FormLabel>
                          <FormControl>
                            <ProductComboboxSearchable
                              value={f.value}
                              onChange={f.onChange}
                              fetchProducts={fetchProducts}
                              disabled={serverApproved}
                              productFallback={
                                pickingLines[index]?.product
                                  ? {
                                      id: pickingLines[index].product!.id,
                                      product_code:
                                        pickingLines[index].product!.product_code,
                                      product_name:
                                        pickingLines[index].product!.product_name,
                                    }
                                  : undefined
                              }
                              placeholder="Search or select product"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name={`picking_lines.${index}.quantity_expected`}
                      render={({ field: f }) => (
                        <FormItem className="w-32">
                          <FormLabel>Qty Expected</FormLabel>
                          <FormControl>
                            <NumberInput
                              min={0}
                              {...f}
                              disabled={serverApproved}
                              onChange={(e) => f.onChange(e.target.value)}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name={`picking_lines.${index}.quantity_picked`}
                      render={({ field: f }) => (
                        <FormItem className="w-32">
                          <FormLabel>Qty Received</FormLabel>
                          <FormControl>
                            <NumberInput
                              min={0}
                              {...f}
                              disabled={serverApproved}
                              onChange={(e) => f.onChange(e.target.value)}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name={`picking_lines.${index}.source_warehouse_id`}
                      render={({ field: f }) => (
                        <FormItem className="min-w-[180px] flex-1">
                          <FormLabel>Location *</FormLabel>
                          <FormControl>
                            <WarehouseCombobox
                              value={f.value ?? ''}
                              onChange={f.onChange}
                              warehouses={warehouses}
                              disabled={serverApproved}
                              warehouseFallback={
                                pickingLines[index]?.source_warehouse
                                  ? {
                                      id: pickingLines[index].source_warehouse!.id,
                                      warehouse_code:
                                        pickingLines[index].source_warehouse!
                                          .warehouse_code,
                                      warehouse_name:
                                        pickingLines[index].source_warehouse!
                                          .warehouse_name,
                                    }
                                  : undefined
                              }
                              placeholder="Select warehouse"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => handleRemoveSingle(index)}
                      className="text-destructive"
                      disabled={serverApproved}
                      aria-label="Delete line"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <div className="flex gap-2">
            <Button type="submit" disabled={isLoading || serverApproved}>
              {isLoading && (
                <LoaderCircleIcon className="me-2 size-4 animate-spin" />
              )}
              {isEditMode ? 'Update' : 'Create'}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.back()}
            >
              Cancel
            </Button>
          </div>
        </form>
      </Form>

      <AlertDialog
        open={removeConfirm.type !== 'idle'}
        onOpenChange={(open) => {
          if (!open) setRemoveConfirm({ type: 'idle' });
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove line(s)?</AlertDialogTitle>
            <AlertDialogDescription>
              {removeConfirm.type === 'single'
                ? 'Are you sure you want to remove this line?'
                : removeConfirm.type === 'bulk'
                  ? `Are you sure you want to remove ${removeConfirm.indices.length} line(s)?`
                  : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={(e) => {
                e.preventDefault();
                confirmRemove();
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
