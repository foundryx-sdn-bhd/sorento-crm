'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  SortingState,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import {
  Columns3,
  FileText,
  ListOrdered,
  LoaderCircleIcon,
  PackageCheck,
  SquarePen,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardFooter,
  CardHeader,
  CardHeading,
  CardTable,
  CardTitle,
  CardToolbar,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridColumnVisibility } from '@/components/ui/data-grid-column-visibility';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import {
  formatMoneyExact,
  multiplyMoney,
  subtractMoney,
  sumMoney,
} from '@/app/(protected)/project-sales/_shared/lib/money';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { usePurchaseOrder, useUpdatePurchaseOrder } from '../../../hooks/usePurchaseOrders';
import { useWarehouseOptions } from '../../../hooks/useScmOptions';
import {
  SELECT_PAGE_SIZE,
  searchProductOptions,
  searchSupplierOptions,
} from '../../../services/scmOptionsService';
import { getSalesOrderUoms } from '../../../services/salesOrderService';
import DetailActions from '@/components/common/DetailActions';
import { purchaseOrdersPagerQuery } from '../../../hooks/usePurchaseOrders';
import { PlanRowDialog } from '../../../components/PlanRowDialog';
import { PlanNumberButton } from '../../../components/PlanNumberButton';
import { BookSoCell, bookSoSortValue } from '../../../components/BookSoCell';
import { PoLinePlacementsBody, placedQtyOf } from './PoLinePlacementsBody';
import { PoPlanCard } from './PoPlanCard';
import { BASE_CURRENCY, fmtDate, fmtInt } from '../../../lib/format';
import {
  isDraftPurchaseOrder,
  purchaseOrderLineStatusPill,
  purchaseOrderStatusPill,
} from '../../../lib/purchaseOrderStatus';
import { cancelSourcesTitle, isPartlyCancelled, PARTLY_CANCELLED } from '@/app/(protected)/scm/lib/autocountCancel';
import type {
  PurchaseOrder,
  PurchaseOrderLine,
  PurchaseOrderLineAllocation,
} from '../../../types/scm.types';
import BackToList from '@/components/common/BackToList';
import { codeWithName, distinctName } from '@/lib/product-display';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { useStableColumns } from '@/hooks/useStableColumns';

/**
 * The purchase-order detail, built section for section as the twin of `SalesOrderDetail`.
 *
 * The captain's instruction was that the two books must look very alike, because one is
 * buying and the other is selling and nothing else about them differs: a planner who has
 * learnt where a figure lives on one has learnt it on the other. So this screen carries the
 * SAME header shape, the SAME three two-column cards, the SAME lines grid with its totals
 * row, search, column picker and pager, and the SAME in-place edit session. Where they
 * differ, they differ because the DOMAIN differs - goods are received rather than delivered,
 * so the delivery panel is a goods-receipt panel and the counterparty is a supplier.
 *
 * WHAT THIS REPLACED. A flat four-across grid of eight fields, no Edit at all, a read-only
 * five-column lines table with no footer, no search and no pager, and two duplicate
 * status-word maps (one here, one on the list) that disagreed with each other.
 *
 * VIEW AND EDIT ARE THE SAME SCREEN. Editing swaps a read-only value for an input IN PLACE -
 * the same fields, in the same order, in the same card, in the same tab. Editable: Order
 * date, Delivery date, Supplier. Lines: Product, Qty ordered, Unit price, Discount, UoM,
 * Location and Delivery date. Everything else (Source, Currency, Supplier code, Qty
 * received, the totals) has no edit counterpart and stays exactly where it was.
 *
 * One Save writes the whole header, plus the lines ONLY when they actually moved - see
 * `lineSignature` - because the PUT upserts the WHOLE `lines` array when the key is sent at
 * all. The service matches each sent line to an existing row by `id` FIRST (this form sends
 * it) or by SKU otherwise, so a matched line keeps its id, its `qty_received` and its
 * `source_system`; only an unmatched existing line is deleted, and the backend refuses that
 * with a 409 when goods have been received against it or a sales order still claims it.
 *
 * MONEY IS A STRING END TO END, and it is in the SUPPLIER's currency. The backend sends
 * `Decimal`, which Pydantic serialises as a string, and every sum here goes through
 * `project-sales/_shared/lib/money` rather than `Number()`. The book is mostly USD, so
 * every figure is labelled with the currency the order was written in - "RM 12.50" against a
 * USD purchase order is a wrong number, not a formatting detail.
 */

/** Where the order came from. `import` is its own answer because "Manual" would claim
 *  somebody keyed a 2020 order by hand. Mirrors the sales-order side's `history`. */
const SOURCE_LABELS: Record<NonNullable<PurchaseOrder['source']>, string> = {
  recommendation: 'Reorder recommendation',
  import: 'Imported history',
  crm: 'Created in CRM',
  manual: 'Manual',
};

/** A line the "Placed" button never renders enabled for (no allocation, figure 0) has no
 *  real row to look up - this is the shape the dialog is defensively given if it is ever
 *  opened on one anyway, so it reads "Nothing is placed on this line" rather than crashing. */
const EMPTY_PLACEMENTS_ALLOCATION: PurchaseOrderLineAllocation = {
  line_id: '',
  sku: '',
  warehouse_code: null,
  outstanding: 0,
  allocated: 0,
  free: 0,
  dedicated_to: [],
  placements: [],
};

/** Does this line answer the product search above the grid? Code and description, because
 *  a buyer looking for one item on a 200-line order knows one or the other, not both. */
function lineMatches(line: PurchaseOrderLine, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    line.sku.toLowerCase().includes(q) || (line.product_name ?? '').toLowerCase().includes(q)
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  /** Set while editing, so the label associates with the input/select it now wraps - the
   *  same label text either way. */
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="text-xs text-muted-foreground">
          {label}
        </label>
      ) : (
        <span className="text-xs text-muted-foreground">{label}</span>
      )}
      <span className="text-sm font-medium">{children}</span>
    </div>
  );
}

type LineDraft = {
  sku: string;
  qty_ordered: string;
  warehouse_code: string;
  expected_date: string;
  uom: string;
  unit_price: string;
  discount: string;
};

/** `sku|qty|warehouse_code|expected_date|uom|unit_price|discount` per line,
 *  order-independent, so a re-save with the same lines in a different order is not read as
 *  a change. `lines` is left off the write entirely when nothing here moved, or a
 *  header-only edit would resend every line for the backend to match-and-upsert for
 *  nothing. */
function lineSignature(ls: LineDraft[]): string {
  return ls
    .map(
      (l) =>
        `${l.sku}|${l.qty_ordered}|${l.warehouse_code}|${l.expected_date}|${l.uom}` +
        `|${l.unit_price}|${l.discount}`,
    )
    .sort()
    .join(',');
}

function seedDraft(row: PurchaseOrderLine): LineDraft {
  return {
    sku: row.sku,
    qty_ordered: String(row.qty_ordered),
    warehouse_code: row.warehouse_code ?? '',
    expected_date: row.expected_date?.slice(0, 10) ?? '',
    uom: row.uom ?? '',
    unit_price: row.unit_price ?? '',
    discount: row.discount ?? '',
  };
}

/** The in-progress draft for a line, or one seeded from the row as loaded when nothing has
 *  touched it yet - so any single field's onChange can spread this and set only the field
 *  it owns without silently dropping the other six. */
function draftOrRow(
  drafts: Record<string, LineDraft>,
  row: PurchaseOrderLine,
): LineDraft {
  return drafts[row.id] ?? seedDraft(row);
}

/**
 * What a line is worth: the total the supplier's document stated, or the arithmetic its
 * parts support. The SAME rule the backend's own `total_amount` follows, so the column, the
 * footer and the header total cannot disagree - and `null` rather than 0 when nobody priced
 * it, because an unpriced line is not a line worth nothing.
 */
/** Qty ordered must be above 0 (the server refuses a zero or blank line). */
const QTY_ORDERED_RULES = { min: 0, minExclusive: true } as const;

/** An edit-session qty that is blank or unusable is pending ("-"), never 0. */
function qtyIsPending(raw: string): boolean {
  return numberFieldError(raw, QTY_ORDERED_RULES) !== undefined;
}

/** A quantity cell: the figure, or "-" while the figure is pending. */
function fmtQtyCell(value: number | null): string {
  return value === null ? '-' : fmtInt(value);
}

function lineAmount(
  qty: string,
  unitPrice: string,
  discount: string,
  lineTotal: string | null | undefined,
): string | null {
  if (lineTotal) return lineTotal;
  if (!unitPrice) return null;
  const gross = multiplyMoney(qty, unitPrice);
  if (gross === null) return null;
  return discount ? subtractMoney(gross, discount) : gross;
}

/**
 * The option the Product select shows for a line whose product is not on the page the
 * server just returned - which is most of them, against a 22,000-row catalogue.
 *
 * Only while the draft still names the line's OWN product: once a different one has been
 * picked, its label comes from the fetched page and this fallback would relabel it.
 */
function productFallback(
  row: PurchaseOrderLine,
  draftSku: string | undefined,
): SearchableSelectOption | undefined {
  const sku = draftSku ?? row.sku;
  if (!row.sku || sku !== row.sku) return undefined;
  return {
    value: row.sku,
    label: codeWithName(row.sku, row.product_name, ' · '),
  };
}

export function PurchaseOrderDetail({ id }: { id: string }) {
  const { data, isLoading, isError } = usePurchaseOrder(id);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // Back returns to the list the user actually had open, filters and page included.

  const updateMut = useUpdatePurchaseOrder();
  const warehouseOptions = useWarehouseOptions();
  // The SAME unit-of-measure list the sales-order detail's own UoM select reads. Served off
  // `/scm/sales-orders/uoms` under this module's `scm.dashboard.view`, which is the
  // permission this screen already holds - a second endpoint returning the identical
  // `units_of_measure` rows would be a copy to keep in step for no gain.
  const uomOptionsQuery = useQuery({
    queryKey: ['scm', 'sales-order-uoms'],
    queryFn: getSalesOrderUoms,
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const uomSelectOptions = useMemo<SearchableSelectOption[]>(
    () => (uomOptionsQuery.data ?? []).map((u) => ({ value: u.uom_code, label: u.uom_name })),
    [uomOptionsQuery.data],
  );
  // The read view's Location cell shows the CODE, and `getWarehouseOptions` labels each
  // option with the NAME - so the same value would read as "BRW" and edit as "Bandar Rawang
  // Warehouse". The code is prefixed here rather than in the shared option list every other
  // SCM picker also uses.
  const warehouseSelectOptions = useMemo(
    () => (warehouseOptions.data ?? []).map((o) => ({ ...o, label: `${o.value} - ${o.label}` })),
    [warehouseOptions.data],
  );

  const [isEditing, setIsEditing] = useState(false);
  const [supplierCode, setSupplierCode] = useState('');
  const [orderDate, setOrderDate] = useState('');
  const [expectedDate, setExpectedDate] = useState('');
  const [lineDrafts, setLineDrafts] = useState<Record<string, LineDraft>>({});
  const [error, setError] = useState<string | null>(null);
  // Set by a refused Save; from then on each quantity box re-checks itself live.
  const [showQtyErrors, setShowQtyErrors] = useState(false);
  // Which LINE's "Placed" figure was pressed (R5, AC-L1) - one dialog for the whole grid,
  // replacing the "Allocated to" card that used to sit under it (AC-L3).
  const [placementsLineId, setPlacementsLineId] = useState<string | null>(null);
  const originalLineSignatureRef = useRef<string | null>(null);

  const beginEdit = (po: PurchaseOrder) => {
    setSupplierCode(po.supplier_code ?? '');
    setOrderDate(po.order_date?.slice(0, 10) ?? '');
    setExpectedDate(po.expected_date?.slice(0, 10) ?? '');
    const drafts: Record<string, LineDraft> = {};
    for (const ln of po.lines) {
      drafts[ln.id] = seedDraft(ln);
    }
    setLineDrafts(drafts);
    originalLineSignatureRef.current = lineSignature(po.lines.map(seedDraft));
    setError(null);
    setShowQtyErrors(false);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setError(null);
    setShowQtyErrors(false);
  };

  // `?edit=1` opens the session on arrival, so a bookmarked link lands in the same place a
  // click on Edit does. Fired once: re-running it after Cancel would put the user straight
  // back into the session they just left.
  const wantsEdit = searchParams.get('edit') === '1';
  const opened = useRef(false);
  useEffect(() => {
    if (!wantsEdit || opened.current || !data) return;
    opened.current = true;
    beginEdit(data);
  }, [wantsEdit, data]);

  const lines = useMemo<PurchaseOrderLine[]>(() => data?.lines ?? [], [data]);
  // Sorted and searched here rather than by the API: the lines come embedded in the order
  // read, so there is no second request to spend and no page boundary to work across.
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: lineSearchInput,
    setValue: setLineSearchInput,
    debouncedValue: lineSearch,
  } = useDebouncedSearch();
  // Kept in the URL, not component state (mirrors `SalesOrderDetail`'s own `tab` param), so a
  // bookmarked or shared link (`?tab=lines`) opens directly on that tab.
  const tab = searchParams.get('tab') || 'general';
  const handleTabChange = useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === 'general') {
        params.delete('tab');
      } else {
        params.set('tab', next);
      }
      const qs = params.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ''}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  /** The currency this order's figures are IN. Per order rather than per line: a purchase
   *  order is written in one currency, and the line column exists only because the import
   *  carries it per row. */
  const currency = data?.currency ?? data?.lines?.[0]?.currency ?? null;

  // While an edit session is open every figure below is read off the DRAFT, so a typed
  // quantity or price moves the row AND the totals row at once. Outside a session they read
  // the stored row, which is the same value.
  const outstandingOf = useCallback(
    (row: PurchaseOrderLine) => {
      // A CLOSED line has nothing still to arrive, whatever its two quantities say. Same
      // rule and the same reason as the sales book's own: a book re-upload closes a line by
      // absence without knowing what arrived, so `qty_received` stays 0 and
      // `ordered - received` would report the whole quantity as still coming.
      if ((row.line_status ?? 'open') !== 'open') return 0;
      // Off the SERVER outside an edit session, so this grid, its footer and the Totals
      // card above all print the one figure the backend computed.
      if (!isEditing && row.outstanding_qty !== undefined && row.outstanding_qty !== null) {
        return row.outstanding_qty;
      }
      const draftQty = draftOrRow(lineDrafts, row).qty_ordered;
      if (isEditing && qtyIsPending(draftQty)) return null;
      const ordered = isEditing ? (parseNumberDraft(draftQty) as number) : Number(row.qty_ordered);
      if (!Number.isFinite(ordered)) return 0;
      // The received figure on screen is net of an AutoCount Cancel PO; the cancelled qty
      // is not coming either, so it is added back before the subtraction (AC-CONSIGN-CRM).
      const settled = Number(row.qty_received) + Number(row.qty_cancelled ?? 0);
      return Math.max(ordered - settled, 0);
    },
    [isEditing, lineDrafts],
  );

  const amountOf = useCallback(
    (row: PurchaseOrderLine) => {
      if (!isEditing) {
        return lineAmount(
          String(row.qty_ordered), row.unit_price ?? '', row.discount ?? '', row.line_total,
        );
      }
      const draft = draftOrRow(lineDrafts, row);
      if (qtyIsPending(draft.qty_ordered)) return null;
      // The stated `line_total` is what the supplier's document charged, so it wins - until
      // one of the figures it was charged ON is edited, at which point it no longer
      // describes what is on the screen and the arithmetic does.
      const touched =
        draft.qty_ordered !== String(row.qty_ordered) ||
        draft.unit_price !== (row.unit_price ?? '') ||
        draft.discount !== (row.discount ?? '');
      return lineAmount(
        draft.qty_ordered, draft.unit_price, draft.discount, touched ? null : row.line_total,
      );
    },
    [isEditing, lineDrafts],
  );

  const qtyOrderedTotal = useMemo(
    () =>
      lines.reduce<number | null>((sum, l) => {
        if (sum === null) return null;
        if (isEditing && qtyIsPending(draftOrRow(lineDrafts, l).qty_ordered)) return null;
        const qty = isEditing
          ? Number(draftOrRow(lineDrafts, l).qty_ordered)
          : Number(l.qty_ordered);
        return sum + (Number.isFinite(qty) ? qty : 0);
      }, 0),
    [lines, isEditing, lineDrafts],
  );
  const qtyReceivedTotal = useMemo(
    () => lines.reduce((sum, l) => sum + Number(l.qty_received), 0),
    [lines],
  );
  const outstandingTotal = useMemo(
    () =>
      lines.reduce<number | null>((sum, l) => {
        const out = outstandingOf(l);
        return sum === null || out === null ? null : sum + out;
      }, 0),
    [lines, outstandingOf],
  );

  // R5: one allocation per LINE, keyed for the Placed column and its dialog (AC-L1). Off
  // `data` rather than `po` - the latter is not defined until after the loading/error
  // returns below, and this map is built before them.
  const allocationByLineId = useMemo(() => {
    const map = new Map<string, PurchaseOrderLineAllocation>();
    for (const a of data?.allocations ?? []) map.set(a.line_id, a);
    return map;
  }, [data]);
  const placedTotal = useMemo(
    () =>
      lines.reduce((sum, l) => {
        const allocation = allocationByLineId.get(l.id);
        return sum + (allocation ? placedQtyOf(allocation) : 0);
      }, 0),
    [lines, allocationByLineId],
  );

  const amountTotal = useMemo(() => {
    // A blank or unusable qty on any line makes the total pending, not a sum that skips it.
    if (isEditing && lines.some((l) => qtyIsPending(draftOrRow(lineDrafts, l).qty_ordered))) return null;
    const amounts = lines.map(amountOf).filter((a): a is string => a !== null);
    return amounts.length ? sumMoney(amounts) : null;
  }, [lines, amountOf, isEditing, lineDrafts]);

  /** The money cell's text, in the order's own currency: the figure, or a plain "-" for a
   *  line nobody priced. */
  const fmtMoneyCell = useCallback(
    (value: string | null | undefined) => (value ? formatMoneyExact(value, currency) : '-'),
    [currency],
  );

  const liveColumns = useMemo<ColumnDef<PurchaseOrderLine>[]>(
    () => [
      {
        accessorKey: 'sku',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = lineDrafts[row.original.id];
            const selectId = `po-edit-line-${row.original.id}-product`;
            return (
              <>
                <label className="sr-only" htmlFor={selectId}>
                  Product on {row.original.sku}
                </label>
                {/* SERVER-SEARCHED, never a static list. `products/select` caps at 100 rows
                    against ~22,000 active products, so a static option list holds 0.5% of
                    the catalogue and answers "no products match" for the rest.
                    `selectedOption` is what keeps the line's OWN product readable when it
                    is not on the page that came back. */}
                <SearchableSelect
                  id={selectId}
                  value={draft?.sku ?? row.original.sku}
                  onChange={(v) =>
                    setLineDrafts((prev) => ({
                      ...prev,
                      [row.original.id]: { ...draftOrRow(prev, row.original), sku: v },
                    }))
                  }
                  paginated
                  pageSize={SELECT_PAGE_SIZE}
                  fetchOptions={searchProductOptions}
                  selectedOption={productFallback(row.original, draft?.sku)}
                  placeholder="Select product"
                  emptyMessage="No product found."
                  size="sm"
                />
              </>
            );
          }
          const showProductName =
            distinctName(row.original.sku, row.original.product_name) !== null;
          return (
            <div className="flex min-w-0 flex-col">
              <span className="font-medium">{row.original.sku}</span>
              {showProductName && (
                <span
                  className="truncate text-xs text-muted-foreground"
                  title={row.original.product_name}
                >
                  {row.original.product_name}
                </span>
              )}
            </div>
          );
        },
        size: 260,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'qty_ordered',
        // The sort value is the NUMBER, never the printed string: a quantity read off the
        // API as "1200.0000" would otherwise order before "45" the way a word does.
        accessorFn: (line) => Number(line.qty_ordered),
        header: ({ column }) => <DataGridColumnHeader title="Qty ordered" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = lineDrafts[row.original.id];
            return (
              <NumberInput
                min={0}
                aria-label={`Qty ordered on ${row.original.sku}`}
                value={draft?.qty_ordered ?? String(row.original.qty_ordered)}
                error={
                  showQtyErrors
                    ? numberFieldError(draft?.qty_ordered ?? String(row.original.qty_ordered), QTY_ORDERED_RULES)
                    : undefined
                }
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [row.original.id]: {
                      ...draftOrRow(prev, row.original),
                      qty_ordered: e.target.value,
                    },
                  }))
                }
                className="h-8 text-right tabular-nums"
              />
            );
          }
          return fmtInt(row.original.qty_ordered);
        },
        size: 130,
        // One `td` per column, so the sum sits UNDER the column it sums and needs no label.
        footer: () => fmtQtyCell(qtyOrderedTotal),
        meta: {
          headerTitle: 'Qty ordered',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'qty_received',
        accessorFn: (line) => Number(line.qty_received),
        header: ({ column }) => <DataGridColumnHeader title="Qty received" column={column} />,
        cell: ({ row }) => fmtInt(row.original.qty_received),
        size: 130,
        footer: () => fmtInt(qtyReceivedTotal),
        meta: {
          headerTitle: 'Qty received',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        // AC-CANCEL-CRM (owner hand test, 13 Oct): AutoCount Cancel PO is part of
        // TransferedQty; this states it, and the tooltip names each cancel document and how
        // it was matched to this line.
        id: 'qty_cancelled',
        accessorFn: (line) => Number(line.qty_cancelled ?? 0),
        header: ({ column }) => <DataGridColumnHeader title="Qty cancelled" column={column} />,
        cell: ({ row }) => {
          const qty = Number(row.original.qty_cancelled ?? 0);
          return (
            <span
              data-testid={`qty-cancelled-${row.original.id}`}
              title={cancelSourcesTitle(row.original.cancels)}
              className="truncate"
            >
              {fmtInt(qty)}
            </span>
          );
        },
        size: 130,
        meta: {
          headerTitle: 'Qty cancelled',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'outstanding',
        // Computed here rather than sent, so it cannot disagree with the two columns beside
        // it. An over-receipt reads as 0 rather than as a negative commitment. In an EDIT
        // session it recomputes from the draft quantity, live: typing 400 over 320 on a
        // line with 100 received has to show 300 still coming straight away, or the row
        // states two figures that contradict each other until the page is reloaded.
        accessorFn: (line) =>
          Math.max(
            Number(line.qty_ordered) - Number(line.qty_received) - Number(line.qty_cancelled ?? 0),
            0,
          ),
        header: ({ column }) => <DataGridColumnHeader title="Outstanding qty" column={column} />,
        cell: ({ row }) => fmtQtyCell(outstandingOf(row.original)),
        size: 140,
        footer: () => fmtQtyCell(outstandingTotal),
        meta: {
          headerTitle: 'Outstanding qty',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        // R5: the sum of every SPO pull, order-inquiry link and dedication on the line -
        // opens the lightbox that replaces the "Allocated to" card (AC-L1, AC-L3).
        id: 'placed',
        accessorFn: (line) => {
          const allocation = allocationByLineId.get(line.id);
          return allocation ? placedQtyOf(allocation) : 0;
        },
        header: ({ column }) => <DataGridColumnHeader title="Placed" column={column} />,
        cell: ({ row }) => {
          const allocation = allocationByLineId.get(row.original.id);
          const figure = allocation ? placedQtyOf(allocation) : 0;
          const label = `Placed on ${row.original.sku}`;
          if (!allocation || figure <= 0) {
            return <PlanNumberButton value={fmtInt(figure)} label={label} onClick={() => {}} disabled />;
          }
          return (
            <PlanNumberButton
              value={fmtInt(figure)}
              label={label}
              onClick={() => setPlacementsLineId(row.original.id)}
            />
          );
        },
        size: 120,
        footer: () => fmtInt(placedTotal),
        meta: {
          headerTitle: 'Placed',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'unit_price',
        accessorFn: (line) => Number(line.unit_price ?? 0),
        header: ({ column }) => <DataGridColumnHeader title="Unit price" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = draftOrRow(lineDrafts, row.original);
            return (
              <NumberInput
                min={0}
                step="0.01"
                aria-label={`Unit price on ${row.original.sku}`}
                value={draft.unit_price}
                error={numberFieldError(draft.unit_price, { min: 0, optional: true })}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [row.original.id]: {
                      ...draftOrRow(prev, row.original),
                      unit_price: e.target.value,
                    },
                  }))
                }
                className="h-8 text-right tabular-nums"
              />
            );
          }
          return fmtMoneyCell(row.original.unit_price);
        },
        size: 140,
        meta: {
          headerTitle: 'Unit price',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'discount',
        accessorFn: (line) => Number(line.discount ?? 0),
        header: ({ column }) => <DataGridColumnHeader title="Discount" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = draftOrRow(lineDrafts, row.original);
            return (
              <NumberInput
                min={0}
                step="0.01"
                aria-label={`Discount on ${row.original.sku}`}
                value={draft.discount}
                error={numberFieldError(draft.discount, { min: 0, optional: true })}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [row.original.id]: {
                      ...draftOrRow(prev, row.original),
                      discount: e.target.value,
                    },
                  }))
                }
                className="h-8 text-right tabular-nums"
              />
            );
          }
          return fmtMoneyCell(row.original.discount);
        },
        size: 130,
        meta: {
          headerTitle: 'Discount',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'line_total',
        // The figure the CELL prints, not the stored column: a line with a price and no
        // stated total shows its arithmetic and would otherwise sort as 0, the same trap
        // the Outstanding qty column above avoids.
        accessorFn: (line) => Number(amountOf(line) ?? 0),
        header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
        cell: ({ row }) => fmtMoneyCell(amountOf(row.original)),
        size: 150,
        footer: () => fmtMoneyCell(amountTotal),
        meta: {
          headerTitle: 'Total',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        accessorKey: 'uom',
        header: ({ column }) => <DataGridColumnHeader title="UoM" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = draftOrRow(lineDrafts, row.original);
            const selectId = `po-edit-line-${row.original.id}-uom`;
            return (
              <>
                {/* SearchableSelect forwards `id`, not arbitrary aria props - the accessible
                    name needs a real (visually-hidden) label. */}
                <label className="sr-only" htmlFor={selectId}>
                  UoM on {row.original.sku}
                </label>
                <SearchableSelect
                  id={selectId}
                  value={draft.uom}
                  onChange={(v) =>
                    setLineDrafts((prev) => ({
                      ...prev,
                      [row.original.id]: { ...draftOrRow(prev, row.original), uom: v },
                    }))
                  }
                  options={uomSelectOptions}
                  // Clearing sends `''`, which the backend reads as an explicit `uom: null`
                  // - "use the product's own default" - not "leave it alone".
                  placeholder="Product default"
                  clearable
                  size="sm"
                />
              </>
            );
          }
          return row.original.uom || '-';
        },
        size: 140,
        meta: { headerTitle: 'UoM' },
      },
      {
        accessorKey: 'warehouse_code',
        // Location is a LINE fact (captain, 20 Aug) - the header field is gone, so the line
        // states its own destination.
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = draftOrRow(lineDrafts, row.original);
            const selectId = `po-edit-line-${row.original.id}-warehouse`;
            return (
              <>
                <label className="sr-only" htmlFor={selectId}>
                  Location on {row.original.sku}
                </label>
                <SearchableSelect
                  id={selectId}
                  value={draft.warehouse_code}
                  onChange={(v) =>
                    setLineDrafts((prev) => ({
                      ...prev,
                      [row.original.id]: { ...draftOrRow(prev, row.original), warehouse_code: v },
                    }))
                  }
                  options={warehouseSelectOptions}
                  placeholder="No location"
                  clearable
                  size="sm"
                />
              </>
            );
          }
          return row.original.warehouse_code || '-';
        },
        size: 140,
        meta: { headerTitle: 'Location' },
      },
      {
        id: 'book_so',
        // The AutoCount book's own linkage, read off the line's own `from_so_line_ref` -
        // not the allocations panel below (that answers who RESERVED the line through our
        // own order-inquiry flow; this answers what the book records the line as raised
        // FOR). ONE sales order, because the column holds one value. Rendered by the same
        // component the order-inquiry PO lightbox uses, so one fact reads one way on both
        // screens.
        accessorFn: bookSoSortValue,
        header: ({ column }) => <DataGridColumnHeader title="S/O" column={column} />,
        cell: ({ row }) => <BookSoCell line={row.original} />,
        size: 150,
        meta: { headerTitle: 'S/O' },
      },
      {
        id: 'expected_date',
        // The ISO date, not the printed "04 Jul 2026": the display form sorts by
        // day-of-month and would put September before July. A line with no date sorts last
        // either way.
        accessorFn: (line) => line.expected_date ?? undefined,
        // Worded "Delivery date", the buyer's own word for it. The column, the accessor and
        // the API field stay `expected_date`: this is a label change, not a data one.
        header: ({ column }) => <DataGridColumnHeader title="Delivery date" column={column} />,
        cell: ({ row }) => {
          if (isEditing) {
            const draft = draftOrRow(lineDrafts, row.original);
            return (
              <Input
                type="date"
                aria-label={`Delivery date on ${row.original.sku}`}
                value={draft.expected_date}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [row.original.id]: {
                      ...draftOrRow(prev, row.original),
                      expected_date: e.target.value,
                    },
                  }))
                }
                className="h-8"
              />
            );
          }
          return row.original.expected_date ? fmtDate(row.original.expected_date) : '-';
        },
        size: 150,
        meta: { headerTitle: 'Delivery date' },
      },
      {
        accessorKey: 'line_status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        // The same light chip as every other pill on this page, worded the same two ways the
        // header pill is - the same helper both read.
        cell: ({ row }) => {
          // AC-CANCEL-CRM: part of the line cancelled by AutoCount Cancel PO.
          const pill = isPartlyCancelled(row.original)
            ? PARTLY_CANCELLED
            : purchaseOrderLineStatusPill(row.original.line_status);
          return (
            <Badge variant={pill.variant} appearance="light" size="md">
              {pill.label}
            </Badge>
          );
        },
        size: 130,
        meta: { headerTitle: 'Status' },
      },
    ],
    [
      isEditing,
      lineDrafts,
      showQtyErrors,
      warehouseSelectOptions,
      uomSelectOptions,
      outstandingOf,
      amountOf,
      fmtMoneyCell,
      qtyOrderedTotal,
      qtyReceivedTotal,
      outstandingTotal,
      allocationByLineId,
      placedTotal,
      amountTotal,
    ],
  );

  const columns = useStableColumns(liveColumns);
  const table = useReactTable({
    columns,
    data: lines,
    getRowId: (row) => row.id,
    state: { sorting, globalFilter: lineSearch },
    onSortingChange: setSorting,
    // Nothing calls table.setGlobalFilter directly - the box drives lineSearch
    // (debounced) instead - but a controlled globalFilter still requires this.
    onGlobalFilterChange: () => {},
    // The whole ROW answers the search, so every column is allowed to carry it and the
    // matcher below decides. Left to the default, only string columns qualify, which made
    // the filter depend on which columns happen to hold strings.
    getColumnCanGlobalFilter: () => true,
    globalFilterFn: (row, _columnId, value) => lineMatches(row.original, String(value ?? '')),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    // Paged like every other listing in the product, so the footer can say "1 - 25 of 213"
    // instead of leaving a 200-line order as one endless scroll with no count on it.
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });
  const visibleLineCount = table.getFilteredRowModel().rows.length;

  // Back and prev/next live on the RIGHT of the record header, next to each other, the way
  // the sales-order and users screens do it.
  // Back carries the list query the row click wrote (S3-01). It lives on the
  // toolbar row now; the empty states below keep one of their own.
  const backLink = (
    <BackToList listPath="/scm/purchase-orders" label="Back to purchase orders" />
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">{backLink}</div>
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">{backLink}</div>
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <div className="text-sm font-semibold">Purchase order not found</div>
          <p className="max-w-md text-sm text-muted-foreground">
            This purchase order doesn&apos;t exist, or it was removed after this link was
            made. Head back to the list to pick another.
          </p>
        </Card>
      </div>
    );
  }

  const po = data;
  const statusPill = purchaseOrderStatusPill(po);
  const lineCount = po.line_count ?? lines.length;
  const placementsLine = placementsLineId
    ? (lines.find((l) => l.id === placementsLineId) ?? null)
    : null;

  const handleSave = async () => {
    setError(null);
    if (!supplierCode) return setError('Select a supplier.');
    // `id` is sent so the backend matches this line by id rather than falling back to SKU.
    // Location / expected date / UoM / price / discount ride the SAME upsert as SKU/qty,
    // each carrying either what the person typed or, for an untouched line, exactly what
    // the order loaded with.
    const cleanedLines = po.lines.map((ln) => {
      const draft = lineDrafts[ln.id];
      return {
        id: ln.id,
        ...seedDraft(ln),
        ...(draft ?? {}),
      };
    });
    if (cleanedLines.some((l) => qtyIsPending(l.qty_ordered))) setShowQtyErrors(true);
    if (cleanedLines.some((l) => !l.sku || qtyIsPending(l.qty_ordered))) {
      return setError('Every line needs a product and a quantity above zero.');
    }
    // The price and discount may stay blank (blank clears the figure), but a typed one is
    // never negative.
    if (
      cleanedLines.some(
        (l) =>
          numberFieldError(l.unit_price, { min: 0, optional: true }) ||
          numberFieldError(l.discount, { min: 0, optional: true }),
      )
    ) {
      return setError('A unit price or discount cannot be negative.');
    }
    const linesUnchanged =
      originalLineSignatureRef.current !== null &&
      lineSignature(cleanedLines) === originalLineSignatureRef.current;
    try {
      await updateMut.mutateAsync({
        id,
        data: {
          supplier_code: supplierCode,
          order_date: orderDate || null,
          expected_date: expectedDate || null,
          ...(linesUnchanged
            ? {}
            : {
                lines: cleanedLines.map((l) => ({
                  id: l.id,
                  sku: l.sku,
                  qty_ordered: Number(l.qty_ordered),
                  uom: l.uom,
                  warehouse_code: l.warehouse_code,
                  expected_date: l.expected_date,
                  // Empty means "clear this figure", which is what the backend reads a
                  // `null` as.
                  unit_price: l.unit_price || null,
                  discount: l.discount || null,
                })),
              }),
        },
      });
      setIsEditing(false);
    } catch {
      // The mutation already toasted the reason; leave the session open so nothing typed
      // is lost.
    }
  };

  return (
    <div className="space-y-4">
      {/* The record header - what the order IS, and what can be done to it. Above the tabs,
          because it belongs to the whole record rather than to any one of its concerns. */}
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <CardTitle className="text-lg">{po.po_number}</CardTitle>
              <Badge variant={statusPill.variant} appearance="light" size="md">
                {statusPill.label}
              </Badge>
            </div>
            {/* In an edit session the header states ONE intent: Save or Cancel. Nav and the
                way out act on the order as it is STORED, and offering them over a screen
                full of unsaved changes is offering to act on a document nobody is reading. */}
            {isEditing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  Nothing is written until you press Save.
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={cancelEdit}
                  disabled={updateMut.isPending}
                >
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSave} disabled={updateMut.isPending}>
                  {updateMut.isPending ? (
                    <LoaderCircleIcon className="me-2 size-4 animate-spin" />
                  ) : null}
                  Save purchase order
                </Button>
              </div>
            ) : (
              <DetailActions
                pager={{
                  ...purchaseOrdersPagerQuery,
                  detailPath: '/scm/purchase-orders',
                  currentId: id,
                  ariaLabel: 'purchase order',
                }}
                gearLabel="Purchase order options"
                primary={
                  <>
                {/* The main action on this page, so it wears the main colour - the same
                    filled primary button an Add is on every list. */}
                <Button
                  variant="primary"
                  size="sm"
                  className="gap-1.5"
                  onClick={() => beginEdit(po)}
                >
                  <SquarePen className="size-4" />
                  Edit
                </Button>
                  </>
                }
              />
            )}
          </div>
          {isEditing && error ? (
            <Alert variant="destructive" className="mt-3">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
        </CardHeader>
      </Card>

      {/* One tab per concern of the order, the same shape as the sales-order screen. The tab
          set is the SAME in view and in edit - editing swaps a value for an input inside the
          tab it already lived in. */}
      <Tabs value={tab} onValueChange={handleTabChange} className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="general">
            <FileText />
            <span>General</span>
          </TabsTrigger>
          <TabsTrigger value="lines">
            <ListOrdered />
            <span>Lines</span>
          </TabsTrigger>
          <TabsTrigger value="goods-receipt">
            <PackageCheck />
            <span>Goods receipt</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* Three cards, each at most TWO columns of label/value - the three things a
              person asks about an order separately: what it is, who it is from, and what it
              comes to. Each is named as a region so a reader (and a test) can address one. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Order</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Order" className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              {/* Correctable: the imported book took this date off a spreadsheet, and the
                  measured supplier lead time reads it (`scm.receipt_lead_v`). */}
              <Field label="Order date" htmlFor={isEditing ? 'po-edit-order-date' : undefined}>
                {isEditing ? (
                  <Input
                    id="po-edit-order-date"
                    type="date"
                    value={orderDate}
                    onChange={(e) => setOrderDate(e.target.value)}
                    className="h-8"
                  />
                ) : (
                  fmtDate(po.order_date)
                )}
              </Field>
              <Field
                label="Delivery date"
                htmlFor={isEditing ? 'po-edit-expected-date' : undefined}
              >
                {isEditing ? (
                  <Input
                    id="po-edit-expected-date"
                    type="date"
                    value={expectedDate}
                    onChange={(e) => setExpectedDate(e.target.value)}
                    className="h-8"
                  />
                ) : po.expected_date ? (
                  fmtDate(po.expected_date)
                ) : (
                  '-'
                )}
              </Field>
              <Field label="Source">{SOURCE_LABELS[po.source ?? 'manual'] ?? 'Manual'}</Field>
              {/* A row with no code predates the book having more than one currency, so its
                  figures already meant ringgit - which is exactly how every amount on this
                  screen is formatted. */}
              <Field label="Currency">{currency || BASE_CURRENCY}</Field>
            </section>
          </Card>

          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Supplier</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Supplier" className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <Field label="Supplier" htmlFor={isEditing ? 'po-edit-supplier' : undefined}>
                {isEditing ? (
                  // SERVER-SEARCHED, the same shape the sales-order screen's Customer select
                  // uses. `selectedOption` keeps the order's OWN supplier readable when it
                  // is not on the page that came back.
                  <SearchableSelect
                    id="po-edit-supplier"
                    value={supplierCode}
                    onChange={setSupplierCode}
                    paginated
                    pageSize={SELECT_PAGE_SIZE}
                    fetchOptions={searchSupplierOptions}
                    selectedOption={
                      supplierCode && supplierCode === po.supplier_code
                        ? {
                            value: po.supplier_code,
                            label: po.supplier_name
                              ? `${po.supplier_code} · ${po.supplier_name}`
                              : po.supplier_code,
                          }
                        : undefined
                    }
                    placeholder="Select supplier"
                    emptyMessage="No supplier found."
                    size="sm"
                  />
                ) : (
                  // An imported historical PO can carry no supplier at all, and blank read
                  // as the warehouse underneath it standing in for one.
                  po.supplier_name || '-'
                )}
              </Field>
              <Field label="Supplier code">{po.supplier_code || '-'}</Field>
            </section>
          </Card>

          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Totals</CardTitle>
              </CardHeading>
            </CardHeader>
            {/* Named as a region so a reader (and a test) can tell this "Outstanding qty"
                from the lines grid's column of the same name. They share the phrase on
                purpose: it is the same quantity, once for the order and once per line. Every
                figure here is the SAME sum the grid's own totals row prints, so the header
                and the footer cannot disagree - including mid-edit, when both read the
                draft. No Locations field: a location belongs to a LINE. */}
            <section aria-label="Totals" className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              {/* A dash, never RM 0.00, for an order nobody priced. */}
              <Field label="Total amount">{fmtMoneyCell(amountTotal)}</Field>
              <Field label="Total qty">{fmtQtyCell(qtyOrderedTotal)}</Field>
              {/* ALWAYS shown, even when it equals the total. On a wholly outstanding order
                  the two agree, and a reader who cannot see the figure at all has to work
                  out from its absence that nothing has arrived. */}
              <Field label="Outstanding qty">{fmtQtyCell(outstandingTotal)}</Field>
              <Field label="Lines">{fmtInt(lineCount)}</Field>
            </section>
          </Card>

          {/* R1 (AC-H7): a `crm_spo` order's own pulls/covers, off `plan_of`. Every other
              order carries no plan (`po.spo_plan` is null), so nothing renders here for it. */}
          {po.source === 'crm' && po.spo_plan ? <PoPlanCard plan={po.spo_plan} /> : null}
        </TabsContent>

        <TabsContent value="lines" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* Lines - always rendered, explicit empty state. */}
          <DataGrid
            table={table}
            recordCount={visibleLineCount}
            isLoading={false}
            tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
            emptyMessage={
              lineSearch.trim()
                ? 'No line on this order matches that product.'
                : 'This purchase order has no lines.'
            }
            // A real key, not the empty "do not persist" one: a 200-line order is read with
            // the same few columns every time, and the choice has to survive the visit.
            // Keyed off the permission slug plus a stable id, never the record's own path.
            // -v2 (AC-CANCEL-CRM, owner 13 Oct): Qty cancelled and Status must show by
            // default; a saved preference could keep either hidden.
            listingKey="scm.dashboard.view::purchase-order-lines-v2"
          >
            <Card>
              <CardHeader className="flex-wrap gap-3">
                <CardHeading>
                  <CardTitle>Order lines</CardTitle>
                </CardHeading>
                <CardToolbar className="flex-wrap">
                  {/* The order is the unit here, so the search is over the lines already
                      loaded - no request, no paging, and it answers "is this item on this
                      order" on a 200-line document. */}
                  <ListSearchInput
                    value={lineSearchInput}
                    onChange={setLineSearchInput}
                    aria-label="Search lines"
                    placeholder="Search product..."
                    className="w-56"
                  />
                  <DataGridColumnVisibility
                    table={table}
                    trigger={
                      <Button variant="outline" size="sm" className="gap-1.5">
                        <Columns3 className="size-4" />
                        Columns
                      </Button>
                    }
                  />
                </CardToolbar>
              </CardHeader>
              <CardTable>
                <DataGridTable />
              </CardTable>
              {/* The same footer every list in the product carries - "1 - 25 of 213" and the
                  page sizes. */}
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </Card>
          </DataGrid>
        </TabsContent>

        <TabsContent value="goods-receipt" className="mt-0 focus-visible:outline-none">
          {/* Goods receipt - always rendered, empty state when none. The counterpart of the
              sales-order screen's delivery panel. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Goods receipt</CardTitle>
              </CardHeading>
            </CardHeader>
            <div className="p-4">
              {po.gr_reference ? (
                <div className="flex items-center gap-2 text-sm">
                  <PackageCheck className="size-4 text-scm-incoming" />
                  <span className="font-medium">{po.gr_reference}</span>
                  <span className="text-muted-foreground">- received in full</span>
                </div>
              ) : qtyReceivedTotal > 0 ? (
                <p className="text-sm text-muted-foreground">
                  {fmtInt(qtyReceivedTotal)} of {fmtQtyCell(qtyOrderedTotal)} received.{' '}
                  {fmtQtyCell(outstandingTotal)} still to arrive.
                </p>
              ) : (
                // No CTA back to the list: the Create GR button that lived there is gone,
                // and pointing at a control that is not there is worse than saying nothing.
                <p className="text-sm text-muted-foreground">
                  {isDraftPurchaseOrder(po.status)
                    ? 'Nothing received yet. This order has not been placed.'
                    : 'Nothing received yet. Receipts recorded against this order show here.'}
                </p>
              )}
            </div>
          </Card>
        </TabsContent>
      </Tabs>

      {/* R5: the "Placed" figure's lightbox - one dialog for the whole grid, the shell every
          SCM screen's drillable figure opens (AC-L1). Replaces "Allocated to" (AC-L3). */}
      {placementsLine ? (
        <PlanRowDialog
          kind="placements"
          productCode={placementsLine.sku}
          productName={placementsLine.product_name}
          open
          onOpenChange={(next) => {
            if (!next) setPlacementsLineId(null);
          }}
        >
          <PoLinePlacementsBody
            allocation={
              allocationByLineId.get(placementsLine.id) ?? EMPTY_PLACEMENTS_ALLOCATION
            }
          />
        </PlanRowDialog>
      ) : null}
    </div>
  );
}

export default PurchaseOrderDetail;
