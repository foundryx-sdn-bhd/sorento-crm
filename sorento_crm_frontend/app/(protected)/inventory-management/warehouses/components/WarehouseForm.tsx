'use client';

import { useEffect, useMemo } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { CalendarRange, Info, LoaderCircleIcon, Save } from 'lucide-react';
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
import { Switch } from '@/components/ui/switch';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useCreateWarehouse, useUpdateWarehouse, useWarehouse, useWarehouses } from '../hooks/useWarehouses';
import type { Warehouse } from '../types/warehouse.types';
import { WarehouseSchema, type WarehouseSchemaType } from '../forms/warehouse-schema';

interface WarehouseFormProps {
  warehouseId?: string;
  onSuccess?: () => void;
}

export default function WarehouseForm({ warehouseId, onSuccess }: WarehouseFormProps) {
  const router = useRouter();
  const isEditMode = !!warehouseId;
  const { data: warehouse, isLoading: isLoadingWarehouse } = useWarehouse(warehouseId || null);
  const createMutation = useCreateWarehouse();
  const updateMutation = useUpdateWarehouse();

  // The warehouse list, read here only to build the pool candidates below. Deliberately NOT
  // also driving prev/next navigation: a chevron on an edit form pushes the neighbour's read
  // view and silently discards whatever the user has typed. prev/next lives on the detail page.
  const listParams = useMemo(
    () => ({
      pageIndex: 0,
      pageSize: 1000,
      sorting: [{ id: 'created_at', desc: true }],
      searchQuery: '',
    }),
    [],
  );
  const { data: warehouseList } = useWarehouses(listParams);
  const allWarehouses = useMemo<Warehouse[]>(() => warehouseList?.data ?? [], [warehouseList]);

  const form = useForm<WarehouseSchemaType>({
    resolver: zodResolver(WarehouseSchema),
    defaultValues: {
      warehouse_code: '',
      warehouse_name: null,
      location: null,
      manager_id: null,
      is_active: true,
      counts_as_available: true,
      fulfilment_planning: false,
      pool_warehouse_id: null,
      segment: null,
    },
    mode: 'onTouched',
  });

  // Candidate pools. Any location can be a pool, so this is the warehouse list itself. The
  // warehouse being edited is offered once, by role rather than by name: a site pool such as
  // BRW or HQ points at itself, and that state has to be visible and keepable here.
  const poolOptions = useMemo(
    () => [
      ...(warehouseId ? [{ value: warehouseId, label: 'This warehouse (own pool)' }] : []),
      ...allWarehouses
        .filter((w) => w.id !== warehouseId)
        .map((w) => ({
          value: w.id,
          label: w.warehouse_name ? `${w.warehouse_name} (${w.warehouse_code})` : w.warehouse_code,
        })),
    ],
    [allWarehouses, warehouseId],
  );

  // Load warehouse data when editing
  useEffect(() => {
    if (warehouse && isEditMode) {
      form.reset({
        warehouse_code: warehouse.warehouse_code,
        warehouse_name: warehouse.warehouse_name ?? null,
        location: warehouse.location || null,
        manager_id: warehouse.manager_id || null,
        is_active: warehouse.is_active,
        counts_as_available: warehouse.counts_as_available ?? true,
        fulfilment_planning: warehouse.fulfilment_planning ?? false,
        pool_warehouse_id: warehouse.pool_warehouse_id ?? null,
        segment: (warehouse.segment as 'dealer' | 'project' | null) ?? null,
      });
    }
  }, [warehouse, isEditMode, form]);

  // Read during render so react-hook-form keeps tracking it; the submit reads it fresh.
  void form.formState.dirtyFields;

  const onSubmit = async (data: WarehouseSchemaType) => {
    try {
      if (isEditMode && warehouseId) {
        // An edit sends only what the user changed. The backend writes every key it is sent,
        // so a whole-form payload would overwrite a value the form cannot show with null.
        const { dirtyFields } = form.formState;
        const changed = Object.fromEntries(
          Object.entries(data).filter(([key]) => dirtyFields[key as keyof WarehouseSchemaType]),
        ) as Partial<WarehouseSchemaType>;
        await updateMutation.mutateAsync({ id: warehouseId, data: changed });
      } else {
        await createMutation.mutateAsync(data);
      }
      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/inventory-management/warehouses');
      }
    } catch {
      // Error is handled by the mutation hook
    }
  };

  if (isEditMode && isLoadingWarehouse) {
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
            <CardTitle>{isEditMode ? 'Edit Warehouse' : 'Create Warehouse'}</CardTitle>
          </CardHeader>
          <CardContent>
            {/* Same tab set, same field order, and the same grid spans as the read view. */}
            <Tabs defaultValue="basic">
              <TabsList>
                <TabsTrigger value="basic">
                  <Info />
                  <span>Basic Information</span>
                </TabsTrigger>
                <TabsTrigger value="planning">
                  <CalendarRange />
                  <span>Planning</span>
                </TabsTrigger>
              </TabsList>

              <TabsContent value="basic" className="mt-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField
                    control={form.control}
                    name="warehouse_code"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>System Location *</FormLabel>
                        <FormControl>
                          <Input placeholder="WH-001" {...field} />
                        </FormControl>
                        <FormDescription>Must be unique.</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="warehouse_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>System Location Description</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="e.g. Selangor Main DC"
                            {...field}
                            value={field.value ?? ''}
                            onChange={(e) => field.onChange(e.target.value || null)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="location"
                    render={({ field }) => (
                      <FormItem className="md:col-span-2">
                        <FormLabel>Warehouse</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="Warehouse name / address"
                            {...field}
                            value={field.value || ''}
                            onChange={(e) => field.onChange(e.target.value || null)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="is_active"
                    render={({ field }) => (
                      <FormItem className="md:col-span-2 flex flex-row items-center justify-between gap-4 rounded-lg border p-4">
                        <div className="min-w-0 space-y-0.5">
                          <FormLabel className="text-base">Active Status</FormLabel>
                          <FormDescription>Off: hidden from dropdowns.</FormDescription>
                        </div>
                        <FormControl>
                          <Switch checked={field.value} onCheckedChange={field.onChange} />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                </div>
              </TabsContent>

              <TabsContent value="planning" className="mt-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormField
                    control={form.control}
                    name="counts_as_available"
                    render={({ field }) => (
                      <FormItem className="md:col-span-2 flex flex-row items-center justify-between gap-4 rounded-lg border p-4">
                        <div className="min-w-0 space-y-0.5">
                          <FormLabel className="text-base">Available for planning</FormLabel>
                          <FormDescription>Off: cannot cover demand.</FormDescription>
                        </div>
                        <FormControl>
                          <Switch checked={field.value} onCheckedChange={field.onChange} />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="fulfilment_planning"
                    render={({ field }) => (
                      <FormItem className="md:col-span-2 flex flex-row items-center justify-between gap-4 rounded-lg border p-4">
                        <div className="min-w-0 space-y-0.5">
                          <FormLabel className="text-base">Fulfilment planning</FormLabel>
                        </div>
                        <FormControl>
                          <Switch checked={field.value} onCheckedChange={field.onChange} />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="pool_warehouse_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Draws stock from</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            value={field.value ?? ''}
                            onChange={(v) => field.onChange(v || null)}
                            options={poolOptions}
                            clearable
                            placeholder="Stands alone"
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="segment"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Sells to</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            value={field.value ?? ''}
                            onChange={(v) => field.onChange(v || null)}
                            options={[
                              { value: 'dealer', label: 'Dealer' },
                              { value: 'project', label: 'Project' },
                            ]}
                            clearable
                            placeholder="Not set"
                          />
                        </FormControl>
                        <FormDescription>Splits cost, price and sales history.</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>

        <div className="flex flex-wrap justify-end gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
            disabled={isLoading}
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
                {isEditMode ? 'Update Warehouse' : 'Create Warehouse'}
              </>
            )}
          </Button>
        </div>
      </form>
    </Form>
  );
}
