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
  locationCodes: string[],
): string {
  const parts = [
    `${productCount.toLocaleString()} ${productCount === 1 ? 'product' : 'products'}`,
    `Book ${BOOK_LABEL[filters.book] ?? filters.book}`,
  ];
  const picked = (filters.locations?.length ?? 0) > 0;
  parts.push(
    !picked
      ? 'All locations'
      : filters.locationsMode === 'exclude'
        ? `All except ${locationCodes.join(', ')}`
        : locationCodes.join(', '),
  );
  if (filters.supplierIds.length > 0) {
    parts.push(`${filters.supplierIds.length} ${filters.supplierIds.length === 1 ? 'supplier' : 'suppliers'}`);
  }
  if (filters.dateFrom || filters.dateTo) {
    parts.push(
      filters.dateFrom && filters.dateTo
        ? `Delivery ${dmy(filters.dateFrom)} to ${dmy(filters.dateTo)}`
        : filters.dateFrom
          ? `Delivery from ${dmy(filters.dateFrom)}`
          : `Delivery to ${dmy(filters.dateTo)}`,
    );
  }
  if (filters.onlyDebt) parts.push('Only in debt');
  if (filters.projected) parts.push('Projected');
  if (filters.showAux) parts.push('Stock and PO columns');
  return parts.join(' · ');
}

/** AC-F1: Save snapshot. Posts the board's current filters, the same object Export sends. */
export function SaveSnapshotDialog({
  open,
  onOpenChange,
  filters,
  productCount,
  locationCodes,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filters: Omit<StockDebtExportParams, 'split'>;
  productCount: number;
  /** The picked locations' codes, in words: the line never prints an id. */
  locationCodes: string[];
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
              {describeFilters(productCount, filters, locationCodes)}
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
