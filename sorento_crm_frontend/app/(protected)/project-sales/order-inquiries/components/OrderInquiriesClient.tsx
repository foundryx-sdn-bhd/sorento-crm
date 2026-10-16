'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  PaginationState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import {
  AlertTriangle,
  Ban,
  Download,
  LayoutGrid,
  Link2,
  List,
  PackageSearch,
  RotateCcw,
  Undo2,
  Unlink,
  Upload,
  Wand2,
  } from 'lucide-react';
import { toast } from '@/lib/toast';
import {
  Alert,
  AlertContent,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTable,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateInMalaysia } from '@/lib/helpers';
import { AutoLinkOrderInquiryDialog } from '../../_shared/components/AutoLinkOrderInquiryDialog';
import { BulkRejectOrderInquiryDialog } from '../../_shared/components/BulkRejectOrderInquiryDialog';
import { LinkDocumentDialog } from '../../_shared/components/LinkDocumentDialog';
import { STATE_LABEL } from '../../_shared/components/OrderInquiryVerbPill';
import { UnlinkAllOrderInquiryDialog } from '../../_shared/components/UnlinkAllOrderInquiryDialog';
import { OutstandingUploadDialog } from '../../../scm/reorder/components/OutstandingUploadDialog';
import {
  useCommitReserveAcrossInquiries,
  useOrderInquiryHandshake,
  useOrderInquiryMatrix,
  useOrderInquiryWorklist,
  useOrderInquiryWorklistSummary,
  useUnplaceAllPreview,
  useUploadedBook,
  type InquiryCommitResult,
} from '../../_shared/hooks/useOrderInquiry';
import { useParkedSendActions } from '../../_shared/hooks/useParkedSendActions';
import { useReserveRowOptions } from '../../_shared/hooks/useReserveRowOptions';
import {
  ORDER_INQUIRY_RESERVE_STATUSES,
  orderInquiryReserveStatusLabel,
  type OrderInquiryReserveStatus,
} from '../../_shared/lib/orderInquiryHeaderStatus';
import { bareLocationCode } from '../../_shared/lib/orderInquiryReserve';
import type { CommitReservePayload } from '../../_shared/services/orderInquiryReserveService';
import { BulkRejectReserveDialog } from '../[id]/components/BulkRejectReserveDialog';
import { ReserveConfirmDialog } from '../[id]/components/ReserveConfirmDialog';
import type { ReserveConfirmInquiry } from '../[id]/components/ReserveConfirmDialog';
import { ReserveDecideControl } from '../[id]/components/ReserveDecideControl';
import { ReserveLineForm } from '../[id]/components/ReserveLineForm';
import type { StagedReserveEntry } from '../[id]/components/orderInquiryHeaderLinesColumns';
import {
  ACK_ANY,
  ACK_FILTER_OPTIONS,
  ackStateOf,
  isBulkRejectable,
} from '../../_shared/lib/orderInquiryAck';
import {
  NO_LINK_HORIZON,
  initialLinkHorizon,
  linkHorizonRequest,
  linkOutcomeText,
  readStoredLinkHorizon,
  readUrlLinkHorizon,
  startsCleared,
  storeLinkHorizon,
} from '../../_shared/lib/linkHorizon';
import { facetSegments } from '../../_shared/lib/orderInquiryKinds';
import type { OrderInquiryKind } from '../../_shared/lib/orderInquiryKinds';
import { buildOrderInquiryMatrix } from '../../_shared/lib/orderInquiryMatrix';
import { deliveryMonthLabel } from '../../_shared/lib/orderInquiryWorklist';
import {
  autoPlaceOrderInquiryRows,
  exportOrderInquiryWorklistXlsx,
  unplaceOrderInquiryRow,
} from '../../_shared/services/orderInquiryService';
import type {
  OrderInquiryMatrixAxis,
  OrderInquiryMatrixCell,
  OrderInquiryMatrixGranularity,
  OrderInquiryMatrixParams,
  OrderInquiryWorklistParams,
  OrderInquiryWorklistRow,
} from '../../_shared/types/orderInquiry.types';
import { OrderInquiryMatrixCellDrilldown } from './OrderInquiryMatrixCellDrilldown';
import { OrderInquiryMonthStrip } from './OrderInquiryMonthStrip';
import { OrderInquiryScheduleMatrix } from './OrderInquiryScheduleMatrix';
import { OrderInquiryStrip } from './OrderInquiryStrip';
import {
  DEFAULT_HIDDEN_COLUMNS,
  useOrderInquiryWorklistColumns,
} from './orderInquiryWorklistColumns';
import { PageHeader } from '@/components/common/PageHeader';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useListingViewPreferences } from '@/lib/listing-column-preferences/useListingViewPreferences';
import { SupplyKindCard } from '../../_shared/components/SupplyKindCard';

function reserveFilterFrom(value: string | null): OrderInquiryReserveStatus | '' {
  return (ORDER_INQUIRY_RESERVE_STATUSES as string[]).includes(value ?? '')
    ? (value as OrderInquiryReserveStatus)
    : '';
}

/** OI-BULK-RESERVE v4: one drafted answer and the line it is for. The row is kept with the
 * draft because the confirmation groups by order inquiry and a draft outlives its page. */
interface ReserveDraft {
  row: OrderInquiryWorklistRow;
  entry: StagedReserveEntry;
}

/** "Link selected (2 of 3)", or "Link selected (3)" when every ticked row is eligible
 * (S4, AC-T2): the count is never "of n" when a === n, which would say the obvious. */
function countLabel(base: string, eligible: number, ticked: number): string {
  return eligible === ticked ? `${base} (${ticked})` : `${base} (${eligible} of ${ticked})`;
}

/**
 * WHETHER anything in either book covers the row, and out of which one (AC-D15). The
 * State column and its filter went with the drafts (item 11): what a buyer wants to know
 * is whether a document was FOUND, which this column already answers, so a second
 * vocabulary beside it was one thing too many to read. The stored values are untouched.
 */
const LINKED_OPTIONS = [
  { value: 'po', label: 'Found: a purchase order' },
  { value: 'spo', label: 'Found: an SPO' },
  { value: 'none', label: 'Not found' },
];

type OrderInquiryView = 'list' | 'schedule';

/** Persisted in the URL as `?view=schedule`. List is the default the page shipped as. */
function viewFrom(value: string | null): OrderInquiryView {
  return value === 'schedule' ? 'schedule' : 'list';
}

/** The vertical axis the captain named, in the order they named it (rework of D1). */
const MATRIX_AXIS_OPTIONS = [
  { value: 'product', label: 'Product' },
  { value: 'sales_order', label: 'Sales order' },
  { value: 'customer', label: 'Customer' },
  { value: 'agent', label: 'Agent' },
];

const MATRIX_AXES: OrderInquiryMatrixAxis[] = [
  'product',
  'sales_order',
  'customer',
  'agent',
];

function matrixAxisFrom(value: string | null): OrderInquiryMatrixAxis {
  return MATRIX_AXES.includes(value as OrderInquiryMatrixAxis)
    ? (value as OrderInquiryMatrixAxis)
    : 'product';
}

const MATRIX_GRANULARITY_OPTIONS = [
  { value: 'day', label: 'By day' },
  { value: 'week', label: 'By week' },
  { value: 'month', label: 'By month' },
  { value: 'year', label: 'By year' },
];

const MATRIX_GRANULARITIES: OrderInquiryMatrixGranularity[] = [
  'day',
  'week',
  'month',
  'year',
];

function matrixGranularityFrom(
  value: string | null,
): OrderInquiryMatrixGranularity {
  return MATRIX_GRANULARITIES.includes(value as OrderInquiryMatrixGranularity)
    ? (value as OrderInquiryMatrixGranularity)
    : 'week';
}

/**
 * The SO month filter's own options (S1, R-K): a 24-month window, this month centred,
 * since the backend answers no facet for it yet (unlike delivery month, which is read
 * off the rows that actually exist). `deliveryMonthLabel` reads the same `YYYY-MM` shape
 * the sheet tabs do, so this filter and the month strip spell a month identically.
 */
function soMonthOptions(): { value: string; label: string }[] {
  const now = new Date();
  const options: { value: string; label: string }[] = [];
  for (let offset = -12; offset < 12; offset += 1) {
    const date = new Date(now.getFullYear(), now.getMonth() + offset, 1);
    const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
    options.push({ value, label: deliveryMonthLabel(value) ?? value });
  }
  return options;
}

/** Same slug the backend gates `unplace-all(-preview)` and `mark`/`auto-place` on. */
const ORDER_INQUIRY_ACTION_PERMISSION = 'projects.order_inquiry.action';

/**
 * The handshake's own grant (`PLAN-scm-oi-handshake.md`): Acknowledge, Reject, Link now
 * and the book uploads. CS holds the read and sees the column and the filter; taking an
 * instruction on is purchasing's, and they may not do it for themselves.
 */
const ORDER_INQUIRY_ACKNOWLEDGE_PERMISSION =
  'projects.order_inquiries.acknowledge';
/** R1 (`PLAN-oi-request-cs-reserve.md`): only the CS head answers a reserve request. */
const ORDER_INQUIRY_RESERVE_PERMISSION = 'projects.order_inquiries.reserve';

/**
 * An absent `?ack=` now opens on To confirm (PLAN-oi-confirm-per-so, AC-CF-11: G4/G5
 * reversed - a row is born `awaiting` again, so purchasing has a work queue to open on).
 * `null` is left unresolved here on purpose: the caller does not yet know whether this
 * browser has a REMEMBERED view (`useListingViewPreferences`) to fall back to before the
 * hardcoded default applies, so the one-time memory-seed effect below is what actually
 * turns an absent param into `to_confirm`. `ack=all` travels as a real value, never
 * emptiness, so a reload cannot mistake a deliberate "show everything" for "nobody has
 * chosen" and put To confirm back over it.
 */
function ackFilterFrom(value: string | null): string {
  return value ?? '';
}

/** The same key `DataGrid` already keys column preferences off (line ~1445 below) - one
 * row, one listing, for columns AND for the remembered sort/filters (S6). */
const ORDER_INQUIRY_LISTING_KEY = 'projects.projects.view::order-inquiry-worklist';

/** What this page remembers per user (S6, AC-CF-17/18): every filter except page and
 * search text, which stay session-only (AC-CF-19) - matching the shape the DataGrid's
 * own sort/filter memory already uses on Stock Inquiries and Sales Orders.
 * BUMP `filtersVersion` on the hook call below if this shape changes (AC-B4). */
type OrderInquiryViewFilters = {
  ack?: string;
  view?: OrderInquiryView;
  granularity?: OrderInquiryMatrixGranularity;
  delivery_month?: string;
  location?: string;
  agent?: string;
  so_month?: string;
  po_number?: string;
  spo_number?: string;
  supplier_id?: string;
  // S5 (`PLAN-oi-project-label-from-so.md` section 5): text, exact match on the Project
  // column - the filter follows the column now, not a registered project's id. A blob
  // stored before this shipped may still carry the old `project_id` key; nothing here
  // reads it any more; a fresh save never writes it back.
  project?: string;
  raised_date?: string;
  raised_by?: string;
  linked?: 'po' | 'spo' | 'none';
  kind?: OrderInquiryKind;
};

/**
 * Purchasing's own order inquiry, across every project and every adopted sales order.
 *
 * The per-project screen answers "what did this project raise". This one answers "what do
 * I still have to buy", which is a different job with a different owner - and the rows an
 * ADOPTED AutoCount order raises belong to no project at all, so before this page existed
 * they were reachable only from the one sales order that raised them.
 *
 * Two ways to read the same worklist (D1, reworked - the captain: "vertically I can see by
 * product, by sales order, by customer, by agent etc, then horizontally is the dates, then
 * of course I can view by date, by month, by year"):
 * - List: their own spreadsheet's columns, their order, unpaged filters including a
 *     delivery-month select. Everything the page has always been.
 * - Schedule: a 2D matrix like the fulfilment planning board's - rows by product, sales
 *     order, customer or agent, columns by day, week, month or year, built entirely off
 *     the same filtered worklist rows the list already fetches (one request, grouped in
 *     the browser). Clicking a cell narrows the SAME columns below it to that cell's own
 *     rows - reusing the list's own columns rather than a second set invented for it.
 * Both views read whatever state/supplier/project/query/raised-date filters are already
 * set; only the List view carries the toolbar that sets them, so there is one filter UI
 * rather than two that could disagree.
 *
 * Nothing is authored here. A row is derived when CS confirms supply, which is the only
 * moment the instruction is true, so there is no Add button and there never should be.
 */
export function OrderInquiriesClient({
  extraHeaderActions,
}: {
  /**
   * `PLAN-oi-header-list-detail.md`, AC-HL-01: the Documents | Lines toggle, rendered by
   * the page-level view switch (`OrderInquiryHeadersOrLinesView.tsx`) BESIDE this
   * screen's own List | Schedule toggle rather than under a second `PageHeader`. One
   * additive, optional slot - a caller that omits it renders exactly as before, and
   * nothing else about this component changes.
   */
  extraHeaderActions?: React.ReactNode;
} = {}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const canActOnOrderInquiry = useHasPermission(
    ORDER_INQUIRY_ACTION_PERMISSION,
  );
  const canAcknowledge = useHasPermission(ORDER_INQUIRY_ACKNOWLEDGE_PERMISSION);
  const canReserve = useHasPermission(ORDER_INQUIRY_RESERVE_PERMISSION);
  const { linkNow, acknowledge, unacknowledge } = useOrderInquiryHandshake();
  const [unlinkingSelected, setUnlinkingSelected] = React.useState(false);
  const [linkingSelected, setLinkingSelected] = React.useState(false);

  // S6: the sort and every filter this listing remembers, per user, off the SAME row the
  // columns are already keyed by. `filters` here is the STORED blob only - what this
  // render should actually show still lives in the plain `useState`s below, seeded from
  // it once (the memory-seed effect, after the individual filters), because the URL has
  // to win over the memory for a visit that names one (AC-CF-20) and half of these
  // filters are ALSO URL-synced for sharing, which an opaque blob cannot drive directly.
  const {
    sorting,
    setSorting,
    filters: viewFilters,
    setFilters: setViewFilters,
    isLoading: isViewPrefsLoading,
  } = useListingViewPreferences<OrderInquiryViewFilters>({
    listingKey: ORDER_INQUIRY_LISTING_KEY,
    defaultSorting: [{ id: 'delivery_date', desc: false }],
    filtersVersion: 1,
  });
  // Captured ONCE, at mount, for exactly the filters that also travel in the URL: whether
  // THIS visit named one, which is what "a URL param present on arrival wins" (AC-CF-20)
  // has to test against - reading `searchParams` again after the sync effect below has
  // started rewriting it would always say yes.
  const urlProvidedFilters = React.useRef({
    ack: searchParams.get('ack') !== null,
    view: searchParams.get('view') !== null,
    granularity: searchParams.get('granularity') !== null,
    delivery_month: searchParams.get('delivery_month') !== null,
    location: searchParams.get('location') !== null,
    agent: searchParams.get('agent') !== null,
    so_month: searchParams.get('so_month') !== null,
    po_number: searchParams.get('po_number') !== null,
    spo_number: searchParams.get('spo_number') !== null,
  });

  const [view, setView] = React.useState<OrderInquiryView>(() =>
    viewFrom(searchParams.get('view')),
  );
  // Sourced from `?query=` on mount (captain: the demand drill's click-through -
  // `orderInquiryWorklistHref` - lands here with an SO number already in the URL), and
  // kept URL-synced the same way as `view`/`rows`/`granularity` below, so a link to a
  // filtered worklist is shareable. `useDebouncedSearch` seeds both halves from the same
  // value so the deep link filters on first render rather than after a flash of "every row".
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
  } = useDebouncedSearch(searchParams.get('query') ?? '');
  // S2, AC-M2: the month tab strip's own selection, URL-synced like `view`/`query` -
  // reading `?delivery_month=` on mount so a reload or a shared link keeps the tab.
  const [month, setMonth] = React.useState(
    () => searchParams.get('delivery_month') ?? '',
  );
  const [supplierFilter, setSupplierFilter] = React.useState('');
  const [projectFilter, setProjectFilter] = React.useState('');
  const [raisedDate, setRaisedDate] = React.useState('');
  const [raisedByFilter, setRaisedByFilter] = React.useState('');
  const [linkedFilter, setLinkedFilter] = React.useState('');
  // S1, R-K: Location, Agent, SO month, PO number, SPO number. The last two are text -
  // a buyer types the number they already hold, never picks it from a list - so they
  // get the same debounce the search box does rather than filtering on every keystroke.
  //
  // All five are seeded from the URL and written back to it, exactly as `query` and
  // `delivery_month` beside them are (AC-F1): a filter that reaches the request and the
  // Filters badge but not the address bar is one a reload silently drops, and one the
  // buyer cannot send to anybody.
  const [locationFilter, setLocationFilter] = React.useState(
    () => searchParams.get('location') ?? '',
  );
  const [agentFilter, setAgentFilter] = React.useState(
    () => searchParams.get('agent') ?? '',
  );
  const [soMonthFilter, setSoMonthFilter] = React.useState(
    () => searchParams.get('so_month') ?? '',
  );
  // AC-OH-61 (R2): the row's own state (raised / partly_linked / actioned / cancelled /
  // placed) - a different question from `ack`/`linked`/`kind` above, and the one field
  // that can actually reach `state=cancelled`, since the list hides cancelled by
  // default otherwise (S5). URL-seeded and synced the same way as its siblings.
  const [stateFilter, setStateFilter] = React.useState(
    () => searchParams.get('state') ?? '',
  );
  // OI-BULK-RESERVE v4 (F1): a line's own reserve status, the Documents view's four
  // statuses. URL-seeded and synced like `state`.
  const [reserveFilter, setReserveFilter] = React.useState<OrderInquiryReserveStatus | ''>(
    () => reserveFilterFrom(searchParams.get('reserve')),
  );
  const {
    value: poNumberInput,
    setValue: setPoNumberInput,
    debouncedValue: poNumberFilter,
    reset: resetPoNumberInput,
  } = useDebouncedSearch(searchParams.get('po_number') ?? '');
  const {
    value: spoNumberInput,
    setValue: setSpoNumberInput,
    debouncedValue: spoNumberFilter,
    reset: resetSpoNumberInput,
  } = useDebouncedSearch(searchParams.get('spo_number') ?? '');
  const soMonthChoices = React.useMemo(() => soMonthOptions(), []);
  // Sourced from `?ack=` on mount and kept URL-synced, like `view` and `query`: the plan
  // page's "N to confirm" chip links straight into this list narrowed to them, and a chip
  // that landed on an unfiltered list would leave the buyer to find them. With NO `?ack=`
  // at all the page opens on its own default (AC-D12).
  const [ackFilter, setAckFilter] = React.useState(() =>
    ackFilterFrom(searchParams.get('ack')),
  );
  // How far out the three presses link (AC-LH1/AC-LH5). Sourced from the URL on mount -
  // `?link_up_to=` for a date and `?link_horizon=none` for a cleared box - then from this
  // browser's own memory, and seeded from the reorder plan's coverage date once the
  // summary answers. The URL first, because a shared link is the buyer telling somebody
  // else which horizon to look at. `seededHorizon` is what stops that seeding from
  // overwriting a date the buyer has since cleared on purpose.
  const storedHorizon = React.useRef(readStoredLinkHorizon());
  const urlHorizon = React.useRef(readUrlLinkHorizon(searchParams));
  const [linkUpTo, setLinkUpTo] = React.useState(() =>
    initialLinkHorizon(urlHorizon.current, storedHorizon.current, null),
  );
  // The buyer took the horizon OFF, as opposed to never having set one (S1). The two used
  // to be the same empty box: an empty date sent nothing, the server read that as "the
  // caller named none" and used the plan's own, so once a plan run named a horizon this
  // page could not link a far-future row at all. Held apart here, remembered per browser,
  // carried in the URL as `link_horizon=none`, and stated on the wire the same way.
  const [horizonCleared, setHorizonCleared] = React.useState(() =>
    startsCleared(urlHorizon.current, storedHorizon.current),
  );
  const seededHorizon = React.useRef(false);
  // What every press says about the horizon - one fragment, four callers, so Acknowledge,
  // Link selected, Link now and Auto-link can never mean different things by the same box.
  const horizonRequest = React.useMemo(
    () => linkHorizonRequest(linkUpTo, horizonCleared),
    [linkUpTo, horizonCleared],
  );
  // Which rows are ticked for the bulk Acknowledge (AC-H2). react-table's own selection
  // state through `buildSelectColumn`, never a hand-rolled Set: the canonical toolbar
  // reads exactly this for its bulk strip.
  const [rowSelection, setRowSelection] = React.useState<
    Record<string, boolean>
  >({});
  // The last upload this page queued, by job id, so the two next steps can be offered when
  // the WORKER is done with it rather than when the request was accepted (AC-H13). Null
  // until somebody uploads a book here.
  const [uploadJobId, setUploadJobId] = React.useState<string | null>(null);
  // Which card is pressed (AC-I11). Not one of the toolbar's filters: it lives on the
  // strip above BOTH views, so the same press narrows the matrix and the list. It IS
  // sent with the summary's own request all the same - only the `kinds` facet inside it
  // drops the card, server-side, which is what keeps the other two cards readable while
  // one is held down (see `listFilters` below).
  const [kindFilter, setKindFilter] = React.useState<OrderInquiryKind | null>(
    null,
  );
  const [exporting, setExporting] = React.useState(false);
  const [autoPlacing, setAutoPlacing] = React.useState(false);
  const [unplacingAll, setUnplacingAll] = React.useState(false);
  // Unlink selected asks first, like every other detach on this page (the ADR's own
  // "confirm before every destructive OR detach action"): it takes the documents off
  // several rows at once, and the quantities go back to demand with nothing to undo it.
  const [unlinkingSelectedOpen, setUnlinkingSelectedOpen] =
    React.useState(false);
  const [rejectingSelected, setRejectingSelected] = React.useState(false);
  // The ONE ticked row the manual Link dialog is about (R8). Held by id rather than by
  // the row, so a refetch between the press and the dialog cannot hand it a stale copy.
  const [linkingRowId, setLinkingRowId] = React.useState<string | null>(null);
  const [uploadingBook, setUploadingBook] = React.useState(false);
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  // `sorting` itself comes from `useListingViewPreferences` above (S6, AC-CF-17) - it was
  // never URL-synced, so remembering it per user is a straight swap with no URL side.
  const [selectAllMatchingActive, setSelectAllMatchingActive] = React.useState(false);
  const [confirmingOpen, setConfirmingOpen] = React.useState(false);
  const [matrixAxis, setMatrixAxis] = React.useState<OrderInquiryMatrixAxis>(
    () => matrixAxisFrom(searchParams.get('rows')),
  );
  const [matrixGranularity, setMatrixGranularity] =
    React.useState<OrderInquiryMatrixGranularity>(() =>
      matrixGranularityFrom(searchParams.get('granularity')),
    );
  const [openCell, setOpenCell] = React.useState<OrderInquiryMatrixCell | null>(
    null,
  );

  // The queued book, watched through the drawer's own feed, and what it wrote once the
  // worker is done with it (AC-H13). Nothing is offered before `landed`: linking against
  // a book still being read links the half of it that exists.
  const uploadedBook = useUploadedBook(uploadJobId);
  const uploadLanded = uploadedBook.landed;
  const uploadFailed = uploadedBook.failed;
  const uploadedProducts = uploadedBook.scope?.product_ids ?? [];
  // The purchase orders THIS upload wrote, when it can name them - the list filters on
  // exactly those numbers. A book naming more than the endpoint lists (or none at all, as
  // a failed read does) sends the buyer to the unfiltered list rather than to a filter
  // that would quietly show fifty of two hundred documents as if they were all of them.
  const uploadedDocuments = uploadedBook.scope?.documents ?? [];
  const purchaseOrdersHref =
    uploadedDocuments.length > 0 &&
    uploadedDocuments.length === (uploadedBook.scope?.document_count ?? 0)
      ? `/scm/purchase-orders?documents=${encodeURIComponent(uploadedDocuments.join(','))}`
      : '/scm/purchase-orders';

  // `view`, `rows`, `granularity` and `query` travel in the URL, so a link to the Schedule
  // view or a filtered search is shareable. `replace`, not `push`: turning a dial (or
  // typing a search) is not a place in history to go back to.
  React.useEffect(() => {
    const next = new URLSearchParams(searchParams.toString());
    if (view === 'list') next.delete('view');
    else next.set('view', view);
    if (matrixAxis === 'product') next.delete('rows');
    else next.set('rows', matrixAxis);
    if (matrixGranularity === 'week') next.delete('granularity');
    else next.set('granularity', matrixGranularity);
    if (debounced) next.set('query', debounced);
    else next.delete('query');
    // S2, AC-M2: "All" (the default) carries no param; any other tab does, so a reload
    // or a shared link opens on the same tab the buyer pressed.
    if (month) next.set('delivery_month', month);
    else next.delete('delivery_month');
    // S1, R-K (AC-F1): the Filters-popover values travel too, so a reload keeps them and
    // a shared link opens on the same narrowed list. Cleared means the parameter goes,
    // which is what "Clear filters" then says in the address bar.
    for (const [key, value] of [
      ['location', locationFilter],
      ['agent', agentFilter],
      ['so_month', soMonthFilter],
      ['po_number', poNumberFilter],
      ['spo_number', spoNumberFilter],
      // AC-OH-61.
      ['state', stateFilter],
      // OI-BULK-RESERVE v4.
      ['reserve', reserveFilter],
    ] as const) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    // AC-CF-11 (G5 reversed): To confirm - the page's own default again - carries no
    // param, the same rule `delivery_month` above follows for ITS default, so a fresh
    // visit and a reload of one read identically. `ack=all` (a deliberate "show
    // everything") and every other value travel explicit, so a reload or a shared link
    // keeps them rather than falling back to the default over the buyer's choice.
    if (ackFilter && ackFilter !== 'to_confirm') next.set('ack', ackFilter);
    else next.delete('ack');
    if (linkUpTo) next.set('link_up_to', linkUpTo);
    else next.delete('link_up_to');
    // A CLEARED horizon travels too (item 6). Dropping `link_up_to` and putting nothing in
    // its place says "nobody has chosen", which is the one thing the buyer did not say, and
    // the browser the link is shared with would open on the plan's own date instead.
    if (!linkUpTo && horizonCleared) next.set('link_horizon', NO_LINK_HORIZON);
    else next.delete('link_horizon');
    const nextQuery = next.toString();
    if (nextQuery === searchParams.toString()) return;
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, {
      scroll: false,
    });
  }, [
    view,
    matrixAxis,
    matrixGranularity,
    debounced,
    month,
    ackFilter,
    locationFilter,
    agentFilter,
    soMonthFilter,
    poNumberFilter,
    spoNumberFilter,
    stateFilter,
    reserveFilter,
    linkUpTo,
    horizonCleared,
    pathname,
    router,
    searchParams,
  ]);

  // A cell drawn from one axis/granularity is not a selection under a different one.
  React.useEffect(() => {
    setOpenCell(null);
  }, [matrixAxis, matrixGranularity]);

  // Narrowing changes which rows exist, so page 3 of the old set is a page of nothing in
  // the new one - and a "Select all N matching" taken under the OLD filters has nothing
  // to do with the new set either.
  React.useEffect(() => {
    setPagination((previous) => ({ ...previous, pageIndex: 0 }));
    setSelectAllMatchingActive(false);
  }, [
    debounced,
    month,
    supplierFilter,
    projectFilter,
    raisedDate,
    raisedByFilter,
    linkedFilter,
    ackFilter,
    kindFilter,
    locationFilter,
    agentFilter,
    soMonthFilter,
    poNumberFilter,
    spoNumberFilter,
    stateFilter,
    reserveFilter,
  ]);

  // S6 (AC-CF-20): once, when the remembered view has resolved, every filter the URL did
  // NOT name for this visit takes the remembered value (or the shipped default when
  // nothing was ever remembered either). `link_up_to`/`link_horizon` are not in this
  // list - they keep the horizon's own, older per-browser memory (`readStoredLinkHorizon`
  // above), which already does the same job for that one field.
  const memorySeededRef = React.useRef(false);
  // Mirrored into STATE as well as the ref, deliberately. The ref makes this run once;
  // the state is what everything downstream waits on, because a ref set inside this
  // effect is already `true` for the effects that run AFTER it in the very same pass -
  // which still see the pre-seed filter values. That is the whole of the AC-CF-18 defect:
  // on a re-mount the write-back below fired in this pass, wrote the empty pre-seed blob
  // over the remembered one, and the seed then read back what it had just erased.
  const [memorySeeded, setMemorySeeded] = React.useState(false);
  React.useEffect(() => {
    if (memorySeededRef.current || isViewPrefsLoading) return;
    memorySeededRef.current = true;
    setMemorySeeded(true);
    const stored = viewFilters ?? {};
    const urlHad = urlProvidedFilters.current;
    if (!urlHad.ack) setAckFilter(stored.ack ?? 'to_confirm');
    if (!urlHad.view) setView(stored.view ?? 'list');
    if (!urlHad.granularity) setMatrixGranularity(stored.granularity ?? 'week');
    if (!urlHad.delivery_month) setMonth(stored.delivery_month ?? '');
    if (!urlHad.location) setLocationFilter(stored.location ?? '');
    if (!urlHad.agent) setAgentFilter(stored.agent ?? '');
    if (!urlHad.so_month) setSoMonthFilter(stored.so_month ?? '');
    if (!urlHad.po_number) resetPoNumberInput(stored.po_number ?? '');
    if (!urlHad.spo_number) resetSpoNumberInput(stored.spo_number ?? '');
    // These six have no URL representation at all today (Measured facts) - the memory is
    // their only persistence, so it always applies.
    setSupplierFilter(stored.supplier_id ?? '');
    // A blob saved before S5 shipped may still carry the old `project_id` key - never
    // read here, so a stored one restores to no project filter rather than a stale id
    // the Project select no longer has an option for.
    setProjectFilter(stored.project ?? '');
    setRaisedDate(stored.raised_date ?? '');
    setRaisedByFilter(stored.raised_by ?? '');
    setLinkedFilter(stored.linked ?? '');
    setKindFilter(stored.kind ?? null);
  }, [isViewPrefsLoading, viewFilters, resetPoNumberInput, resetSpoNumberInput]);

  // What every request on this page waits for (AC-CF-21): not just the stored row landing,
  // but the seed above having moved it into the filters this render actually reads. The
  // hook's own `isLoading` releases one commit earlier, which used to let the first list
  // call go out with the shipped defaults and flash the whole worklist before the
  // remembered filter narrowed it a render later.
  const isViewMemoryPending = isViewPrefsLoading || !memorySeeded;

  // The other half (AC-CF-18): every change here is written back to the same remembered
  // row. Safe to run from the very first commit - `useListingViewPreferences` does not
  // persist anything until IT has applied what was already stored, so an early call here
  // only updates its in-memory value and is superseded the moment that happens.
  //
  // GUARDED on `memorySeeded` (AC-CF-18, browser pass 17 Sep): until the seed above has
  // LANDED IN A RENDER, the filters read here are this mount's blank starting state, not
  // anybody's choice. Writing them back was the measured defect - leave the page by the
  // sidebar and come back, and the return mount wrote `{ack: 'to_confirm'}` over the
  // `{ack, location}` it had just read, because this effect ran a commit before the seed
  // did. A re-mount is indistinguishable from a first visit from in here, so there is
  // nothing to test but "has this page decided what it shows yet".
  React.useEffect(() => {
    if (!memorySeeded) return;
    const blob: OrderInquiryViewFilters = {};
    if (ackFilter) blob.ack = ackFilter;
    if (view !== 'list') blob.view = view;
    if (matrixGranularity !== 'week') blob.granularity = matrixGranularity;
    if (month) blob.delivery_month = month;
    if (locationFilter) blob.location = locationFilter;
    if (agentFilter) blob.agent = agentFilter;
    if (soMonthFilter) blob.so_month = soMonthFilter;
    if (poNumberFilter) blob.po_number = poNumberFilter;
    if (spoNumberFilter) blob.spo_number = spoNumberFilter;
    if (supplierFilter) blob.supplier_id = supplierFilter;
    if (projectFilter) blob.project = projectFilter;
    if (raisedDate) blob.raised_date = raisedDate;
    if (raisedByFilter) blob.raised_by = raisedByFilter;
    if (linkedFilter) blob.linked = linkedFilter as 'po' | 'spo' | 'none';
    if (kindFilter) blob.kind = kindFilter;
    setViewFilters(Object.keys(blob).length ? blob : null);
  }, [
    memorySeeded,
    ackFilter,
    view,
    matrixGranularity,
    month,
    locationFilter,
    agentFilter,
    soMonthFilter,
    poNumberFilter,
    spoNumberFilter,
    supplierFilter,
    projectFilter,
    raisedDate,
    raisedByFilter,
    linkedFilter,
    kindFilter,
    setViewFilters,
  ]);

  const filters = React.useMemo(
    () => ({
      query: debounced || undefined,
      delivery_month: month || undefined,
      raised_date: raisedDate || undefined,
      supplier_id: supplierFilter || undefined,
      project: projectFilter || undefined,
      raised_by: raisedByFilter || undefined,
      linked: (linkedFilter || undefined) as 'po' | 'spo' | 'none' | undefined,
      // AC-CF-11: `ack=all` (the explicit "show everything") sends no filter at all;
      // anything else - including the transient '' before the memory-seed effect above
      // resolves it - reads as `to_confirm`, the page's own default. The list query is
      // gated off until that effect has run (`!isViewMemoryPending`), so `to_confirm` is
      // never actually asked for on the transient's account.
      ack: (ackFilter === ACK_ANY
        ? undefined
        : (ackFilter || 'to_confirm')) as OrderInquiryWorklistParams['ack'],
      // S1, R-K. The backend does not read these yet (Phase 1 mock contract) - forwarded
      // all the same so the page keeps working once it does, and unknown params are
      // ignored server-side in the meantime.
      location: locationFilter || undefined,
      agent: agentFilter || undefined,
      so_month: soMonthFilter || undefined,
      po_number: poNumberFilter || undefined,
      spo_number: spoNumberFilter || undefined,
      // AC-OH-61: the row's own state - the one field that can ask for `cancelled`
      // even though the list hides it by default otherwise (S5).
      state: stateFilter || undefined,
      reserve: reserveFilter || undefined,
    }),
    [
      debounced,
      month,
      raisedDate,
      supplierFilter,
      projectFilter,
      raisedByFilter,
      linkedFilter,
      ackFilter,
      locationFilter,
      agentFilter,
      soMonthFilter,
      poNumberFilter,
      spoNumberFilter,
      stateFilter,
      reserveFilter,
    ],
  );

  // Everything the screen reads honours whichever card is pressed - the rows, the
  // totals, the export. Only the `kinds` facet inside the summary drops it, and it does
  // that server-side, the same rule the month, supplier, project and raised-by controls
  // are computed by: a control that empties itself the moment you use it cannot be used
  // a second time.
  const listFilters = React.useMemo(
    () => ({ ...filters, kind: kindFilter ?? undefined }),
    [filters, kindFilter],
  );

  // "Unplace all"'s own scope (the captain, 20-21 Aug): the SAME filters as `filters`,
  // minus the ones about where the row stands - the action is always about linked rows,
  // whatever else is filtered.
  const unplaceAllFilters = React.useMemo(
    () => ({
      query: debounced || undefined,
      delivery_month: month || undefined,
      raised_date: raisedDate || undefined,
      supplier_id: supplierFilter || undefined,
      project: projectFilter || undefined,
      raised_by: raisedByFilter || undefined,
    }),
    [
      debounced,
      month,
      raisedDate,
      supplierFilter,
      projectFilter,
      raisedByFilter,
    ],
  );

  const params = React.useMemo(
    () => ({
      ...listFilters,
      page: pagination.pageIndex + 1,
      limit: pagination.pageSize,
      sort: sorting[0]?.id ?? 'delivery_date',
      dir: (sorting[0]?.desc ? 'desc' : 'asc') as 'asc' | 'desc',
    }),
    [listFilters, pagination, sorting],
  );

  // Gated on `!isViewMemoryPending` (AC-CF-21): the first fetch waits for the remembered
  // view, so it asks with the filters the buyer actually left it on rather than the
  // shipped defaults for one request and the real ones for the next.
  const list = useOrderInquiryWorklist(params, {
    enabled: view === 'list' && !isViewMemoryPending,
  });
  // Asked WITH the pressed card, so the header badges and the month / supplier / project
  // controls describe the rows actually on screen. Its `kinds` facet is the one thing
  // computed with the card dropped (server-side), which is what keeps the other two
  // cards readable while one is held down.
  const summary = useOrderInquiryWorklistSummary(listFilters, {
    enabled: !isViewMemoryPending,
  });
  const planHorizon = summary.data?.link_up_to_default ?? null;

  // The plan's own coverage date, taken ONCE and only when neither the URL nor this
  // browser already carried one (AC-LH5). Once, because after the first answer the date on
  // screen is the buyer's - re-seeding on every refetch would put it back the moment they
  // cleared it, and a control that undoes itself is one nobody uses twice.
  React.useEffect(() => {
    if (seededHorizon.current || horizonCleared || !planHorizon) return;
    seededHorizon.current = true;
    setLinkUpTo((current) => current || planHorizon);
  }, [horizonCleared, planHorizon]);

  // Remembered per browser, so the buyer states their horizon once rather than every visit
  // - INCLUDING "no horizon", which used to remove the key and so read as "never chosen"
  // on the next visit, letting the plan default seed straight back over the choice.
  React.useEffect(() => {
    storeLinkHorizon(linkUpTo || (horizonCleared ? NO_LINK_HORIZON : null));
  }, [horizonCleared, linkUpTo]);

  // The Schedule view's own request (S3): the same filters, plus the axis and the date
  // cut - a server-side GROUP BY now, not an unpaged list fetch grouped in the browser,
  // so there is no more row cap.
  const matrixParams = React.useMemo<OrderInquiryMatrixParams>(
    () => ({
      ...listFilters,
      axis: matrixAxis,
      by: matrixGranularity,
    }),
    [listFilters, matrixAxis, matrixGranularity],
  );
  const matrixQuery = useOrderInquiryMatrix(matrixParams, {
    enabled: view === 'schedule' && !isViewMemoryPending,
  });
  const matrix = React.useMemo(
    () => buildOrderInquiryMatrix(matrixQuery.data?.data ?? [], matrixGranularity),
    [matrixQuery.data, matrixGranularity],
  );

  const rows = React.useMemo(() => list.data?.data ?? [], [list.data]);
  const total = list.data?.total ?? 0;
  const months = summary.data?.by_month ?? [];
  // The default (`to_confirm`, an unfilled ack has not resolved yet either) counts as
  // filtered - the page opens narrowed on purpose (R3) - `ack=all` (a deliberate "show
  // everything") does not.
  const filtered = Boolean(
    debounced ||
    month ||
    supplierFilter ||
    projectFilter ||
    raisedDate ||
    raisedByFilter ||
    linkedFilter ||
    (ackFilter && ackFilter !== ACK_ANY) ||
    kindFilter ||
    locationFilter ||
    agentFilter ||
    soMonthFilter ||
    poNumberFilter ||
    spoNumberFilter ||
    stateFilter ||
    reserveFilter,
  );

  // S2/S3 (code review, 20 Aug 2026): what the confirm dialog names as the scope. `state`
  // is never one of these - `unplaceAllFilters` above always drops it, so the dialog must
  // never claim "the current view" (which DOES include State) as its scope; it says
  // exactly the filters that narrowed it, or "every placed row" when none did. Supplier/
  // project resolve through the SAME lists the filter selects already render, so this
  // never puts a raw id on screen.
  const activeUnplaceScopeLabels = React.useMemo(() => {
    const parts: string[] = [];
    if (debounced) parts.push(`matching "${debounced}"`);
    if (month) parts.push(`for delivery ${deliveryMonthLabel(month) ?? month}`);
    if (raisedDate) parts.push(`raised on ${formatDateInMalaysia(raisedDate)}`);
    if (supplierFilter) {
      const supplier = (summary.data?.suppliers ?? []).find(
        (s) => s.id === supplierFilter,
      );
      if (supplier) parts.push(`from ${supplier.label}`);
    }
    if (projectFilter) {
      const project = (summary.data?.projects ?? []).find(
        (p) => p.id === projectFilter,
      );
      if (project) parts.push(`for ${project.label}`);
    }
    if (raisedByFilter) {
      const person = (summary.data?.raised_by ?? []).find(
        (p) => p.id === raisedByFilter,
      );
      if (person) parts.push(`raised by ${person.label}`);
    }
    return parts;
  }, [
    debounced,
    month,
    raisedDate,
    supplierFilter,
    projectFilter,
    raisedByFilter,
    summary.data,
  ]);

  // "Unplace all" (the captain, 20-21 Aug) operates on the CURRENT worklist scope - one
  // product when the filters happen to narrow to it, every placed row when they name
  // nothing. The count comes from the server, resolved against the full matching set
  // (never just the loaded page - the worklist paginates server-side), so it is right
  // whether the scope is a single product or the whole company.
  //
  // Gated on `canActOnOrderInquiry` (N1, code review, 20 Aug 2026): the preview route is
  // ACTION-gated on the backend (it previews a write, not a browse), so a view-only
  // principal 403'd it, `count` fell back to 0, and the disabled tooltip lied - "No placed
  // rows to unplace" on a company that may hold hundreds. Held off entirely rather than
  // fired-and-403'd for a person who could never press the button anyway.
  const unplacePreview = useUnplaceAllPreview(unplaceAllFilters, {
    enabled: view === 'list' && canActOnOrderInquiry && !isViewMemoryPending,
  });
  const unplaceCount = unplacePreview.data?.count ?? 0;

  // The link presses are purchasing's: the acknowledge grant is what marks purchasing.
  // CS ticks lines too, but only to answer reserve requests (reserve grant).
  const canBulkLink = canAcknowledge && canActOnOrderInquiry;

  // OI-BULK-RESERVE v4 (F3): CS's drafted answers, keyed by line. A draft keeps its row (it
  // outlives a page change) and is lost on reload, like the OI Lines tab's own.
  const [drafts, setDrafts] = React.useState<Record<string, ReserveDraft>>({});
  const stagedByRowId = React.useMemo(
    () => Object.fromEntries(Object.entries(drafts).map(([id, draft]) => [id, draft.entry])),
    [drafts],
  );
  // `rows` = the Reject... dialog's lines: the strip's counted lines (ticks clear on Save)
  // or one line's own x (ticks untouched). Kept after close so the title does not flash.
  const [rejectTarget, setRejectTarget] = React.useState<{
    rows: OrderInquiryWorklistRow[];
    fromStrip: boolean;
  } | null>(null);
  const [reserveRejectOpen, setReserveRejectOpen] = React.useState(false);
  const [editingRow, setEditingRow] = React.useState<OrderInquiryWorklistRow | null>(null);
  const [reserveConfirmOpen, setReserveConfirmOpen] = React.useState(false);
  // After a commit the dialog keeps its rows and shows each order inquiry's result.
  const [commitOutcome, setCommitOutcome] = React.useState<{
    inquiries: ReserveConfirmInquiry[];
    results: InquiryCommitResult[];
  } | null>(null);
  const commitReserve = useCommitReserveAcrossInquiries();
  const { actions: parkedSendActions, dialog: parkedSendDialog } = useParkedSendActions();

  const stageAnswers = React.useCallback(
    (targets: OrderInquiryWorklistRow[], reason: string | null) => {
      // A row that names no order inquiry cannot be committed, so it is never drafted.
      const draftable = targets.filter((row) => row.order_inquiry_id);
      if (draftable.length === 0) return;
      setDrafts((previous) => {
        const next = { ...previous };
        for (const row of draftable) {
          next[row.id] = {
            row,
            entry: {
              kind: 'reserve',
              qty: reason == null ? Number(row.requested_qty || '0') : 0,
              warehouseId: row.reserve_warehouse_id ?? null,
              locationLabel: row.reserve_location ?? null,
              reason,
            },
          };
        }
        return next;
      });
    },
    [],
  );
  const reserveControls = React.useMemo(
    () =>
      canReserve
        ? {
            stagedByRowId,
            onTickReserve: (row: OrderInquiryWorklistRow) => stageAnswers([row], null),
            onRejectReserve: (row: OrderInquiryWorklistRow) => {
              setRejectTarget({ rows: [row], fromStrip: false });
              setReserveRejectOpen(true);
            },
            onEditReserve: (row: OrderInquiryWorklistRow) => setEditingRow(row),
            onUndoStaged: (rowId: string) =>
              setDrafts((previous) => {
                if (!(rowId in previous)) return previous;
                const next = { ...previous };
                delete next[rowId];
                return next;
              }),
          }
        : undefined,
    [canReserve, stagedByRowId, stageAnswers],
  );
  const columns = useOrderInquiryWorklistColumns({
    selectable: canAcknowledge || canReserve,
    reserveControls,
  });

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    // S6 (AC-OH-01): the Order inquiry column starts hidden. `initialState` only, not
    // `state` - a saved column preference (`useListingColumnPreferences`, driven by
    // `listingKey` below) applies afterwards via `table.setColumnVisibility` and wins.
    initialState: {
      columnVisibility: Object.fromEntries(
        DEFAULT_HIDDEN_COLUMNS.map((id) => [id, false]),
      ),
    },
    state: { pagination, sorting, rowSelection },
    // The PREDICATE lives on the table, which is where TanStack reads `getCanSelect` from -
    // a column-level `enableRowSelection` is silently ignored, and every row would tick
    // (`FulfilmentPlanningClient` carries the same note over the same trap).
    //
    // R-A (S4, PLAN-scm-oi-worklist-excel-parity.md): every row except `cancelled` ticks,
    // fully linked rows included - there is no per-row disabled checkbox any more. Most
    // actions count their OWN eligible subset off the ticked rows (`selectedLinked` /
    // `selectedRejectable` below) and say so in their own label, so a row ineligible for
    // one action can still be ticked for another in the same batch. "Link selected" is the
    // exception (R18): it acts on every ticked row, whatever its state.
    enableRowSelection: (row) => row.original.state !== 'cancelled',
    onRowSelectionChange: setRowSelection,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    pageCount: Math.max(1, Math.ceil(total / pagination.pageSize)),
    manualPagination: true,
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
  });

  const selectedRows = table
    .getSelectedRowModel()
    .rows.map((row) => row.original);
  // Decide (N) counts the ticked lines that still need a reserve.
  const decideRows = selectedRows.filter((row) => row.reserve_state === 'requested');
  const draftCount = Object.keys(drafts).length;
  const selectedLinked = selectedRows.filter(
    (row) => row.state === 'placed' || row.state === 'partly_linked',
  );
  // Every OWED row, linked or not (plan section 1): with drafts written at raise most
  // rows in front of purchasing are already `placed`, so a Reject that only took
  // unlinked ones would refuse almost nothing.
  const selectedRejectable = selectedRows.filter((row) =>
    isBulkRejectable(row),
  );
  // What Confirm (N) acts on (PLAN-oi-confirm-per-so, AC-CF-5): ticked rows purchasing
  // has not yet signed off - `awaiting` or `changed` - and not cancelled (cancelled rows
  // do not tick at all, but a row can be ticked before it is cancelled elsewhere).
  const selectedConfirmable = selectedRows.filter((row) => {
    const state = ackStateOf(row);
    return (state === 'awaiting' || state === 'changed') && row.state !== 'cancelled';
  });
  // Unconfirm (N) (PLAN-oi-worklist-split-customer-project.md, owner 18 Sep 2026): ticked rows
  // purchasing HAS signed off - `acknowledged` or `changed` - the reverse of Confirm's
  // own set above. Excludes a cancelled row even if ticked (review round 1, S1): its
  // handshake is history, not something to reopen.
  const selectedUnconfirmable = selectedRows.filter((row) => {
    const state = ackStateOf(row);
    return (state === 'acknowledged' || state === 'changed') && row.state !== 'cancelled';
  });
  // The manual Link dialog is a ONE-row override (R8/S4 "Choose document"), so it is
  // offered at exactly one tick: two ticked rows would leave the page choosing which of
  // them it meant.
  const linkTarget =
    selectedRows.length === 1 ? (selectedRows[0] ?? null) : null;
  const linkingRow = linkingRowId
    ? (rows.find((row) => row.id === linkingRowId) ?? null)
    : null;
  // AC-CF-7 (review round fix): once "Select all N matching" is taken, Confirm's own
  // count is the ELIGIBLE total (`summary.ack.to_confirm` - awaiting + changed, computed
  // with every OTHER filter applied and the `ack` filter itself dropped, same as `kinds`
  // above), never the list's own `total`. `total` counts every row the current filters
  // match regardless of ack state, so on `ack=all` it over-states by every already-
  // acknowledged and rejected row in scope - "Confirm 11809 rows?" for a press that only
  // ever confirms the ones still to confirm.
  const confirmCount = selectAllMatchingActive
    ? (summary.data?.ack?.to_confirm ?? 0)
    : selectedConfirmable.length;

  function reserveTickedAsRequested() {
    stageAnswers(decideRows, null);
    setRowSelection({});
  }

  function stageReject(reason: string) {
    if (!reserveRejectOpen || !rejectTarget) return;
    stageAnswers(rejectTarget.rows, reason);
    if (rejectTarget.fromStrip) setRowSelection({});
    setReserveRejectOpen(false);
  }

  // The pen's pools, resolved for that ONE row while the form is open.
  const editingOptionsEntries = React.useMemo(
    () =>
      editingRow
        ? [
            {
              key: editingRow.id,
              productId: editingRow.product_id ?? null,
              location: editingRow.location ?? null,
            },
          ]
        : [],
    [editingRow],
  );
  const editingOptionsResolved = useReserveRowOptions(editingOptionsEntries);
  const editingOptions = editingRow ? editingOptionsResolved[editingRow.id] : undefined;

  function saveEditedReserve(payload: {
    warehouse_id?: string;
    qty_reserved: number;
    reason: string | null;
  }) {
    if (!editingRow) return;
    const locationOption = (editingOptions?.options ?? []).find(
      (option) => option.value === payload.warehouse_id,
    );
    setDrafts((previous) => ({
      ...previous,
      [editingRow.id]: {
        row: editingRow,
        entry: {
          kind: 'reserve',
          qty: payload.qty_reserved,
          warehouseId: payload.warehouse_id ?? null,
          locationLabel: locationOption ? bareLocationCode(locationOption.label) : null,
          reason: payload.reason,
        },
      },
    }));
    setEditingRow(null);
  }

  // One line per draft, grouped by order inquiry for the confirmation and the commits.
  const draftInquiries = React.useMemo(() => {
    const byInquiry = new Map<string, ReserveConfirmInquiry>();
    for (const { row, entry } of Object.values(drafts)) {
      const id = row.order_inquiry_id as string;
      const current = byInquiry.get(id) ?? {
        inquiry_id: id,
        inquiry_no: row.inquiry_no ?? '',
        reserve_count: 0,
        reject_count: 0,
      };
      if (entry.qty > 0) current.reserve_count += 1;
      else current.reject_count += 1;
      byInquiry.set(id, current);
    }
    return [...byInquiry.values()].sort((a, b) => a.inquiry_no.localeCompare(b.inquiry_no));
  }, [drafts]);

  function closeReserveConfirm(next: boolean) {
    // Escape, the overlay and the X are ignored while the commits are in flight, so the
    // outcome always lands in a dialog that is still open.
    if (!next && commitReserve.isPending) return;
    setReserveConfirmOpen(next);
    if (!next) setCommitOutcome(null);
  }

  /** F9: one commit per order inquiry; saved ones drop their drafts, failed ones keep them. */
  async function commitDrafts(parkNotice: boolean) {
    const groups = new Map<string, CommitReservePayload['reserves']>();
    for (const { row, entry } of Object.values(drafts)) {
      const id = row.order_inquiry_id as string;
      const reserves = groups.get(id) ?? [];
      reserves.push({
        row_id: row.id,
        ...(entry.warehouseId ? { warehouse_id: entry.warehouseId } : {}),
        qty_reserved: entry.qty,
        reason: entry.reason ?? null,
      });
      groups.set(id, reserves);
    }
    const results = await commitReserve.mutateAsync(
      [...groups].map(([inquiryId, reserves]) => ({
        inquiryId,
        payload: { reserves, amendments: [], park_notice: parkNotice },
      })),
    );
    const saved = new Set(results.filter((result) => result.ok).map((result) => result.inquiryId));
    setCommitOutcome({ inquiries: draftInquiries, results });
    setDrafts((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(
          ([, draft]) => !saved.has(draft.row.order_inquiry_id as string),
        ),
      ),
    );
  }
  const confirmInquiries: ReserveConfirmInquiry[] = (
    commitOutcome?.inquiries ?? draftInquiries
  ).map((inquiry) => {
    const result = commitOutcome?.results.find(
      (candidate) => candidate.inquiryId === inquiry.inquiry_id,
    );
    return result ? { ...inquiry, result: { ok: result.ok, message: result.message } } : inquiry;
  });

  /** Confirm (N) (AC-CF-5/7/8): ticked rows by id, or the whole matching scope by
   * `filter` once "Select all N matching" is taken - the endpoint accepts exactly one. */
  function runConfirm() {
    setConfirmingOpen(false);
    acknowledge.mutate(
      selectAllMatchingActive
        ? { filter: listFilters, horizon: horizonRequest }
        : { rowIds: selectedConfirmable.map((row) => row.id), horizon: horizonRequest },
      {
        onSuccess: () => {
          setRowSelection({});
          setSelectAllMatchingActive(false);
        },
      },
    );
  }

  /** Unconfirm (N) (PLAN-oi-worklist-split-customer-project.md): ticked rows by id only - no
   * "Select all N matching" scope, and no confirmation dialog - reversible, a plain
   * Confirm undoes it. */
  function runUnconfirm() {
    unacknowledge.mutate(selectedUnconfirmable.map((row) => row.id), {
      onSuccess: () => setRowSelection({}),
    });
  }

  async function unlinkSelected() {
    if (selectedLinked.length === 0) return;
    setUnlinkingSelected(true);
    try {
      for (const row of selectedLinked) {
        await unplaceOrderInquiryRow(row.id);
      }
      toast.success(`Unlinked ${selectedLinked.length}`);
      setUnlinkingSelectedOpen(false);
      setRowSelection({});
      void list.refetch();
      void summary.refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to unlink');
    } finally {
      setUnlinkingSelected(false);
    }
  }

  /**
   * "Link selected" (R18, owner ruling from the hand test on stack C, 25 Sep 2026,
   * supersedes G1): never turns a suggestion into a link on its own - "the user should
   * always go to autocount to do linking, the link selected is to recalculate with
   * autocount linkage in case of mistake in the automation." It is the SAME `auto-place`
   * call "Auto link all" uses, scoped to exactly the ticked rows via `row_ids`: the book
   * step re-runs for them (writing only what AutoCount names, in AutoCount's own name),
   * then their suggestions refresh through the cascade the same way Auto link all's does.
   * There is no separate route for it any more.
   */
  async function linkSelected() {
    if (selectedRows.length === 0) return;
    setLinkingSelected(true);
    try {
      const rowIds = selectedRows.map((row) => row.id);
      const result = await autoPlaceOrderInquiryRows({ row_ids: rowIds });
      toast.success(linkOutcomeText(result));
      setRowSelection({});
      void list.refetch();
      void summary.refetch();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'Failed to link those rows',
      );
    } finally {
      setLinkingSelected(false);
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      // Lane B (AC-B6, R4): async, through My Downloads - the sync blob-save is
      // retired from this screen (the sync GET route itself stays for one release,
      // for MCP / other callers).
      await exportOrderInquiryWorklistXlsx(listFilters);
      toast.success('Preparing the order inquiry export - it will appear in My Downloads.');
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : 'Failed to export the order inquiry',
      );
    } finally {
      setExporting(false);
    }
  }

  // A pressed CARD counts as a filter here even though it lives on the strip rather than
  // in this popover: it narrows exactly what the rest of these narrow, so leaving it out
  // hid "Clear filters" from the one person who most needs it - somebody who pressed Buy,
  // sees three rows, and has nothing on the toolbar offering to give the rest back.

  // What the chip above the grid says, so the buyer can see WHY the list is short and
  // take the narrowing off in one press (AC-CF-11/12). `ack=all` shows no chip - it is
  // the "show everything" state, not a narrowing - and the default (`to_confirm`) DOES,
  // because the page opens narrowed to purchasing's own work queue on purpose.
  const ackChipLabel =
    ackFilter && ackFilter !== ACK_ANY
      ? (ACK_FILTER_OPTIONS.find((option) => option.value === ackFilter)
          ?.label ?? ackFilter)
      : null;

  const filtersActiveCount =
    (month ? 1 : 0) +
    (supplierFilter ? 1 : 0) +
    (projectFilter ? 1 : 0) +
    (raisedDate ? 1 : 0) +
    (raisedByFilter ? 1 : 0) +
    (linkedFilter ? 1 : 0) +
    (ackFilter && ackFilter !== ACK_ANY ? 1 : 0) +
    (kindFilter ? 1 : 0) +
    (locationFilter ? 1 : 0) +
    (agentFilter ? 1 : 0) +
    (soMonthFilter ? 1 : 0) +
    (poNumberFilter ? 1 : 0) +
    (spoNumberFilter ? 1 : 0) +
    (stateFilter ? 1 : 0) +
    (reserveFilter ? 1 : 0);

  // S3: the cell already carries its own axis label; only the bucket's granularity-aware
  // reading (`buildOrderInquiryMatrix`'s own label) still has to be looked up.
  const openCellBucket = openCell
    ? matrix.buckets.find((bucket) => bucket.key === openCell.period)
    : undefined;

  // S2, R-H: the List | Schedule toggle, moved into the PageHeader's own right slot -
  // same line as the title rather than a separate row above the cards.
  const viewToggle = (
    <div
      className="inline-flex rounded-md border border-input"
      role="group"
      aria-label="Order inquiry view"
    >
      <Button
        type="button"
        size="sm"
        variant={view === 'list' ? 'primary' : 'ghost'}
        className="rounded-e-none"
        aria-pressed={view === 'list'}
        onClick={() => setView('list')}
      >
        <List className="size-4" aria-hidden />
        List
      </Button>
      <Button
        type="button"
        size="sm"
        variant={view === 'schedule' ? 'primary' : 'ghost'}
        className="rounded-s-none border-s border-input"
        aria-pressed={view === 'schedule'}
        onClick={() => setView('schedule')}
      >
        <LayoutGrid className="size-4" aria-hidden />
        Schedule
      </Button>
    </div>
  );

  // S1, R-K: the Filters popover's own content, shared between the List toolbar and the
  // Schedule toolbar (both mount the SAME `toolbarElement` below, never two copies of
  // this JSX) - one filter UI, whichever view happens to be on screen.
  const filtersContent = (
    // AC-OH-70 (S7): the popover's own shared primitive
    // (`data-grid-list-toolbar.tsx`'s `DropdownMenuContent`) has no max-height prop of
    // its own, so the bound lives here, on the content - the same convention
    // `data-grid-column-visibility.tsx` and the board's popovers already use, except
    // `dvh` rather than their `vh` (mobile-vh.inventory.test.ts's fixed-viewport-unit
    // sweep: a NEW `vh` site is not grandfathered onto the follow-up #567 allowlist,
    // `dvh` tracks the actual visible area on mobile Safari where `vh` sits under the
    // address bar's chrome).
    <div className="max-h-[60dvh] space-y-3 overflow-y-auto">
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Location</Label>
        <SearchableSelect
          value={locationFilter}
          onChange={setLocationFilter}
          clearable
          options={(summary.data?.locations ?? []).map((entry) => ({
            value: entry.id,
            label: `${entry.label} (${entry.rows})`,
          }))}
          placeholder="Every location"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Agent</Label>
        <SearchableSelect
          value={agentFilter}
          onChange={setAgentFilter}
          clearable
          options={(summary.data?.agents ?? []).map((entry) => ({
            value: entry.id,
            label: `${entry.label} (${entry.rows})`,
          }))}
          placeholder="Every agent"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">SO month</Label>
        <SearchableSelect
          value={soMonthFilter}
          onChange={setSoMonthFilter}
          clearable
          options={soMonthChoices}
          placeholder="Every month"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground" htmlFor="po-number-filter">
          PO number
        </Label>
        <Input
          id="po-number-filter"
          value={poNumberInput}
          onChange={(event) => setPoNumberInput(event.target.value)}
          placeholder="202605"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground" htmlFor="spo-number-filter">
          SPO number
        </Label>
        <Input
          id="spo-number-filter"
          value={spoNumberInput}
          onChange={(event) => setSpoNumberInput(event.target.value)}
          placeholder="SPO-2026/07"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Linked</Label>
        <SearchableSelect
          value={linkedFilter}
          onChange={setLinkedFilter}
          clearable
          options={LINKED_OPTIONS}
          placeholder="Anywhere"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Confirmed</Label>
        <SearchableSelect
          value={ackFilter}
          onChange={setAckFilter}
          options={ACK_FILTER_OPTIONS.map((option) => {
            const count =
              summary.data?.ack?.[
                option.value as keyof NonNullable<typeof summary.data.ack>
              ];
            return {
              value: option.value,
              label: count === undefined ? option.label : `${option.label} (${count})`,
            };
          })}
          placeholder="Any"
        />
      </div>
      <div className="space-y-1.5">
        {/* AC-OH-61: the row's OWN state - Raised / Partly linked / Actioned /
            Cancelled / Linked, off `summary.by_state` (`total` is a count, not an
            option). The one field that can ask for `state=cancelled`, since the list
            hides cancelled by default otherwise (S5). */}
        <Label className="text-xs text-muted-foreground">State</Label>
        <SearchableSelect
          value={stateFilter}
          onChange={setStateFilter}
          clearable
          options={Object.entries(summary.data?.by_state ?? {})
            .filter(([key]) => key !== 'total')
            .map(([key, count]) => ({
              value: key,
              label: `${STATE_LABEL[key] ?? key} (${count})`,
            }))}
          placeholder="Every state"
        />
      </div>
      <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Reserve</Label>
          <SearchableSelect
            value={reserveFilter}
            onChange={(next) => setReserveFilter(reserveFilterFrom(next))}
            clearable
            options={ORDER_INQUIRY_RESERVE_STATUSES.map((status) => ({
              value: status,
              label: orderInquiryReserveStatusLabel(status),
            }))}
            placeholder="Every status"
          />
        </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Supplier</Label>
        <SearchableSelect
          value={supplierFilter}
          onChange={setSupplierFilter}
          clearable
          options={(summary.data?.suppliers ?? []).map((entry) => ({
            value: entry.id,
            label: entry.label,
          }))}
          placeholder="Every supplier"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Project</Label>
        <SearchableSelect
          value={projectFilter}
          onChange={setProjectFilter}
          clearable
          options={(summary.data?.projects ?? []).map((entry) => ({
            value: entry.id,
            label: entry.label,
          }))}
          placeholder="Every project"
          // S5 (`PLAN-oi-project-label-from-so.md`): the option's own text is now the
          // SO's project label, not a short registered-project title - long enough
          // (e.g. "KITACON / PHASE 6A & 6B@BDR TSK PUTERI") that the trigger needs to
          // truncate to one line with the full text in `title`, same as a picker
          // inside a fixed-width table cell.
          truncateTriggerLabel
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">Raised by</Label>
        <SearchableSelect
          value={raisedByFilter}
          onChange={setRaisedByFilter}
          clearable
          options={(summary.data?.raised_by ?? []).map((entry) => ({
            value: entry.id,
            label: entry.label,
          }))}
          placeholder="Everyone"
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground" htmlFor="raised-on">
          Raised on
        </Label>
        <Input
          id="raised-on"
          type="date"
          value={raisedDate}
          onChange={(event) => setRaisedDate(event.target.value)}
        />
      </div>
      {filtersActiveCount > 0 && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={() => {
            setMonth('');
            setSupplierFilter('');
            setProjectFilter('');
            setRaisedDate('');
            setRaisedByFilter('');
            setLinkedFilter('');
            // Clearing means "show everything" (`ack=all`), never the default
            // `to_confirm` - the default is what an UNTOUCHED filter reads as,
            // and this press is the buyer actively asking to see it all.
            setAckFilter(ACK_ANY);
            setLocationFilter('');
            setAgentFilter('');
            setSoMonthFilter('');
            setPoNumberInput('');
            setSpoNumberInput('');
            setStateFilter('');
            setReserveFilter('');
            // Counted above, so it is cleared here: "Clear filters" that
            // left a card pressed would leave the screen still narrowed.
            setKindFilter(null);
          }}
        >
          Clear filters
        </Button>
      )}
    </div>
  );

  // S2: the ONE toolbar (search, Filters, Columns, refresh, Actions, Upload), mounted in
  // BOTH views rather than duplicated - `DataGridListToolbar` takes its `table` as a
  // prop, not from a `DataGrid` context provider, so it stands on its own outside the
  // List-only grid wrapper below.
  //
  // The wrapper is named without its angle bracket on purpose: `options.inventory`'s
  // consumer scan reads the RAW file, comments and all, so a JSX-looking mention in a
  // comment reads to it as a grid that forwards no `isPlaceholderData`.
  const toolbarElement = (
    <DataGridListToolbar
      table={table}
      searchSlot={
        <ListSearchInput
          value={search}
          onChange={setSearch}
          isSettling={isSearchInFlight(debouncedSettling, list.isFetching, debounced)}
          placeholder="Search S/O, item, PO, SPO, customer, agent"
          aria-label="Search order inquiry rows"
          className="w-full max-w-xs"
        />
      }
      filters={{
        kind: 'custom',
        active: filtersActiveCount > 0,
        activeCount: filtersActiveCount,
        // The page opens narrowed to what purchasing has not confirmed, and a
        // list that is short for a reason nobody stated reads as missing data.
        activeSummary: [
          ...(ackChipLabel
            ? [{ label: ackChipLabel, onClear: () => setAckFilter(ACK_ANY) }]
            : []),
          ...(reserveFilter
            ? [
                {
                  label: `Reserve: ${orderInquiryReserveStatusLabel(reserveFilter)}`,
                  onClear: () => setReserveFilter(''),
                },
              ]
            : []),
        ],
        content: filtersContent,
      }}
      // Their own workbook, with their own headings and a sheet per delivery
      // month, is the file anyone outside the system reads - so the generic
      // selection-scoped export is replaced rather than offered beside it.
      exportConfig={false}
      // The bulk strip keeps its COUNT and its Clear and nothing else
      // (item 12, AC-D13). Every press moved into the Actions menu, where
      // each one states how many ticked rows it applies to - a strip of
      // buttons on the left and a menu of the same names on the right was two
      // places to look for one action.
      bulkActions={[]}
      secondaryActions={[
        {
          key: 'auto-place',
          label: 'Auto link all…',
          icon: Wand2,
          onClick: () => setAutoPlacing(true),
        },
        ...(canBulkLink
          ? [
              {
                key: 'choose-document',
                label: 'Choose document (1)',
                icon: Link2,
                disabled: !linkTarget,
                disabledReason: linkTarget
                  ? undefined
                  : 'Tick exactly one row to choose its document by hand.',
                onClick: () => setLinkingRowId(linkTarget?.id ?? null),
              },
              {
                key: 'link-selected',
                // R18: acts on every ticked row, whatever its state - no eligible
                // subset any more, so the count is never "of n".
                label: `Link selected (${selectedRows.length})`,
                icon: Wand2,
                disabled: selectedRows.length === 0 || linkingSelected,
                disabledReason:
                  selectedRows.length === 0 ? 'Tick rows to recalculate against AutoCount.' : undefined,
                onClick: () => void linkSelected(),
              },
              {
                key: 'unlink-selected',
                label: countLabel('Unlink selected', selectedLinked.length, selectedRows.length),
                icon: Unlink,
                disabled:
                  selectedLinked.length === 0 || unlinkingSelected,
                disabledReason:
                  selectedLinked.length === 0
                    ? 'Tick linked rows to unlink.'
                    : undefined,
                onClick: () => setUnlinkingSelectedOpen(true),
              },
            ]
          : []),
        ...(canAcknowledge
          ? [
              {
                key: 'reject-selected',
                label: countLabel('Reject selected', selectedRejectable.length, selectedRows.length),
                icon: Ban,
                destructive: true,
                disabled: selectedRejectable.length === 0,
                disabledReason:
                  selectedRejectable.length === 0
                    ? 'Tick rows purchasing still owes an answer on.'
                    : undefined,
                onClick: () => setRejectingSelected(true),
              },
              {
                // Unconfirm (N) (PLAN-oi-worklist-split-customer-project.md, owner 18 Sep 2026): the
                // reverse of Confirm, for a row taken on by mistake or a reconfirm CS
                // has not actually made yet. Reversible - a plain Confirm undoes it -
                // so no confirmation dialog, unlike the destructive/detach actions above.
                key: 'unconfirm-selected',
                label: countLabel(
                  'Unconfirm',
                  selectedUnconfirmable.length,
                  selectedRows.length,
                ),
                icon: RotateCcw,
                disabled: selectedUnconfirmable.length === 0 || unacknowledge.isPending,
                disabledReason:
                  selectedUnconfirmable.length === 0
                    ? 'Tick rows purchasing has already confirmed.'
                    : undefined,
                onClick: () => runUnconfirm(),
              },
            ]
          : []),
        {
          key: 'unplace-all',
          label: 'Unlink all…',
          icon: Undo2,
          onClick: () => setUnplacingAll(true),
          // N1: a lacking action grant, or the preview call failing for any
          // other reason, must never read the same as "genuinely nothing to
          // unplace" - each says its own thing.
          disabled:
            !canActOnOrderInquiry ||
            unplacePreview.isError ||
            unplaceCount === 0,
          disabledReason: !canActOnOrderInquiry
            ? "You don't have permission to unlink rows"
            : unplacePreview.isError
              ? 'Could not check linked rows - try again'
              : unplaceCount === 0
                ? 'No linked rows to unlink'
                : undefined,
        },
        ...(canAcknowledge
          ? [
              {
                key: 'upload-purchase-orders',
                // Moved off the primary slot (PLAN-oi-confirm-per-so, AC-CF-5): the
                // primary press is Confirm now that a row is born `awaiting` again -
                // feeding the book is still purchasing's, just no longer the ONE thing
                // this toolbar does.
                label: 'Upload purchase orders',
                icon: Upload,
                onClick: () => setUploadingBook(true),
              },
            ]
          : []),
        {
          key: 'export',
          label: 'Export Excel',
          icon: Download,
          disabled: exporting,
          onClick: () => void handleExport(),
        },
        ...parkedSendActions,
      ]}
      // AC-CF-7: the header tick is the current page only - this is what offers the
      // WHOLE matching set once every loaded row is ticked. Purchasing's own affordance;
      // it feeds Confirm, which is theirs alone.
      selectAllMatching={
        canAcknowledge
          ? {
              total,
              loadedCount: rows.length,
              active: selectAllMatchingActive,
              onSelectAll: () => setSelectAllMatchingActive(true),
              onClear: () => {
                setSelectAllMatchingActive(false);
                setRowSelection({});
              },
            }
          : undefined
      }
      // Confirm (N) (AC-CF-5): the primary press once a row is born `awaiting` again -
      // ticked rows purchasing has not yet signed off, or the whole matching scope once
      // "Select all N matching" is taken. Disabled at 0, with the reason as its title
      // (D3): a dead button with nothing beside it to say why reads as broken.
      primaryAction={
        <>
          {/* OI-BULK-RESERVE v4 (F4): Decide while lines are ticked, then Reserve (N) once
              answers are drafted - the OI Lines tab's own pair. */}
          {canReserve && selectedRows.length > 0 ? (
            <ReserveDecideControl
              count={decideRows.length}
              onReserveAsRequested={reserveTickedAsRequested}
              onReject={() => {
                setRejectTarget({ rows: decideRows, fromStrip: true });
                setReserveRejectOpen(true);
              }}
            />
          ) : null}
          {canReserve && draftCount > 0 ? (
            <Button
              type="button"
              size="sm"
              disabled={commitReserve.isPending}
              onClick={() => setReserveConfirmOpen(true)}
            >
              {`Reserve (${draftCount})`}
            </Button>
          ) : null}
          {canAcknowledge ? (
            <Button
              type="button"
              size="sm"
              disabled={confirmCount === 0 || acknowledge.isPending}
              title={
                confirmCount === 0
                  ? 'Tick rows still to confirm, or Select all matching.'
                  : undefined
              }
              onClick={() => setConfirmingOpen(true)}
            >
              {`Confirm (${confirmCount})`}
            </Button>
          ) : null}
        </>
      }
      onRefresh={() => {
        if (view === 'list') void list.refetch();
        else void matrixQuery.refetch();
        void summary.refetch();
      }}
      isRefreshing={
        view === 'list'
          ? list.isFetching && !list.isLoading
          : matrixQuery.isFetching && !matrixQuery.isLoading
      }
    />
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Order inquiries"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {extraHeaderActions}
            {viewToggle}
          </div>
        }
      />

      <div className="flex flex-wrap items-start gap-2">
        {/* The three cards, above BOTH views and pressed in both (AC-I11/AC-I14): what the
            rows in view still need, in the same colours the cells and the "Linked to"
            column draw. No legend beside them - each card carries its own swatch and its
            own words, so there is nothing left for a legend to say. */}
        <OrderInquiryStrip
          totals={facetSegments(summary.data?.kinds)}
          active={kindFilter}
          onToggle={(kind) =>
            setKindFilter((current) => (current === kind ? null : kind))
          }
        />
        {/* AC-CF-12: purchasing's own work-queue count, beside the three supply cards -
            what still needs a press, not what still needs a document. CS never sees it -
            it is not their queue to clear. */}
        {canAcknowledge ? (
          <SupplyKindCard
            kind="to_confirm"
            label="To confirm"
            swatchClass="bg-amber-500"
            selected={ackFilter === 'to_confirm'}
            disabled={false}
            // AC-CL-17: toggles off on a second press, the same as the three supply
            // cards beside it (`setKindFilter` above) - the chip's own `x` already did
            // this; the card itself never did.
            onClick={() =>
              setAckFilter((current) => (current === 'to_confirm' ? ACK_ANY : 'to_confirm'))
            }
            testId="order-inquiry-strip-to-confirm"
          >
            <span
              data-testid="order-inquiry-strip-to-confirm-count"
              className="mt-1.5 block text-lg font-semibold tabular-nums text-amber-700"
            >
              {summary.data?.ack?.to_confirm ?? '-'}
            </span>
          </SupplyKindCard>
        ) : null}
      </div>

      {/* The book this page queued has been READ (AC-H13) - the worker is done with it,
          which is when its documents exist to link against. Two next steps and no third:
          link what they can now cover, or go and look at the purchase orders that
          arrived. Dismissed by acting, never by a timer. */}
      {uploadLanded && canAcknowledge ? (
        <Alert appearance="light">
          <AlertIcon>
            <Upload />
          </AlertIcon>
          <AlertContent>
            <AlertTitle>
              {uploadFailed
                ? 'The book could not be read'
                : 'The book has been read'}
            </AlertTitle>
            <AlertDescription>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={linkNow.isPending}
                  onClick={() =>
                    linkNow.mutate(
                      // The products the upload wrote, so one book does not re-deal every
                      // open instruction in the company. Empty means the job named none,
                      // and then it IS every acknowledged row - the same rule the endpoint
                      // states for an omitted list.
                      {
                        ...(uploadedProducts.length
                          ? { product_ids: uploadedProducts }
                          : {}),
                        ...horizonRequest,
                      },
                      { onSuccess: () => setUploadJobId(null) },
                    )
                  }
                >
                  <Link2 className="size-4" aria-hidden />
                  {linkNow.isPending ? 'Linking…' : 'Link now'}
                </Button>
                <Button asChild size="sm" variant="outline">
                  <Link href={purchaseOrdersHref}>Open purchase orders</Link>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setUploadJobId(null)}
                >
                  Dismiss
                </Button>
              </div>
            </AlertDescription>
          </AlertContent>
        </Alert>
      ) : null}

      {/* S2, R-G: the month tab strip, between the cards and the toolbar in BOTH
          views - "All" first, then one tab per delivery month that has rows. */}
      <OrderInquiryMonthStrip months={months} active={month} onSelect={setMonth} />

      {view === 'schedule' ? (
        <div className="space-y-4">
          {/* S2, AC-M5: the same toolbar the List view carries - search, Filters,
              Columns, refresh, Actions, Upload - so there is one filter UI whichever
              view is on screen. */}
          <Card>
            <CardHeader className="block">{toolbarElement}</CardHeader>
          </Card>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Label
                htmlFor="matrix-rows"
                className="text-sm text-muted-foreground"
              >
                Rows
              </Label>
              <div className="w-44">
                <SearchableSelect
                  id="matrix-rows"
                  value={matrixAxis}
                  onChange={(value) =>
                    setMatrixAxis(value as OrderInquiryMatrixAxis)
                  }
                  options={MATRIX_AXIS_OPTIONS}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Label
                htmlFor="matrix-granularity"
                className="text-sm text-muted-foreground"
              >
                By
              </Label>
              <div className="w-40">
                <SearchableSelect
                  id="matrix-granularity"
                  value={matrixGranularity}
                  onChange={(value) =>
                    setMatrixGranularity(value as OrderInquiryMatrixGranularity)
                  }
                  options={MATRIX_GRANULARITY_OPTIONS}
                />
              </div>
            </div>
          </div>

          {matrixQuery.isError ? (
            <Alert variant="destructive" appearance="light">
              <AlertIcon>
                <AlertTriangle />
              </AlertIcon>
              <AlertContent>
                <AlertTitle>The schedule could not be loaded</AlertTitle>
                <AlertDescription>
                  {matrixQuery.error instanceof Error
                    ? matrixQuery.error.message
                    : 'Try again in a moment.'}
                </AlertDescription>
              </AlertContent>
            </Alert>
          ) : matrixQuery.isLoading || isViewMemoryPending ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-72 w-full" />
            </div>
          ) : matrix.rows.length === 0 ? (
            <Card>
              <CardContent className="px-6 py-10 text-center">
                <PackageSearch
                  className="mx-auto size-6 text-muted-foreground"
                  aria-hidden
                />
                <h3 className="mt-2 text-sm font-semibold">
                  No inquiries in this view
                </h3>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  {filtered
                    ? 'Clear the month and the filters to see everything purchasing has been told to buy.'
                    : 'Confirming supply in Fulfilment Planning raises the rows purchasing acts on.'}
                </p>
              </CardContent>
            </Card>
          ) : (
            <OrderInquiryScheduleMatrix
              buckets={matrix.buckets}
              rows={matrix.rows}
              rowHeader={
                MATRIX_AXIS_OPTIONS.find(
                  (option) => option.value === matrixAxis,
                )?.label ?? 'Product'
              }
              cells={matrix.cells}
              onOpenCell={setOpenCell}
            />
          )}

          {openCell && (
            <OrderInquiryMatrixCellDrilldown
              cell={openCell}
              axis={matrixAxis}
              granularity={matrixGranularity}
              filters={listFilters}
              rowLabel={openCell.axis_label}
              bucketLabel={openCellBucket?.label ?? ''}
              onClose={() => setOpenCell(null)}
            />
          )}
        </div>
      ) : (
        <DataGrid
          table={table}
          recordCount={total}
          isLoading={list.isLoading || isViewMemoryPending}
          isPlaceholderData={list.isPlaceholderData}
          listingKey="projects.projects.view::order-inquiry-worklist"
          tableLayout={{
            width: 'fixed',
            columnsResizable: true,
            columnsVisibility: true,
          }}
          // REV-S6/S1 (17 Sep review round): a redirected row reads muted -
          // the DataGrid's own row-level hook, not a per-cell wrapper (a
          // `display: contents` wrapper has no box, so `opacity-60` on it
          // never applies). Precedent: PlanRowDialog.tsx's rowClassName.
          // AC-CL-2d: a row on a cancelled line reads muted the SAME way a used row
          // does - one class, two reasons.
          rowClassName={(row) =>
            row.redirected_to_pool || row.line_cancelled ? 'opacity-60' : undefined
          }
          emptyMessage={
            <div className="px-6 py-10 text-center">
              <p className="text-sm font-semibold">
                {filtered ? 'No rows match' : 'Nothing has been raised yet'}
              </p>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                {filtered
                  ? 'Clear the month and the filters to see everything purchasing has been told to buy.'
                  : 'Confirming supply in Fulfilment Planning raises the rows purchasing acts on.'}
              </p>
              {!filtered && (
                <Button asChild variant="outline" className="mt-4">
                  <Link href="/project-sales/fulfilment-planning">
                    Open Fulfilment Planning
                  </Link>
                </Button>
              )}
            </div>
          }
        >
          <Card>
            <CardHeader className="block">{toolbarElement}</CardHeader>
            <CardTable>
              {list.isError ? (
                <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
                  <h2 className="text-sm font-semibold text-destructive">
                    The order inquiry could not be loaded
                  </h2>
                  <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                    {list.error instanceof Error
                      ? list.error.message
                      : 'Try again shortly.'}
                  </p>
                </div>
              ) : (
                <DataGridTable />
              )}
            </CardTable>
            <CardFooter>
              <DataGridPagination />
            </CardFooter>
          </Card>
        </DataGrid>
      )}

      <AutoLinkOrderInquiryDialog
        open={autoPlacing}
        onOpenChange={setAutoPlacing}
        linkUpTo={linkUpTo}
        horizonCleared={horizonCleared}
        onHorizonChange={(value, cleared) => {
          setLinkUpTo(value);
          setHorizonCleared(cleared);
        }}
      />
      {parkedSendDialog}
      <BulkRejectReserveDialog
        open={reserveRejectOpen}
        count={rejectTarget?.rows.length ?? 0}
        onOpenChange={setReserveRejectOpen}
        onConfirm={stageReject}
      />
      {/* Multi-OI mode: `lines` / `inquiryNo` / recipients belong to the single-OI mode. */}
      <ReserveConfirmDialog
        open={reserveConfirmOpen}
        onOpenChange={closeReserveConfirm}
        inquiryNo=""
        lines={[]}
        inquiries={confirmInquiries}
        done={commitOutcome != null}
        onConfirm={commitDrafts}
        isPending={commitReserve.isPending}
      />
      {/* Mounted once the row's pool options have loaded, so the default location is real. */}
      {editingRow && editingOptions && !editingOptions.isLoading ? (
        <ReserveLineForm
          open
          onOpenChange={(next) => {
            if (!next) setEditingRow(null);
          }}
          itemCode={editingRow.item_code ?? null}
          mode="reserve"
          requestedQty={editingRow.requested_qty ?? '0'}
          locationOptions={editingOptions.options}
          availableQtyByLocation={editingOptions.availableQtyByWarehouseId}
          defaultLocationId={editingRow.reserve_warehouse_id ?? editingOptions.defaultWarehouseId}
          onStage={saveEditedReserve}
        />
      ) : null}
      {/* One reason for the batch (item 15/AC-D6). */}
      <BulkRejectOrderInquiryDialog
        open={rejectingSelected}
        onOpenChange={setRejectingSelected}
        rowIds={selectedRejectable.map((row) => row.id)}
        onRejected={() => setRowSelection({})}
      />
      {/* The manual override for ONE ticked row (R8), reached from the Actions menu. */}
      {linkingRow ? (
        <LinkDocumentDialog
          rowId={linkingRow.id}
          itemCode={linkingRow.item_code}
          qty={linkingRow.qty}
          linkedQty={linkingRow.linked_qty}
          deliveryDate={linkingRow.delivery_date}
          linkUpTo={linkUpTo}
          onDone={() => setLinkingRowId(null)}
        />
      ) : null}
      {/* The book purchasing feeds, from purchasing's own page (AC-H12) - the SAME dialog
          and the same worker job the purchase orders list mounts, never a second
          importer. The history book is not offered here: it is a different desk's file. */}
      {uploadingBook ? (
        <OutstandingUploadDialog
          open
          onOpenChange={(next) => !next && setUploadingBook(false)}
          kind="purchase-orders"
          onQueued={(queued) => setUploadJobId(queued.job_id)}
        />
      ) : null}
      <AlertDialog
        open={unlinkingSelectedOpen}
        onOpenChange={setUnlinkingSelectedOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unlink selected</AlertDialogTitle>
            <AlertDialogDescription>
              {selectedLinked.length === 1
                ? 'Remove this row\u2019s links? That quantity goes back to demand, and the next reorder suggestion counts it again.'
                : `Remove every link on the ${selectedLinked.length} selected rows? Those quantities go back to demand, and the next reorder suggestion counts them again.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={unlinkingSelected}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                void unlinkSelected();
              }}
              disabled={unlinkingSelected}
            >
              Unlink
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <UnlinkAllOrderInquiryDialog
        open={unplacingAll}
        onOpenChange={setUnplacingAll}
        filters={unplaceAllFilters}
        count={unplaceCount}
        productCode={unplacePreview.data?.product_code}
        scopeLabels={activeUnplaceScopeLabels}
      />
      {/* Confirm (N) (AC-CF-5), the same shape as the fulfilment board's own Confirm
          dialog: state the count, then the press - no explanation, the count IS the
          statement. */}
      <AlertDialog open={confirmingOpen} onOpenChange={setConfirmingOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {`Confirm ${confirmCount} row${confirmCount === 1 ? '' : 's'}?`}
            </AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={acknowledge.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                runConfirm();
              }}
              disabled={acknowledge.isPending}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
