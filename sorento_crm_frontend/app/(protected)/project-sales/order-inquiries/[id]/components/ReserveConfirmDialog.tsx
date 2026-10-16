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
      <span
        className="block truncate text-sm font-medium"
        title={row.original.item_code ?? undefined}
      >
        {row.original.item_code}
      </span>
    ),
    size: 160,
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

const INQUIRY_COLUMNS: ColumnDef<ReserveConfirmInquiry>[] = [
  {
    id: 'inquiry_no',
    header: 'OI no',
    cell: ({ row }) => (
      <span className="block truncate text-sm font-medium" title={row.original.inquiry_no}>
        {row.original.inquiry_no}
      </span>
    ),
    size: 160,
    enableSorting: false,
    meta: { headerTitle: 'OI no' },
  },
  {
    id: 'reserve_count',
    header: 'Reserve',
    cell: ({ row }) => cell(row.original.reserve_count),
    size: 90,
    enableSorting: false,
    meta: { headerTitle: 'Reserve', headerClassName: 'text-end', cellClassName: 'text-end' },
  },
  {
    id: 'reject_count',
    header: 'Reject',
    cell: ({ row }) => cell(row.original.reject_count),
    size: 90,
    enableSorting: false,
    meta: { headerTitle: 'Reject', headerClassName: 'text-end', cellClassName: 'text-end' },
  },
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
    size: 260,
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
 * one grid row per order inquiry, no recipient line, and once `done` only Close.
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
    columns: COLUMNS,
    data: lines,
    getRowId: (line) => line.row_id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const inquiryTable = useReactTable({
    columns: INQUIRY_COLUMNS,
    data: inquiries ?? [],
    getRowId: (inquiry) => inquiry.inquiry_id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const toName = inquiries ? null : recipients?.to?.name ?? null;
  const ccNames = (recipients?.cc ?? []).map((person) => person.name);
  const noticeLine = toName
    ? `Notice to purchasing: To ${toName}${ccNames.length ? `, Cc ${ccNames.join(', ')}` : ''}`
    : 'Notice to purchasing: no recipient';

  const lineTotal = (inquiries ?? []).reduce(
    (sum, inquiry) => sum + inquiry.reserve_count + inquiry.reject_count,
    0,
  );
  const title = inquiries
    ? `Reserve ${lineTotal} ${lineTotal === 1 ? 'line' : 'lines'} on ${inquiries.length} ${inquiries.length === 1 ? 'order inquiry' : 'order inquiries'}`
    : `Reserve ${lines.length} ${lines.length === 1 ? 'line' : 'lines'} on ${inquiryNo}`;
  const rowCount = inquiries ? inquiries.length : lines.length;

  async function handleConfirm() {
    await onConfirm(park);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={!done} className="flex max-h-[85dvh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex-1 space-y-4 overflow-y-auto">
          <DataGrid
            table={(inquiries ? inquiryTable : table) as typeof table}
            recordCount={rowCount}
            tableLayout={{
              width: 'fixed',
              columnsResizable: true,
              columnsDraggable: false,
              // The DialogBody above already owns the scroll viewport.
              scrollerMaxHeight: false,
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
              <Button onClick={handleConfirm} disabled={isPending || rowCount === 0}>
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
