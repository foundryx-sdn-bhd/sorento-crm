'use client';

import * as React from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { EXPORT_SPLIT_OPTIONS } from '@/components/common/export-split';
import { useExportStockDebt } from '../hooks/useStockDebtQuery';
import { previewStockDebtExport } from '../services/stockDebtService';
import type { StockDebtExportParams } from '../services/stockDebtService';
import type { StockDebtExportSplit, StockDebtListResponse } from '../types/stockDebt.types';

/**
 * The board's ONE primary CTA (R12, R13): a single "Export" button that opens a small
 * popover rather than growing the toolbar with a second control. Split None / Supplier /
 * Category / Supplier x Category (R5, AC-33; the four options now live in the shared
 * `EXPORT_SPLIT_OPTIONS` - the low stock report's own dialog renders the same list), a
 * one-line rows/sheets preview built from whatever the board already has loaded
 * (`previewStockDebtExport` - no network call of its own), then Export - which starts the
 * workbook through My Downloads and never blocks on the worker (AC-34).
 */

export function StockDebtExportPopover({
  envelope,
  filters,
  rowCount,
  onExport,
  exporting = false,
}: {
  /** The live board's loaded list: the rows/sheets preview reads it. */
  envelope?: StockDebtListResponse | undefined;
  /** The board's current filters, minus paging and split - what the export sends (AC-17). */
  filters?: Omit<StockDebtExportParams, 'split'>;
  /** Without an `envelope` (a stored snapshot), the row count the preview states. */
  rowCount?: number;
  /** Replaces the board's own export: the snapshot page starts its stored workbook instead.
   *  Call `done` once it has started, so the popover closes. */
  onExport?: (split: StockDebtExportSplit, done: () => void) => void;
  exporting?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const [split, setSplit] = React.useState<StockDebtExportSplit>('none');
  const exportMutation = useExportStockDebt();

  const preview = envelope
    ? previewStockDebtExport(envelope, split)
    : { rows: rowCount ?? 0, sheets: null };
  const pending = exportMutation.isPending || exporting;

  const handleExport = () => {
    if (onExport) {
      onExport(split, () => setOpen(false));
      return;
    }
    exportMutation.mutate(
      { ...(filters as Omit<StockDebtExportParams, 'split'>), split },
      { onSuccess: () => setOpen(false) },
    );
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" className="gap-1.5">
          <Download className="size-4" />
          Export
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 space-y-4" align="end">
        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">Split into sheets</Label>
          <RadioGroup
            className="gap-2"
            value={split}
            onValueChange={(value) => setSplit(value as StockDebtExportSplit)}
          >
            {EXPORT_SPLIT_OPTIONS.map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-sm">
                <RadioGroupItem value={option.value} id={`export-split-${option.value}`} />
                {option.label}
              </label>
            ))}
          </RadioGroup>
        </div>
        <p className="text-xs text-muted-foreground">
          {preview.rows.toLocaleString()} row{preview.rows === 1 ? '' : 's'}
          {preview.sheets !== null && (
            <>
              , {preview.sheets.toLocaleString()} sheet{preview.sheets === 1 ? '' : 's'}
            </>
          )}
        </p>
        <Button
          className="w-full"
          onClick={handleExport}
          disabled={pending || preview.rows === 0}
        >
          {pending ? 'Starting…' : 'Export'}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
