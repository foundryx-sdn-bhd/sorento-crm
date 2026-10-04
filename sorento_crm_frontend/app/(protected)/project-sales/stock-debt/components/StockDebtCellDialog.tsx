'use client';

import * as React from 'react';
import Link from 'next/link';
import { ColumnDef } from '@tanstack/react-table';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { OrderInquiryDocumentLink } from '../../order-inquiries/components/OrderInquiryDocumentDialog';
import { useStockDebtCellQuery } from '../hooks/useStockDebtQuery';
import type {
  StockDebtAssignedFrom,
  StockDebtBook,
  StockDebtDemandLine,
  StockDebtDemandStatus,
  StockDebtSupplyEvent,
  StockDebtSupplyKind,
} from '../types/stockDebt.types';

/**
 * The two tables behind one cell (R28, AC-S2-11): what is DUE in that month, and what
 * is HELD or ARRIVING for it - each with its assignments.
 *
 * ONE lightbox for the SCM family: the shell (sizing, header layout, scrolling body) is
 * COPIED from `scm/components/PlanRowDialog.tsx`, exactly the way that file copied it from
 * the reorder lane's `PlanRowDialogs.tsx` - the same object to a reader, with none of that
 * module's six data hooks dragged into this page's bundle for twenty lines of frame. At
 * whichever merge lands last, the three re-point at one file (plan section 9).
 *
 * Demand and Supply are TABS, like `ProjectRetailTabs` next door: two stacked grids made
 * the reader scroll past one to reach the other, and the counts belong in the trigger where
 * they can be read without opening anything.
 *
 * Both are the system list (`PanelDataGrid`), so a reader gets the same table here as
 * on every other screen. The view never decides: the only press is Plan, which hands
 * the order to the board (R23).
 *
 * Each tab foots with the cell that opened it (R37): `Free` less `Uncovered` IS the
 * balance in the title, because a month states its own month and nothing carries into it.
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

const KIND_LABEL: Record<StockDebtSupplyKind, string> = {
  on_hand: 'On hand',
  spo: 'SPO',
  po: 'PO',
};

/** A stable reference for "no rows yet" - `cell.data?.demand ?? []` would otherwise hand
 *  the `filteredDemand`/`demandTotals` memos below a NEW empty array every render,
 *  invalidating them for no reason. */
const EMPTY_DEMAND: StockDebtDemandLine[] = [];
const EMPTY_SUPPLY: StockDebtSupplyEvent[] = [];

function date(value: string | null): string {
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
function PoLineLink({
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

export function StockDebtCellDialog({
  productId,
  productCode,
  productName,
  month,
  monthLabel,
  balance,
  dateFrom,
  dateTo,
  book,
  onClose,
}: {
  productId: string;
  productCode: string;
  productName: string | null;
  /** `YYYY-MM`, `tba`, `undated` or `unlocated`. */
  month: string;
  /** What the column header said, so the dialog names the same thing the reader clicked. */
  monthLabel: string;
  balance: number;
  /** The board's own due date range (AC-11/AC-11b, R14), echoed so the drill foots with
   *  the cell that opened it. R16 retired the Ownership group this dialog used to take
   *  instead - there is no `group` prop any more. */
  dateFrom?: string;
  dateTo?: string;
  /** The board's own book (AC-11), same reason. */
  book?: StockDebtBook;
  onClose: () => void;
}) {
  // The board's own narrowing travels with the request AND with the cache key: the same
  // product and month answer differently under a due date range / a book, so the
  // drill has to be recomputed over the span the cell that opened it was.
  const cell = useStockDebtCellQuery(productId, month, dateFrom, dateTo, book);

  const demand = cell.data?.demand ?? EMPTY_DEMAND;
  const supply = cell.data?.supply ?? EMPTY_SUPPLY;

  // R20: the Demand grid's own search, by SO number / agent / bin. Client-side and
  // filtered here (not via `PanelDataGrid`'s own `searchOf`) because the tab label needs
  // to react to how many rows still match, which `searchOf`'s internal state does not
  // expose to a caller.
  const [demandSearch, setDemandSearch] = React.useState('');
  const filteredDemand = React.useMemo(() => {
    const needle = demandSearch.trim().toLowerCase();
    if (!needle) return demand;
    return demand.filter((line) =>
      `${line.so_number} ${line.agent_code ?? ''} ${line.warehouse_code ?? ''}`
        .toLowerCase()
        .includes(needle),
    );
  }, [demand, demandSearch]);

  // R25: the tab labels state total QUANTITY, never a record count - the envelope's own
  // `demand_total_qty`/`supply_total_qty`, summed over the WHOLE tab server-side so the
  // FE never re-derives it (and cannot disagree with the board's own arithmetic). Falls
  // back to summing the rows on hand only for a fixture built before this round, which
  // never carries either field.
  const demandTotalQty =
    cell.data?.demand_total_qty ?? demand.reduce((total, row) => total + row.open_qty, 0);
  const supplyTotalQty =
    cell.data?.supply_total_qty ?? supply.reduce((total, row) => total + row.qty, 0);
  const filteredDemandQty = filteredDemand.reduce((total, row) => total + row.open_qty, 0);
  const demandTabLabel = demandSearch.trim()
    ? `Demand (${filteredDemandQty.toLocaleString()} of ${demandTotalQty.toLocaleString()})`
    : `Demand (${demandTotalQty.toLocaleString()})`;
  const supplyTabLabel = `Supply (${supplyTotalQty.toLocaleString()})`;

  // R24: a Total FOOTER ROW on each grid replaces the standalone "Uncovered N"/"Free N"
  // lines that used to sit under them - summed over the SAME rows the tab itself is
  // showing (the filtered set on Demand while a search narrows it, never the unfiltered
  // total the search left behind).
  const demandTotals = React.useMemo(
    () =>
      filteredDemand.reduce(
        (totals, row) => ({
          ordered: totals.ordered + (row.qty_ordered ?? 0),
          delivered: totals.delivered + (row.qty_delivered ?? 0),
          outstanding: totals.outstanding + row.open_qty,
          assigned: totals.assigned + row.assigned_qty,
          short: totals.short + row.short_qty,
        }),
        { ordered: 0, delivered: 0, outstanding: 0, assigned: 0, short: 0 },
      ),
    [filteredDemand],
  );
  const supplyTotals = React.useMemo(
    () =>
      supply.reduce(
        (totals, row) => ({
          qty: totals.qty + row.qty,
          received: totals.received + (row.received_qty ?? 0),
          outstanding: totals.outstanding + (row.outstanding_qty ?? 0),
          free: totals.free + row.free_qty,
        }),
        { qty: 0, received: 0, outstanding: 0, free: 0 },
      ),
    [supply],
  );

  const demandColumns = React.useMemo<ColumnDef<StockDebtDemandLine>[]>(
    () => [
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
        cell: ({ row }) => <span>{date(row.original.required_date)}</span>,
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
    ],
    [demandTotals],
  );

  const supplyColumns = React.useMemo<ColumnDef<StockDebtSupplyEvent>[]>(
    () => [
      {
        id: 'kind',
        header: 'Kind',
        size: 90,
        // R24: the Total footer row's own label, same convention as the Demand grid's.
        footer: () => 'Total',
        cell: ({ row }) => <span>{KIND_LABEL[row.original.kind]}</span>,
      },
      {
        // R31a (supersedes R30's own muted " line N"): the SAME `OrderInquiryDocumentLink`
        // the OI screens use, never a bespoke href, the trigger alone - matching the OI
        // lines grid's own `DocumentCell` exactly. On hand names a bin, not a document,
        // so it stays plain text.
        id: 'ref',
        header: 'Document',
        size: 210,
        cell: ({ row }) =>
          row.original.kind === 'po' && row.original.po_number ? (
            // R42: a PO line, linked the way Covered by links it.
            <PoLineLink
              poNumber={row.original.po_number}
              poLineNumber={row.original.po_line_number ?? null}
              poId={row.original.po_id ?? null}
              poLineId={row.original.po_line_id ?? null}
            />
          ) : row.original.spo_number ? (
            <OrderInquiryDocumentLink kind="spo" document={row.original.spo_number} />
          ) : (
            <span className="block truncate" title={row.original.ref ?? ''}>
              {row.original.ref ?? '-'}
            </span>
          ),
      },
      {
        id: 'warehouse_code',
        header: 'Bin',
        size: 110,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.warehouse_code ?? ''}>
            {row.original.warehouse_code ?? '-'}
          </span>
        ),
      },
      {
        id: 'date',
        header: 'Arrival',
        size: 120,
        cell: ({ row }) => <span>{date(row.original.date)}</span>,
      },
      {
        // R26: an SPO's (and, R42, a PO line's) own RAW ordered quantity; the on-hand
        // figure, unchanged, for on hand.
        id: 'qty',
        header: 'Qty',
        size: 90,
        meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
        footer: () => supplyTotals.qty.toLocaleString(),
        cell: ({ row }) => (
          <span className="tabular-nums">{row.original.qty.toLocaleString()}</span>
        ),
      },
      {
        // R26: a document's own quantity received so far - blank for on hand, which has
        // no received/outstanding history to state.
        id: 'received_qty',
        header: 'Received',
        size: 100,
        meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
        footer: () => supplyTotals.received.toLocaleString(),
        cell: ({ row }) => (
          <span className="tabular-nums">
            {row.original.received_qty != null ? row.original.received_qty.toLocaleString() : ''}
          </span>
        ),
      },
      {
        // R26: the walk's own NETTED balance for a document (Qty minus Received) - what
        // the walk actually counts as incoming. Blank for on hand, same reason.
        id: 'outstanding_qty',
        header: 'Outstanding',
        size: 100,
        meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
        footer: () => supplyTotals.outstanding.toLocaleString(),
        cell: ({ row }) => (
          <span className="tabular-nums">
            {row.original.outstanding_qty != null
              ? row.original.outstanding_qty.toLocaleString()
              : ''}
          </span>
        ),
      },
      {
        id: 'assigned_to',
        header: 'Assigned to',
        size: 190,
        cell: ({ row }) => {
          // R29: `line_no` names the SO LINE itself, beside the SO number - "SO382618
          // line 2 (100)" - null when the core line has no project-line number of its
          // own, which keeps the pre-R29 "SO382618 (100)" shape.
          const label = row.original.assigned_to.length
            ? row.original.assigned_to
                .map(
                  (entry) =>
                    `${entry.so_number}${entry.line_no != null ? ` line ${entry.line_no}` : ''} (${entry.qty.toLocaleString()})`,
                )
                .join(', ')
            : 'Free';
          return (
            <span className="block truncate text-muted-foreground" title={label}>
              {label}
            </span>
          );
        },
      },
      {
        id: 'note',
        header: 'Note',
        size: 230,
        // R24 addendum: the Total row's own Free - sum of `free_qty` - sits here rather
        // than a standalone "Free N" line under the grid (R37 still foots the SAME
        // number, just relocated).
        footer: () => `Free ${supplyTotals.free.toLocaleString()}`,
        cell: ({ row }) => {
          // A PO line's `expected_date` is the SO date it was TYPED against (R29). Since
          // R42 the view parks the PO on it, so the server sends `bought_for` only when it
          // is NOT the row's date (the board's reading), and this prints only then.
          const boughtFor =
            row.original.kind === 'po' && row.original.bought_for
              ? `bought for ${date(row.original.bought_for)}`
              : null;
          return (
            <div className="flex min-w-0 items-center gap-2">
              {row.original.overdue && (
                <span
                  className={cn(
                    STATUS_PILL_BASE,
                    'shrink-0 normal-case',
                    'bg-amber-100 text-amber-800',
                  )}
                >
                  overdue, not counted
                </span>
              )}
              {boughtFor && (
                <span className="truncate text-muted-foreground" title={boughtFor}>
                  {boughtFor}
                </span>
              )}
              {!row.original.overdue && !boughtFor && (
                <span className="text-muted-foreground">-</span>
              )}
            </div>
          );
        },
      },
    ],
    [supplyTotals],
  );

  const signedBalance = balance > 0 ? `+${balance.toLocaleString()}` : balance.toLocaleString();
  // R16 retired the Ownership-group qualifier that used to sit here; no due-date-range /
  // book qualifier has replaced it on this line yet, so it is just the month and balance.
  const context = [monthLabel, signedBalance].filter(Boolean).join(' · ');
  // R27: the muted description line under the title repeats nothing - it renders only
  // when the name is SET and actually differs from the code, the same guard the board's
  // own list rows apply server-side (AC-9) but this dialog was never given.
  const distinctProductName =
    productName && productName !== productCode ? productName : null;

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      {/* The SCM family's shell, copied from `scm/components/PlanRowDialog.tsx`: same
          sizing, same header, same scrolling body, so the two screens' lightboxes are one
          object to a reader. */}
      <DialogContent
        data-testid="stock-debt-cell-dialog"
        className="flex max-h-[85vh] w-full flex-col overflow-hidden p-0 sm:max-w-[95vw]"
      >
        {/* `pe-10` is the one departure from the copied shell: the close button is absolute
            at `end-5`, and at 375px the month and the balance ran underneath it. */}
        <DialogHeader className="shrink-0 space-y-1 border-b p-4 pe-10 sm:p-6 sm:pe-10">
          <DialogTitle className="min-w-0 break-words">
            {`Product · ${productCode}`}
            <span className="ms-2 text-xs font-normal text-muted-foreground">{context}</span>
          </DialogTitle>
          <DialogDescription className="truncate text-xs" title={distinctProductName ?? undefined}>
            {distinctProductName}
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
          {cell.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : (
            <Tabs defaultValue="demand">
              <TabsList>
                <TabsTrigger value="demand">{demandTabLabel}</TabsTrigger>
                <TabsTrigger value="supply">{supplyTabLabel}</TabsTrigger>
              </TabsList>

              <TabsContent value="demand">
                <PanelDataGrid<StockDebtDemandLine>
                  title="Demand"
                  toolbar={
                    <Input
                      type="search"
                      value={demandSearch}
                      onChange={(e) => setDemandSearch(e.target.value)}
                      placeholder="Search SO, agent or bin…"
                      aria-label="Search"
                      className="h-8 w-full sm:w-56"
                    />
                  }
                  columns={demandColumns}
                  rows={filteredDemand}
                  sortable
                  // Page resets on every keystroke - `filteredDemand` is filtered OUTSIDE
                  // `PanelDataGrid` (see the tab-label note above), so its own
                  // `searchOf`-driven reset never runs; this is the `pageResetKey` an
                  // external filter is documented to use instead.
                  pageResetKey={demandSearch}
                  listingKey="projects.stock_debt.view::cell-demand"
                  error={cell.error}
                  emptyTitle="Nothing is due here"
                  emptyBody="No sales order line falls in this column."
                  pageSize={10}
                  // The DialogBody above already owns the scroll viewport (overflow-y-auto).
                  scrollerMaxHeight={false}
                />
              </TabsContent>

              <TabsContent value="supply">
                <PanelDataGrid<StockDebtSupplyEvent>
                  title="Supply"
                  columns={supplyColumns}
                  rows={supply}
                  listingKey="projects.stock_debt.view::cell-supply"
                  error={cell.error}
                  emptyTitle="Nothing arrives here"
                  emptyBody="No stock is held or on the way for this column."
                  pageSize={10}
                  // The DialogBody above already owns the scroll viewport (overflow-y-auto).
                  scrollerMaxHeight={false}
                />
              </TabsContent>
            </Tabs>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
