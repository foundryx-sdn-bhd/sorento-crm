'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { getProductsForVariantSelect } from '../../services/productService';
import type { ProductVariantRef } from '../../types/product.types';
import { codeWithName } from '@/lib/product-display';

export interface ProductVariantPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  /** Label for the confirm button (e.g. "Set parent", "Add variant"). */
  confirmLabel: string;
  /** Product ids to hide from the picker (the current product + existing children). */
  excludeIds?: string[];
  submitting?: boolean;
  /** Called with the chosen product's id. */
  onConfirm: (productId: string) => void;
  /** Extra fields shown above the product picker (the Compatible section's role choice). */
  children?: ReactNode;
}

function displayProduct(p: ProductVariantRef): string {
  return codeWithName(p.product_code, p.product_name);
}

/**
 * Shared product search combobox for variant curation (set/change parent + add
 * child). Resolves to a human-readable `code - name` label - never a raw UUID.
 */
export default function ProductVariantPickerDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  excludeIds = [],
  submitting = false,
  onConfirm,
  children,
}: ProductVariantPickerDialogProps) {
  const [selected, setSelected] = useState<ProductVariantRef | null>(null);

  useEffect(() => {
    if (!open) setSelected(null);
  }, [open]);

  const excludeSet = useMemo(() => new Set(excludeIds), [excludeIds]);

  // onChange hands back an id, but onConfirm needs the whole ref to render its label, so keep
  // the last fetched page around to resolve it.
  const lastFetchedRef = useRef<ProductVariantRef[]>([]);

  const fetchOptions = useCallback(
    async (query: string) => {
      const products = await getProductsForVariantSelect(query || undefined);
      const visible = products.filter((p) => !excludeSet.has(p.id));
      lastFetchedRef.current = visible;
      return visible.map((p) => ({ value: p.id, label: displayProduct(p) }));
    },
    [excludeSet],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {children}
        <div className="space-y-2 py-1">
          <Label>Product</Label>
          <SearchableSelect
            value={selected?.id ?? ''}
            onChange={(id) =>
              setSelected(lastFetchedRef.current.find((p) => p.id === id) ?? null)
            }
            fetchOptions={fetchOptions}
            // Keeps the trigger label when the chosen product falls out of the current page.
            selectedOption={
              selected ? { value: selected.id, label: displayProduct(selected) } : undefined
            }
            disabled={submitting}
            placeholder="Select a product"
            emptyMessage="No products found."
          />
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            onClick={() => selected && onConfirm(selected.id)}
            disabled={!selected || submitting}
          >
            {submitting && <LoaderCircleIcon className="animate-spin me-2 size-4" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
