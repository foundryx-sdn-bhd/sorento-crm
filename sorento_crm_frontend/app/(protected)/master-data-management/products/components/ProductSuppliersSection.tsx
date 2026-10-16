'use client';

import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { getProductSuppliersByProductId, createProductSupplier, updateProductSupplier } from '../../../procurement-management/product-suppliers/services/productSupplierService';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { useSupplierSelectQuery } from '../../../procurement-management/suppliers/hooks/useSupplierSelectQuery';
import { getCurrencyRates } from '../../../scm/services/currencyRateService';
import { toast } from '@/lib/toast';
import {
  ProductSupplierTermsRow,
  draftToPatch,
  termsError,
  type SupplierTermsDraft,
} from './ProductSupplierTermsRow';

interface ProductSuppliersSectionProps {
  productId: string | undefined;
  isEditMode: boolean;
}

export default function ProductSuppliersSection({
  productId,
  isEditMode,
}: ProductSuppliersSectionProps) {
  const queryClient = useQueryClient();
  const [selectedSupplierId, setSelectedSupplierId] = useState<string>('');
  const [leadTimeDays, setLeadTimeDays] = useState<number | null>(null);
  const [leadTimeChecked, setLeadTimeChecked] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);

  // Only currencies we hold a rate for are offered. A price in a currency with no rate
  // cannot be compared to the budget, so the plan would drop the buy straight back into
  // "No price yet" - offering it here would be inviting the buyer to do work twice.
  const { data: currencyRates } = useQuery({
    queryKey: ['scm-currency-rates'],
    queryFn: getCurrencyRates,
    enabled: isEditMode,
    // The read is gated on an SCM permission a master-data editor may not hold. A failure
    // is not worth a toast: the select falls back to whatever is already on the row.
    retry: false,
  });
  const currencyOptions = useMemo(() => {
    const base = currencyRates?.base_currency;
    const codes = [
      ...(base ? [base] : []),
      ...(currencyRates?.rates ?? []).map((r) => r.currency),
    ];
    return Array.from(new Set(codes)).map((c) => ({ value: c, label: c }));
  }, [currencyRates]);

  // Fetch existing product suppliers
  const { data: productSuppliers, isLoading: isLoadingSuppliers } = useQuery({
    queryKey: ['product-suppliers', productId],
    queryFn: () => {
      if (!productId) throw new Error('Product ID is required');
      return getProductSuppliersByProductId(productId);
    },
    enabled: !!productId && isEditMode,
  });

  // Fetch all suppliers for the dropdown
  const { data: suppliers = [] } = useSupplierSelectQuery();

  // Create mutation
  const createMutation = useMutation({
    mutationFn: ({ supplierId, leadTime }: { supplierId: string; leadTime: number }) => {
      if (!productId) throw new Error('Product ID is required');
      return createProductSupplier({
        product_id: productId,
        supplier_id: supplierId,
        standard_lead_time_days: leadTime,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['product-suppliers', productId] });
      setSelectedSupplierId('');
      setLeadTimeDays(null);
      setLeadTimeChecked(false);
      toast.success('Supplier added successfully');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to add supplier');
    },
  });

  // One save per row, carrying every term at once: a price and its currency have to land
  // together or the row is briefly priced in the wrong money.
  const updateMutation = useMutation({
    mutationFn: ({ id, draft }: { id: string; draft: SupplierTermsDraft }) =>
      updateProductSupplier(id, draftToPatch(draft)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['product-suppliers', productId] });
      toast.success('Supplier terms updated');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to update supplier terms');
    },
    onSettled: () => setSavingId(null),
  });

  // Removing a supplier asks nothing (D7). The row's Remove parks the detach on the
  // server for the reversible window and a toast carries the countdown, so the way back
  // is Cancel rather than a dialog to read first.
  const removal = useDeferredRowAction({
    actionKey: 'product_supplier.unlink',
    entityType: 'product_supplier',
    verb: 'Removing',
    successMessage: 'Supplier removed',
    invalidateKeys: [['product-suppliers', productId]],
  });

  const leadTimeError = (value: number | null) => numberFieldError(value, { min: 0, integer: true });

  const handleAddSupplier = () => {
    if (!selectedSupplierId) {
      toast.error('Please select a supplier');
      return;
    }

    setLeadTimeChecked(true);
    if (leadTimeError(leadTimeDays)) return;
    const leadTime = parseNumberDraft(leadTimeDays) as number;

    // Check if supplier is already added
    const alreadyAdded = productSuppliers?.some(
      (ps) => ps.supplier_id === selectedSupplierId
    );
    if (alreadyAdded) {
      toast.error('This supplier is already added');
      return;
    }

    createMutation.mutate({ supplierId: selectedSupplierId, leadTime });
  };

  // Get available suppliers (not already added)
  const availableSuppliers = suppliers.filter(
    (supplier) =>
      !productSuppliers?.some((ps) => ps.supplier_id === supplier.id)
  );

  if (!isEditMode) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Suppliers</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Save the product first to configure suppliers.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Suppliers</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Add Supplier Section */}
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>Supplier *</Label>
              <SearchableSelect
                value={selectedSupplierId}
                onChange={setSelectedSupplierId}
                placeholder="Select a supplier to add"
                // Was a disabled "__no_suppliers__" option faking an empty state. Keep the two
                // cases distinct: "none left to add" is not the same as "your search matched
                // nothing", and showing the former for a failed search reads as a bug.
                emptyMessage={
                  availableSuppliers.length === 0
                    ? 'No available suppliers'
                    : 'No suppliers found.'
                }
                options={availableSuppliers.map((supplier) => ({
                  value: supplier.id,
                  label: `${supplier.supplier_code} - ${supplier.supplier_name}`,
                }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Lead Time (Days) *</Label>
              <NumberInput
                placeholder="e.g., 7"
                value={leadTimeDays}
                onValueChange={setLeadTimeDays}
                min="0"
                error={leadTimeChecked ? leadTimeError(leadTimeDays) : undefined}
              />
            </div>
          </div>
          <Button
            type="button"
            onClick={handleAddSupplier}
            disabled={!selectedSupplierId || createMutation.isPending}
          >
            {createMutation.isPending ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <Plus className="size-4" />
            )}
            Add Supplier
          </Button>
        </div>

        {/* Existing Suppliers List */}
        {isLoadingSuppliers ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : productSuppliers && productSuppliers.length > 0 ? (
          <div className="space-y-2">
            {productSuppliers.map((ps) => (
              <ProductSupplierTermsRow
                key={ps.id}
                ps={ps}
                currencyOptions={currencyOptions}
                onSave={(draft) => {
                  const invalid = termsError(draft);
                  if (invalid) {
                    toast.error(invalid);
                    return;
                  }
                  setSavingId(ps.id);
                  updateMutation.mutate({ id: ps.id, draft });
                }}
                onRemove={() =>
                  removal.run({
                    id: ps.id,
                    subject: ps.supplier?.supplier_name || 'this supplier',
                  })
                }
                isSaving={updateMutation.isPending && savingId === ps.id}
                isDeleting={removal.targetId === ps.id && removal.isPending}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No suppliers configured for this product.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
