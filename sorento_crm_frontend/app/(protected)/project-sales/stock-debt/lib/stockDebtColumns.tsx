import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { Skeleton } from '@/components/ui/skeleton';
import { codeWithName, distinctName } from '@/lib/product-display';
import type { StockDebtRow, StockDebtTone, StockDebtTotals } from '../types/stockDebt.types';

/**
 * The Stock Debt grid's column set, one builder for the live board and the snapshot page so
 * the two can never draw the same book differently. Pure: everything that differs (what a
 * value cell does when pressed, how a header selects its column, what sits after Product) is
 * handed in.
 */

/** Cell tone as a CLASS, not a component (plan 3.4): three lines, no new file. */
export const TONE_CLASS: Record<StockDebtTone, string> = {
  red: 'bg-destructive/10 text-destructive',
  amber: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  green: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
};

/**
 * TBA and No date carry no tone: they draw no supply at all (R14), so a colour that
 * elsewhere means "can this still be bought in time" would be answering a question
 * nobody asked of them. Informational, per the plan's tone card.
 */
export const NEUTRAL_CLASS = 'bg-muted text-foreground';

/** The Total column carries no tone at all (AC-21): it is a sum, not a reading. */
export const TOTAL_CLASS = 'font-semibold';

/** The press target every value cell shares (live board and snapshot). */
export const VALUE_CELL_CLASS =
  'block w-full rounded px-2 py-1 text-end text-sm tabular-nums transition-colors hover:brightness-95 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring';

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/** `2026-08` -> `Aug 26`. Narrow on purpose: fifteen of these share one width. */
export function monthLabel(key: string): string {
  const [year, month] = key.split('-');
  const index = Number(month) - 1;
  return `${MONTH_NAMES[index] ?? month} ${year?.slice(2) ?? ''}`;
}

/** `-16` reads as debt, `+84` as surplus; a bare `84` reads as neither. */
export function signed(value: number): string {
  return value > 0 ? `+${value.toLocaleString()}` : value.toLocaleString();
}

/** Aux quantities print plain: no leading `+`, and never `-0`. */
export function plainQty(value: number): string {
  return (value || 0).toLocaleString();
}

/** The four read-only columns between Product and the months. */
export const AUX_COLUMNS = [
  { key: 'pool_on_hand', header: 'BRW Retail On Hand Qty' },
  { key: 'pool_po_qty', header: 'BRW Retail PO Qty' },
  { key: 'pool_incoming_qty', header: 'BRW Retail Incoming Qty' },
  { key: 'dealer_outstanding', header: 'Dealer O/standing Qty' },
] as const;

/** What a value cell is asked to draw. `openDrill` is false for Total, which sums three
 * buckets that carry no drill of their own. */
export interface ValueCellArgs<R> {
  row: R;
  columnKey: string;
  month: string;
  label: string;
  balance: number;
  toneClass: string;
  openDrill: boolean;
}

export interface StockDebtColumnsInput<R extends StockDebtRow> {
  months: string[];
  tbaMonth: string | null;
  totals: StockDebtTotals | undefined;
  showAux: boolean;
  renderValueCell: (args: ValueCellArgs<R>) => React.ReactNode;
  renderColumnHeader: (columnKey: string, label: React.ReactNode, titleText: string) => React.ReactNode;
  /** Columns placed right after Product (the snapshot's Decision). */
  afterProduct?: ColumnDef<R>[];
  /** Replaces the plain Product cell body (the snapshot makes it a press target). */
  renderProduct?: (row: R, content: React.ReactNode) => React.ReactNode;
}

export function buildStockDebtColumns<R extends StockDebtRow>({
  months,
  tbaMonth,
  totals,
  showAux,
  renderValueCell,
  renderColumnHeader,
  afterProduct = [],
  renderProduct,
}: StockDebtColumnsInput<R>): ColumnDef<R>[] {
  return [
    {
      id: 'product',
      header: 'Product',
      // PINNED, not hand-stuck. The DataGrid's own column pinning
      // (`tableLayout.columnsPinnable` + `initialState.columnPinning`) writes
      // `position: sticky` and the `left` offset as an INLINE style, which is the only
      // way it holds: a `sticky left-0` utility in `headerClassName` sits in the same
      // Tailwind position group as the `relative` the base cell already carries, and
      // which of the two wins is decided by their order in the generated stylesheet,
      // not by the order in the class attribute. It lost, and the column computed to
      // `position: relative` in the browser while reading as sticky in the source.
      meta: {
        headerTitle: 'Product',
        skeleton: <Skeleton className="h-4 w-40" />,
      },
      size: 240,
      footer: () => 'Total',
      cell: ({ row }) => {
        const label = codeWithName(row.original.product_code, row.original.product_name);
        const content = (
          <div className="min-w-0" title={label}>
            <div className="truncate text-sm font-medium">{row.original.product_code}</div>
            {distinctName(row.original.product_code, row.original.product_name) && (
              <div className="truncate text-xs text-muted-foreground">
                {row.original.product_name}
              </div>
            )}
          </div>
        );
        return renderProduct ? renderProduct(row.original, content) : content;
      },
    },
    ...afterProduct,
    ...(showAux
      ? AUX_COLUMNS.map<ColumnDef<R>>(({ key, header }) => ({
          id: `aux:${key}`,
          header: () => <span className="block truncate text-end" title={header}>{header}</span>,
          meta: {
            headerTitle: header,
            headerClassName: 'text-end',
            skeleton: <Skeleton className="h-4 w-full" />,
          },
          size: 112,
          footer: () => (totals ? plainQty(totals[key] ?? 0) : '-'),
          cell: ({ row }) => (
            <span className="block px-2 py-1 text-end text-sm tabular-nums">
              {plainQty(row.original[key] ?? 0)}
            </span>
          ),
        }))
      : []),
    ...months.map<ColumnDef<R>>((key) => ({
      id: `m:${key}`,
      header: () => renderColumnHeader(`m:${key}`, monthLabel(key), monthLabel(key)),
      meta: {
        headerTitle: monthLabel(key),
        headerClassName: 'text-end',
        skeleton: <Skeleton className="h-4 w-full" />,
      },
      size: 96,
      footer: () => (totals ? signed(totals.months[key] ?? 0) : '-'),
      cell: ({ row }) => {
        const month = row.original.months.find((entry) => entry.key === key);
        if (!month) return <span className="block text-end text-muted-foreground">-</span>;
        return renderValueCell({
          row: row.original,
          columnKey: `m:${key}`,
          month: key,
          label: monthLabel(key),
          balance: month.balance,
          toneClass: TONE_CLASS[month.tone],
          openDrill: true,
        });
      },
    })),
    {
      id: 'tba',
      // R18: the HEADER reads "TBA" literally always - the policy's own TBA month is
      // display-only, in the title tooltip. The CELL's own label (aria-label, dialog
      // title) still names the actual month, exactly as the drill it opens does.
      header: () =>
        renderColumnHeader('tba', 'TBA', tbaMonth ? `TBA (${monthLabel(tbaMonth)})` : 'TBA'),
      meta: {
        headerTitle: tbaMonth ? `TBA (${monthLabel(tbaMonth)})` : 'TBA',
        headerClassName: 'text-end',
        skeleton: <Skeleton className="h-4 w-full" />,
      },
      size: 104,
      footer: () => (totals ? signed(totals.tba) : '-'),
      cell: ({ row }) =>
        renderValueCell({
          row: row.original,
          columnKey: 'tba',
          month: 'tba',
          label: tbaMonth ?? 'TBA',
          balance: row.original.tba,
          toneClass: NEUTRAL_CLASS,
          openDrill: true,
        }),
    },
    {
      id: 'total',
      // AC-21: the row's own total, right-aligned, signed, no tone, last column. AC-22:
      // its footer is the WHOLE filtered set's total, not the page's.
      header: () => renderColumnHeader('total', 'Total', 'Total'),
      meta: {
        headerTitle: 'Total',
        headerClassName: 'text-end',
        skeleton: <Skeleton className="h-4 w-full" />,
      },
      size: 104,
      footer: () => (totals ? signed(totals.total) : '-'),
      cell: ({ row }) =>
        renderValueCell({
          row: row.original,
          columnKey: 'total',
          month: 'total',
          label: 'Total',
          balance: row.original.total,
          toneClass: TOTAL_CLASS,
          openDrill: false,
        }),
    },
  ];
}
