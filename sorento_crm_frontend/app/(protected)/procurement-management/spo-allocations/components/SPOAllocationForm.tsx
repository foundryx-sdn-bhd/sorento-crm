'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { LoaderCircleIcon } from 'lucide-react';
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
import { ProductCombobox } from './ProductCombobox';
import { WarehouseCombobox } from './WarehouseCombobox';
import { PackingListCombobox } from './PackingListCombobox';
import { useCreateSPOAllocation, useUpdateSPOAllocation, useSPOAllocation } from '../hooks/useSPOAllocations';
import { spoAllocationSchema, type SPOAllocationSchemaType } from '../forms/spo-allocation-schema';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import { getWarehouses } from '@/app/(protected)/inventory-management/warehouses/services/warehouseService';
import { getPackingLists } from '../../packing-lists/services/packingListService';
import type { Warehouse } from '@/app/(protected)/inventory-management/warehouses/types/warehouse.types';
import type { PackingList } from '../../packing-lists/types/packingList.types';

interface SPOAllocationFormProps {
  spoAllocationId?: string;
  onSuccess?: () => void;
}

export default function SPOAllocationForm({
  spoAllocationId,
  onSuccess,
}: SPOAllocationFormProps) {
  const router = useRouter();
  const isEditMode = !!spoAllocationId;
  const { data: spoAllocation, isLoading: isLoadingSPO } = useSPOAllocation(spoAllocationId ?? null);
  const [products, setProducts] = useState<Array<{ id: string; product_code: string; product_name?: string }>>([]);
  const [productSearch, setProductSearch] = useState('');
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [packingLists, setPackingLists] = useState<PackingList[]>([]);

  const createMutation = useCreateSPOAllocation();
  const updateMutation = useUpdateSPOAllocation();

  const form = useForm<SPOAllocationSchemaType>({
    resolver: zodResolver(spoAllocationSchema),
    mode: 'onTouched',
    defaultValues: {
      spo_number: '',
      product_id: '',
      warehouse_id: '',
      allocated_quantity: 1,
      inbound_shipment_id: '',
    },
  });

  // Warehouses + packing-lists fully enumerated once (small tables, max 100).
  // Products fetched on demand from the combobox search input so the entire
  // 10k+ catalog is reachable instead of a single 500-row first page.
  useEffect(() => {
    Promise.all([
      getWarehouses({
        pageIndex: 0,
        pageSize: 100,
        sorting: [],
        searchQuery: '',
        is_active: true,
      }),
      getPackingLists({
        pageIndex: 0,
        pageSize: 100,
        sorting: [{ id: 'created_at', desc: true }],
        searchQuery: '',
        shipment_status: 'in_transit',
      }),
    ]).then(([warehousesRes, packingListsRes]) => {
      setWarehouses(warehousesRes.data ?? []);
      setPackingLists(packingListsRes.data ?? []);
    });
  }, []);

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

  // Same pattern as ProductForm: useLayoutEffect + ref so reset runs before paint,
  // avoiding flash of empty dropdowns. Combobox components use display fallbacks
  // (productFallback, warehouseFallback, packingListFallback) so they show the
  // correct label even before option lists load.
  const lastInitializedIdRef = useRef<string | null>(null);

  useEffect(() => {
    lastInitializedIdRef.current = null;
    return () => { lastInitializedIdRef.current = null; };
  }, [spoAllocationId]);

  useLayoutEffect(() => {
    if (!spoAllocation || !isEditMode || lastInitializedIdRef.current === spoAllocation.id) return;

    form.reset({
      spo_number: spoAllocation.spo_number ?? '',
      product_id: String(spoAllocation.product_id ?? ''),
      warehouse_id: String(spoAllocation.warehouse_id ?? ''),
      allocated_quantity: spoAllocation.allocated_quantity,
      inbound_shipment_id: String(spoAllocation.inbound_shipment_id ?? ''),
    });
    lastInitializedIdRef.current = spoAllocation.id;
  }, [spoAllocation, isEditMode, form]);

  const onSubmit = async (data: SPOAllocationSchemaType) => {
    try {
      const payload = {
        spo_number: data.spo_number,
        product_id: data.product_id,
        warehouse_id: data.warehouse_id,
        allocated_quantity: data.allocated_quantity,
        inbound_shipment_id: data.inbound_shipment_id,
      };

      if (isEditMode && spoAllocationId) {
        await updateMutation.mutateAsync({ id: spoAllocationId, data: payload });
      } else {
        await createMutation.mutateAsync({ ...payload, receipt_status: 'pending' });
      }
      if (onSuccess) {
        onSuccess();
      } else {
        router.push(
          isEditMode ? `/procurement-management/spo-allocations/${spoAllocationId}` : '/procurement-management/spo-allocations'
        );
      }
    } catch (error) {
      console.error('SPO allocation form submission error:', error);
    }
  };

  if (isEditMode && isLoadingSPO) {
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
            <CardTitle>{isEditMode ? 'Edit SPO Allocation' : 'Create SPO Allocation'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <FormField
                control={form.control}
                name="spo_number"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>SPO Number *</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g. SPO-2025.10-0050" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="inbound_shipment_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Packing List / Inbound Shipment *</FormLabel>
                    <FormControl>
                      <PackingListCombobox
                        value={field.value}
                        onChange={field.onChange}
                        packingLists={packingLists}
                        packingListFallback={spoAllocation?.inbound_shipment}
                        placeholder="Select packing list"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="product_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Product *</FormLabel>
                    <FormControl>
                      <ProductCombobox
                        value={field.value}
                        onChange={field.onChange}
                        products={products}
                        productFallback={spoAllocation?.product}
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
                name="warehouse_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Warehouse *</FormLabel>
                    <FormControl>
                      <WarehouseCombobox
                        value={field.value}
                        onChange={field.onChange}
                        warehouses={warehouses}
                        warehouseFallback={spoAllocation?.warehouse}
                        placeholder="Select warehouse"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="allocated_quantity"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Allocated Quantity *</FormLabel>
                    <FormControl>
                      <NumberInput
                        min={1}
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
