'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
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
  CalendarRange,
  Columns3,
  FileText,
  ListOrdered,
  LoaderCircleIcon,
  Move,
  Plus,
  Trash2,
  Truck,
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
import { StockTransfersPanel } from '@/app/(protected)/inventory-management/stock-transfers/components/StockTransfersPanel';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useTableDeepLinkHighlight } from '@/hooks/useTableDeepLinkHighlight';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { DEMAND_CLASS_OPTIONS } from '@/app/(protected)/master-data-management/sales-agents/lib/demandClass';
// The SAME pill the order-inquiry worklist reads, not a second one worded differently:
// "Placed" has to mean the same thing on both screens or the two disagree in a glance.
import { OrderInquiryStatePill } from '@/app/(protected)/project-sales/_shared/components/OrderInquiryVerbPill';
import {
  formatMyrExact,
  multiplyMoney,
  subtractMoney,
  sumMoney,
} from '@/app/(protected)/project-sales/_shared/lib/money';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useSalesOrder, useUpdateSalesOrder } from '../../../hooks/useSalesOrders';
import { useWarehouseOptions } from '../../../hooks/useScmOptions';
import {
  SELECT_PAGE_SIZE,
  searchCustomerOptions,
  searchProductOptions,
} from '../../../services/scmOptionsService';
import { useSalesAgentOptions } from '../../hooks/useSalesAgentOptions';
import DetailActions from '@/components/common/DetailActions';
import { salesOrdersPagerQuery } from '../../../hooks/useSalesOrders';
import { getSalesOrderUoms, type SalesOrderPlanningChangeBatch } from '../../../services/salesOrderService';
import { PlanRowDialog } from '../../../components/PlanRowDialog';
import { PlanNumberButton } from '../../../components/PlanNumberButton';
import { SoLineLinksBody } from './SoLineLinksBody';
import { fmtDate, fmtInt } from '../../../lib/format';
import { demandClassBadge } from '../../../lib/demandClass';
import { transferableBadge } from '../../../lib/transferable';
import { salesOrderPlannedBadge } from '../../../lib/salesOrderPlanned';
import {
  salesOrderPriorityVariant,
  salesOrderStatusLabel,
  salesOrderStatusVariant,
} from '../../../lib/salesOrderStatus';
import {
  productFallbackFor,
  type LineDraft,
} from './salesOrderLineDraft';
import type {
  SalesOrder,
  SalesOrderLine,
  SalesOrderLineSupplyComponent,
  SalesOrderPriority,
} from '../../../types/scm.types';
// ONE vocabulary for where supply comes from (PLAN-scm-cs-planning-uat.md section 2), shared
// with the planning board rather than restated here.
import { describe as describeSupply } from '../../../../project-sales/_shared/lib/supplyVocabulary';
import { BoardChangeTable } from '@/app/(protected)/project-sales/fulfilment-planning/components/BoardChangeTable';
import { lineChangeAnnotation } from './salesOrderLineChange';
import BackToList, { useBackToListHref } from '@/components/common/BackToList';
import { useSalesOrderActions } from '../../actions';
import { PLAN_PERMISSION } from '../../lib/planActions';
// Aliased: this file already has its own header-scoped `SOURCE_LABELS` above, worded
// slightly differently ("Order inquiry sheet") for the header's own Source field - the
// LINE column reads the list's map instead, so the two sit side by side rather than one
// shadowing the other.
import { SOURCE_LABELS as LINE_SOURCE_LABELS } from '../../lib/sourceLabels';
import { useHasPermission } from '@/hooks/usePermissions';

/**
 * The sales-order detail, built to mirror `PurchaseOrderDetail` section for section: the
 * same header shape, the same summary grid, the same lines DataGrid, and the same "always
 * render every section, with an explicit empty state" rule.
 *
 * Mirrored deliberately rather than reinvented. These two screens are one click apart in the
 * same menu and they answer the same question about opposite sides of the book, so a planner
 * who has learnt where a figure lives on one has learnt it on the other. Where they differ,
 * they differ because the DOMAIN differs - a sales order is delivered rather than received,
 * so the goods-receipt panel becomes a delivery panel - never because they were written on
 * different days.
 *
 * THE RECORD IS TABBED, the same shape as the user detail page: the page header (number,
 * status, actions, prev/next) sits above a `variant="line"` tab strip, and each concern of the
 * order owns one tab - General (the summary and the note), Lines, Delivery. Every section is
 * still rendered with its own empty state; a tab is where it lives, not a condition on it.
 *
 * THE GENERAL TAB IS THREE CARDS, each at most TWO columns of label/value fields - Order,
 * Customer, Totals. It was one four-across grid of eleven fields, which reads as a wall
 * rather than as three things a person asks separately.
 *
 * VIEW AND EDIT ARE THE SAME SCREEN (A5). Editing swaps a read-only value for an input IN
 * PLACE - the same fields, in the same order, in the same card, in the same tab. Editable:
 * Order type, Priority, Order date, Delivery date, Agent, Customer. Lines: Product, Qty
 * ordered, Unit price, Discount, Location, Delivery date and UoM. Everything else (Source,
 * Customer code, the three totals) has no edit counterpart and stays exactly where it was.
 *
 * ORDER TYPE IS THE PLANNING CLASS, both ways. It used to RENDER `demand_class` and EDIT
 * `order_type` - a column that is NULL on 96% of this book - and then refuse to save while
 * the order type was empty, so most orders could not be header-edited at all. The select now
 * offers the same two words the class has (Project / Retail), is clearable, and saves as
 * `demand_class`; leaving it empty is allowed and leaves the stored classification alone.
 *
 * One Save writes the whole header, plus the lines ONLY when they actually moved - see
 * `lineSignature` - because the PUT endpoint still upserts the WHOLE `lines` array when the
 * key is sent at all: omit it for a header-only edit or every line is re-sent for no reason.
 * The service UPSERTS what it is sent rather than delete-and-reinsert, matching each sent
 * line to an existing row by `id` FIRST (this form now sends it) or by SKU otherwise - so a
 * matched line keeps its id, `qty_delivered` and `source_system`; only an unmatched existing
 * line is deleted, and the BE refuses that with a 409 when the line is still reconciled to a
 * project sales order or claimed by a purchase order.
 *
 * `warehouse_code` / `required_date` / `uom` / `unit_price` / `discount` ride on the SAME line
 * objects as SKU/qty. The BE applies each key independently via `model_fields_set` - a key a
 * sent LINE does not carry leaves that line's stored value untouched - but this form always
 * sends all five for every line once `lines` is sent at all (the same "resend everything on
 * any line change" rule the SKU/qty columns already followed), each carrying either the
 * draft's edited value or the value the order loaded with, so an untouched line reads back
 * exactly as it was. `line_total` is NOT sent: it is what the source document charged.
 *
 * A LINE IS REMOVED BY OMISSION, never a separate endpoint. The trash icon that appears per
 * row while editing drops the line from `removedLineIds` and its draft immediately - no
 * confirmation, because nothing is written until Save and Cancel restores every removed row.
 * The row disappears from the grid and the totals below it the moment it is clicked; Save is
 * the actual commit point, and until then the removal is just staged edit-session state, the
 * same as a typed quantity. At Save, the removed line is simply left out of `lines`: the BE
 * upserts what it is sent and deletes any existing row that is missing, which is what the
 * class docstring above already describes - a removal is that same mechanism, carried by
 * absence rather than a new one. The BE refuses with a 409 when the line is still reconciled
 * to a project sales order or claimed by a purchase order, and the mutation's own error toast
 * is enough - the session stays open either way.
 *
 * MONEY IS A STRING END TO END. The backend sends `Decimal`, which Pydantic serialises as a
 * string, and every sum here goes through `project-sales/_shared/lib/money` - which does the
 * arithmetic on scaled integers - rather than `Number()`. A float sum of 200 line totals
 * drifts, and a footer that disagrees with the header by one cent is read as a data problem.
 */

function titleCase(v: string): string {
  return v.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Where the order came from. `history` is its own answer because "Manual" would claim
 *  somebody keyed a 2020 order by hand. Mirrors the purchase-order side's `import`. */
const SOURCE_LABELS: Record<string, string> = {
  autocount: 'AutoCount',
  inquiry: 'Order inquiry sheet',
  // The same word the list's Source column uses for the same row. It is read under a
  // "Source" label on the record of one sales order, so "Sales order upload" restated both.
  upload: 'Upload',
  history: 'Absorbed history',
  manual: 'Manual',
};

/** Which rule wrote `project_label`, worded for the Order card's muted word beside the
 *  name - never the internal source code. */
const PROJECT_LABEL_SOURCE_WORDS: Record<string, string> = {
  inquiry: 'Inquiry sheet',
  note: 'Note',
  ref: 'AutoCount ref',
  delivery: 'Delivery address',
};

const PRIORITY_LABELS: Record<string, string> = {
  urgent: 'Urgent',
  high: 'High',
  normal: 'Normal',
  low: 'Low',
};

const PRIORITY_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'normal', label: 'Normal' },
  { value: 'high', label: 'High' },
  { value: 'urgent', label: 'Urgent' },
];

/** Does this line answer the product search above the grid? Code and description, because
 *  a planner looking for one item on a 200-line order knows one or the other, not both. */
function lineMatches(line: SalesOrderLine, query: string): boolean {
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
  /** Set while editing, so the label associates with the input/select it now wraps and
   *  the field is reachable by its name - the same label text either way. */
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

/** `sku|qty|warehouse_code|required_date|uom|unit_price|discount` per line,
 *  order-independent, so a re-save with the same lines in a different order is not read as a
 *  change. Mirrors `SalesOrderFormModal`'s own invariant: `lines` is left off the write
 *  entirely when nothing here moved, or a header-only edit would resend every line for the
 *  BE to match-and-upsert for no reason. */
function lineSignature(
  ls: {
    sku: string;
    qty_ordered: number;
    warehouse_code: string;
    required_date: string;
    uom: string;
    unit_price: string;
    discount: string;
  }[],
): string {
  return ls
    .map(
      (l) =>
        `${l.sku}|${l.qty_ordered}|${l.warehouse_code}|${l.required_date}|${l.uom}` +
        `|${l.unit_price}|${l.discount}`,
    )
    .sort()
    .join(',');
}

/** The in-progress draft for a line, or one seeded from the row as loaded when nothing has
 *  touched it yet - so any single field's onChange can spread this and set only the field it
 *  owns without silently dropping the other six. */
function draftOrRow(
  drafts: Record<string, LineDraft>,
  row: SalesOrderLine,
): LineDraft {
  return drafts[row.id] ?? seedDraft(row);
}

function seedDraft(row: SalesOrderLine): LineDraft {
  return {
    sku: row.sku,
    qty_ordered: String(row.qty_ordered),
    warehouse_code: row.warehouse_code ?? '',
    required_date: row.required_date?.slice(0, 10) ?? '',
    uom: row.uom ?? '',
    unit_price: row.unit_price ?? '',
    discount: row.discount ?? '',
  };
}

/**
 * A removal or a qty-to-zero edit ends a line as `line_status: 'cancelled'` on the CORE
 * row rather than deleting it (`PLAN-scm-change-management-one-engine.md`, Slice A rule 5)
 * - the backend writes nothing for such a row, so an edit made here would silently revert
 * on save. It can still arrive on THIS order's next load, inside an edit session, sitting
 * beside lines that are still open - it renders read-only there (the same cells view mode
 * already renders) rather than as another line the planner can edit or remove again.
 */
function isCancelledLine(row: SalesOrderLine): boolean {
  return (row.line_status ?? 'open') === 'cancelled';
}

/** A line typed in THIS session: it has no server id yet, so Save sends it without one. */
const NEW_LINE_PREFIX = 'new-';

function isNewLine(row: Pick<SalesOrderLine, 'id'>): boolean {
  return row.id.startsWith(NEW_LINE_PREFIX);
}

/**
 * What a line is worth: the total the source document stated, or the arithmetic its parts
 * support. The SAME rule the backend's own `total_amount` follows, so the column, the
 * footer and the header total cannot disagree - and `null` rather than 0 when nobody
 * priced it, because an unpriced line is not a line worth nothing.
 */
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

/** The money cell's text: the figure, or a plain "-" for a line nobody priced. */
function fmtMoneyCell(value: string | null | undefined): string {
  return value ? formatMyrExact(value) : '-';
}

/**
 * One frozen composition, in the planning board's own words (AC-D4).
 *
 * `describe` is the SAME function the board, its list view and the cell popover render with,
 * imported rather than restated: PLAN section 2 is one vocabulary, and a second phrasing of
 * "Shared 71 (BRW)" on this page is how two screens start disagreeing about one decision.
 *
 * `ownLocation` is what tells the agent's own group from the shared pool on a component
 * frozen before the rung was recorded.
 */
function SupplyText({
  parts,
  ownLocation,
  absent,
}: {
  parts?: SalesOrderLineSupplyComponent[] | null;
  ownLocation?: string | null;
  absent: string;
}) {
  if (!parts) return <span className="text-muted-foreground">{absent}</span>;
  const text = describeSupply(parts, ownLocation);
  if (!text) return <span className="text-muted-foreground">-</span>;
  return (
    <span className="block truncate" title={text}>
      {text}
    </span>
  );
}

export function SalesOrderDetail({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data, isLoading, isError } = useSalesOrder(id);
  // Read here rather than off `so` below, which is narrowed after the loading and error
  // returns: the Lines columns are built above them, and the What-changed dialog titles
  // itself with the order this line belongs to.
  const soNumber = data?.so_number ?? '';
  const backHref = useBackToListHref('/scm/sales-orders');
  // Same gate the list's own "Plan selected" carries (S2, R3): a door that answers 403
  // is worse than not offering it, so the primary is simply absent without it.
  const canPlan = useHasPermission(PLAN_PERMISSION);
  const searchParams = useSearchParams();

  const updateMut = useUpdateSalesOrder();
  const agentOptions = useSalesAgentOptions();
  const warehouseOptions = useWarehouseOptions();
  // Inline rather than a shared hook file: this select is used on this one screen. Mirrors
  // `useSalesOrderAgents`'s own staleTime - the UoM master changes rarely enough that a
  // 5-minute cache is not worth a dedicated hooks file for one caller.
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
  // Code review nit: the read view's Location cell shows the code (`row.original.warehouse_code`,
  // below), and `getWarehouseOptions` labels each option with the NAME - so the same value read
  // as "BRW" and edited as "Bandar Rawang Warehouse". Prefix the code onto the label here so the
  // edit select reads the same value the view does, without changing the shared option list every
  // other SCM picker (filter bar, planning modal, preview card) also uses.
  const warehouseSelectOptions = useMemo(
    () => (warehouseOptions.data ?? []).map((o) => ({ ...o, label: `${o.value} - ${o.label}` })),
    [warehouseOptions.data],
  );

  const [isEditing, setIsEditing] = useState(false);
  // The PLANNING CLASS, not `order_type`: this is the value the read view renders, so it is
  // the value the edit form seeds from and writes back.
  const [demandClass, setDemandClass] = useState('');
  const [customerCode, setCustomerCode] = useState('');
  const [priority, setPriority] = useState<SalesOrderPriority>('normal');
  const [orderDate, setOrderDate] = useState('');
  const [requestedDate, setRequestedDate] = useState('');
  const [agentId, setAgentId] = useState('');
  const [lineDrafts, setLineDrafts] = useState<Record<string, LineDraft>>({});
  const [error, setError] = useState<string | null>(null);
  const originalLineSignatureRef = useRef<string | null>(null);
  // Set only when a save's response actually carries one (PLAN-so-book-diff-replanning.md
  // section 2) - most saves never touch this. Cleared on the next edit session, so it
  // cannot linger and describe a batch a later save superseded.
  const [planningChangeBatch, setPlanningChangeBatch] =
    useState<SalesOrderPlanningChangeBatch | null>(null);
  // Which LINE's "Linked" figure was pressed (R5, AC-L4) - one dialog for the whole grid,
  // replacing the inline multi-link text the cell used to render.
  const [linksLineId, setLinksLineId] = useState<string | null>(null);
  // Lines dropped from THIS session, by id. Nothing is deleted here - the row and its draft
  // just stop being sent, and the BE reads the omission as a removal on Save (see the class
  // docstring). Reset on every fresh session and after a save, so a leftover removal from a
  // prior edit cannot silently carry into the next one.
  const [removedLineIds, setRemovedLineIds] = useState<Set<string>>(new Set());
  /**
   * Lines ADDED in this session, before Save (R4c, the SO400884 walk).
   *
   * Placeholder rows, not server rows: they carry a `new-` id so the grid, the drafts map
   * and the remove button can all address them the way they address a stored line, and the
   * SAME cells edit them - one row control for a line however it got onto the order. Save
   * sends them with no `id`, which is what the upsert reads as "this one is new"; Cancel
   * drops them.
   */
  const [newLines, setNewLines] = useState<SalesOrderLine[]>([]);

  const beginEdit = (so: SalesOrder) => {
    setPlanningChangeBatch(null);
    setDemandClass(so.demand_class ?? '');
    setCustomerCode(so.customer_code);
    setPriority(so.priority);
    setOrderDate(so.order_date?.slice(0, 10) ?? '');
    setRequestedDate(so.requested_delivery_date?.slice(0, 10) ?? '');
    setAgentId(so.sales_agent_id ?? '');
    const drafts: Record<string, LineDraft> = {};
    for (const ln of so.lines) {
      drafts[ln.id] = seedDraft(ln);
    }
    setLineDrafts(drafts);
    setRemovedLineIds(new Set());
    setNewLines([]);
    originalLineSignatureRef.current = lineSignature(
      so.lines.map((l) => ({
        sku: l.sku,
        qty_ordered: l.qty_ordered,
        warehouse_code: l.warehouse_code ?? '',
        required_date: l.required_date?.slice(0, 10) ?? '',
        uom: l.uom ?? '',
        unit_price: l.unit_price ?? '',
        discount: l.discount ?? '',
      })),
    );
    setError(null);
    setIsEditing(true);
  };

  // The set the list row's "..." renders too (D15). Delete used to be a red icon in the
  // list and nothing at all here, so a record could only be removed by finding it again in
  // the list. Edit moved in here too (S2, R3): the header's primary slot is now Plan, and
  // the list surface calls this same hook with no `onEdit`, so its row menu shows no Edit.
  const { actions, pending: deletionPending } = useSalesOrderActions(data, {
    onDeleted: () => router.push(backHref),
    onEdit: () => beginEdit(data as SalesOrder),
  });

  const cancelEdit = () => {
    setIsEditing(false);
    setError(null);
    setRemovedLineIds(new Set());
    setNewLines([]);
  };

  // `?edit=1` opens the session on arrival - the same entry the list's Pencil action uses -
  // so a click there and a bookmarked link land in the same place. Fired once: re-running it
  // after Cancel would put the user straight back into the session they just left.
  const wantsEdit = searchParams.get('edit') === '1';
  const opened = useRef(false);
  useEffect(() => {
    if (!wantsEdit || opened.current || !data) return;
    opened.current = true;
    beginEdit(data);
  }, [wantsEdit, data]);

  // Excludes a removed line the moment it is confirmed, not only on Save - the grid, its
  // totals footer and the "last line" guard all read this one array, so a row that has been
  // removed cannot still count toward any of them.
  const lines = useMemo<SalesOrderLine[]>(
    () => {
      const stored = (data?.lines ?? []).filter(
        (l) => !isEditing || !removedLineIds.has(l.id),
      );
      // A line added in this session sits at the END, where it was typed, and only while
      // the session is open.
      return isEditing && newLines.length ? [...stored, ...newLines] : stored;
    },
    [data, isEditing, newLines, removedLineIds],
  );
  // Sorted and searched here rather than by the API: the lines come embedded in the order
  // read, so there is no second request to spend and no page boundary to work across.
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: lineSearchInput,
    setValue: setLineSearchInput,
    debouncedValue: lineSearch,
    reset: resetLineSearch,
  } = useDebouncedSearch();
  // Kept in the URL, not component state (mirrors `ProductDetail`'s own `tab` param), so a
  // bookmarked or shared link (`?tab=lines`) opens directly on that tab. S6 (AC-B6-3): a
  // `?line=<id>` deep link always lands on Lines, whatever `tab` says - the only tab that
  // line could ever be found on.
  const tab = searchParams.get('line') ? 'lines' : searchParams.get('tab') || 'general';
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

  // While an edit session is open every figure below is read off the DRAFT, so a typed
  // quantity or price moves the row AND the totals row at once. Outside a session they read
  // the stored row, which is the same value.
  const outstandingOf = useCallback(
    (row: SalesOrderLine) => {
      // A CLOSED line is outstanding NOTHING, whatever its two quantities say. A book
      // re-upload closes a line by absence without knowing what shipped, so `qty_delivered`
      // stays 0 and `ordered - delivered` read the whole quantity as still owed: SO397450
      // showed 306 Completed lines and a footer summing 39,008 outstanding.
      if ((row.line_status ?? 'open') !== 'open') return 0;
      // Off the SERVER outside an edit session, so this grid, its footer and the Totals
      // card above all print the one figure the backend computed.
      if (!isEditing && row.outstanding_qty !== undefined && row.outstanding_qty !== null) {
        return row.outstanding_qty;
      }
      const ordered = isEditing
        ? Number(draftOrRow(lineDrafts, row).qty_ordered)
        : Number(row.qty_ordered);
      if (!Number.isFinite(ordered)) return 0;
      return Math.max(ordered - Number(row.qty_delivered), 0);
    },
    [isEditing, lineDrafts],
  );

  const amountOf = useCallback(
    (row: SalesOrderLine) => {
      if (!isEditing) {
        return lineAmount(
          String(row.qty_ordered), row.unit_price ?? '', row.discount ?? '', row.line_total,
        );
      }
      const draft = draftOrRow(lineDrafts, row);
      // The stated `line_total` is what the source document charged, so it wins - until one
      // of the figures it was charged ON is edited, at which point it no longer describes
      // what is on the screen and the arithmetic does.
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

  // The trash icon's click: removes the row right away, unless removing would leave nothing
  // on the order - in which case nothing is removed, only said. Nothing is written here; Save
  // is the commit point and Cancel restores every removed row (see the class docstring).
  // `lines` already excludes any line removed earlier in this same session, so its length IS
  // what would remain before this one too.
  const handleRemoveLine = useCallback(
    (row: SalesOrderLine) => {
      if (lines.length <= 1) {
        setError('An order needs at least one line.');
        return;
      }
      setError(null);
      if (isNewLine(row)) {
        // Never saved, so there is nothing for the backend to be told about: the row simply
        // stops existing.
        setNewLines((prev) => prev.filter((ln) => ln.id !== row.id));
      } else {
        setRemovedLineIds((prev) => {
          const next = new Set(prev);
          next.add(row.id);
          return next;
        });
      }
      setLineDrafts((prev) => {
        const next = { ...prev };
        delete next[row.id];
        return next;
      });
    },
    [lines],
  );

  // A CANCELLED line is out of every total (R4, the SO400884 walk). The book closed it, so
  // its quantity is not ordered any more, nothing about it is outstanding, and what it was
  // once priced at is not part of what this order is worth - the stale figures sat in the
  // footer reading as though the line still counted.
  const countedLines = useMemo(() => lines.filter((l) => !isCancelledLine(l)), [lines]);
  /**
   * Add line (R4c, the SO400884 walk): a blank row at the end of the grid, editable by the
   * SAME cells every other line is edited by.
   *
   * A placeholder row rather than a second form: the grid already knows how to render a
   * product select, a quantity, a date and a UoM for a line, so a new line is just a line
   * the backend has not seen yet. It is sent on Save with no `id`, and Cancel drops it.
   */
  const handleAddLine = useCallback(() => {
    setError(null);
    const row: SalesOrderLine = {
      id: `${NEW_LINE_PREFIX}${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      sku: '',
      product_name: '',
      qty_ordered: 0,
      qty_delivered: 0,
      uom: '',
      warehouse_code: '',
      line_status: 'open',
      required_date: null,
    };
    setNewLines((prev) => [...prev, row]);
    setLineDrafts((prev) => ({
      ...prev,
      [row.id]: {
        sku: '',
        qty_ordered: '',
        warehouse_code: '',
        required_date: '',
        uom: '',
        unit_price: '',
        discount: '',
      },
    }));
  }, []);

  const qtyOrderedTotal = useMemo(
    () =>
      countedLines.reduce((sum, l) => {
        const qty = isEditing
          ? Number(draftOrRow(lineDrafts, l).qty_ordered)
          : Number(l.qty_ordered);
        return sum + (Number.isFinite(qty) ? qty : 0);
      }, 0),
    [countedLines, isEditing, lineDrafts],
  );
  const qtyDeliveredTotal = useMemo(
    () => lines.reduce((sum, l) => sum + Number(l.qty_delivered), 0),
    [lines],
  );
  const outstandingTotal = useMemo(
    () => countedLines.reduce((sum, l) => sum + outstandingOf(l), 0),
    [countedLines, outstandingOf],
  );
  const amountTotal = useMemo(() => {
    const amounts = countedLines.map(amountOf).filter((a): a is string => a !== null);
    return amounts.length ? sumMoney(amounts) : null;
  }, [countedLines, amountOf]);

  const columns = useMemo<ColumnDef<SalesOrderLine>[]>(
    () => [
      // LEFTMOST, so the grid reads left to right in the same order AutoCount does
      // (PLAN-so-lines-autocount-order.md). A saved column order that predates this column
      // has never seen its id, so `mergeColumnOrderWithLeafColumns` (the shared column-config
      // merge every `DataGrid` runs) has no preceding neighbour to anchor it after and places
      // it first regardless - the same reason a first-time visitor sees it first.
      {
        id: 'line_no',
        accessorFn: (line) => line.line_no ?? null,
        header: ({ column }) => <DataGridColumnHeader title="No." column={column} />,
        // A VALUE, never an input, in or out of an edit session (3.3): AutoCount's own
        // number is not something this screen offers to renumber. A session-local new line
        // (`NEW_LINE_PREFIX`) has none yet, so it reads the same dash a never-numbered
        // stored line does.
        cell: ({ row }) =>
          row.original.line_no == null ? (
            <span className="text-muted-foreground">-</span>
          ) : (
            <span className="tabular-nums">{row.original.line_no}</span>
          ),
        // Numeric, never lexicographic - a bare string sort would read 1, 10, 11, 12, 2, 3.
        sortingFn: 'basic',
        size: 64,
        meta: {
          headerTitle: 'No.',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        accessorKey: 'sku',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = lineDrafts[row.original.id];
            const selectId = `so-edit-line-${row.original.id}-product`;
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
                  // The whole option, not only its code: this is what the cell shows after
                  // the popover has closed and the page it was picked from is gone.
                  onOptionChange={(option) =>
                    setLineDrafts((prev) => ({
                      ...prev,
                      [row.original.id]: {
                        ...draftOrRow(prev, row.original),
                        sku: option?.value ?? '',
                        picked_product: option,
                      },
                    }))
                  }
                  paginated
                  pageSize={SELECT_PAGE_SIZE}
                  fetchOptions={searchProductOptions}
                  selectedOption={productFallbackFor(row.original, draft)}
                  placeholder="Select product"
                  emptyMessage="No product found."
                  size="sm"
                />
              </>
            );
          }
          const showProductName =
            !!row.original.product_name?.trim() &&
            row.original.product_name.trim().toLowerCase() !==
              row.original.sku?.trim().toLowerCase();
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
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = lineDrafts[row.original.id];
            return (
              <Input
                type="number"
                min={0}
                value={draft?.qty_ordered ?? String(row.original.qty_ordered)}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [row.original.id]: { ...draftOrRow(prev, row.original), qty_ordered: e.target.value },
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
        // Rendered by the shared grid (`DataGridTable`) as soon as any column declares a
        // `footer`, which is why this is a column property rather than a hand-rolled tfoot.
        footer: () => fmtInt(qtyOrderedTotal),
        meta: {
          headerTitle: 'Qty ordered',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'qty_delivered',
        accessorFn: (line) => Number(line.qty_delivered),
        header: ({ column }) => <DataGridColumnHeader title="Qty delivered" column={column} />,
        cell: ({ row }) => fmtInt(row.original.qty_delivered),
        size: 130,
        footer: () => fmtInt(qtyDeliveredTotal),
        meta: {
          headerTitle: 'Qty delivered',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'outstanding',
        // Computed here rather than sent, so it cannot disagree with the two columns beside
        // it. A negative delivery (over-shipped) reads as 0 rather than as a negative
        // commitment, which is what the committed figure does too. The accessor is what the
        // cell prints, so "sort by what is outstanding" sorts by the figure on screen - an
        // id-only column carries a sort arrow it cannot honour.
        //
        // In an EDIT session it recomputes from the draft quantity, live: typing 400 over
        // 320 on a line with 100 delivered has to show 300 owed straight away, or the row
        // states two figures that contradict each other until the page is reloaded.
        accessorFn: (line) =>
          Math.max(Number(line.qty_ordered) - Number(line.qty_delivered), 0),
        header: ({ column }) => <DataGridColumnHeader title="Outstanding qty" column={column} />,
        cell: ({ row }) => fmtInt(outstandingOf(row.original)),
        size: 140,
        footer: () => fmtInt(outstandingTotal),
        meta: {
          headerTitle: 'Outstanding qty',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        // R5: was the inline multi-link text (AC-I9); now the SUM of every link's qty as a
        // figure that opens the lightbox (AC-L4) - the same child table the order-inquiry
        // worklist's "Linked to" column and the PO occupancy panel read from, so the three
        // surfaces still answer with one voice, one level down. A line whose inquiry row
        // exists but holds no link reads "Not linked"; a line with no inquiry row at all
        // reads "-", which is the difference between "nothing has been linked" and "nobody
        // was told".
        id: 'linked_to',
        accessorFn: (row) =>
          row.linked_to ? row.linked_to.reduce((sum, link) => sum + Number(link.qty), 0) : null,
        header: ({ column }) => <DataGridColumnHeader title="Linked to" column={column} />,
        cell: ({ row }) => {
          const links = row.original.linked_to;
          if (!links) return <span className="text-muted-foreground">-</span>;
          if (links.length === 0)
            return <span className="text-muted-foreground">Not linked</span>;
          const total = links.reduce((sum, link) => sum + Number(link.qty), 0);
          return (
            <PlanNumberButton
              value={fmtInt(total)}
              label={`Linked to ${row.original.sku}`}
              onClick={() => setLinksLineId(row.original.id)}
            />
          );
        },
        size: 120,
        meta: { headerTitle: 'Linked to' },
      },
      {
        id: 'unit_price',
        accessorFn: (line) => Number(line.unit_price ?? 0),
        header: ({ column }) => <DataGridColumnHeader title="Unit price" column={column} />,
        cell: ({ row }) => {
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = draftOrRow(lineDrafts, row.original);
            return (
              <Input
                type="number"
                min={0}
                step="0.01"
                aria-label={`Unit price on ${row.original.sku}`}
                value={draft.unit_price}
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
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = draftOrRow(lineDrafts, row.original);
            return (
              <Input
                type="number"
                min={0}
                step="0.01"
                aria-label={`Discount on ${row.original.sku}`}
                value={draft.discount}
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
        // stated total shows its arithmetic and would otherwise sort as 0, the same trap the
        // Outstanding qty column above avoids.
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
        accessorKey: 'warehouse_code',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        cell: ({ row }) => {
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = draftOrRow(lineDrafts, row.original);
            const selectId = `so-edit-line-${row.original.id}-warehouse`;
            return (
              <>
                {/* SearchableSelect forwards `id`, not arbitrary aria props - the accessible
                    name needs a real (visually-hidden) label. */}
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
        id: 'required_date',
        // The ISO date, not the printed "04 Jul 2026": the display form sorts by day-of-month
        // and would put September before July. A line with no date sorts last either way.
        // Labelled "Delivery date" - the same field (`required_date`) reads as "when this
        // line ships", which is what the customer wants to know, not an internal deadline.
        accessorFn: (line) => line.required_date ?? undefined,
        header: ({ column }) => <DataGridColumnHeader title="Delivery date" column={column} />,
        cell: ({ row }) => {
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = draftOrRow(lineDrafts, row.original);
            return (
              <Input
                type="date"
                aria-label={`Delivery date on ${row.original.sku}`}
                value={draft.required_date}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [row.original.id]: {
                      ...draftOrRow(prev, row.original),
                      required_date: e.target.value,
                    },
                  }))
                }
                className="h-8"
              />
            );
          }
          return row.original.required_date ? fmtDate(row.original.required_date) : '-';
        },
        size: 150,
        meta: { headerTitle: 'Delivery date' },
      },
      {
        accessorKey: 'uom',
        header: ({ column }) => <DataGridColumnHeader title="UoM" column={column} />,
        cell: ({ row }) => {
          if (isEditing && !isCancelledLine(row.original)) {
            const draft = draftOrRow(lineDrafts, row.original);
            const selectId = `so-edit-line-${row.original.id}-uom`;
            return (
              <>
                {/* SearchableSelect forwards `id`, not arbitrary aria props - same pattern
                    as the Location select above. */}
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
                  // Clearing sends `''`, which the BE reads as an explicit `uom: null` -
                  // "use the product's own default" - not "leave it alone" (this form
                  // always sends the key once `lines` is sent at all, see the class
                  // docstring).
                  placeholder="Product default"
                  clearable
                  size="sm"
                />
              </>
            );
          }
          return row.original.uom;
        },
        size: 140,
        meta: { headerTitle: 'UoM' },
      },
      {
        accessorKey: 'line_status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        // The same light chip as every other pill on this page, worded the way AutoCount
        // words it - the same helper the header pill and the list column use.
        cell: ({ row }) => (
          <Badge
            variant={salesOrderStatusVariant(row.original.line_status ?? 'open')}
            appearance="light"
            size="md"
          >
            {salesOrderStatusLabel(row.original.line_status ?? 'open')}
          </Badge>
        ),
        size: 130,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'source',
        accessorFn: (line) => line.source ?? 'manual',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        // R2: the LINE's own provenance, never inherited from the header - a line with no
        // source system of its own reads Manual even under an AutoCount or uploaded header.
        // Same map the list's Source column and Source filter read (`lib/sourceLabels`).
        cell: ({ row }) => (
          <Badge
            variant={row.original.source === 'inquiry' ? 'primary' : 'secondary'}
            appearance="light"
            size="md"
          >
            {LINE_SOURCE_LABELS[row.original.source ?? 'manual'] ?? 'Manual'}
          </Badge>
        ),
        size: 130,
        // Matches the list's own Source column: five values is a filter, not a sort.
        enableSorting: false,
        meta: { headerTitle: 'Source' },
      },
      {
        id: 'order_inquiry',
        accessorFn: (row) => row.order_inquiry?.inquiry_no ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Order inquiry" column={column} />
        ),
        // What purchasing has already been told about this line, by the number they quote.
        // A dash is the honest answer for a line nobody has raised an inquiry on: the
        // planning record only mirrors an order somebody adopted, and most of the book is
        // not adopted.
        cell: ({ row }) => {
          const inquiry = row.original.order_inquiry;
          if (!inquiry) return <span className="text-muted-foreground">-</span>;
          const reference = inquiry.inquiry_no ?? '-';
          // S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-2): lands on this exact row of
          // the inquiry. Plain text when the payload carries neither id - a row raised
          // before inquiries were numbered - rather than a link that lands nowhere.
          const href =
            inquiry.inquiry_id && inquiry.row_id
              ? `/project-sales/order-inquiries/${inquiry.inquiry_id}?row=${inquiry.row_id}`
              : null;
          return (
            <div className="flex min-w-0 items-center gap-1.5">
              {href ? (
                <Link
                  href={href}
                  className="min-w-0 truncate tabular-nums font-medium text-primary hover:underline"
                  title={reference}
                >
                  {reference}
                </Link>
              ) : (
                <span className="min-w-0 truncate tabular-nums" title={reference}>
                  {reference}
                </span>
              )}
              <OrderInquiryStatePill state={inquiry.state} />
            </div>
          );
        },
        size: 200,
        meta: { headerTitle: 'Order inquiry' },
      },
      {
        // AC-D4: the board's two compositions, on the order they belong to. The SECONDARY
        // surface for the same question - the board is where the decision is taken, this is
        // where somebody looking at the order alone can see what was taken.
        id: 'supply_suggested',
        accessorFn: (row) => row.supply_proposed ?? null,
        header: ({ column }) => <DataGridColumnHeader title="Suggested" column={column} />,
        cell: ({ row }) => (
          <SupplyText
            parts={row.original.supply_proposed}
            ownLocation={row.original.warehouse_code}
            absent={row.original.decision_revision == null ? '-' : 'Not recorded'}
          />
        ),
        size: 220,
        meta: { headerTitle: 'Suggested' },
      },
      {
        id: 'supply_decided',
        accessorFn: (row) => row.supply_decided ?? row.supply_saved ?? null,
        header: ({ column }) => <DataGridColumnHeader title="Decided" column={column} />,
        // D10 (captain, 3 Sep): a SAVED (unconfirmed) decision reads here too, until
        // Confirm replaces it with the frozen one - a save on the board used to read "-"
        // on this page, which looked like the save had done nothing. `supply_decided` and
        // `supply_saved` never both answer for one line (Confirm deletes the draft it
        // promotes), so this is never a choice between two real compositions.
        cell: ({ row }) => {
          const line = row.original;
          const parts = line.supply_decided ?? line.supply_saved ?? null;
          const text = (
            <SupplyText parts={parts} ownLocation={line.warehouse_code} absent="-" />
          );
          // THE BOOK ACTUALLY CHANGED THIS LINE. The board's own dialog, on this screen: it
          // says what moved and - once Apply has run - where the held quantity went, which a
          // static "Suggestion changed" badge cannot, and which the board itself can no
          // longer be asked once a cancelled line closes and leaves it.
          const annotation = lineChangeAnnotation(line, soNumber);
          if (annotation) {
            return (
              <div className="flex min-w-0 items-center gap-1.5">
                <span className="min-w-0 flex-1">{text}</span>
                <BoardChangeTable annotation={annotation} compact className="shrink-0" />
              </div>
            );
          }
          // Unchanged lines keep exactly what they had: the decided composition alone, or
          // the saved draft with its Saved / Suggestion changed badge beside it.
          if (line.supply_decided != null || line.supply_saved == null) return text;
          return (
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="min-w-0 flex-1">{text}</span>
              <Badge
                data-testid={`saved-decision-badge-${line.id}`}
                variant={line.saved_stale ? 'warning' : 'success'}
                appearance="light"
                size="sm"
                className="shrink-0"
              >
                {line.saved_stale ? 'Suggestion changed' : 'Saved'}
              </Badge>
            </div>
          );
        },
        size: 240,
        meta: { headerTitle: 'Decided' },
      },
      {
        id: 'decision_revision',
        accessorFn: (row) =>
          row.decision_revision ?? (row.supply_saved != null ? 'saved' : null),
        header: ({ column }) => <DataGridColumnHeader title="Decision" column={column} />,
        // Which confirmed revision covers this line, or - before that - who saved it and
        // when (D10). A line the active decision left out, with no draft either, is as
        // undecided as a line on an order nobody planned, and both read "-".
        cell: ({ row }) => {
          if (row.original.decision_revision != null) {
            return <span className="tabular-nums">{`Rev ${row.original.decision_revision}`}</span>;
          }
          if (row.original.supply_saved != null) {
            const savedBy = row.original.saved_by;
            const savedAt = row.original.saved_at;
            const title = savedBy
              ? `Saved by ${savedBy}${savedAt ? ` · ${formatDateTimeInMalaysia(savedAt)}` : ''}`
              : undefined;
            return (
              <span className="text-sm" title={title}>
                Saved
              </span>
            );
          }
          return <span className="text-muted-foreground">-</span>;
        },
        size: 110,
        meta: { headerTitle: 'Decision' },
      },
      // Trailing, EDIT-only: a view-mode row cannot be removed, so the column has nothing to
      // offer there and does not appear. Empty header - the icon-only button in every cell
      // says what the column is for.
      ...(isEditing
        ? [
            {
              id: 'remove',
              header: () => null,
              enableSorting: false,
              enableHiding: false,
              // A cancelled line cannot be removed again - it already ended, and the
              // backend has nothing left to do with a second removal of the same row.
              cell: ({ row }: { row: { original: SalesOrderLine } }) =>
                isCancelledLine(row.original) ? null : (
                  <Button
                    type="button"
                    mode="icon"
                    variant="ghost"
                    size="sm"
                    aria-label="Remove line"
                    title="Remove line"
                    onClick={() => handleRemoveLine(row.original)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                ),
              size: 56,
            } as ColumnDef<SalesOrderLine>,
          ]
        : []),
    ],
    [
      isEditing,
      lineDrafts,
      warehouseSelectOptions,
      uomSelectOptions,
      outstandingOf,
      amountOf,
      qtyOrderedTotal,
      qtyDeliveredTotal,
      outstandingTotal,
      amountTotal,
      handleRemoveLine,
      soNumber,
    ],
  );

  const table = useReactTable({
    columns,
    data: lines,
    getRowId: (row) => row.id,
    state: { sorting, globalFilter: lineSearch },
    onSortingChange: setSorting,
    // The whole ROW answers the search, so every column is allowed to carry it and the
    // matcher below decides. Left to the default, only string columns qualify, which made
    // the filter depend on which columns happen to hold strings.
    getColumnCanGlobalFilter: () => true,
    globalFilterFn: (row, _columnId, value) => lineMatches(row.original, String(value ?? '')),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    // Paged like every other listing in the product, so the footer can say "1 - 25 of 213"
    // instead of leaving a 200-line contract as one endless scroll with no count on it.
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });
  const visibleLineCount = table.getFilteredRowModel().rows.length;

  // S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-3/10/11/12): a link from the OI Lines
  // tab / worklist's own "SO line" cell lands on this exact row (`?line=<id>`) - `row.id`
  // IS the core sales-order line id `core_line_id` addresses.
  const lineDeepLink = useTableDeepLinkHighlight(table, {
    paramName: 'line',
    rowId: (row: SalesOrderLine) => row.id,
    currentSearch: lineSearch,
    clearSearch: () => resetLineSearch(''),
    enabled: !isLoading,
  });

  // Back and prev/next live on the RIGHT of the record header, next to each other, the way
  // the purchase-order and users screens do it.
  // Back carries the list query the row click wrote (S3-01). It lives on the
  // toolbar row now; the empty states below keep one of their own.
  const backLink = (
    <BackToList listPath="/scm/sales-orders" label="Back to sales orders" />
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
          <div className="text-sm font-semibold">Sales order not found</div>
          <p className="max-w-md text-sm text-muted-foreground">
            This sales order doesn&apos;t exist, or it was removed after this link was made.
            Head back to the list to pick another.
          </p>
        </Card>
      </div>
    );
  }

  const so = data;
  const lineCount = so.line_count ?? lines.length;
  // How far the order has been planned, in the same chip the list carries.
  const plannedBadge = salesOrderPlannedBadge(so.planned_lines, so.plannable_lines);
  const linksLine = linksLineId ? (lines.find((l) => l.id === linksLineId) ?? null) : null;

  const handleSave = async () => {
    setError(null);
    // No order-type check. 96% of this book carries no classification, so refusing the save
    // on an empty one made most orders un-editable; an empty class means "leave the stored
    // classification alone", which is what the BE does with it.
    if (!customerCode) return setError('Select a customer.');
    // `id` is sent so the BE matches this line by id rather than falling back to SKU.
    // Location / delivery date / UoM / price / discount ride the SAME upsert as SKU/qty -
    // see the class docstring - carrying either what the person typed or, for an untouched
    // line, exactly what the order loaded with. A removed line is left out here, before the
    // draft loop even runs - the BE's own upsert deletes whatever `lines` does not name.
    const cleanedLines = [
      ...so.lines.filter((ln) => !removedLineIds.has(ln.id)),
      ...newLines,
    ].map((ln) => {
      const draft = lineDrafts[ln.id];
      return {
        id: ln.id,
        sku: draft?.sku ?? ln.sku,
        qty_ordered: Number(draft?.qty_ordered ?? ln.qty_ordered),
        warehouse_code: draft?.warehouse_code ?? ln.warehouse_code ?? '',
        required_date: draft?.required_date ?? ln.required_date?.slice(0, 10) ?? '',
        uom: draft?.uom ?? ln.uom ?? '',
        unit_price: draft?.unit_price ?? ln.unit_price ?? '',
        discount: draft?.discount ?? ln.discount ?? '',
      };
    });
    // ZERO IS A REAL EDIT ON A LINE THAT EXISTS (R4, the SO400884 walk): the book's own way
    // of closing a line is to order none of it, and the backend cancels the line for exactly
    // that quantity (Slice A rule 5). Refusing it here left the screen unable to say the one
    // thing the planner came to say. A line that does not exist yet is the other case - "add
    // a line for none of something" is not an instruction, so a NEW row still needs a
    // quantity above zero, and so does every line on create.
    const badQuantity = cleanedLines.some((l) => {
      if (!Number.isFinite(l.qty_ordered) || l.qty_ordered < 0) return true;
      return isNewLine(l) && l.qty_ordered === 0;
    });
    if (cleanedLines.some((l) => !l.sku) || badQuantity) {
      return setError('Every line needs a product and a quantity above zero.');
    }
    // A removal carries no field change to compare - it is the LINE COUNT that moves, so a
    // signature built from one fewer line can never match the original's and `lines` is
    // sent, same as any other line edit.
    const linesUnchanged =
      originalLineSignatureRef.current !== null &&
      lineSignature(cleanedLines) === originalLineSignatureRef.current;
    try {
      const result = await updateMut.mutateAsync({
        id,
        data: {
          demand_class: demandClass || null,
          customer_code: customerCode,
          priority,
          order_date: orderDate || null,
          requested_delivery_date: requestedDate || null,
          sales_agent_id: agentId || null,
          ...(linesUnchanged
            ? {}
            : {
                lines: cleanedLines.map(({ id: lineId, ...l }) => ({
                  // A session-local `new-` id is not a row the backend has ever seen, so it
                  // is left off: no id means "match by SKU, or create it".
                  ...(lineId.startsWith(NEW_LINE_PREFIX) ? {} : { id: lineId }),
                  ...l,
                  // Empty means "clear this figure", which is what the BE reads a `null` as.
                  unit_price: l.unit_price || null,
                  discount: l.discount || null,
                })),
              }),
        },
      });
      setPlanningChangeBatch(result.planning_change_batch ?? null);
      setIsEditing(false);
      setRemovedLineIds(new Set());
      setNewLines([]);
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
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                <CardTitle className="text-lg">{so.so_number}</CardTitle>
                <Badge variant={salesOrderStatusVariant(so.status)} appearance="light" size="md">
                  {salesOrderStatusLabel(so.status)}
                </Badge>
                {/* How far the order has been PLANNED, beside the status it keeps being
                    confused with. The SAME chip the list carries, in the header rather than a
                    tab body because it is read-only metadata about the whole record - so view
                    and edit show it identically, with nothing to change about it here. */}
                <Badge
                  variant={plannedBadge.variant}
                  appearance="light"
                  size="md"
                >
                  {plannedBadge.label}
                </Badge>
              </div>
              {/* Read-only metadata belongs in the header, not a tab body - the project the
                  order carries, resolved from whichever source ranked highest. Nothing when
                  no source has ever named one. */}
              {so.project_label ? (
                <span className="truncate text-sm text-muted-foreground" title={so.project_label}>
                  {so.project_label}
                </span>
              ) : null}
            </div>
            {/* In an edit session the header states ONE intent: Save or Cancel. Nav and the
                way out act on the order as it is STORED, and offering them over a screen
                full of unsaved changes is offering to act on a document nobody is reading. */}
            {isEditing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  Nothing is written until you press Save.
                </span>
                <Button variant="outline" size="sm" onClick={cancelEdit} disabled={updateMut.isPending}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSave} disabled={updateMut.isPending}>
                  {updateMut.isPending ? (
                    <LoaderCircleIcon className="me-2 size-4 animate-spin" />
                  ) : null}
                  Save sales order
                </Button>
              </div>
            ) : (
              <DetailActions
                pager={{
                  ...salesOrdersPagerQuery,
                  detailPath: '/scm/sales-orders',
                  currentId: id,
                  ariaLabel: 'sales order',
                }}
                actions={actions}
                pendingAction={deletionPending}
                gearLabel="Sales order options"
                // The main action on this page is now PLAN, not Edit (S2, R3): this is the
                // same board the list's "Plan selected" opens, on this one order, and it
                // wears the main colour for the same reason an Add does on every list. Edit
                // moved into the gear above, since a record already open has less need for
                // a second, louder door into changing it. Absent - not disabled - without
                // the permission the board's own page requires: a door that answers 403 is
                // worse than no door. Also absent off a non-project demand class (AC-S2-6b):
                // the fulfilment board only accepts project demand, so a retail order's Plan
                // would open onto an empty board.
                primary={
                  canPlan && so.demand_class === 'project' ? (
                    <Button asChild variant="primary" size="sm" className="gap-1.5">
                      <Link
                        href={`/project-sales/fulfilment-planning?orders=${encodeURIComponent(
                          so.so_number,
                        )}`}
                      >
                        <CalendarRange className="size-4" />
                        Plan
                      </Link>
                    </Button>
                  ) : undefined
                }
              />
            )}
          </div>
          {isEditing && error ? (
            <Alert variant="destructive" className="mt-3">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {/* Only after a save whose response carried one - the same reaction an uploaded
              book's own preview surfaces (`OutstandingUploadDialog`'s `PlanningChangeBatchCard`).
              Quiet: a count and a link, nothing explaining what a planning change is. */}
          {!isEditing && planningChangeBatch ? (
            <div className="mt-3 flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-medium">
                {`Planning changes raised on ${fmtInt(planningChangeBatch.line_count)} line${
                  planningChangeBatch.line_count === 1 ? '' : 's'
                }`}
              </p>
              {/* Straight to the board, on THIS order and THIS batch (AC-P3-1): the change
                  is decided where the plan is, in one vocabulary. */}
              <Button asChild variant="outline" size="sm">
                <Link
                  href={`/project-sales/fulfilment-planning?orders=${encodeURIComponent(
                    so.so_number,
                  )}&batch=${encodeURIComponent(planningChangeBatch.id)}`}
                >
                  Plan
                </Link>
              </Button>
            </div>
          ) : null}
        </CardHeader>
      </Card>

      {/* One tab per concern of the order, the same shape as the user detail page. The tab
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
          <TabsTrigger value="delivery">
            <Truck />
            <span>Delivery</span>
          </TabsTrigger>
          <TabsTrigger value="transfers">
            <Move />
            <span>Transfers</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* Three cards, each at most TWO columns of label/value. One eleven-field grid four
              across reads as a wall; these are the three things a person asks about an order
              separately - what it is, who it is for, and what it comes to. Each is named as a
              region so a reader (and a test) can address one of them. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Order</CardTitle>
              </CardHeading>
            </CardHeader>
            <section
              aria-label="Order"
              className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2"
            >
              {/* The PLANNING CLASS, read and written. It used to render `demand_class` and
                  edit `order_type` - a different column, NULL on 96% of this book - so what
                  the pill said and what the select set were never the same fact. The ERP
                  document type still rides along as a hint when the order carries one and it
                  says something the class does not already say. */}
              <Field label="Order type" htmlFor={isEditing ? 'so-edit-order-type' : undefined}>
                {isEditing ? (
                  <SearchableSelect
                    id="so-edit-order-type"
                    value={demandClass}
                    onChange={setDemandClass}
                    options={DEMAND_CLASS_OPTIONS}
                    // Unclassified is a real, common answer, so it must be reachable - and
                    // saving with it empty leaves the stored classification alone rather
                    // than clearing it.
                    placeholder="Unclassified"
                    clearable
                    size="sm"
                  />
                ) : (
                  (() => {
                    const cls = demandClassBadge(so.demand_class);
                    const hint = so.order_type_label;
                    const showHint = hint && hint !== cls.label;
                    return (
                      <span className="inline-flex flex-wrap items-center gap-1.5">
                        <Badge variant={cls.variant} appearance="light" size="md">
                          {cls.label}
                        </Badge>
                        {showHint ? (
                          // `text-2xs`, not `text-xs` - the view/edit parity test walks
                          // `span.text-xs` as the Field label selector, and this hint is a
                          // VALUE, not a label.
                          <span className="text-2xs font-normal text-muted-foreground">
                            {hint}
                          </span>
                        ) : null}
                      </span>
                    );
                  })()
                )}
              </Field>
              <Field label="Priority" htmlFor={isEditing ? 'so-edit-priority' : undefined}>
                {isEditing ? (
                  <SearchableSelect
                    id="so-edit-priority"
                    value={priority}
                    onChange={(v) => setPriority((v || 'normal') as SalesOrderPriority)}
                    options={PRIORITY_OPTIONS}
                    placeholder="Select priority"
                    size="sm"
                  />
                ) : (
                  <Badge
                    variant={salesOrderPriorityVariant(so.priority)}
                    appearance="light"
                    size="md"
                  >
                    {PRIORITY_LABELS[so.priority] ?? titleCase(so.priority)}
                  </Badge>
                )}
              </Field>
              {/* Correctable: the absorbed book took this date off a spreadsheet, and the
                  demand trend reads 24 months of this column. */}
              <Field label="Order date" htmlFor={isEditing ? 'so-edit-order-date' : undefined}>
                {isEditing ? (
                  <Input
                    id="so-edit-order-date"
                    type="date"
                    value={orderDate}
                    onChange={(e) => setOrderDate(e.target.value)}
                    className="h-8"
                  />
                ) : (
                  fmtDate(so.order_date)
                )}
              </Field>
              {/* `requested_delivery_date`, labelled the way the Lines tab labels the same
                  fact per line: what the customer is waiting for, not an internal request. */}
              <Field
                label="Delivery date"
                htmlFor={isEditing ? 'so-edit-requested-date' : undefined}
              >
                {isEditing ? (
                  <Input
                    id="so-edit-requested-date"
                    type="date"
                    value={requestedDate}
                    onChange={(e) => setRequestedDate(e.target.value)}
                    className="h-8"
                  />
                ) : so.requested_delivery_date ? (
                  fmtDate(so.requested_delivery_date)
                ) : (
                  '-'
                )}
              </Field>
              {/* Who sold it - the sales_agents master, resolved by the backend. Read as the
                  code with the person it has been annotated to, when known; edited as a
                  clearable select, since not every order names an agent. */}
              <Field label="Agent" htmlFor={isEditing ? 'so-edit-agent' : undefined}>
                {isEditing ? (
                  <SearchableSelect
                    id="so-edit-agent"
                    value={agentId}
                    onChange={setAgentId}
                    options={agentOptions.options}
                    placeholder="No agent"
                    clearable
                    size="sm"
                  />
                ) : so.sales_agent_code ? (
                  <span title={so.sales_agent_label ?? undefined}>
                    {so.sales_agent_code}
                    {so.sales_agent_label ? (
                      <span className="ms-1 font-normal text-muted-foreground">
                        · {so.sales_agent_label}
                      </span>
                    ) : null}
                  </span>
                ) : (
                  '-'
                )}
              </Field>
              <Field label="Source">{SOURCE_LABELS[so.source ?? 'manual'] ?? 'Manual'}</Field>
              {/* AutoCount's Transferable flag (SO-TRANSFERABLE). Read-only in view AND edit:
                  AutoCount owns it, and the order edit has no input for it. */}
              <Field label="Transferable">
                {(() => {
                  const flag = transferableBadge(so.is_transferable);
                  return (
                    <span className="inline-flex flex-wrap items-center gap-1.5">
                      <Badge variant={flag.variant} appearance="light" size="md">
                        {flag.label}
                      </Badge>
                      {/* `text-2xs`: a value hint, not a Field label (see Order type). Only on
                          an AutoCount order: an Excel / manual / inquiry order never had the
                          flag stated by AutoCount, so the hint would be untrue there. */}
                      {so.source === 'autocount' ? (
                        <span className="text-2xs font-normal text-muted-foreground">
                          From AutoCount
                        </span>
                      ) : null}
                    </span>
                  );
                })()}
              </Field>
              {/* Resolved from whichever of the four rules ranked highest - the sheet, the
                  note, the AutoCount `Ref`, or a delivery address. Read-only: correcting one
                  is a later slice (rank 5, manual edit), not this one. */}
              <Field label="Project">
                {so.project_label ? (
                  <span title={so.project_label}>
                    {so.project_label}
                    {so.project_label_source ? (
                      <span className="ms-1 font-normal text-muted-foreground">
                        · {PROJECT_LABEL_SOURCE_WORDS[so.project_label_source] ?? so.project_label_source}
                      </span>
                    ) : null}
                  </span>
                ) : (
                  '-'
                )}
              </Field>
              {/* What purchasing has been told to do about this order. Read-only in both
                  views, so nothing moves between them: it is a record of what happened,
                  not a field anybody sets here. Empty states as "-", never hidden. */}
              <Field label="Order inquiries">
                {(so.order_inquiries ?? []).length ? (
                  <span className="flex flex-col gap-1">
                    {(so.order_inquiries ?? []).map((inquiry, index) => (
                      <span key={inquiry.inquiry_no ?? index} className="block">
                        <Link
                          // S3, AC-LK-01: the OI detail page, by header id, when the
                          // payload carries one; the old filtered-list search only for
                          // a payload from before that column existed.
                          href={
                            inquiry.id
                              ? `/project-sales/order-inquiries/${inquiry.id}`
                              : `/project-sales/order-inquiries?query=${encodeURIComponent(
                                  so.so_number,
                                )}`
                          }
                          className="text-primary hover:underline"
                          title={`${inquiry.rows_placed}/${inquiry.rows_total} placed`}
                        >
                          {inquiry.inquiry_no ?? 'Unnumbered'}
                        </Link>
                        {/* Who raised it and when, ON the header rather than in a
                            tooltip (AC-H2): "who pushed this to purchasing" is the
                            first thing asked of this field, and a fact nobody hovers
                            over is a fact nobody has. Malaysian wall clock, with the
                            hour - two revisions of one order are raised hours apart. */}
                        <span className="ms-1 font-normal text-muted-foreground">
                          · {inquiry.raised_by_name ?? 'Not recorded'}
                          {inquiry.raised_at
                            ? ` · ${formatDateTimeInMalaysia(inquiry.raised_at)}`
                            : ''}
                        </span>
                      </span>
                    ))}
                  </span>
                ) : (
                  '-'
                )}
              </Field>
            </section>
          </Card>

          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Customer</CardTitle>
              </CardHeading>
            </CardHeader>
            <section
              aria-label="Customer"
              className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2"
            >
              <Field label="Customer" htmlFor={isEditing ? 'so-edit-customer' : undefined}>
                {isEditing ? (
                  // SERVER-SEARCHED. The static list pulls the whole debtor master (6,397
                  // rows) for the browser to filter, which is why this select took seconds
                  // to open. `selectedOption` keeps the order's OWN customer readable when
                  // it is not on the page that came back.
                  <SearchableSelect
                    id="so-edit-customer"
                    value={customerCode}
                    onChange={setCustomerCode}
                    paginated
                    pageSize={SELECT_PAGE_SIZE}
                    fetchOptions={searchCustomerOptions}
                    selectedOption={
                      customerCode && customerCode === so.customer_code
                        ? { value: so.customer_code, label: so.customer_name || so.customer_code }
                        : undefined
                    }
                    placeholder="Select customer"
                    emptyMessage="No customer found."
                    size="sm"
                  />
                ) : (
                  so.customer_name || '-'
                )}
              </Field>
              <Field label="Customer code">{so.customer_code || '-'}</Field>
            </section>
          </Card>

          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Totals</CardTitle>
              </CardHeading>
            </CardHeader>
            {/* Named as a region so a reader (and a test) can tell this "Outstanding qty" from
                the lines grid's column of the same name. They share the phrase on purpose: it
                is the same quantity, once for the order and once per line. No Locations field:
                a location belongs to a LINE - one order routinely ships from two - and the
                Lines tab carries it per row. */}
            <section
              aria-label="Totals"
              className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2"
            >
              {/* What the order is worth, summed on the backend from the same line figures
                  the Lines tab prints. A dash, never RM 0.00, for an order nobody priced. */}
              <Field label="Total amount">
                {so.total_amount ? formatMyrExact(so.total_amount) : '-'}
              </Field>
              <Field label="Total qty">{fmtInt(so.total_qty)}</Field>
              {/* ALWAYS shown, even when it equals the total. It used to appear only when
                  the two differed, on the grounds that a repeated figure is noise - but a
                  field that comes and goes is worse: on a wholly open order the reader has
                  to work out from its ABSENCE that nothing has shipped, and a section that
                  hides on some records teaches nobody where anything lives. */}
              <Field label="Outstanding qty">{fmtInt(so.committed_qty)}</Field>
              <Field label="Lines">{fmtInt(lineCount)}</Field>
            </section>
          </Card>

          {/* Note - always rendered, because a blank panel says "there is no note" where a
              missing panel says nothing at all. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Note</CardTitle>
              </CardHeading>
            </CardHeader>
            <div className="p-4">
              {so.internal_note ? (
                <p className="min-w-0 text-sm whitespace-pre-wrap break-words">
                  {so.internal_note}
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No note. Absorbed and imported orders keep the customer name and code here
                  when the customer could not be matched.
                </p>
              )}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="lines" className="mt-0 focus-visible:outline-none">
          {/* Lines - always rendered, explicit empty state. */}
          <DataGrid
            table={table}
            recordCount={visibleLineCount}
            isLoading={false}
            tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
            emptyMessage={
              lineSearch.trim()
                ? 'No line on this order matches that product.'
                : 'This sales order has no lines.'
            }
            // A real key, not the empty "do not persist" one: a 200-line order is read with
            // the same few columns every time, and the choice has to survive the visit. Keyed
            // off the permission slug plus a stable id, never the record's own path.
            // -v2 (F4, review round): R4 moved `linked_to` earlier in the column order, but a
            // saved preference keeps whatever POSITION a user last dragged it to - so anyone
            // who had already used this screen kept the OLD 13th-of-16 slot regardless of the
            // code change (AC-K1 failed on a live account for exactly this reason). A fresh
            // key means everyone reads R4's own order once, same as a first-time visitor.
            listingKey="scm.dashboard.view::sales-order-lines-v2"
            rowAttributes={lineDeepLink.rowAttributes}
            rowClassName={lineDeepLink.rowClassName}
          >
            <Card>
              <CardHeader className="flex-wrap gap-3">
                <CardHeading>
                  <CardTitle>Order lines</CardTitle>
                </CardHeading>
                <CardToolbar className="flex-wrap">
                  {/* Only inside an edit session: outside one there is nothing to add a line
                      TO that would ever be saved. */}
                  {isEditing ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="gap-1.5"
                      onClick={handleAddLine}
                    >
                      <Plus className="size-4" aria-hidden />
                      Add line
                    </Button>
                  ) : null}
                  {/* The order is the unit here, so the search is over the lines already
                      loaded - no request, no paging, and it answers "is this item on this
                      order" on a 200-line contract. */}
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
                  page sizes. A 200-line contract had neither, so the only way to know how
                  many lines it held was to scroll to the bottom and count. */}
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </Card>
          </DataGrid>
        </TabsContent>

        <TabsContent value="delivery" className="mt-0 focus-visible:outline-none">
          {/* Delivery - always rendered, empty state when nothing has shipped. The counterpart
              of the purchase-order screen's goods receipt. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Delivery</CardTitle>
              </CardHeading>
            </CardHeader>
            <div className="p-4">
              {so.total_qty > 0 && so.committed_qty === 0 ? (
                <div className="flex items-center gap-2 text-sm">
                  <Truck className="size-4 text-scm-incoming" />
                  <span className="font-medium">{fmtInt(so.total_qty)}</span>
                  <span className="text-muted-foreground">delivered in full</span>
                </div>
              ) : so.committed_qty > 0 && so.committed_qty < so.total_qty ? (
                <p className="text-sm text-muted-foreground">
                  {fmtInt(so.total_qty - so.committed_qty)} of {fmtInt(so.total_qty)} delivered.{' '}
                  {fmtInt(so.committed_qty)} outstanding across {fmtInt(so.open_line_count ?? 0)}{' '}
                  {(so.open_line_count ?? 0) === 1 ? 'line' : 'lines'}.
                </p>
              ) : (
                // No CTA to a Create DO button any more: the list no longer carries one, and
                // pointing at a control that is not there is worse than saying nothing.
                <p className="text-sm text-muted-foreground">
                  Nothing delivered yet. Deliveries recorded against this order show here.
                </p>
              )}
            </div>
          </Card>
        </TabsContent>

        {/* AC-E6: the movements this order's supply decision asked for. The SAME grid the
            Transfers page is, pinned to this order - so a transfer cannot read one way here
            and another way there. Always rendered; it carries its own empty state. */}
        <TabsContent value="transfers" className="mt-0 focus-visible:outline-none">
          <StockTransfersPanel
            salesOrderId={so.id}
            listingKey="scm.sales_orders.view::stock-transfers"
            showFilters={false}
          />
        </TabsContent>
      </Tabs>

      {/* R5: the "Linked" figure's lightbox - one dialog for the whole grid, the shell every
          SCM screen's drillable figure opens (AC-L4). */}
      {linksLine ? (
        <PlanRowDialog
          kind="links"
          productCode={linksLine.sku}
          productName={linksLine.product_name}
          open
          onOpenChange={(next) => {
            if (!next) setLinksLineId(null);
          }}
        >
          <SoLineLinksBody links={linksLine.linked_to ?? []} />
        </PlanRowDialog>
      ) : null}
    </div>
  );
}

export default SalesOrderDetail;
