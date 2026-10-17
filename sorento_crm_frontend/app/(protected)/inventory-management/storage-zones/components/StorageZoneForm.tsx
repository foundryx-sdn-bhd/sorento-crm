'use client';

import { useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import {
  Dialog,
  DialogContent,
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
import { requiredNumber } from '@/lib/number-field-schema';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Button } from '@/components/ui/button';
import { useCreateStorageZone, useUpdateStorageZone } from '../hooks/useStorageZones';
import type { StorageZoneFormData } from '../types/storageZone.types';
import { useQuery } from '@tanstack/react-query';
import { getWarehouses } from '../../warehouses/services/warehouseService';
import type { Warehouse } from '../../warehouses/types/warehouse.types';

const StorageZoneSchema = z.object({
  warehouse_id: z.string().uuid('Warehouse is required'),
  zone_code: z.string().min(1, 'Zone code is required').max(50),
  zone_name: z.string().max(150).optional().nullable(),
  zone_type: z.enum(['shelf', 'rack', 'bin', 'pallet']),
  capacity: requiredNumber({ min: 1, integer: true }),
  is_active: z.boolean(),
});

interface StorageZoneFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  zoneId?: string;
}

export default function StorageZoneForm({ open, onOpenChange, zoneId }: StorageZoneFormProps) {
  const createMutation = useCreateStorageZone();
  const updateMutation = useUpdateStorageZone();
  
  // Fetch warehouses for dropdown
  const { data: warehousesData } = useQuery({
    queryKey: ['warehouses-select'],
    queryFn: async () => {
      const response = await getWarehouses({
        pageIndex: 0,
        pageSize: 1000,
        sorting: [],
        searchQuery: '',
      });
      return response.data || [];
    },
    staleTime: 1000 * 60 * 5, // 5 minutes
  });

  const form = useForm<StorageZoneFormData>({
    resolver: zodResolver(StorageZoneSchema),
    mode: 'onTouched',
    defaultValues: {
      warehouse_id: '',
      zone_code: '',
      zone_name: '',
      zone_type: 'shelf',
      capacity: 0,
      is_active: true,
    },
  });

  useEffect(() => {
    if (open) {
      form.reset();
    }
  }, [open, form]);

  const onSubmit = async (data: StorageZoneFormData) => {
    try {
      if (zoneId) {
        await updateMutation.mutateAsync({ id: zoneId, data });
      } else {
        await createMutation.mutateAsync(data);
      }
      onOpenChange(false);
      form.reset();
    } catch (error) {
      // Error handled by mutation
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{zoneId ? 'Edit Storage Zone' : 'Create Storage Zone'}</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="warehouse_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Warehouse *</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      value={field.value}
                      onChange={field.onChange}
                      options={(warehousesData || []).map((warehouse: Warehouse) => ({
                        value: warehouse.id,
                        label: `${warehouse.warehouse_name} (${warehouse.warehouse_code})`,
                      }))}
                      placeholder="Select warehouse"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="zone_code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Zone Code *</FormLabel>
                  <FormControl>
                    <Input placeholder="ZONE-001" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="zone_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Zone Name</FormLabel>
                  <FormControl>
                    <Input placeholder="Enter zone name" {...field} value={field.value || ''} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="zone_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Zone Type *</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      value={field.value}
                      onChange={field.onChange}
                      options={[
                        { value: 'shelf', label: 'Shelf' },
                        { value: 'rack', label: 'Rack' },
                        { value: 'bin', label: 'Bin' },
                        { value: 'pallet', label: 'Pallet' },
                      ]}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="capacity"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Capacity *</FormLabel>
                  <FormControl>
                    <NumberInput
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

            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
                {zoneId ? 'Update' : 'Create'}
              </Button>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
