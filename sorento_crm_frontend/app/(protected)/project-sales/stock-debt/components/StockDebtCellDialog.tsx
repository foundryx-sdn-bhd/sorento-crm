'use client';

import * as React from 'react';
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
import { STATUS_PILL_BASE } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { OrderInquiryDocumentLink } from '../../order-inquiries/components/OrderInquiryDocumentDialog';
import { useStockDebtCellQuery } from '../hooks/useStockDebtQuery';
import type { StockDebtLocationsMode } from '../services/stockDebtService';
import type {
  StockDebtBook,
  StockDebtDemandLine,
  StockDebtProjection,
  StockDebtSupplyEvent,
  StockDebtSupplyKind,
} from '../types/stockDebt.types';
import { distinctName } from '@/lib/product-display';
import { PoLineLink, buildDemandColumns, dateOrDash as date, sumDemand } from '../lib/stockDebtDemandColumns';

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

function signedQty(value: number): string {
  return value > 0 ? `+${value.toLocaleString()}` : value.toLocaleString();
}

/** The drill's projection line, plainly: what the project bins are short or spare this
 *  month (pool stock only through a link, as Covered by shows), then each reserve term
 *  that is not zero, then the result and what carries on. */
function projectionParts(projection: StockDebtProjection): string[] {
  const parts = [`Project bins ${signedQty(projection.month_balance)}`];
  if (projection.carried_in) parts.push(`Carried in ${signedQty(projection.carried_in)}`);
  if (projection.reserve_supply) {
    const sources = (projection.reserve_sources ?? [])
      .map((source) => `${source.label} ${source.qty.toLocaleString()}`)
      .join(', ');
    parts.push(
      `Reserve supply ${projection.reserve_supply.toLocaleString()}${sources ? ` (${sources})` : ''} applied to this month`,
    );
  }
  parts.push(`Projected ${signedQty(projection.projected)}`);
  if (projection.carried_out) {
    parts.push(`Carried forward ${signedQty(projection.carried_out)}`);
  }
  return parts;
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
  projected = false,
  locations,
  locationsMode,
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
   *  the cell that opened it. */
  dateFrom?: string;
  dateTo?: string;
  /** The board's own book (AC-11), same reason. */
  book?: StockDebtBook;
  /** STOCK-DEBT-PROJECTION: the board's "Projected balance" switch, same reason. */
  projected?: boolean;
  /** The board's Location pick (owner, 14 Oct 2026), same reason. */
  locations?: string[];
  locationsMode?: StockDebtLocationsMode;
  onClose: () => void;
}) {
  // The board's own narrowing travels with the request AND with the cache key: the same
  // product and month answer differently under a due date range / a book, so the
  // drill has to be recomputed over the span the cell that opened it was.
  const cell = useStockDebtCellQuery(productId, month, dateFrom, dateTo, book, projected, locations, locationsMode);
  const projection = cell.data?.projection ?? null;

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
  // STOCK-DEBT-STALE-PIN: an empty Supply tab beside a demand row that shows cover is a
  // column covered from stock listed earlier (today's on hand), never "no stock is held".
  const demandShowsCover = demand.some((line) => line.assigned_qty > 0);

  // R24: a Total FOOTER ROW on each grid replaces the standalone "Uncovered N"/"Free N"
  // lines that used to sit under them - summed over the SAME rows the tab itself is
  // showing (the filtered set on Demand while a search narrows it, never the unfiltered
  // total the search left behind).
  const demandTotals = React.useMemo(() => sumDemand(filteredDemand), [filteredDemand]);
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

  const demandColumns = React.useMemo(() => buildDemandColumns(demandTotals), [demandTotals]);

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
  const distinctProductName = distinctName(productCode, productName);

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
          {projection && (
            <p
              data-testid="stock-debt-projection-line"
              className="text-xs text-muted-foreground"
            >
              {projectionParts(projection).join(' · ')}
            </p>
          )}
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
                  emptyBody={
                    demandShowsCover
                      ? 'What covers these lines is listed in an earlier column.'
                      : 'No stock is held or on the way for this column.'
                  }
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
