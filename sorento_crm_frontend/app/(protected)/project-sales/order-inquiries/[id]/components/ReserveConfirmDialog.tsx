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

/**
 * AC-SN-41: the confirmation Reserve (N) opens. The footer is the request lightbox's
 * (tickbox left, Cancel, one primary button); the tickbox is unticked on every open and
 * the primary button's label follows it. `onConfirm(park)` does the commit.
 */
export function ReserveConfirmDialog({
  open,
  onOpenChange,
  inquiryNo,
  lines,
  recipients,
  onConfirm,
  isPending = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  inquiryNo: string;
  lines: ReserveConfirmLine[];
  recipients?: ReserveNoticeRecipients | null;
  onConfirm: (park: boolean) => Promise<unknown> | void;
  isPending?: boolean;
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

  const toName = recipients?.to?.name ?? null;
  const ccNames = (recipients?.cc ?? []).map((person) => person.name);
  const noticeLine = toName
    ? `Notice to purchasing: To ${toName}${ccNames.length ? `, Cc ${ccNames.join(', ')}` : ''}`
    : 'Notice to purchasing: no recipient';

  async function handleConfirm() {
    await onConfirm(park);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{`Reserve ${lines.length} ${lines.length === 1 ? 'line' : 'lines'} on ${inquiryNo}`}</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex-1 space-y-4 overflow-y-auto">
          <DataGrid
            table={table}
            recordCount={lines.length}
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
          <p className="text-sm text-muted-foreground">{noticeLine}</p>
        </DialogBody>
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
      </DialogContent>
    </Dialog>
  );
}

export default ReserveConfirmDialog;
