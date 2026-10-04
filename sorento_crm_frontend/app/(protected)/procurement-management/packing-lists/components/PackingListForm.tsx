'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
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
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { PACKING_LIST_REGION_OPTIONS } from '@/lib/packing-list-regions';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  useCreatePackingList,
  useUpdatePackingList,
  usePackingList,
  useClearanceCheckpoints,
} from '../hooks/usePackingLists';
import {
  packingListSchema,
  clearanceSchema,
  CLEARANCE_ATTRIBUTE_FIELDS,
  type PackingListSchemaType,
} from '../forms/packing-list-schema';
import { useSupplierSelectQuery } from '../../suppliers/hooks/useSupplierSelectQuery';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import { ProductCombobox } from './ProductCombobox';
import { SupplierCombobox } from './SupplierCombobox';

/** Every clearance key, straight off the schema - one list, three uses
 * (defaults, hydration, submit), so none of them can fall behind the others. */
const CLEARANCE_KEYS = Object.keys(clearanceSchema.shape) as Array<
  keyof typeof clearanceSchema.shape
>;

/** Checkpoints whose field is already an input in the Shipment card above.
 * ETA is a checkpoint AND the packing list's own `estimated_arrival_date`, so
 * rendering it in both places would bind two inputs to one field - edit one and
 * the other silently disagrees until the form re-renders. The Shipment card keeps
 * it, because that is where someone creating a packing list expects to type it. */
const CHECKPOINTS_RENDERED_ELSEWHERE = new Set(['estimated_arrival_date']);

const emptyClearance = () =>
  Object.fromEntries(CLEARANCE_KEYS.map((k) => [k, ''])) as Record<string, string>;

/** ISO date -> yyyy-mm-dd for <input type="date">; anything else passes through. */
const toFormValue = (v: unknown): string | number => {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return v;
  const s = String(v);
  return /^\d{4}-\d{2}-\d{2}T/.test(s) ? s.slice(0, 10) : s;
};

interface PackingListFormProps {
  packingListId?: string;
  onSuccess?: () => void;
}

export default function PackingListForm({
  packingListId,
  onSuccess,
}: PackingListFormProps) {
  const router = useRouter();
  const isEditMode = !!packingListId;
  const { data: packingList, isLoading: isLoadingPackingList } = usePackingList(packingListId ?? null);
  const { data: suppliers = [] } = useSupplierSelectQuery();
  // Same source as the read-only timeline, so a checkpoint renamed or
  // deactivated in config changes both views at once instead of drifting.
  const { data: checkpoints = [] } = useClearanceCheckpoints();
  const [products, setProducts] = useState<Array<{ id: string; product_code: string; product_name?: string }>>([]);
  const [productSearch, setProductSearch] = useState('');

  const createMutation = useCreatePackingList();
  const updateMutation = useUpdatePackingList();

  const form = useForm<PackingListSchemaType>({
    resolver: zodResolver(packingListSchema) as Resolver<PackingListSchemaType>,
    mode: 'onTouched',
    defaultValues: {
      supplier_id: '',
      shipment_date: '',
      estimated_arrival_date: '',
      bill_of_lading_number: '',
      shipping_container_number: '',
      invoice_number: '',
      shipment_status: 'in_transit',
      regions: ['west'],
      shipment_lines: [{ product_id: '', quantity_shipped: 1 }],
      ...emptyClearance(),
    },
  });

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: 'shipment_lines',
  });

  // Server-side product search. Refetches whenever the combobox input changes
  // (debounced inside ProductCombobox). Backend product list endpoint already
  // filters by code / name / description via the `query` param, so any of the
  // 10k+ products in the catalog is reachable - no more 500-row client slice.
  useEffect(() => {
    let cancelled = false;
    getProducts({
      pageIndex: 0,
      pageSize: 100,
      sorting: [],
      searchQuery: productSearch,
      status: 'active',
    }).then((res) => {
      if (!cancelled) setProducts(res.data ?? []);
    });
    return () => {
      cancelled = true;
    };
  }, [productSearch]);

  /** Every product we have ever resolved a label for, keyed by id.
   *  The combobox only ever holds ONE page of search results, and a line's saved
   *  product is usually not on it, so a line whose product is missing from the
   *  page renders as an empty "Select product" trigger even though the form value
   *  is intact. Keying the fallback by id (not by row index) is what makes a row
   *  survive a `remove()` - indexes shift, product ids do not. */
  const [knownProducts, setKnownProducts] = useState<
    Record<string, { id: string; product_code: string; product_name?: string }>
  >({});

  const mergeKnownProducts = (
    incoming: Array<{ id: string; product_code: string; product_name?: string } | undefined | null>,
  ) => {
    setKnownProducts((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const p of incoming) {
        if (p?.id && !next[p.id]) {
          next[p.id] = p;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  };

  useEffect(() => {
    mergeKnownProducts(products);
  }, [products]);

  useEffect(() => {
    mergeKnownProducts((packingList?.shipment_lines ?? []).map((l) => l.product));
  }, [packingList]);

  const lastInitializedIdRef = useRef<string | null>(null);
  useEffect(() => {
    lastInitializedIdRef.current = null;
    return () => { lastInitializedIdRef.current = null; };
  }, [packingListId]);

  useLayoutEffect(() => {
    if (!packingList || !isEditMode || lastInitializedIdRef.current === packingList.id) return;

    form.reset({
      supplier_id: packingList.supplier_id ?? '',
      shipment_date: packingList.shipment_date ? new Date(packingList.shipment_date).toISOString().slice(0, 10) : '',
      estimated_arrival_date: packingList.estimated_arrival_date
        ? new Date(packingList.estimated_arrival_date).toISOString().slice(0, 10)
        : '',
      bill_of_lading_number: packingList.bill_of_lading_number ?? '',
      shipping_container_number: packingList.shipping_container_number ?? '',
      invoice_number: packingList.invoice_number ?? '',
      shipment_status: packingList.shipment_status ?? 'in_transit',
      regions: (packingList.regions?.length ? packingList.regions : ['west']) as ('west' | 'east')[],
      shipment_lines:
        packingList.shipment_lines?.length && packingList.shipment_lines.length > 0
          ? packingList.shipment_lines.map((l) => ({
              id: l.id,
              product_id: l.product_id,
              quantity_shipped: l.quantity_shipped,
              supplier_id: l.supplier_id ?? undefined,
            }))
          : [{ product_id: '', quantity_shipped: 1 }],
      ...(Object.fromEntries(
        CLEARANCE_KEYS.map((k) => [
          k,
          toFormValue((packingList as unknown as Record<string, unknown>)[k]),
        ]),
      ) as Record<string, string | number>),
    });
    lastInitializedIdRef.current = packingList.id;
  }, [packingList, isEditMode, form]);

  const onSubmit = async (data: PackingListSchemaType) => {
    try {
      const payload = {
        supplier_id: data.supplier_id || undefined,
        shipment_date: data.shipment_date,
        estimated_arrival_date: data.estimated_arrival_date || undefined,
        bill_of_lading_number: data.bill_of_lading_number || undefined,
        shipping_container_number: data.shipping_container_number || undefined,
        invoice_number: data.invoice_number || undefined,
        shipment_status: data.shipment_status || 'in_transit',
        regions: data.regions,
        shipment_lines: data.shipment_lines
          ?.filter((l) => l.product_id && l.quantity_shipped > 0)
          .map((l) => ({
            // Which line this is. Two lines of one product from one supplier are legal
            // now (the supplier's carton split), so the backend claims by id first and
            // refuses an id-less line it cannot tell apart.
            id: l.id,
            product_id: l.product_id,
            quantity_shipped: l.quantity_shipped,
            // Whose line it already was. There is no per-line picker here on purpose -
            // the attribution comes from the packing list that was uploaded - but a save
            // that dropped it would hand a mixed container's lines to the header supplier.
            // A new line leaves it unset and the backend falls back to the header.
            supplier_id: l.supplier_id ?? undefined,
          })),
        // Blank clears the field: send null, not undefined. `exclude_unset` on the
        // backend drops undefined entirely, so omitting it would make a cleared
        // date impossible to save - it would silently keep its old value.
        ...Object.fromEntries(
          CLEARANCE_KEYS.map((k) => {
            const v = (data as Record<string, unknown>)[k];
            return [k, v === '' || v === undefined ? null : v];
          }),
        ),
      };

      if (isEditMode && packingListId) {
        await updateMutation.mutateAsync({ id: packingListId, data: payload as any });
      } else {
        await createMutation.mutateAsync(payload as any);
      }
      if (onSuccess) {
        onSuccess();
      } else {
        router.push(isEditMode ? `/procurement-management/packing-lists/${packingListId}` : '/procurement-management/packing-lists');
      }
    } catch (error) {
      console.error('Packing list form submission error:', error);
    }
  };

  if (isEditMode && isLoadingPackingList) {
    return (
      <div className="flex justify-center p-8">
        <LoaderCircleIcon className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{isEditMode ? 'Edit Packing List' : 'Create Packing List'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {/* No Shipment Number field. It is ours to issue, not something to invent
                  before the container has been described - the backend numbers a packing
                  list that arrives without one, and it stays editable on the detail page. */}
              <FormField
                control={form.control}
                name="shipping_container_number"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Container Number</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g. TIIU4090481" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="supplier_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Supplier</FormLabel>
                    <FormControl>
                      <SupplierCombobox
                        value={field.value ?? ''}
                        onChange={field.onChange}
                        suppliers={suppliers}
                        supplierFallback={packingList?.supplier}
                        placeholder="Select supplier"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="shipment_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Shipment Date *</FormLabel>
                    <FormControl>
                      <Input type="date" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="estimated_arrival_date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Estimated Arrival Date</FormLabel>
                    <FormControl>
                      <Input type="date" {...field} value={field.value || ''} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="regions"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Regions</FormLabel>
                    <FormControl>
                      <SearchableMultiSelect
                        value={field.value ?? []}
                        onChange={(next) => {
                          if (next.length > 0) field.onChange(next);
                        }}
                        options={PACKING_LIST_REGION_OPTIONS}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="bill_of_lading_number"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Bill of Lading Number</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="invoice_number"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Invoice Number</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
          </CardContent>
        </Card>

        {/* Clearance & Delivery - the contingency path.
            The workbook import normally fills these, but it is not the only way
            they arrive: before the first import, or when a liner revises an ETA
            between imports, someone types the date in here.

            Checkpoint labels and order come from the SAME config the read-only
            timeline reads, so renaming or deactivating a checkpoint moves both
            views together instead of leaving the edit form describing a
            checkpoint the detail page no longer shows. */}
        <Card>
          <CardHeader>
            <CardTitle>Clearance &amp; Delivery</CardTitle>
            <p className="text-sm text-muted-foreground">
              Filled by the Container Status import. Enter them by hand when the
              import has not run, or when a date changed after it did.
            </p>
          </CardHeader>
          <CardContent className="space-y-6">
            {checkpoints.some((cp) => !CHECKPOINTS_RENDERED_ELSEWHERE.has(cp.field)) && (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {checkpoints
                  .filter((cp) => !CHECKPOINTS_RENDERED_ELSEWHERE.has(cp.field))
                  .map((cp) => (
                  <FormField
                    key={cp.field}
                    control={form.control}
                    name={cp.field as keyof PackingListSchemaType}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{cp.label}</FormLabel>
                        <FormControl>
                          <Input
                            type="date"
                            {...field}
                            value={(field.value as string) ?? ''}
                          />
                        </FormControl>
                        {cp.caption && (
                          <p className="text-xs text-muted-foreground">{cp.caption}</p>
                        )}
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ))}
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {CLEARANCE_ATTRIBUTE_FIELDS.map((f) => (
                <FormField
                  key={f.name}
                  control={form.control}
                  name={f.name as keyof PackingListSchemaType}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{f.label}</FormLabel>
                      <FormControl>
                        <Input
                          type={f.name === 'free_days_available' ? 'number' : 'text'}
                          min={f.name === 'free_days_available' ? 0 : undefined}
                          {...field}
                          value={(field.value as string | number) ?? ''}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Shipment Lines</CardTitle>
              <Button type="button" variant="outline" size="sm" onClick={() => append({ product_id: '', quantity_shipped: 1 })}>
                <Plus className="size-4" /> Add Line
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {fields.map((field, index) => (
                <div key={field.id} className="flex flex-wrap items-end gap-2">
                  <FormField
                    control={form.control}
                    name={`shipment_lines.${index}.product_id`}
                    render={({ field }) => (
                      <FormItem className="min-w-[200px] flex-1">
                        <FormLabel>Product</FormLabel>
                        <FormControl>
                          <ProductCombobox
                            value={field.value}
                            onChange={field.onChange}
                            products={products}
                            productFallback={knownProducts[field.value] ?? null}
                            placeholder="Select product"
                            onSearch={setProductSearch}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name={`shipment_lines.${index}.quantity_shipped`}
                    render={({ field }) => (
                      <FormItem className="w-32">
                        <FormLabel>Qty Shipped</FormLabel>
                        <FormControl>
                          <Input type="number" min={1} {...field} onChange={(e) => field.onChange(parseInt(e.target.value, 10) || 0)} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <Button type="button" variant="ghost" size="icon" onClick={() => remove(index)} className="text-destructive" aria-label="Delete line">
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <div className="flex gap-2">
          <Button type="submit" disabled={isLoading}>
            {isLoading && <LoaderCircleIcon className="me-2 size-4 animate-spin" />}
            {isEditMode ? 'Update' : 'Create'}
          </Button>
          <Button type="button" variant="outline" onClick={() => router.back()}>
            Cancel
          </Button>
        </div>
      </form>
    </Form>
  );
}
