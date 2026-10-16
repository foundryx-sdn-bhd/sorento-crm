'use client';

import * as React from 'react';
import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import type { ReserveNoticeRecipients } from '../../../_shared/services/orderInquiryReserveService';

/** One staged line as the confirmation prints it. */
export interface ReserveConfirmLine {
  row_id: string;
  item_code: string | null;
  qty_requested: number | string | null;
  qty_reserved: number | string;
  /** What is left to buy once this line is committed. */
  balance: number | string | null;
  location: string | null;
  reason: string | null;
  /** Multi-OI mode (the cross-OI Lines page): the product's name, muted under the code. */
  description?: string | null;
  /** Multi-OI mode: the order inquiry this line belongs to. */
  inquiry_no?: string | null;
  /** Multi-OI mode: set once the commit has answered for this line's order inquiry. */
  result?: { ok: boolean; message?: string } | null;
}

const cell = (value: number | string | null | undefined) => (
  <span className="block truncate tabular-nums" title={value == null ? undefined : String(value)}>
    {value ?? ''}
  </span>
);

const COLUMNS: ColumnDef<ReserveConfirmLine>[] = [
  {
    id: 'product',
    header: 'Product',
    cell: ({ row }) => (
      <div className="min-w-0">
        <span
          className="block truncate text-sm font-medium"
          title={row.original.item_code ?? undefined}
        >
          {row.original.item_code}
        </span>
        {row.original.description ? (
          <span
            className="block truncate text-xs text-muted-foreground"
            title={row.original.description}
          >
            {row.original.description}
          </span>
        ) : null}
      </div>
    ),
    size: 180,
    enableSorting: false,
    meta: { headerTitle: 'Product' },
  },
  {
    id: 'requested',
    header: 'Requested',
    cell: ({ row }) => cell(row.original.qty_requested),
    size: 90,
    enableSorting: false,
    meta: { headerTitle: 'Requested', headerClassName: 'text-end', cellClassName: 'text-end' },
  },
  {
    id: 'reserve',
    header: 'Reserve',
    cell: ({ row }) => cell(row.original.qty_reserved),
    size: 90,
    enableSorting: false,
    meta: { headerTitle: 'Reserve', headerClassName: 'text-end', cellClassName: 'text-end' },
  },
  {
    id: 'balance',
    header: 'Balance to buy',
    cell: ({ row }) => cell(row.original.balance),
    size: 120,
    enableSorting: false,
    meta: { headerTitle: 'Balance to buy', headerClassName: 'text-end', cellClassName: 'text-end' },
  },
  {
    id: 'location',
    header: 'Location',
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.location ?? undefined}>
        {row.original.location}
      </span>
    ),
    size: 100,
    enableSorting: false,
    meta: { headerTitle: 'Location' },
  },
  {
    id: 'reason',
    header: 'Reason',
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.reason ?? undefined}>
        {row.original.reason}
      </span>
    ),
    size: 200,
    enableSorting: false,
    meta: { headerTitle: 'Reason' },
  },
];

/** OI-BULK-RESERVE v4 (F8): one order inquiry's share of a cross-OI confirmation. */
export interface ReserveConfirmInquiry {
  inquiry_id: string;
  inquiry_no: string;
  reserve_count: number;
  reject_count: number;
  /** Set once the commit has answered for this order inquiry. */
  result?: { ok: boolean; message?: string };
}

const MULTI_SIZES: Record<string, number> = {
  product: 185,
  requested: 105,
  reserve: 100,
  balance: 135,
  location: 105,
  reason: 155,
};

/** Multi-OI mode: the line columns, with the order inquiry first and the commit result last. */
const MULTI_COLUMNS: ColumnDef<ReserveConfirmLine>[] = [
  {
    id: 'inquiry_no',
    header: 'OI no',
    cell: ({ row }) => (
      <span className="block truncate text-sm" title={row.original.inquiry_no ?? undefined}>
        {row.original.inquiry_no}
      </span>
    ),
    size: 140,
    enableSorting: false,
    meta: { headerTitle: 'OI no' },
  },
  // Widths that fit all eight columns in the wider dialog at 1280 without a sideways scroll.
  ...COLUMNS.map((column) => ({ ...column, size: MULTI_SIZES[column.id as string] ?? column.size })),
  {
    id: 'result',
    header: 'Result',
    cell: ({ row }) => {
      const result = row.original.result;
      const text = result ? (result.ok ? 'Saved' : (result.message ?? 'Failed')) : '';
      return (
        <span className="block truncate" title={text || undefined}>
          {text}
        </span>
      );
    },
    size: 155,
    enableSorting: false,
    meta: { headerTitle: 'Result' },
  },
];

/**
 * AC-SN-41: the confirmation Reserve (N) opens. The footer is the request lightbox's
 * (tickbox left, Cancel, one primary button); the tickbox is unticked on every open and
 * the primary button's label follows it. `onConfirm(park)` does the commit.
 *
 * OI-BULK-RESERVE v4 (F8): with `inquiries` it is the cross-OI Lines page's confirmation -
 * one grid row per LINE (`lines`, each with its OI no and result), a summary above it, no
 * recipient line, and once `done` only Close. `inquiries.length` is the order inquiry count.
 */
export function ReserveConfirmDialog({
  open,
  onOpenChange,
  inquiryNo,
  lines,
  recipients,
  onConfirm,
  isPending = false,
  inquiries,
  done = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  inquiryNo: string;
  lines: ReserveConfirmLine[];
  recipients?: ReserveNoticeRecipients | null;
  onConfirm: (park: boolean) => Promise<unknown> | void;
  isPending?: boolean;
  inquiries?: ReserveConfirmInquiry[];
  done?: boolean;
}) {
  const [park, setPark] = React.useState(false);
  React.useEffect(() => {
    if (open) setPark(false);
  }, [open]);

  const table = useReactTable({
    columns: inquiries ? MULTI_COLUMNS : COLUMNS,
    data: lines,
    getRowId: (line) => line.row_id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const toName = inquiries ? null : recipients?.to?.name ?? null;
  const ccNames = (recipients?.cc ?? []).map((person) => person.name);
  const noticeLine = toName
    ? `Notice to purchasing: To ${toName}${ccNames.length ? `, Cc ${ccNames.join(', ')}` : ''}`
    : 'Notice to purchasing: no recipient';

  const plural = (count: number, one: string, many: string) => (count === 1 ? one : many);
  const inquiryCount = inquiries?.length ?? 0;
  const lineNoun = plural(lines.length, 'line', 'lines');
  const title = inquiries
    ? `Reserve ${lines.length} ${lineNoun} on ${inquiryCount} ${plural(inquiryCount, 'order inquiry', 'order inquiries')}`
    : `Reserve ${lines.length} ${lineNoun} on ${inquiryNo}`;
  const rejectCount = lines.filter((line) => Number(line.qty_reserved) === 0).length;
  const summary = `${lines.length} ${lineNoun} on ${inquiryCount} ${plural(inquiryCount, 'order inquiry', 'order inquiries')} \u00b7 Reserve ${lines.length - rejectCount} \u00b7 Reject ${rejectCount}`;

  async function handleConfirm() {
    await onConfirm(park);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={!done}
        className={`flex max-h-[85dvh] flex-col ${inquiries ? 'sm:max-w-6xl' : 'sm:max-w-4xl'}`}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex-1 space-y-4 overflow-y-auto">
          {inquiries ? <p className="text-sm font-medium">{summary}</p> : null}
          <DataGrid
            table={table}
            recordCount={lines.length}
            tableLayout={{
              width: 'fixed',
              columnsResizable: true,
              columnsDraggable: false,
              // The DialogBody above owns the scroll viewport, except for a cross-OI list,
              // which can run to hundreds of lines: its own bounded scroller keeps the
              // header in view while the body scrolls.
              ...(inquiries
                ? { headerSticky: true, scrollerMaxHeight: 'max-h-[50dvh] overflow-y-auto' }
                : { scrollerMaxHeight: false }),
            }}
          >
            <DataGridTable />
          </DataGrid>
          {inquiries ? null : <p className="text-sm text-muted-foreground">{noticeLine}</p>}
        </DialogBody>
        {done ? (
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </DialogFooter>
        ) : (
          <DialogFooter className="flex-wrap items-center gap-2 sm:justify-between">
            <div className="flex items-center gap-2">
              <Checkbox
                id="reserve-confirm-park"
                checked={park}
                onCheckedChange={(value) => setPark(value === true)}
                disabled={isPending}
              />
              <Label htmlFor="reserve-confirm-park">Park notice first</Label>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
                Cancel
              </Button>
              <Button onClick={handleConfirm} disabled={isPending || lines.length === 0}>
                {park
                  ? 'Reserve and park notice'
                  : toName
                    ? `Reserve and send to ${toName}`
                    : 'Reserve and send'}
              </Button>
            </div>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default ReserveConfirmDialog;
