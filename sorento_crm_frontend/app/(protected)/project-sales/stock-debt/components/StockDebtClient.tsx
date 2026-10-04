'use client';

import * as React from 'react';
import {
  ColumnDef,
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Copy } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DateRangePicker, parseIsoDate } from '@/components/ui/date-range-picker';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { cn } from '@/lib/utils';
import { toast } from '@/lib/toast';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useCellSelection } from '../hooks/useCellSelection';
import { useStockDebtQuery } from '../hooks/useStockDebtQuery';
import type { StockDebtBook, StockDebtRow, StockDebtTone } from '../types/stockDebt.types';
import { StockDebtCellDialog } from './StockDebtCellDialog';
import { StockDebtExportPopover } from './StockDebtExportPopover';

/**
 * Stock Debt: one row per product, one column per month, and the cell is that MONTH's own
 * balance (R37, AC-S2-10) - the supply dated in it that stayed free, less what the lines
 * due in it went short of on their own dates. What is debted in August stays in August; a
 * month with nothing due and nothing arriving reads 0.
 *
 * The view shows and never decides (R23). A cell is a way into the two tables behind
 * it - the demand due and the supply held - and each demand line's Plan press hands
 * the order to the board, which is where deciding happens.
 *
 * The screen carries no explanation of what a colour means: the tone is a reading of
 * the number beside it, and a legend on a planner's daily screen is a paragraph they
 * read once (cursor rule: no feature explanations in the UI).
 *
 * Extended 24 Sep 2026 (PLAN-stock-debt-filters-totals-export-24sep.md, Phase 1): a
 * Filters panel (Book / Group / Supplier / Cutoff / Only in debt), a `Total` column and
 * footer over the WHOLE filtered set, Excel-style cell selection with a summary bar, and
 * an Export popover. The toolbar itself gets simpler (R13): Search, Filters, Export -
 * nothing else.
 *
 * Owner's hand-test round (R14-R19, same day): the single Cutoff date became a Due date
 * RANGE (`dateFrom`/`dateTo`, R14); the single Supplier select became a multi-select
 * (`supplierIds`, R15); the Ownership group control left the screen entirely, backend
 * `group` support untouched (R16); the "No date"/"No location" columns left the screen
 * and the workbook (R17, `Total` = months + TBA only); the TBA header reads "TBA"
 * literally, the policy's own month living in the header's title tooltip (R18); Copy
 * falls back to `document.execCommand('copy')` off a non-secure context (R19).
 *
 * Hand-test round 2 (R14b, superseded same day by R14c below): the `DateRangePicker` R14
 * introduced could not be driven at all inside this screen's Filters `DropdownMenu` - its
 * month arrows never advanced (a pointerdown anywhere in the Filters panel's OWN popovers
 * was mistaken for "outside the table" by the cell-selection-clear listener below, AC-29,
 * and the resulting re-render landed between the arrow's own mousedown and mouseup) - and
 * it offered no typed input at all. R14b's own fix was two typeable `DatePicker`s
 * labelled "From"/"To"; the AC-29 fix (the no-op `clear()` plus the
 * `[data-radix-popper-content-wrapper]` guard) stays, it was never the thing R14c reverses.
 *
 * R14c (owner, same day, on sight: "use the same date range component but I can type; I
 * don't want two different date fields; call it sales order delivery date"): back to ONE
 * `DateRangePicker`, relabelled "Sales order delivery date" - the component itself now
 * has a typeable `DD/MM/YYYY - DD/MM/YYYY` trigger (own file, own tests), so the AC-29 fix
 * is what actually made it usable, not the two-field detour. Chip reads `Delivery:
 * 1 Nov 26 to 30 Nov 26`.
 */

/** Cell tone as a CLASS, not a component (plan 3.4): three lines, no new file. */
const TONE_CLASS: Record<StockDebtTone, string> = {
  red: 'bg-destructive/10 text-destructive',
  amber: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
  green: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
};

/**
 * TBA and No date carry no tone: they draw no supply at all (R14), so a colour that
 * elsewhere means "can this still be bought in time" would be answering a question
 * nobody asked of them. Informational, per the plan's tone card.
 */
const NEUTRAL_CLASS = 'bg-muted text-foreground';

/** The Total column carries no tone at all (AC-21): it is a sum, not a reading. */
const TOTAL_CLASS = 'font-semibold';

/** `2026-08` -> `Aug 26`. Narrow on purpose: fifteen of these share one width. */
function monthLabel(key: string): string {
  const [year, month] = key.split('-');
  const index = Number(month) - 1;
  const names = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return `${names[index] ?? month} ${year?.slice(2) ?? ''}`;
}

const SHOW_AUX_KEY = 'stock-debt.show-aux';

/** The four read-only columns between Product and the months. */
const AUX_COLUMNS = [
  { key: 'pool_on_hand', header: 'BRW On Hand Qty' },
  { key: 'pool_po_qty', header: 'BRW PO Qty' },
  { key: 'pool_incoming_qty', header: 'BRW Incoming Qty' },
  { key: 'dealer_outstanding', header: 'Dealer O/standing Qty' },
] as const;

/** `-16` reads as debt, `+84` as surplus; a bare `84` reads as neither. */
function signed(value: number): string {
  return value > 0 ? `+${value.toLocaleString()}` : value.toLocaleString();
}

/** Aux quantities print plain: no leading `+`, and never `-0`. */
function plainQty(value: number): string {
  return (value || 0).toLocaleString();
}

/** Which cell is open. `month` is a `YYYY-MM` key, or `tba` / `undated`. */
interface OpenCell {
  productId: string;
  productCode: string;
  productName: string | null;
  month: string;
  label: string;
  balance: number;
}

/** `2026-11-30` -> `30 Nov 26` (chip), the shared `DateRangePicker`'s own ISO parser
 *  reused rather than a second one (R14c). */
function formatDateChip(value: string): string {
  const date = parseIsoDate(value);
  if (!date) return value;
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' });
}

/** Copy without `navigator.clipboard` (R19/AC-30b): the owner reaches the stack over http
 *  on a LAN hostname, a non-secure context where the Clipboard API does not exist at all.
 *  A hidden, off-screen textarea is the standard fallback - select it, ask the browser to
 *  copy the current selection, then remove it. */
function copyViaExecCommand(text: string): boolean {
  if (typeof document.execCommand !== 'function') return false;
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.top = '-1000px';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(textarea);
  return ok;
}

const BOOK_LABEL: Record<StockDebtBook, string> = {
  all: 'All',
  project: 'Project',
  retail: 'Retail',
};

export function StockDebtClient() {
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
  } = useDebouncedSearch();
  const [book, setBook] = React.useState<StockDebtBook>('all');
  // R15: a MULTI select - a product matches when its last supplier is ANY of these.
  const [supplierIds, setSupplierIds] = React.useState<string[]>([]);
  // R14: the single Cutoff date became a Due date RANGE. '' is "no bound", matching the
  // `DateRangePicker`'s own `string | null` contract (empty here rather than null, so the
  // wire helpers' `dateFrom || undefined` guard has one falsy shape to check, not two).
  const [dateFrom, setDateFrom] = React.useState('');
  const [dateTo, setDateTo] = React.useState('');
  // Default ON (AC-S2-10): the whole catalogue is ~4,000 products and the answer the
  // planner came for is the short list that owes something.
  const [onlyDebt, setOnlyDebt] = React.useState(true);
  // Display preference, not a filter: remembered per browser, default ON. Read after mount
  // so the server render and first client render agree.
  const [showAux, setShowAux] = React.useState(true);
  React.useEffect(() => {
    try {
      if (window.localStorage.getItem(SHOW_AUX_KEY) === 'false') setShowAux(false);
    } catch {
      // Storage denied: keep the default.
    }
  }, []);
  const changeShowAux = (next: boolean) => {
    setShowAux(next);
    try {
      window.localStorage.setItem(SHOW_AUX_KEY, String(next));
    } catch {
      // Storage denied: the choice lasts for this visit only.
    }
  };
  const [openCell, setOpenCell] = React.useState<OpenCell | null>(null);
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });

  // Narrowing changes which rows exist, so page 3 of the old set is a page of nothing
  // (AC-24).
  React.useEffect(() => {
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
  }, [debounced, onlyDebt, book, supplierIds, dateFrom, dateTo]);

  const list = useStockDebtQuery({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    query: debounced,
    onlyDebt,
    book,
    supplierIds,
    dateFrom,
    dateTo,
  });

  const rows = React.useMemo(() => list.data?.data ?? [], [list.data]);
  const total = list.data?.pagination.total ?? 0;
  const months = React.useMemo(() => list.data?.months ?? [], [list.data]);
  const tbaMonth = list.data?.tba_month ?? null;
  const suppliers = React.useMemo(() => list.data?.suppliers ?? [], [list.data]);
  const totals = list.data?.totals;

  // R15: two or more picked suppliers render as ONE chip "Suppliers: N" (AC-19c); one
  // picked supplier prints its own name, resolved from the envelope's `suppliers` facet -
  // never the raw id (cursor rule: no UUIDs in the UI).
  const supplierChipLabel = React.useMemo(() => {
    if (supplierIds.length === 0) return null;
    if (supplierIds.length > 1) return `Suppliers: ${supplierIds.length}`;
    const [id] = supplierIds;
    if (id === 'none') return 'Supplier: No supplier';
    const name = suppliers.find((entry) => entry.id === id)?.name;
    return `Supplier: ${name ?? 'Selected supplier'}`;
  }, [supplierIds, suppliers]);

  // ── Excel-style cell selection (R7, AC-25 to AC-32) ──────────────────────────────────
  // R17: "No date" and "No location" are gone from the screen - neither is a selectable
  // column any more (the row still carries `undated`/`unlocated` on the wire, unchanged).
  const valueColumnKeys = React.useMemo(
    () => [...months.map((key) => `m:${key}`), 'tba', 'total'],
    [months],
  );
  const rowIds = React.useMemo(() => rows.map((row) => row.product_id), [rows]);
  const rowsById = React.useMemo(() => {
    const map = new Map<string, StockDebtRow>();
    rows.forEach((row) => map.set(row.product_id, row));
    return map;
  }, [rows]);
  const getCellValue = React.useCallback(
    (rowId: string, columnKey: string): number | null => {
      const row = rowsById.get(rowId);
      if (!row) return null;
      if (columnKey === 'tba') return row.tba;
      if (columnKey === 'total') return row.total;
      const monthKey = columnKey.startsWith('m:') ? columnKey.slice(2) : null;
      if (!monthKey) return null;
      return row.months.find((month) => month.key === monthKey)?.balance ?? null;
    },
    [rowsById],
  );
  const selection = useCellSelection({
    rowIds,
    columnKeys: valueColumnKeys,
    getValue: getCellValue,
  });
  // Read through a REF inside `columns` below, reassigned every render (diagnosed
  // flicker fix): `columns` used to list `selection` itself as a dependency, and every
  // drag step changes `selection.selected` - so `columns` rebuilt, TanStack's
  // `flexRender` treated each fresh inline `cell` closure as a NEW component type, and
  // ~400 cells remounted per pointer move (a button re-queried mid-drag was a different
  // DOM node). `columns` now depends on the envelope alone; the cell/header renderers
  // dereference `selectionRef.current` at RENDER time, which is always this render's
  // latest selection since the assignment below runs before the JSX that reads it.
  const selectionRef = React.useRef(selection);
  selectionRef.current = selection;
  const cellRefs = React.useRef(new Map<string, HTMLButtonElement>());
  const registerCellRef = (rowId: string, columnKey: string) => (node: HTMLButtonElement | null) => {
    const key = `${rowId}::${columnKey}`;
    if (node) cellRefs.current.set(key, node);
    else cellRefs.current.delete(key);
  };
  const tableContainerRef = React.useRef<HTMLDivElement>(null);

  // AC-29: a click OUTSIDE the table clears the selection (Escape is handled inside the
  // hook itself, since it has nothing to do with where the pointer is). Attached once
  // (empty deps) and read through the ref for the same reason as `columns` above - a
  // fresh `selection` every drag step is not a reason to re-subscribe the listener.
  //
  // Owner hand-test round diagnosis: a pointerdown anywhere in the Filters panel's OWN
  // popovers (Due date, Supplier, ...) - or the Export popover - lands outside
  // `tableContainerRef` (Radix portals them to `document.body`) and used to count as
  // "outside the table" too, clearing a selection the reader was never touching. Beyond
  // being wrong on its own terms, that `clear()` call - even a no-op one before the fix
  // in `useCellSelection` - re-rendered this component BETWEEN a real click's own
  // mousedown and mouseup, which is what silently swallowed clicks on the Due date
  // calendar's month arrows. Excluded here by the one marker every Radix popper content
  // wrapper carries, so tweaking a filter never touches a selection the reader is mid-way
  // through building elsewhere on the same screen.
  React.useEffect(() => {
    function onPointerDown(e: PointerEvent) {
      if (!tableContainerRef.current) return;
      if (!(e.target instanceof Node)) return;
      if (tableContainerRef.current.contains(e.target)) return;
      if (
        e.target instanceof Element &&
        e.target.closest('[data-radix-popper-content-wrapper]')
      ) {
        return;
      }
      selectionRef.current.clear();
    }
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, []);

  // R19/AC-30b: `navigator.clipboard` does not exist at all off a secure context (the
  // owner reaches the stack over http on a LAN hostname) - fall back to a hidden
  // textarea + `document.execCommand('copy')`, and only toast an error when NEITHER
  // exists.
  const handleCopy = async () => {
    const text = selection.copyText();
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        toast.success('Copied');
        return;
      } catch {
        // Fall through to the execCommand fallback below.
      }
    }
    if (copyViaExecCommand(text)) {
      toast.success('Copied');
    } else {
      toast.error('Could not copy to the clipboard');
    }
  };

  const columns = React.useMemo<ColumnDef<StockDebtRow>[]>(() => {
    const openFor = (
      row: StockDebtRow,
      month: string,
      label: string,
      balance: number,
    ) =>
      setOpenCell({
        productId: row.product_id,
        productCode: row.product_code,
        productName: row.product_name,
        month,
        label,
        balance,
      });

    /**
     * Every value cell: a press that opens the drill (R28) UNLESS the press was really a
     * drag or a modifier-click building a selection (AC-25/AC-26), in which case
     * `selection.onCellClick` says so by returning `false`. `openDrill=false` for the
     * Total column - it sums three buckets that carry no drill of their own.
     */
    const cell = (
      row: StockDebtRow,
      columnKey: string,
      month: string,
      label: string,
      balance: number,
      toneClass: string,
      openDrill: boolean,
    ) => {
      const rowId = row.product_id;
      const selected = selectionRef.current.isSelected(rowId, columnKey);
      return (
        <button
          type="button"
          ref={registerCellRef(rowId, columnKey)}
          onPointerDown={(e) => selectionRef.current.onCellPointerDown(rowId, columnKey, e)}
          onPointerEnter={(e) => selectionRef.current.onCellPointerEnter(rowId, columnKey, e)}
          onClick={(e) => {
            const plain = selectionRef.current.onCellClick(rowId, columnKey, e);
            if (plain && openDrill) openFor(row, month, label, balance);
          }}
          onKeyDown={(e) => {
            const next = selectionRef.current.onCellKeyDown(rowId, columnKey, e);
            if (next) cellRefs.current.get(`${next.rowId}::${next.columnKey}`)?.focus();
          }}
          title={`${row.product_code} - ${label}: ${signed(balance)}`}
          aria-label={`${row.product_code}, ${label}, balance ${signed(balance)}`}
          className={cn(
            'block w-full rounded px-2 py-1 text-end text-sm tabular-nums transition-colors hover:brightness-95 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
            toneClass,
            selected && 'ring-1 ring-primary',
          )}
        >
          {signed(balance)}
        </button>
      );
    };

    const columnHeader = (columnKey: string, label: React.ReactNode, titleText: string) => (
      <button
        type="button"
        onClick={() => selectionRef.current.onColumnHeaderClick(columnKey)}
        title={`Select the whole ${titleText} column`}
        className="block w-full truncate text-end hover:underline"
      >
        {label}
      </button>
    );

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
          const label = row.original.product_name
            ? `${row.original.product_code} - ${row.original.product_name}`
            : row.original.product_code;
          return (
            <div className="min-w-0" title={label}>
              <div className="truncate text-sm font-medium">
                {row.original.product_code}
              </div>
              {row.original.product_name && (
                <div className="truncate text-xs text-muted-foreground">
                  {row.original.product_name}
                </div>
              )}
            </div>
          );
        },
      },
      ...(showAux
        ? AUX_COLUMNS.map<ColumnDef<StockDebtRow>>(({ key, header }) => ({
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
      ...months.map<ColumnDef<StockDebtRow>>((key) => ({
        id: `m:${key}`,
        header: () => columnHeader(`m:${key}`, monthLabel(key), monthLabel(key)),
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
          return cell(
            row.original,
            `m:${key}`,
            key,
            monthLabel(key),
            month.balance,
            TONE_CLASS[month.tone],
            true,
          );
        },
      })),
      {
        id: 'tba',
        // R18: the HEADER reads "TBA" literally always - the policy's own TBA month is
        // display-only, in the title tooltip. The CELL's own label (aria-label, dialog
        // title) still names the actual month, exactly as the drill it opens does.
        header: () =>
          columnHeader('tba', 'TBA', tbaMonth ? `TBA (${monthLabel(tbaMonth)})` : 'TBA'),
        meta: {
          headerTitle: tbaMonth ? `TBA (${monthLabel(tbaMonth)})` : 'TBA',
          headerClassName: 'text-end',
          skeleton: <Skeleton className="h-4 w-full" />,
        },
        size: 104,
        footer: () => (totals ? signed(totals.tba) : '-'),
        cell: ({ row }) =>
          cell(row.original, 'tba', 'tba', tbaMonth ?? 'TBA', row.original.tba, NEUTRAL_CLASS, true),
      },
      {
        id: 'total',
        // AC-21: the row's own total, right-aligned, signed, no tone, last column. AC-22:
        // its footer is the WHOLE filtered set's total, not the page's.
        header: () => columnHeader('total', 'Total', 'Total'),
        meta: {
          headerTitle: 'Total',
          headerClassName: 'text-end',
          skeleton: <Skeleton className="h-4 w-full" />,
        },
        size: 104,
        footer: () => (totals ? signed(totals.total) : '-'),
        cell: ({ row }) =>
          cell(row.original, 'total', 'total', 'Total', row.original.total, TOTAL_CLASS, false),
      },
    ];
    // `selection` deliberately NOT a dependency (diagnosed flicker fix, see the
    // `selectionRef` note above): the renderers close over `selectionRef.current`
    // instead, so `columns` - and the per-cell component identity TanStack's
    // `flexRender` sees - stays stable across every drag step and click.
  }, [months, tbaMonth, totals, showAux]);

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.product_id,
    // The product column is pinned left for the life of the screen; nothing on the toolbar
    // can unpin it, so it is initial state rather than controlled state with no setter.
    initialState: { columnPinning: { left: ['product'] } },
    state: { pagination },
    onPaginationChange: setPagination,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    manualPagination: true,
    enableSorting: false,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  const filtered = Boolean(
    debounced || book !== 'all' || supplierIds.length > 0 || dateFrom || dateTo,
  );

  // AC-19b: what the Filters button's badge counts. "Only in debt" is the SCREEN's
  // default, so it counts only when the reader has turned it OFF (AC-19c). R16: no more
  // Ownership group to count.
  const activeFilterCount =
    (book !== 'all' ? 1 : 0) +
    (supplierIds.length > 0 ? 1 : 0) +
    (dateFrom || dateTo ? 1 : 0) +
    (onlyDebt ? 0 : 1);

  // R14c: both ends reads "Delivery: 1 Nov 26 to 30 Nov 26"; either end alone reads
  // "from" or "to" rather than padding the other side with a placeholder.
  const deliveryChipLabel =
    dateFrom && dateTo
      ? `Delivery: ${formatDateChip(dateFrom)} to ${formatDateChip(dateTo)}`
      : dateFrom
        ? `Delivery: from ${formatDateChip(dateFrom)}`
        : dateTo
          ? `Delivery: to ${formatDateChip(dateTo)}`
          : null;

  const activeChips = [
    book !== 'all'
      ? { label: `Book: ${BOOK_LABEL[book]}`, onClear: () => setBook('all') }
      : null,
    supplierChipLabel
      ? { label: supplierChipLabel, onClear: () => setSupplierIds([]) }
      : null,
    deliveryChipLabel
      ? {
          label: deliveryChipLabel,
          onClear: () => {
            setDateFrom('');
            setDateTo('');
          },
        }
      : null,
    !onlyDebt ? { label: 'Including covered products', onClear: () => setOnlyDebt(true) } : null,
  ].filter((chip): chip is { label: string; onClear: () => void } => chip !== null);

  return (
    // `min-w-0` so the grid's own horizontal scroll stays INSIDE the card: without it
    // the wide table pushes the page body sideways and the sidebar goes with it (AC-S2-12).
    <div className="min-w-0 space-y-4">
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={list.isLoading}
        isPlaceholderData={list.isPlaceholderData}
        listingKey={null}
        // `listingKey={null}`: the columns ARE the months, so a stored order or visibility
        // would pin an axis that moves on the first of every month. Nothing to persist -
        // and it has to be said out loud, because omitting the prop makes the grid persist
        // under the PATHNAME instead. A row saved that way was re-applied against the
        // columns that existed the moment it arrived, which walked the three no-supply
        // columns up next to Product and left TanStack warning about months not yet built.
        // `columnsDraggable: false` is load-bearing, not tidiness. The DataGrid defaults
        // it to TRUE, and in that mode every cell gets `position: relative` as an INLINE
        // style from dnd-kit and the table drops `border-separate border-spacing-0` - so a
        // pinned column cannot stick however it is spelled. Reordering is meaningless here
        // anyway: the columns are the calendar (which is also why there is no `listingKey`).
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          columnsPinnable: true,
          columnsDraggable: false,
        }}
        emptyMessage={
          <div className="px-6 py-10 text-center">
            <p className="text-sm font-semibold">
              {filtered ? 'No product matches' : 'No product is in debt'}
            </p>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
              {filtered
                ? 'Clear the search and the filters to see the whole book.'
                : 'Every product covers its orders from stock already held or already on the way.'}
            </p>
            {onlyDebt && (
              <Button
                variant="outline"
                className="mt-4"
                onClick={() => setOnlyDebt(false)}
              >
                Show every product
              </Button>
            )}
          </div>
        }
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              // Nothing to personalise while the axis is the calendar (see above), and
              // no selection to export: this table is a reading, not a worklist.
              showColumns={false}
              exportConfig={false}
              searchSlot={
                <ListSearchInput
                  value={search}
                  onChange={setSearch}
                  isSettling={isSearchInFlight(debouncedSettling, list.isFetching, debounced)}
                  placeholder="Search product code or name…"
                  aria-label="Search products"
                  className="w-full max-w-xs"
                />
              }
              filters={{
                kind: 'custom',
                active: activeFilterCount > 0,
                activeCount: activeFilterCount,
                modal: false,
                activeSummary: activeChips,
                content: (
                  <div className="space-y-4">
                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">Book</Label>
                      <RadioGroup
                        className="flex flex-wrap gap-3"
                        value={book}
                        onValueChange={(value) => setBook(value as StockDebtBook)}
                      >
                        {(['all', 'project', 'retail'] as StockDebtBook[]).map((value) => (
                          <label key={value} className="flex items-center gap-1.5 text-sm">
                            <RadioGroupItem value={value} id={`book-${value}`} />
                            {BOOK_LABEL[value]}
                          </label>
                        ))}
                      </RadioGroup>
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">Supplier</Label>
                      <SearchableMultiSelect
                        value={supplierIds}
                        onChange={setSupplierIds}
                        options={[
                          { value: 'none', label: 'No supplier' },
                          ...suppliers.map((entry) => ({ value: entry.id, label: entry.name })),
                        ]}
                        placeholder="Every supplier"
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-xs text-muted-foreground">
                        Sales order delivery date
                      </Label>
                      {/* R14c: back to ONE shared `DateRangePicker` - it now has a
                          typeable `DD/MM/YYYY - DD/MM/YYYY` trigger of its own (own file,
                          own tests), which is what R14b's two-field detour was standing
                          in for. */}
                      <DateRangePicker
                        from={dateFrom || null}
                        to={dateTo || null}
                        onChange={(next) => {
                          setDateFrom(next.from ?? '');
                          setDateTo(next.to ?? '');
                        }}
                        aria-label="Sales order delivery date"
                      />
                    </div>

                    <div className="flex items-center gap-2 border-t pt-3">
                      <Switch
                        id="only-debt"
                        checked={onlyDebt}
                        onCheckedChange={setOnlyDebt}
                      />
                      <Label htmlFor="only-debt" className="text-sm whitespace-nowrap">
                        Only products in debt
                      </Label>
                    </div>

                    <div className="flex items-center gap-2">
                      <Switch
                        id="show-aux"
                        checked={showAux}
                        onCheckedChange={changeShowAux}
                      />
                      <Label htmlFor="show-aux" className="text-sm whitespace-nowrap">
                        Show stock and PO columns
                      </Label>
                    </div>
                  </div>
                ),
              }}
              primaryAction={
                <StockDebtExportPopover
                  envelope={list.data}
                  filters={{ query: debounced, onlyDebt, book, supplierIds, dateFrom, dateTo, showAux }}
                />
              }
            />
          </CardHeader>
          <CardTable>
            {list.isError ? (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
                <h2 className="text-sm font-semibold text-destructive">
                  Stock debt could not be loaded
                </h2>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {list.error instanceof Error
                    ? list.error.message
                    : 'Try again shortly.'}
                </p>
                <Button
                  variant="outline"
                  className="mt-4"
                  onClick={() => void list.refetch()}
                >
                  Retry
                </Button>
              </div>
            ) : (
              // A PLAIN overflow container, not the shared `ScrollArea`. Radix puts a
              // `display: table !important` wrapper inside its viewport, and a pinned cell
              // then sticks to a box that is as wide as its own content and never scrolls
              // out - so the column reads as pinned and moves anyway. `overscroll-x-contain`
              // keeps a sideways flick inside the grid, which is what stops the page body
              // scrolling horizontally at 375px (AC-S2-12); the same shape
              // `ContainerRequestScheduleMatrix` already uses for the same reason.
              <div
                ref={tableContainerRef}
                className="relative w-full overflow-x-auto overscroll-x-contain"
              >
                <DataGridTable />
              </div>
            )}
          </CardTable>
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      {/* AC-28: the summary bar - Excel's own four numbers, plus a Copy that reproduces
          the rectangle when pasted into a spreadsheet (AC-30). */}
      {selection.summary && (
        <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border bg-background/95 px-4 py-2.5 shadow-lg backdrop-blur">
          <span className="text-sm font-medium">{selection.summary.count} cells</span>
          <span className="text-sm tabular-nums">
            Sum <span className="font-semibold">{signed(selection.summary.sum)}</span>
          </span>
          <span className="text-sm tabular-nums">
            Avg{' '}
            <span className="font-semibold">
              {signed(Math.round(selection.summary.avg * 10) / 10)}
            </span>
          </span>
          <span className="text-sm tabular-nums">
            Min <span className="font-semibold">{signed(selection.summary.min)}</span>
          </span>
          <span className="text-sm tabular-nums">
            Max <span className="font-semibold">{signed(selection.summary.max)}</span>
          </span>
          <Button variant="outline" size="sm" className="ms-auto gap-1.5" onClick={handleCopy}>
            <Copy className="size-3.5" />
            Copy
          </Button>
        </div>
      )}

      {openCell && (
        <StockDebtCellDialog
          productId={openCell.productId}
          productCode={openCell.productCode}
          productName={openCell.productName}
          month={openCell.month}
          monthLabel={openCell.label}
          balance={openCell.balance}
          // AC-11b: the board's own due date range and book travel straight through to
          // the drill - R16 already dropped the Ownership group this dialog used to take
          // instead, so there is nothing else to echo. `dateFrom` passes RAW (its own
          // default is already ''); `dateTo`'s `|| undefined` is a no-op once it is set,
          // it only clears the default.
          dateFrom={dateFrom}
          dateTo={dateTo || undefined}
          book={book}
          onClose={() => setOpenCell(null)}
        />
      )}
    </div>
  );
}
