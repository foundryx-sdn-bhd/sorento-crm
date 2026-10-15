'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { formatDateInMalaysia } from '@/lib/helpers';
import { useCreateSnapshot } from '../hooks/useStockDebtSnapshots';
import type { StockDebtExportParams } from '../services/stockDebtService';

const BOOK_LABEL: Record<string, string> = { all: 'All', project: 'Project', retail: 'Retail' };

/** `2023-01-01` -> `01/01/2023` (the picker's ISO value, printed the way the app prints dates). */
function dmy(iso: string): string {
  const [year, month, day] = iso.split('-');
  return `${day}/${month}/${year}`;
}

/** The read-only line: what is about to be saved, in words (never an id). */
function describeFilters(
  productCount: number,
  filters: Omit<StockDebtExportParams, 'split'>,
): string {
  const parts = [`${productCount.toLocaleString()} ${productCount === 1 ? 'product' : 'products'}`];
  parts.push(`Book ${BOOK_LABEL[filters.book] ?? filters.book}`);
  if (filters.query) parts.push(`Search "${filters.query}"`);
  const locationCount = filters.locations?.length ?? 0;
  if (locationCount > 0) {
    const noun = locationCount === 1 ? 'location' : 'locations';
    parts.push(`${filters.locationsMode === 'exclude' ? 'All except ' : ''}${locationCount} ${noun}`);
  }
  if (filters.supplierIds.length > 0) {
    parts.push(`${filters.supplierIds.length} ${filters.supplierIds.length === 1 ? 'supplier' : 'suppliers'}`);
  }
  if (filters.dateFrom || filters.dateTo) {
    parts.push(
      `Delivery ${filters.dateFrom ? dmy(filters.dateFrom) : 'any'} to ${filters.dateTo ? dmy(filters.dateTo) : 'any'}`,
    );
  }
  if (filters.onlyDebt) parts.push('Only in debt');
  if (filters.projected) parts.push('Projected');
  return parts.join(' · ');
}

/** AC-F1: Save snapshot. Posts the board's current filters, the same object Export sends. */
export function SaveSnapshotDialog({
  open,
  onOpenChange,
  filters,
  productCount,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filters: Omit<StockDebtExportParams, 'split'>;
  productCount: number;
}) {
  const router = useRouter();
  const [name, setName] = React.useState('');
  const create = useCreateSnapshot((id) => router.push(`/project-sales/stock-debt/snapshots/${id}`));

  React.useEffect(() => {
    if (open) setName(`Stock Debt ${formatDateInMalaysia(new Date())}`);
  }, [open]);

  const trimmed = name.trim();
  const save = (event: React.FormEvent) => {
    event.preventDefault();
    if (!trimmed || create.isPending) return;
    create.mutate({ name: trimmed, filters }, { onSuccess: () => onOpenChange(false) });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save snapshot</DialogTitle>
          <DialogDescription className="sr-only">Save snapshot</DialogDescription>
        </DialogHeader>
        <form onSubmit={save}>
          <DialogBody className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="snapshot-name">Name</Label>
              <Input
                id="snapshot-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
                autoFocus
              />
            </div>
            <p className="text-sm text-muted-foreground">
              {describeFilters(productCount, filters)}
            </p>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!trimmed || create.isPending}>
              Save snapshot
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
