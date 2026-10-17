'use client';

/**
 * Available stock status: AutoCount's report for one item, embedded wherever an item is
 * looked at (plan `PLAN-stock-status-embed-17oct.md`, mock round 4).
 *
 * One component for every place it appears: the stand-alone Inventory page, the order inquiry
 * line, the order inquiry worklist and fulfilment board popovers, the loading plan dialog, and
 * (after #1718) the sample request line. AutoCount is the truth; this only reads.
 */
import * as React from 'react';
import Link from 'next/link';
import { ColumnDef, ExpandedState } from '@tanstack/react-table';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { ListLoadFailure } from '@/components/common/ListLoadFailure';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useIsMobile } from '@/hooks/use-mobile';
import { usePermissions } from '@/hooks/usePermissions';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import { useStockStatus } from './useStockStatus';
import {
  GROUP_BY_OPTIONS,
  type GroupBy,
  type LocationDocument,
  type SoGroup,
  documentsForLocation,
  groupSalesOrders,
  horizonOptions,
  matchesLocation,
  soColumns,
  sumLocations,
} from './stockStatusMath';
import type { StockStatus, StockStatusIncomingPl, StockStatusLocation } from './stockStatus.types';

// Every grid here passes `listingKey={null}`: the panel is embedded in many screens, its
// columns are fixed (or are data, in SO totals), and a per-user column-preferences read per
// embed would buy nothing.

/** A quantity the AutoCount way: thousands separated, negatives red, zero muted. */
export function Qty({ value, signed = false }: { value: number; signed?: boolean }) {
  if (!value) return <span className="text-muted-foreground">0</span>;
  const text = Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (value < 0) return <span className="text-destructive">-{text}</span>;
  return <span>{signed ? `+${text}` : text}</span>;
}

const date = (value: string | null) => (value ? formatDateInMalaysia(value) : null);

/** The permission each document's CRM page is reached by (the menu's gate on that page). */
const PAGE_PERMISSION: Record<LocationDocument['kind'] | 'PL', string> = {
  SO: 'scm.dashboard.view',
  PO: 'scm.dashboard.view',
  SPO: 'procurement.spo_allocations.view',
  PL: 'procurement.packing_lists.view',
};

/** A document number: a link to its page, or plain text for a viewer who cannot open it (R7). */
function DocNumber({ kind, href, number }: { kind: keyof typeof PAGE_PERMISSION; href: string; number: string }) {
  const { permissionSet } = usePermissions();
  if (!permissionSet.has(PAGE_PERMISSION[kind])) {
    return (
      <span className="truncate font-medium" title={number}>
        {number}
      </span>
    );
  }
  return (
    <Link
      href={href}
      className="truncate font-medium text-primary hover:underline"
      title={number}
      onClick={(event) => event.stopPropagation()}
    >
      {number}
    </Link>
  );
}

export interface StockStatusPanelProps {
  productId: string;
  /** Sales order lines the panel was opened for: their SO rows read "This line". */
  lineIds?: string[];
  /** Embedded: show a link to the stand-alone page. */
  showOpenFullPage?: boolean;
  className?: string;
}

export function StockStatusPanel({ productId, lineIds = [], showOpenFullPage = false, className }: StockStatusPanelProps) {
  const horizons = React.useMemo(() => horizonOptions(), []);
  const [horizon, setHorizon] = React.useState<string>('all');
  const soDueBy = horizons.find((h) => h.key === horizon)?.value ?? null;
  const query = useStockStatus(productId, soDueBy);
  const [search, setSearch] = React.useState('');
  const [tab, setTab] = React.useState('location');
  // Lifted so Enter in the search can open the first shown location (R11).
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  const openFirstShown = () => {
    const first = query.data?.locations.find((l) => matchesLocation(l, search));
    if (!first) return;
    setTab('location');
    setExpanded({ [first.warehouse_id]: true });
  };

  return (
    <div className={cn('flex min-w-0 flex-col gap-3', className)} data-testid="stock-status-panel">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">SO due by</span>
        <Tabs value={horizon} onValueChange={setHorizon}>
          <TabsList variant="default" size="xs" aria-label="SO due by">
            {horizons.map((h) => (
              <TabsTrigger key={h.key} value={h.key}>
                {h.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <ListSearchInput
          value={search}
          onChange={setSearch}
          placeholder="Search locations"
          aria-label="Search locations"
          onSubmit={openFirstShown}
          className="w-full sm:w-56"
        />
        {showOpenFullPage && (
          <Link
            href={`/inventory-management/stock-status?product_id=${encodeURIComponent(productId)}`}
            className="ms-auto text-sm font-medium text-primary hover:underline"
            target="_blank"
          >
            Open full page
          </Link>
        )}
      </div>

      {query.isLoading ? (
        <SectionSkeleton rows={4} />
      ) : query.error || !query.data ? (
        <ListLoadFailure error={query.error} onRetry={() => query.refetch()} />
      ) : query.data.locations.length === 0 && query.data.incoming_pl.length === 0 ? (
        <div className="rounded-md border px-4 py-8 text-center" data-testid="stock-status-empty">
          <p className="font-medium">No stock, orders or incoming for this item</p>
          <p className="text-sm text-muted-foreground">
            Nothing on hand at any location, no open SO, PO, SPO or packing list.
          </p>
        </div>
      ) : (
        <>
          {query.data.as_of && (
            <p className="text-xs text-muted-foreground">
              On hand as of <span className="font-medium text-foreground">{formatDateTimeInMalaysia(query.data.as_of)}</span>
            </p>
          )}
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList variant="line" size="sm">
              <TabsTrigger value="location">By location</TabsTrigger>
              <TabsTrigger value="totals">SO totals</TabsTrigger>
            </TabsList>
            <TabsContent value="location" className="pt-3">
              <LocationsView
                data={query.data}
                search={search}
                lineIds={lineIds}
                expanded={expanded}
                onExpandedChange={setExpanded}
              />
            </TabsContent>
            <TabsContent value="totals" className="pt-3">
              <SoTotalsView data={query.data} search={search} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}

interface LocationsViewProps {
  data: StockStatus;
  search: string;
  lineIds: string[];
  expanded: ExpandedState;
  onExpandedChange: React.Dispatch<React.SetStateAction<ExpandedState>>;
}

function LocationsView({ data, search, lineIds, expanded, onExpandedChange: setExpanded }: LocationsViewProps) {
  const isMobile = useIsMobile();
  const [plOpen, setPlOpen] = React.useState(false);
  const shown = React.useMemo(() => data.locations.filter((l) => matchesLocation(l, search)), [data, search]);
  const sums = sumLocations(shown);
  const filtered = search.trim() !== '';

  const columns = React.useMemo<ColumnDef<StockStatusLocation>[]>(
    () => [
      {
        id: 'expand',
        header: () => null,
        cell: ({ row }) =>
          row.getIsExpanded() ? (
            <ChevronDown className="size-4 text-muted-foreground" aria-hidden />
          ) : (
            <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
          ),
        size: 36,
        enableResizing: false,
        meta: {
          headerTitle: 'Expand',
          expandedContent: (loc: StockStatusLocation) => (
            <DocumentsGrid docs={documentsForLocation(data, loc.warehouse_id)} lineIds={lineIds} />
          ),
        },
      },
      {
        id: 'location',
        header: 'Location',
        cell: ({ row }) => (
          <span className="flex min-w-0 items-center gap-1.5" title={locationTitle(row.original)}>
            <span className="truncate font-medium">{row.original.warehouse_code}</span>
            {!row.original.is_active && (
              <Badge variant="secondary" appearance="light" size="xs">
                Inactive in CRM
              </Badge>
            )}
          </span>
        ),
        footer: () => (
          <span className="font-semibold">{filtered ? `Shown, ${shown.length} of ${data.locations.length}` : 'Total'}</span>
        ),
        size: 190,
        meta: { headerTitle: 'Location' },
      },
      numberColumn('on_hand', 'On hand', (l) => l.on_hand, sums.on_hand),
      numberColumn('so_qty', 'SO', (l) => -l.so_qty, -sums.so_qty),
      numberColumn('po_qty', 'PO', (l) => l.po_qty, sums.po_qty),
      {
        id: 'incoming_pl',
        header: () => <span className="block text-end">Incoming PL</span>,
        cell: () => (
<span className="block text-end text-muted-foreground">-</span>
        ),
        footer: () => (
          <span className="block text-end font-semibold">{filtered ? '-' : <Qty value={data.totals.incoming_pl} />}</span>
        ),
        size: 110,
        meta: { headerTitle: 'Incoming PL' },
      },
      numberColumn('spo_qty', 'SPO', (l) => l.spo_qty, sums.spo_qty),
      numberColumn('forecast', 'Forecast', (l) => l.forecast, sums.forecast, true),
    ],
    [data, lineIds, filtered, shown.length, sums.on_hand, sums.so_qty, sums.po_qty, sums.spo_qty, sums.forecast],
  );

  return (
    <div className="flex flex-col gap-2">
      {isMobile ? (
        <LocationCards
          data={data}
          shown={shown}
          filtered={filtered}
          search={search}
          lineIds={lineIds}
          expanded={expanded}
          onToggle={(id) => setExpanded((prev) => toggle(prev, id))}
        />
      ) : (
        <PanelDataGrid
          columns={columns}
          rows={shown}
          getRowId={(l) => l.warehouse_id}
          listingKey={null}
          emptyTitle={`No location matches ${search.trim()}`}
          paginate={false}
          sortable={false}
          expanded={expanded}
          onExpandedChange={setExpanded}
          onRowClick={(l) => setExpanded((prev) => toggle(prev, l.warehouse_id))}
          scrollerMaxHeight={false}
        />
      )}
      <div className="rounded-md border text-sm" data-testid="stock-status-summary">
        {filtered && (
          <SummaryLine label="All locations">
            <span className="text-muted-foreground">On hand</span> <Qty value={data.totals.on_hand} />
            <span className="ms-3 text-muted-foreground">SO</span> <Qty value={-data.totals.so_qty} />
            <span className="ms-3 text-muted-foreground">PO</span> <Qty value={data.totals.po_qty} />
            <span className="ms-3 text-muted-foreground">SPO</span> <Qty value={data.totals.spo_qty} />
            <span className="ms-3 text-muted-foreground">Forecast</span>{' '}
            <b>
              <Qty value={data.totals.forecast} />
            </b>
          </SummaryLine>
        )}
        <button
          type="button"
          className="flex w-full items-center gap-2 border-t px-3 py-2 text-start first:border-t-0 hover:bg-muted/40"
          onClick={() => setPlOpen((v) => !v)}
          aria-expanded={plOpen}
        >
          {plOpen ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
          <span className="font-medium">Incoming PL</span>
          <span className="text-muted-foreground">item level, on no SPO yet</span>
          <span className="ms-auto font-semibold">
            <Qty value={data.totals.incoming_pl} signed />
          </span>
        </button>
        {plOpen && <IncomingPlGrid rows={data.incoming_pl} />}
        <SummaryLine label="Forecast incl. incoming PL" strong>
          <Qty value={data.totals.forecast_incl_pl} />
        </SummaryLine>
      </div>
    </div>
  );
}

function locationTitle(l: StockStatusLocation): string {
  const code = l.warehouse_code ?? '';
  return l.warehouse_name && l.warehouse_name !== code ? `${code}\n${l.warehouse_name}` : code;
}

/** First cards shown on a phone before "+ n more locations" (mock round 4, frame 1 at 375). */
const CARDS_BEFORE_MORE = 4;

interface LocationCardsProps {
  data: StockStatus;
  shown: StockStatusLocation[];
  filtered: boolean;
  search: string;
  lineIds: string[];
  expanded: ExpandedState;
  onToggle: (warehouseId: string) => void;
}

/** The phone layout of By location: one card per location, then a Total card. */
function LocationCards({ data, shown, filtered, search, lineIds, expanded, onToggle }: LocationCardsProps) {
  const [all, setAll] = React.useState(false);
  const isOpen = (id: string) => expanded === true || Boolean(expanded[id]);
  // A search, or a location opened from it, shows every match.
  const capped = !all && !filtered && shown.length > CARDS_BEFORE_MORE;
  const visible = capped ? shown.slice(0, CARDS_BEFORE_MORE) : shown;
  const sums = sumLocations(shown);
  return (
    <div className="flex flex-col gap-2" data-testid="stock-status-cards">
      {shown.length === 0 && (
        <p className="rounded-md border px-3 py-6 text-center text-sm text-muted-foreground">
          No location matches {search.trim()}
        </p>
      )}
      {visible.map((l) => (
        <div key={l.warehouse_id} className="rounded-md border">
          <button
            type="button"
            className="flex w-full flex-col gap-1 px-3 py-2 text-start"
            onClick={() => onToggle(l.warehouse_id)}
            aria-expanded={isOpen(l.warehouse_id)}
          >
            <span className="flex w-full items-center gap-1.5">
              <span className="truncate font-medium" title={locationTitle(l)}>
                {l.warehouse_code}
              </span>
              {!l.is_active && (
                <Badge variant="secondary" appearance="light" size="xs">
                  Inactive in CRM
                </Badge>
              )}
              <span className="ms-auto font-semibold tabular-nums">
                <Qty value={l.forecast} />
              </span>
            </span>
            <CardFigures onHand={l.on_hand} so={l.so_qty} po={l.po_qty} spo={l.spo_qty} />
          </button>
          {isOpen(l.warehouse_id) && (
            <div className="border-t px-1">
              <DocumentsGrid docs={documentsForLocation(data, l.warehouse_id)} lineIds={lineIds} />
            </div>
          )}
        </div>
      ))}
      {capped && (
        <button
          type="button"
          className="rounded-md border border-dashed px-3 py-2 text-sm font-medium text-primary"
          onClick={() => setAll(true)}
        >
          + {shown.length - CARDS_BEFORE_MORE} more locations
        </button>
      )}
      {shown.length > 0 && (
        <div className="flex flex-col gap-1 rounded-md border bg-muted/40 px-3 py-2" data-testid="stock-status-total-card">
          <span className="flex items-center font-semibold">
            <span>{filtered ? `Shown, ${shown.length} of ${data.locations.length}` : 'Total'}</span>
            <span className="ms-auto tabular-nums">
              <Qty value={sums.forecast} />
            </span>
          </span>
          <CardFigures onHand={sums.on_hand} so={sums.so_qty} po={sums.po_qty} spo={sums.spo_qty} />
        </div>
      )}
    </div>
  );
}

function CardFigures({ onHand, so, po, spo }: { onHand: number; so: number; po: number; spo: number }) {
  const figure = (label: string, value: number) => (
    <span className="flex flex-col">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="tabular-nums">
        <Qty value={value} />
      </span>
    </span>
  );
  return (
    <span className="grid w-full grid-cols-4 gap-2 text-sm">
      {figure('On hand', onHand)}
      {figure('SO', -so)}
      {figure('PO', po)}
      {figure('SPO', spo)}
    </span>
  );
}

function SummaryLine({ label, strong = false, children }: { label: string; strong?: boolean; children: React.ReactNode }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-1 border-t px-3 py-2 first:border-t-0', strong && 'font-semibold')}>
      <span>{label}</span>
      <span className="ms-auto text-end">{children}</span>
    </div>
  );
}

function numberColumn(
  id: string,
  title: string,
  of: (l: StockStatusLocation) => number,
  total: number,
  strong = false,
): ColumnDef<StockStatusLocation> {
  return {
    id,
    header: () => <span className="block text-end">{title}</span>,
    cell: ({ row }) => (
      <span className={cn('block text-end tabular-nums', strong && 'font-semibold')}>
        <Qty value={of(row.original)} />
      </span>
    ),
    footer: () => (
      <span className="block text-end font-semibold tabular-nums">
        <Qty value={total} />
      </span>
    ),
    size: 100,
    meta: { headerTitle: title },
  };
}

function toggle(prev: ExpandedState, id: string): ExpandedState {
  const current = prev === true ? {} : { ...prev };
  if (current[id]) delete current[id];
  else current[id] = true;
  return current;
}

const KIND_VARIANT: Record<LocationDocument['kind'], 'destructive' | 'primary' | 'success'> = {
  SO: 'destructive',
  PO: 'primary',
  SPO: 'success',
};

interface DocumentsGridProps {
  docs: LocationDocument[];
  lineIds: string[];
  /** SO totals: the documents span locations, so each row names its own. */
  locationCodes?: Record<string, string>;
}

function DocumentsGrid({ docs, lineIds, locationCodes }: DocumentsGridProps) {
  const mine = React.useMemo(() => new Set(lineIds), [lineIds]);
  const columns = React.useMemo<ColumnDef<LocationDocument>[]>(() => {
    const all: ColumnDef<LocationDocument>[] = [
      {
        id: 'kind',
        header: 'Type',
        cell: ({ row }) => (
          <Badge variant={KIND_VARIANT[row.original.kind]} appearance="light" size="sm">
            {row.original.kind}
          </Badge>
        ),
        size: 84,
        meta: { headerTitle: 'Type' },
      },
      {
        id: 'number',
        header: 'Doc no',
        cell: ({ row }) => (
          <span className="flex min-w-0 items-center gap-1.5">
            <DocNumber kind={row.original.kind} href={row.original.href} number={row.original.number} />
            {row.original.lineId && mine.has(row.original.lineId) && (
              <Badge variant="info" appearance="light" size="xs">
                This line
              </Badge>
            )}
          </span>
        ),
        size: 190,
        meta: { headerTitle: 'Doc no' },
      },
      {
        id: 'account',
        header: 'Account',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.account ?? undefined}>
            {row.original.account ?? '-'}
          </span>
        ),
        size: 116,
        meta: { headerTitle: 'Account' },
      },
      {
        id: 'name',
        header: 'Name',
        cell: ({ row }) => (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate" title={row.original.name ?? undefined}>
              {row.original.name ?? '-'}
            </span>
            {row.original.coveredInCrm && (
              <Badge variant="secondary" appearance="light" size="xs">
                Covered in CRM
              </Badge>
            )}
          </span>
        ),
        size: 240,
        meta: { headerTitle: 'Name' },
      },
      {
        id: 'project',
        header: 'Project',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.project ?? undefined}>
            {row.original.project ?? '-'}
          </span>
        ),
        size: 140,
        meta: { headerTitle: 'Project' },
      },
      {
        id: 'docDate',
        header: 'Doc date',
        cell: ({ row }) => <span>{date(row.original.docDate) ?? '-'}</span>,
        size: 116,
        meta: { headerTitle: 'Doc date' },
      },
      {
        id: 'due',
        header: 'Due / ETA',
        cell: ({ row }) =>
          row.original.due ? (
            <span>{date(row.original.due)}</span>
          ) : (
            <Badge variant="secondary" appearance="light" size="xs">
              No date
            </Badge>
          ),
        size: 122,
        meta: { headerTitle: 'Due / ETA' },
      },
      {
        id: 'loading',
        header: 'Loading',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.loading ?? undefined}>
            {row.original.loading ?? '-'}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'Loading' },
      },
      {
        id: 'qty',
        header: () => <span className="block text-end">Qty</span>,
        cell: ({ row }) => (
          <span className="block text-end tabular-nums">
            <Qty value={row.original.qty} signed />
          </span>
        ),
        size: 90,
        meta: { headerTitle: 'Qty' },
      },
    ];
    if (!locationCodes) return all;
    const location: ColumnDef<LocationDocument> = {
      id: 'location',
      header: 'Location',
      cell: ({ row }) => {
        const code = locationCodes[row.original.warehouseId] ?? '-';
        return (
          <span className="truncate" title={code}>
            {code}
          </span>
        );
      },
      size: 110,
      meta: { headerTitle: 'Location' },
    };
    return [all[0], all[1], location, ...all.slice(2)];
  }, [mine, locationCodes]);
  return (
    <div className="py-1" data-testid="stock-status-documents">
      <PanelDataGrid
        columns={columns}
        rows={docs}
        getRowId={(d) => d.key}
        listingKey={null}
        emptyTitle="No open documents at this location"
        paginate={false}
        sortable={false}
        scrollerMaxHeight={false}
      />
    </div>
  );
}

function IncomingPlGrid({ rows }: { rows: StockStatusIncomingPl[] }) {
  const columns = React.useMemo<ColumnDef<StockStatusIncomingPl>[]>(
    () => [
      {
        id: 'number',
        header: 'Packing list',
        cell: ({ row }) => (
          <DocNumber
            kind="PL"
            href={`/procurement-management/packing-lists/${row.original.shipment_id}`}
            number={row.original.shipment_number}
          />
        ),
        size: 150,
        meta: { headerTitle: 'Packing list' },
      },
      {
        id: 'container',
        header: 'Container',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.container_number ?? undefined}>
            {row.original.container_number ?? '-'}
          </span>
        ),
        size: 130,
        meta: { headerTitle: 'Container' },
      },
      {
        id: 'eta',
        header: 'ETA',
        cell: ({ row }) => <span>{date(row.original.eta) ?? '-'}</span>,
        size: 100,
        meta: { headerTitle: 'ETA' },
      },
      plNumber('still_to_land', 'Still to land', (r) => r.still_to_land),
      plNumber('on_spo', 'Already on SPO', (r) => r.on_spo),
      plNumber('counted', 'Counted', (r) => r.counted),
    ],
    [],
  );
  return (
    <div className="border-t px-2 py-2">
      <PanelDataGrid
        columns={columns}
        rows={rows}
        getRowId={(r) => r.shipment_id}
        listingKey={null}
        emptyTitle="No packing list on the way"
        paginate={false}
        sortable={false}
        rowClassName={(r) => (r.counted === 0 ? 'text-muted-foreground' : undefined)}
        scrollerMaxHeight={false}
      />
    </div>
  );
}

function plNumber(id: string, title: string, of: (r: StockStatusIncomingPl) => number): ColumnDef<StockStatusIncomingPl> {
  return {
    id,
    header: () => <span className="block text-end">{title}</span>,
    cell: ({ row }) => (
      <span className="block text-end tabular-nums">
        <Qty value={of(row.original)} />
      </span>
    ),
    size: 120,
    meta: { headerTitle: title },
  };
}

function SoTotalsView({ data, search }: { data: StockStatus; search: string }) {
  const [by, setBy] = React.useState<GroupBy>('project');
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  const codes = React.useMemo(
    () => Object.fromEntries(data.locations.map((l) => [l.warehouse_id, l.warehouse_code ?? ''])),
    [data],
  );
  const cols = React.useMemo(() => soColumns(data.locations, search), [data, search]);
  const filtered = search.trim() !== '';
  const groups = React.useMemo(() => {
    const visible = new Set(cols.map((c) => c.warehouse_id));
    return groupSalesOrders(
      data.sales_orders.filter((s) => visible.has(s.warehouse_id)),
      by,
    );
  }, [data, cols, by]);
  const withSo = data.locations.filter((l) => l.so_qty > 0).length;
  const shownTotal = cols.reduce((t, c) => t + c.so_qty, 0);
  const groupLabel = GROUP_BY_OPTIONS.find((o) => o.value === by)?.label ?? 'Project';

  const columns = React.useMemo<ColumnDef<SoGroup>[]>(
    () => [
      {
        id: 'expand',
        header: () => null,
        cell: ({ row }) =>
          row.getIsExpanded() ? (
            <ChevronDown className="size-4 text-muted-foreground" aria-hidden />
          ) : (
            <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
          ),
        size: 36,
        enableResizing: false,
        meta: {
          headerTitle: 'Expand',
          expandedContent: (g: SoGroup) => (
            <DocumentsGrid
              docs={documentsForLocation({ sales_orders: g.rows, purchase_orders: [], spos: [] }, null)}
              lineIds={[]}
              locationCodes={codes}
            />
          ),
        },
      },
      {
        id: 'label',
        header: groupLabel,
        cell: ({ row }) => (
          <span className="flex min-w-0 flex-col">
            <span className="truncate font-medium" title={row.original.label}>
              {row.original.label}
            </span>
            {row.original.secondary && (
              <span className="truncate text-xs text-muted-foreground">{row.original.secondary}</span>
            )}
          </span>
        ),
        footer: () => (
          <span className="font-semibold">{filtered ? `Shown, ${cols.length} of ${withSo} locations with SO` : 'Total'}</span>
        ),
        size: 220,
        meta: { headerTitle: groupLabel },
      },
      ...cols.map<ColumnDef<SoGroup>>((c) => ({
        id: `wh-${c.warehouse_id}`,
        header: () => <span className="block truncate text-end">{c.warehouse_code}</span>,
        cell: ({ row }) => (
          <span className="block text-end tabular-nums">
            <Qty value={-(row.original.byWarehouse[c.warehouse_id] ?? 0)} />
          </span>
        ),
        footer: () => (
          <span className="block text-end font-semibold tabular-nums">
            <Qty value={-c.so_qty} />
          </span>
        ),
        size: 100,
        meta: { headerTitle: c.warehouse_code },
      })),
      {
        id: 'total',
        header: () => <span className="block text-end">SO</span>,
        cell: ({ row }) => (
          <span className="block text-end font-semibold tabular-nums">
            <Qty value={-row.original.total} />
          </span>
        ),
        footer: () => (
          <span className="block text-end font-semibold tabular-nums">
            <Qty value={-shownTotal} />
          </span>
        ),
        size: 110,
        meta: { headerTitle: 'SO' },
      },
    ],
    [cols, codes, filtered, groupLabel, shownTotal, withSo],
  );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Group by</span>
        <SearchableSelect
          value={by}
          onChange={(v) => setBy((v || 'project') as GroupBy)}
          options={GROUP_BY_OPTIONS}
          clearable={false}
          size="sm"
          triggerClassName="w-48"
        />
      </div>
      <PanelDataGrid
        columns={columns}
        rows={groups}
        getRowId={(g) => g.key}
        listingKey={null}
        emptyTitle={filtered ? `No SO at locations matching ${search.trim()}` : 'No open SO for this item'}
        paginate={false}
        sortable={false}
        expanded={expanded}
        onExpandedChange={setExpanded}
        onRowClick={(g) => setExpanded((prev) => toggle(prev, g.key))}
        scrollerMaxHeight={false}
      />
      {filtered && (
        <div className="flex items-center rounded-md border px-3 py-2 text-sm font-semibold">
          <span>All locations</span>
          <span className="ms-auto">
            <Qty value={-data.totals.so_qty} />
          </span>
        </div>
      )}
    </div>
  );
}
