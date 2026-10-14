'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { type ColumnDef } from '@tanstack/react-table';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { DrillTable, RIGHT, TOTAL_LABEL } from '../../../components/PlanRowDialog';
import { EM_DASH, fmtDate, fmtInt } from '../../../lib/format';
import { MATCH_LABEL } from '../../../lib/autocountCancel';
import type {
  SalesOrderDeliveries,
  SalesOrderLine,
  SoLineCancelRow,
  SoLineDeliveryRow,
} from '../../../types/scm.types';

/**
 * The lightbox the truck button on a sales-order line opens (SO-DELIVERIES-VIEW): the line's
 * figures, the DOs delivered against it and the Cancel SO documents that cancelled it, plus the
 * product's "not tied to one line" block when the SO holds the product on several lines.
 */

const dash = <span className="text-muted-foreground">{EM_DASH}</span>;

function textCell(value: string | null | undefined) {
  return value ? value : dash;
}

function plural(n: number, one: string, many: string) {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

function Block({ title, count, children }: { title: string; count: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-semibold">{title}</span>
        <span className="text-xs text-muted-foreground">{count}</span>
      </div>
      {children}
    </section>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-24 flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-medium tabular-nums">{value}</span>
    </div>
  );
}

function DeliveriesTable({
  rows,
  canOpenDo,
  emptyMessage,
}: {
  rows: SoLineDeliveryRow[];
  canOpenDo: boolean;
  emptyMessage: string;
}) {
  const total = rows.reduce((sum, r) => sum + Number(r.qty ?? 0), 0);
  const columns = useMemo<ColumnDef<SoLineDeliveryRow>[]>(
    () => [
      {
        id: 'do_number',
        header: 'DO no.',
        cell: ({ row }) =>
          canOpenDo ? (
            <Link
              href={`/order-management/orders/${row.original.order_id}`}
              className="font-medium text-primary hover:underline"
              title={`Open ${row.original.do_number}`}
            >
              {row.original.do_number}
            </Link>
          ) : (
            row.original.do_number
          ),
        footer: () => TOTAL_LABEL,
        size: 130,
      },
      { id: 'do_date', header: 'DO date', cell: ({ row }) => fmtDate(row.original.do_date), size: 100 },
      { id: 'location', header: 'Location', cell: ({ row }) => textCell(row.original.location), size: 110 },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) =>
          row.original.status ? <Badge status={row.original.status}>{row.original.status}</Badge> : dash,
        size: 170,
      },
      {
        id: 'actual_delivery_date',
        header: 'Actual delivery',
        cell: ({ row }) => (row.original.actual_delivery_date ? fmtDate(row.original.actual_delivery_date) : dash),
        size: 120,
      },
      {
        id: 'qty',
        header: 'Qty',
        cell: ({ row }) => fmtInt(row.original.qty),
        footer: () => fmtInt(total),
        size: 80,
        meta: RIGHT,
      },
    ],
    [canOpenDo, total],
  );
  return (
    <DrillTable
      columns={columns}
      rows={rows}
      getRowId={(r, i) => `${r.do_number}-${i}`}
      emptyMessage={emptyMessage}
      scrollerMaxHeight={false}
    />
  );
}

function CancelsTable({ rows, emptyMessage }: { rows: SoLineCancelRow[]; emptyMessage: string }) {
  const total = rows.reduce((sum, r) => sum + Number(r.qty ?? 0), 0);
  const columns = useMemo<ColumnDef<SoLineCancelRow>[]>(
    () => [
      { id: 'doc_no', header: 'Cancel SO no.', cell: ({ row }) => row.original.doc_no, footer: () => TOTAL_LABEL, size: 130 },
      { id: 'doc_date', header: 'Date', cell: ({ row }) => fmtDate(row.original.doc_date), size: 100 },
      { id: 'location', header: 'Location', cell: ({ row }) => textCell(row.original.location), size: 110 },
      {
        id: 'match_reason',
        header: 'Matched by',
        cell: ({ row }) => textCell(MATCH_LABEL[row.original.match_reason ?? '']),
        size: 190,
      },
      {
        id: 'qty',
        header: 'Qty',
        cell: ({ row }) => (row.original.qty == null ? dash : fmtInt(row.original.qty)),
        footer: () => fmtInt(total),
        size: 80,
        meta: RIGHT,
      },
    ],
    [total],
  );
  return (
    <DrillTable
      columns={columns}
      rows={rows}
      getRowId={(r, i) => `${r.doc_no}-${i}`}
      emptyMessage={emptyMessage}
      scrollerMaxHeight={false}
    />
  );
}

export function SoLineHistoryBody({
  line,
  data,
  isLoading,
  isError,
  onRetry,
  canOpenDo,
}: {
  line: SalesOrderLine;
  data: SalesOrderDeliveries | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  canOpenDo: boolean;
}) {
  const strip = (
    <div className="flex flex-wrap gap-x-6 gap-y-2">
      <Figure label="Ordered" value={fmtInt(line.qty_ordered)} />
      <Figure label="Delivered" value={fmtInt(line.qty_delivered)} />
      <Figure label="Cancelled" value={fmtInt(line.qty_cancelled ?? 0)} />
      <Figure label="Outstanding" value={fmtInt(line.outstanding_qty)} />
      <Figure label="Location" value={line.warehouse_code || EM_DASH} />
      <Figure label="Delivery date" value={fmtDate(line.required_date)} />
    </div>
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        {strip}
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-4">
        {strip}
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>Could not load deliveries.</span>
            <Button type="button" variant="outline" size="sm" onClick={onRetry}>
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const own = data.lines.find((l) => l.line_id === line.id);
  const deliveries = own?.deliveries ?? [];
  const cancels = own?.cancels ?? [];
  const group = data.groups.find((g) => g.line_ids.includes(line.id));

  return (
    <div className="space-y-6">
      {strip}
      <Block title="Delivered" count={plural(deliveries.length, 'DO line', 'DO lines')}>
        <DeliveriesTable rows={deliveries} canOpenDo={canOpenDo} emptyMessage="No DO in the CRM for this line." />
      </Block>
      <Block title="Cancelled" count={plural(cancels.length, 'Cancel SO line', 'Cancel SO lines')}>
        <CancelsTable rows={cancels} emptyMessage="No Cancel SO for this line." />
      </Block>
      {group ? (
        <section className="space-y-4 border-t pt-4">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-semibold">{`${group.sku} on this SO, not tied to one line`}</span>
            <span className="text-xs text-muted-foreground">{`· ${group.line_ids.length} lines`}</span>
          </div>
          <Block title="Delivered" count={plural(group.deliveries.length, 'DO line', 'DO lines')}>
            <DeliveriesTable
              rows={group.deliveries}
              canOpenDo={canOpenDo}
              emptyMessage="No DO in the CRM for this product here."
            />
          </Block>
          <Block title="Cancelled" count={plural(group.cancels.length, 'Cancel SO line', 'Cancel SO lines')}>
            <CancelsTable rows={group.cancels} emptyMessage="No Cancel SO for this product here." />
          </Block>
        </section>
      ) : null}
    </div>
  );
}

export default SoLineHistoryBody;
