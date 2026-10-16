'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useSupplierSelectQuery } from '../../../../procurement-management/suppliers/hooks/useSupplierSelectQuery';
import { getProductsForVariantSelect } from '../../services/productService';
import type { ProductVariantRef } from '../../types/product.types';
import { useCreateCompanionRule } from '../../hooks/useProductCompanions';
import { codeWithName } from '@/lib/product-display';

interface AddCompanionRuleModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  companionProductId: string;
}

function displayProduct(p: { product_code: string; product_name: string }): string {
  return codeWithName(p.product_code, p.product_name);
}

/**
 * Add a "supplied with" rule (UAC A2): the companion product this modal opens from
 * rides inside one or more HOST products' own line. Two hosts means BOTH must be
 * present on the order before the rule bites (the X + Y pair) - one host is the
 * common case (CKS1050).
 */
export function AddCompanionRuleModal({
  open,
  onOpenChange,
  companionProductId,
}: AddCompanionRuleModalProps) {
  const [hostIds, setHostIds] = useState<string[]>([]);
  const [supplierId, setSupplierId] = useState('');
  const [ratio, setRatio] = useState('1');
  const hostRefsRef = useRef<Map<string, ProductVariantRef>>(new Map());

  const { data: suppliers = [] } = useSupplierSelectQuery();
  const create = useCreateCompanionRule(companionProductId);

  useEffect(() => {
    if (!open) return;
    setHostIds([]);
    setSupplierId('');
    setRatio('1');
  }, [open]);

  // The companion cannot host itself. Existing rules are left selectable - the caller
  // is warned by the 409 (UAC A4), which names the rule already covering it.
  const fetchHostOptions = useCallback(
    async (query: string) => {
      const products = await getProductsForVariantSelect(query || undefined);
      const visible = products.filter((p) => p.id !== companionProductId);
      for (const product of visible) hostRefsRef.current.set(product.id, product);
      return visible.map((product) => ({ value: product.id, label: displayProduct(product) }));
    },
    [companionProductId],
  );

  const selectedHostOptions = hostIds.map((id) => {
    const ref = hostRefsRef.current.get(id);
    // Never a raw id (no UUIDs in the UI) - a ref that has not resolved yet reads as
    // blank rather than as the id underneath it. In practice this never shows: an id
    // only ever enters `hostIds` by picking a fetched option, which caches its ref
    // into `hostRefsRef` first.
    return { value: id, label: ref ? displayProduct(ref) : '' };
  });

  const ratioValue = Number(ratio);
  const canSave =
    hostIds.length > 0 && Number.isFinite(ratioValue) && ratioValue > 0 && !create.isPending;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    try {
      await create.mutateAsync({
        companion_product_id: companionProductId,
        host_product_ids: hostIds,
        supplier_id: supplierId || null,
        ratio,
      });
      onOpenChange(false);
    } catch {
      // Toasted by useCreateCompanionRule's onError; the modal stays open so a 409
      // (UAC A4) can be corrected without re-picking everything.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Add "supplied with" rule</DialogTitle>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="companion-rule-hosts">Included with</Label>
              <SearchableMultiSelect
                id="companion-rule-hosts"
                value={hostIds}
                onChange={setHostIds}
                fetchOptions={fetchHostOptions}
                selectedOptions={selectedHostOptions}
                placeholder="Search products"
                emptyMessage="No products found."
                disabled={create.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="companion-rule-supplier">Supplier</Label>
              <SearchableSelect
                id="companion-rule-supplier"
                clearable
                value={supplierId}
                onChange={setSupplierId}
                options={suppliers.map((s) => ({
                  value: s.id,
                  label: `${s.supplier_code} - ${s.supplier_name}`,
                }))}
                placeholder="Any supplier"
                emptyMessage="No suppliers found."
                disabled={create.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="companion-rule-ratio">Ratio</Label>
              <Input
                id="companion-rule-ratio"
                type="number"
                min={0}
                step="0.0001"
                value={ratio}
                onChange={(e) => setRatio(e.target.value)}
                disabled={create.isPending}
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={create.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!canSave}>
              {create.isPending ? 'Saving...' : 'Save rule'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default AddCompanionRuleModal;
