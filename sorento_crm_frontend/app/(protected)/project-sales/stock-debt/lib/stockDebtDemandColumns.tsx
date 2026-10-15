import * as React from 'react';
import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import { OrderInquiryDocumentLink } from '../../order-inquiries/components/OrderInquiryDocumentDialog';
import type {
  StockDebtAssignedFrom,
  StockDebtDemandLine,
  StockDebtDemandStatus,
} from '../types/stockDebt.types';

/**
 * The drill's Demand columns, one definition for the live drill (`StockDebtCellDialog`) and the
 * snapshot drill (`SnapshotDrillDialog`), so a frozen line reads exactly like a live one.
 */

/**
 * The shared soft-pastel status palette (ADR 1d), paired with `STATUS_PILL_BASE` - the
 * shape `BoardDecisionPill` uses for the board's own verdicts. A local map rather than
 * `statusPillClass`, for the same reason that pill keeps one: these four words are this
 * screen's vocabulary, and the shared map is keyed on form/document statuses that mean
 * something else. What is shared is the LOOK, so a status here and a status on the board
 * read as the same kind of thing.
 */
const STATUS_CLASS: Record<StockDebtDemandStatus, string> = {
  covered: 'bg-emerald-100 text-emerald-800',
  pinned: 'bg-sky-100 text-sky-800',
  late: 'bg-amber-100 text-amber-800',
  short: 'bg-red-100 text-red-800',
  // STOCK-DEBT-LENDABLE: a planned re-buy, neither short (the goods exist, lent to a
  // nearer line) nor pinned (they are not this line's any more) - its own colour.
  order_back: 'bg-violet-100 text-violet-800',
};

export function dateOrDash(value: string | null): string {
  return value ? formatDateInMalaysia(value) : '-';
}

/**
 * ONE `assigned_from` entry, as R31a's single "Covered by" column prints it: an on-hand
 * bin is plain text with NO quantity suffix (R30's had one), a document is the SAME
 * `OrderInquiryDocumentLink` the OI screens use with NO "line N (qty)" beside it (R30's
 * had one too) - the drill states WHERE a line drew from, `Assigned` already states how
 * much. `highlightLines` marks the document's own line when the dialog opens from here
 * (R31b), so a reader can jump straight to it.
 */
/** R42: a PO line - the document link, then its line number, muted. */
export function PoLineLink({
  poNumber,
  poLineNumber,
  poId,
  poLineId,
}: {
  poNumber: string;
  poLineNumber: number | null;
  poId: string | null;
  poLineId: string | null;
}) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1">
      <OrderInquiryDocumentLink kind="po" document={poNumber} poId={poId} poLineId={poLineId} />
      {poLineNumber != null && (
        <span className="shrink-0 text-muted-foreground">line {poLineNumber}</span>
      )}
    </span>
  );
}

function CoveredByEntry({ entry }: { entry: StockDebtAssignedFrom }) {
  if (entry.kind === 'on_hand') {
    // STOCK-DEBT-LENDABLE: the server's own wording carries the lender's name when the
    // take was lent ("On hand BRW-BB (from SO381065)"); a free take stays plain.
    return (
      <span className="truncate text-muted-foreground" title={entry.ref}>
        {entry.ref}
      </span>
    );
  }
  if (entry.kind === 'lent') {
    // STOCK-DEBT-LENDABLE: a line that lent its landed goods names each receiver,
    // linked to the receiving order the way the Sales order cell links its own.
    return entry.sales_order_id ? (
      <Link
        href={`/scm/sales-orders/${entry.sales_order_id}`}
        target="_blank"
        rel="noreferrer"
        className="truncate text-muted-foreground hover:underline"
        title={entry.ref}
      >
        {entry.ref}
      </Link>
    ) : (
      <span className="truncate text-muted-foreground" title={entry.ref}>
        {entry.ref}
      </span>
    );
  }
  if (entry.kind === 'po') {
    // R42: a purchase order covers a line too. The same link, kind `po`, opened on the
    // PO line itself (`poLineId` marks it and offers the jump). The line number rides
    // beside it, unlike an SPO's (R31a): one PO often covers one order from two lines
    // (PO 202609-S0029's 1,305 and 4 both name SO419208), and two identical numbers
    // side by side read as one document listed twice.
    return entry.po_number ? (
      <PoLineLink
        poNumber={entry.po_number}
        poLineNumber={entry.po_line_number ?? null}
        poId={entry.po_id ?? null}
        poLineId={entry.po_line_id ?? null}
      />
    ) : (
      <span className="truncate text-muted-foreground">{entry.ref}</span>
    );
  }
  if (!entry.spo_number) {
    return null;
  }
  return (
    <OrderInquiryDocumentLink
      kind={entry.kind}
      document={entry.spo_number}
      highlightLines={entry.spo_line_number != null ? [entry.spo_line_number] : undefined}
    />
  );
}

/** The Demand footer's figures, over the rows a grid is showing. */
export interface DemandTotals {
  ordered: number;
  delivered: number;
  outstanding: number;
  assigned: number;
  short: number;
}

export function sumDemand(lines: StockDebtDemandLine[]): DemandTotals {
  return lines.reduce(
    (totals, row) => ({
      ordered: totals.ordered + (row.qty_ordered ?? 0),
      delivered: totals.delivered + (row.qty_delivered ?? 0),
      outstanding: totals.outstanding + row.open_qty,
      assigned: totals.assigned + row.assigned_qty,
      short: totals.short + row.short_qty,
    }),
    { ordered: 0, delivered: 0, outstanding: 0, assigned: 0, short: 0 },
  );
}

export function buildDemandColumns<R extends StockDebtDemandLine = StockDebtDemandLine>(
  demandTotals: DemandTotals,
): ColumnDef<R>[] {
  return [
  {
    id: 'so_number',
    accessorKey: 'so_number',
    header: ({ column }) => <DataGridColumnHeader title="Sales order" column={column} />,
    size: 130,
    // R24: the Total footer row's own label, in the leftmost column - the same
    // convention the board's own footer row uses.
    footer: () => 'Total',
    // R29: linked to the sales order's own page - new tab, so a reader keeps this
    // drill open beside it.
    cell: ({ row }) =>
      row.original.sales_order_id ? (
        <Link
          href={`/scm/sales-orders/${row.original.sales_order_id}`}
          target="_blank"
          rel="noreferrer"
          className="truncate font-medium text-primary hover:underline"
          title={row.original.so_number}
        >
          {row.original.so_number}
        </Link>
      ) : (
        <span className="truncate font-medium" title={row.original.so_number}>
          {row.original.so_number}
        </span>
      ),
  },
  {
    id: 'agent_code',
    accessorKey: 'agent_code',
    header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
    size: 110,
    cell: ({ row }) => (
      <span className="truncate" title={row.original.agent_code ?? ''}>
        {row.original.agent_code ?? '-'}
      </span>
    ),
  },
  {
    // Before the date, because "which bin" is what decides whether this line and the
    // supply beside it are even the same pile (the ownership group is the code's suffix).
    id: 'warehouse_code',
    accessorKey: 'warehouse_code',
    header: ({ column }) => <DataGridColumnHeader title="Bin" column={column} />,
    size: 110,
    cell: ({ row }) => (
      <span className="truncate" title={row.original.warehouse_code ?? ''}>
        {row.original.warehouse_code ?? '-'}
      </span>
    ),
  },
  {
    id: 'required_date',
    accessorKey: 'required_date',
    header: ({ column }) => <DataGridColumnHeader title="Due" column={column} />,
    size: 120,
    cell: ({ row }) => <span>{dateOrDash(row.original.required_date)}</span>,
  },
  {
    // R22: CS's own Order Inquiry statement when set, else the sales-order book's
    // own `qty_ordered` - `plan_qty()` server-side, echoed here rather than
    // re-derived. Optional on the wire TYPE (not on the real response, which always
    // carries it) only so a fixture built before this round still type-checks.
    id: 'qty_ordered',
    accessorKey: 'qty_ordered',
    header: ({ column }) => <DataGridColumnHeader title="Ordered" column={column} />,
    size: 100,
    meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
    footer: () => demandTotals.ordered.toLocaleString(),
    cell: ({ row }) => (
      <span className="tabular-nums">
        {(row.original.qty_ordered ?? 0).toLocaleString()}
      </span>
    ),
  },
  {
    id: 'qty_delivered',
    accessorKey: 'qty_delivered',
    header: ({ column }) => <DataGridColumnHeader title="Delivered" column={column} />,
    size: 100,
    meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
    footer: () => demandTotals.delivered.toLocaleString(),
    cell: ({ row }) => (
      <span className="tabular-nums">
        {(row.original.qty_delivered ?? 0).toLocaleString()}
      </span>
    ),
  },
  {
    // R22: renamed from "Open" - the same figure (`open_qty`), unchanged.
    id: 'open_qty',
    accessorKey: 'open_qty',
    header: ({ column }) => <DataGridColumnHeader title="Outstanding" column={column} />,
    size: 100,
    meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
    footer: () => demandTotals.outstanding.toLocaleString(),
    cell: ({ row }) => (
      <span className="tabular-nums">{row.original.open_qty.toLocaleString()}</span>
    ),
  },
  {
    id: 'assigned_qty',
    accessorKey: 'assigned_qty',
    header: ({ column }) => <DataGridColumnHeader title="Assigned" column={column} />,
    size: 100,
    meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
    footer: () => demandTotals.assigned.toLocaleString(),
    cell: ({ row }) => (
      <span className="tabular-nums">{row.original.assigned_qty.toLocaleString()}</span>
    ),
  },
  {
    // R31a (owner, 25 Sep) supersedes R30's three columns (From/SPO/OI) with ONE:
    // on-hand entries print plain text, a document entry the SAME
    // `OrderInquiryDocumentLink` the OI screens use, both together when a line drew
    // from both, and a dash when nothing did. Neither carries a quantity or line
    // number any more - `Assigned` already states how much, and the document's own
    // dialog (opened with `highlightLines`, R31b) is where a reader learns which line.
    id: 'covered_by',
    header: ({ column }) => <DataGridColumnHeader title="Covered by" column={column} />,
    size: 220,
    cell: ({ row }) => {
      const entries = row.original.assigned_from ?? [];
      if (!entries.length) {
        return <span className="text-muted-foreground">-</span>;
      }
      return (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          {entries.map((entry, index) => (
            <CoveredByEntry key={`${entry.kind}:${entry.ref}:${index}`} entry={entry} />
          ))}
        </div>
      );
    },
  },
  {
    id: 'status',
    accessorKey: 'status',
    header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
    size: 120,
    // R24 addendum: the Total row's own Short - sum of `short_qty` - sits here rather
    // than a standalone "Uncovered N" line under the grid (R37 still foots the SAME
    // number, just relocated).
    footer: () => `Short ${demandTotals.short.toLocaleString()}`,
    cell: ({ row }) => {
      // `short_qty` is the SERVER's own figure - what the line went without ON ITS OWN
      // DATE (R37), which is also what its month books and what the footer below sums.
      // Re-deriving it as `open - assigned` disagreed with the payload the moment later
      // supply cleared the shortfall: a `late` line ended fully assigned and printed
      // "short 0" beside a cell the same line had put in debt.
      //
      // A LATE line is exactly that case - later supply cleared it, so Open and Assigned
      // read the same - and it STILL books its shortfall in this month (R37, AC-S2-7).
      // Printing only the word "late" left the figure the cell was made of unsaid, so
      // the row is stated as `late . short 40`: what happened, and how much of it.
      const { status, short_qty: shortQty, lent_qty: lentQty = 0 } = row.original;
      const pill = cn(STATUS_PILL_BASE, STATUS_CLASS[status]);
      const shortLabel = `short ${shortQty.toLocaleString()}`;
      // STOCK-DEBT-LENDABLE: a line that lent its landed goods reads "order back N"
      // (its own status), N being what it is still OWED for the lend - never more
      // than its own shortfall, so a line that re-covered part of it elsewhere does
      // not read as owed the whole lend. When it is short beyond that, short outranks
      // and the two halves are said apart: "short 12 · order back 88" adds up to the
      // 100 the month books, where "short 100 · order back 88" read as 188.
      const orderBack = Math.min(lentQty, shortQty);
      const orderBackLabel = `order back ${orderBack.toLocaleString()}`;
      if (status === 'order_back') {
        return <span className={pill}>{orderBackLabel}</span>;
      }
      if (shortQty > 0 && status === 'short' && orderBack > 0) {
        return (
          <span className={cn(pill, 'gap-1')}>
            {`short ${(shortQty - orderBack).toLocaleString()}`}
            <span aria-hidden="true">&middot;</span>
            <span>{orderBackLabel}</span>
          </span>
        );
      }
      if (shortQty > 0 && status === 'short') {
        // "short 16" already says both, so the word is not repeated.
        return <span className={pill}>{shortLabel}</span>;
      }
      if (shortQty > 0 && status === 'late') {
        return (
          <span className={cn(pill, 'gap-1')}>
            {status}
            <span aria-hidden="true">&middot;</span>
            <span>{shortLabel}</span>
          </span>
        );
      }
      return <span className={pill}>{status}</span>;
    },
  },
]
}
