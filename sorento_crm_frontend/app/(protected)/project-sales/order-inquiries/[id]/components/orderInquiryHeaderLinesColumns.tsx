'use client';

import * as React from 'react';
import {
  Check,
  ChevronDown,
  CircleCheck,
  CircleDashed,
  ChevronRight,
  History as HistoryIcon,
  Pencil,
  X,
} from 'lucide-react';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateInMalaysia } from '@/lib/helpers';
import {
  OrderInquiryLineStatePill,
  OrderInquiryVerbPill,
  ReservePill,
} from '../../../_shared/components/OrderInquiryVerbPill';
import { OrderInquiryStockGrid } from '../../../_shared/components/OrderInquiryStockGrid';
import { formatInquiryQty, raisedKindLabel } from '../../../_shared/lib/orderInquiryWorklist';
import {
  lineConfirmationOf,
  lineFooterTotals,
  lineOf,
  type OrderInquiryLine,
  type OrderInquiryLineRow,
} from '../../../_shared/lib/orderInquiryLineFold';
import {
  DeliveryDateCell,
  documentsOf,
  ItemCodeCell,
  LocationCell,
  orderInquirySuggestedColumn,
  RaisedCell,
  SupplierCell,
  WorklistPill,
} from '../../components/orderInquiryWorklistColumns';
import type {
  OrderInquiryLineSplit,
  OrderInquiryWorklistRow,
} from '../../../_shared/types/orderInquiry.types';
import { OrderInquiryDocumentLink, ViaSpoPoNumber } from '../../components/OrderInquiryDocumentDialog';
import { SoLineAttachmentsButton } from '../../../_shared/components/SoLineAttachmentsButton';
import type { SoLineAttachmentsByLine } from '../../../_shared/services/soLineAttachmentService';

/**
 * The Lines tab's own columns (AC-DP-03): Expand, Product, SO line, Qty, Taken,
 * Remaining, Delivery date, Supplier, PO, SPO, Location, Instruction, State, Reserve
 * actions. Taken/Remaining are S3 (`PLAN-board-oi-mechanical-22sep.md`, AC-B3-1); SO
 * line is S6 (AC-B6-1). Product/Qty/Delivery date/Supplier/Location/Instruction reuse
 * the worklist's OWN cell renderers (`orderInquiryWorklistColumns.tsx`) so the same
 * fact reads the same way on both screens. PO/SPO are the ONE deliberate difference
 * (AC-DP-04): this single-header screen opens the simpler `OrderInquiryDocumentDialog`,
 * not the worklist's bundling/reallocate-aware `DocumentsCell` - a header's own lines
 * have no "which OTHER row is this bundled with" question to answer, since every row
 * here already belongs to the one document.
 *
 * Round 4 (`PLAN-oi-request-cs-reserve.md` section 6e.2, owner round 4, 24 Sep,
 * AC-RS-83): the State cell's own reserve pill is plain text again (round 3's
 * click-target button is retired) - the reserve icons render beside it in the same
 * cell (AC-RS-83c), gated by `canReserve` and driven by the
 * caller's own staged-decision map (`OrderInquiryDetail.tsx` owns that state; this
 * column is a pure renderer over it).
 *
 * `PLAN-oi-no-double-count-25sep.md` S0 (issue #1248, owner rulings 26 Sep 2026): each
 * grid row is ONE sales order line (`OrderInquiryLineRow`: the line's primary row plus the
 * line, `orderInquiryLineFold.ts`). No. replaces the SO line column; SO Qty, Requested,
 * Taken, Remaining replace Qty / Taken / Remaining (G4) and carry no Was / now annotation
 * (G1); PO / SPO list every document of the line's live rows as first + "+N" (G5); the
 * State cell carries ONE History icon for the line (G3), replacing the reserve History
 * icon and the decision trail icon.
 */

/** One line's staged decision (6e.2) - not yet posted, held in `OrderInquiryDetail`'s
 * own `Record<rowId, StagedReserveEntry>` and cleared on commit or Undo. */
export interface StagedReserveEntry {
  kind: 'reserve' | 'amend';
  qty: number;
  /** Only meaningful for `kind: 'reserve'` - the chip's own "@ CODE" suffix
   * (AC-RS-84/85); an amend chip never names a location (its own is locked). */
  locationLabel?: string | null;
  warehouseId?: string | null;
  reason?: string | null;
}

/** The first PO (or SPO) document this line carries, for a document trigger - reusing the
 * worklist's OWN derivation (`documentsOf`, issue #1215 point 5) rather than a bare
 * `kind === 'po'` link. An SPO-only link naming its source PO (`source_po_number`) used
 * to read a plain dash here even though the worklist already printed it "via SPO" - the
 * two screens now agree about what counts as a document on this row. `null` when the
 * line names none of that kind - the cell then reads a plain dash, same as the worklist. */
function firstLinkOf(row: OrderInquiryWorklistRow, kind: 'po' | 'spo') {
  return documentsOf(row, kind)[0] ?? null;
}

/** Every document of `kind` across the line's live rows, first-seen order, each with the
 * row that carries it (the link lookup below needs that row). */
function lineDocumentsOf(line: OrderInquiryLine, kind: 'po' | 'spo') {
  const seen = new Set<string>();
  const entries: { row: OrderInquiryWorklistRow; document: string }[] = [];
  for (const row of line.liveRows) {
    for (const entry of documentsOf(row, kind)) {
      if (seen.has(entry.document)) continue;
      seen.add(entry.document);
      entries.push({ row, document: entry.document });
    }
  }
  return entries;
}

function LineDocumentsCell({ line, kind }: { line: OrderInquiryLine; kind: 'po' | 'spo' }) {
  const entries = lineDocumentsOf(line, kind);
  if (entries.length === 0) return <span className="text-muted-foreground">-</span>;
  const [first, ...rest] = entries;
  return (
    <span className="flex min-w-0 items-center gap-1">
      <DocumentCell row={first.row} kind={kind} document={first.document} />
      {rest.length > 0 ? (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-5 shrink-0 rounded-full px-1.5 text-xs text-muted-foreground"
              aria-label={`${rest.length} more ${kind.toUpperCase()}`}
            >
              +{rest.length}
            </Button>
          </PopoverTrigger>
          {/* Portalled (owner, 29 Sep 2026): unportalled, the content painted inside the
              table and the next row's own "+N" chip showed through it. At the document
              root it sits above every row, and `collisionPadding` keeps it inside the
              viewport the way the shared `PillOverflow` popover does. */}
          <PopoverPortal>
            <PopoverContent
              align="start"
              collisionPadding={8}
              className="w-auto max-w-xs space-y-1 p-2"
            >
              {entries.map((entry) => (
                <div key={entry.document}>
                  <DocumentCell row={entry.row} kind={kind} document={entry.document} />
                </div>
              ))}
            </PopoverContent>
          </PopoverPortal>
        </Popover>
      ) : null}
    </span>
  );
}

function DocumentCell({
  row,
  kind,
  document,
}: {
  row: OrderInquiryWorklistRow;
  kind: 'po' | 'spo';
  document?: string;
}) {
  const entry = document
    ? (documentsOf(row, kind).find((candidate) => candidate.document === document) ?? null)
    : firstLinkOf(row, kind);
  if (!entry) return <span className="text-muted-foreground">-</span>;
  // The REAL link behind this entry, for the PO/SPO popover's own id and (#1215 point 2,
  // R15) the line it sits on - `documentsOf` states the document, not the link's
  // identity.
  const link = (row.links ?? []).find(
    (candidate) => candidate.kind === kind && candidate.document === entry.document,
  );
  // R17 (owner rulings, 25 Sep 2026, "I also need here to be clickable"): a PO entry
  // read off an SPO link's own `source_po_number` (`entry.via === 'spo'`) has no real
  // po-kind link behind it, so `link` above is always undefined - `entry.poId` is the
  // resolved identity instead (review round 1's should-fix 4 plain-text fix reversed
  // by this ruling; the dead-lightbox bug is fixed by resolving the PO, not by
  // removing the trigger).
  const derived = kind === 'po' && entry.via === 'spo';
  return (
    <span className="flex min-w-0 items-center gap-1">
      {derived ? (
        <ViaSpoPoNumber poNumber={entry.document} purchaseOrderId={entry.poId} />
      ) : (
        <OrderInquiryDocumentLink
          kind={kind}
          document={entry.document}
          poId={link?.po_id}
          poLineId={link?.po_line_id}
          spoLineId={link?.spo_allocation_id}
        />
      )}
      {entry.via ? (
        <WorklistPill testId={`lines-${kind}-via-${row.id}`}>
          via {entry.via === 'po' ? 'PO' : 'SPO'}
        </WorklistPill>
      ) : null}
    </span>
  );
}

/** `20/10/2026`. */
function splitDate(iso: string | null): string {
  return iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '';
}

/** OI-LINE-SPLIT (UAC D3): the row of a split line carries "Split N". */
function SplitChip({ row }: { row: OrderInquiryWorklistRow }) {
  const count = row.splits?.length ?? 0;
  if (count === 0) return null;
  return (
    <Badge variant="secondary" appearance="light" size="sm" className="shrink-0">
      {`Split ${count}`}
    </Badge>
  );
}

/** Where a split sits, in words: a numbered line, an unnumbered one, or a new line. */
function sitsOnText(split: OrderInquiryLineSplit, itemCode: string | null | undefined): string {
  if (split.is_cancel) return '';
  if (!split.sits_on_line_id) return 'New line (not in AutoCount yet)';
  if (split.sits_on_line_no != null) return `line ${split.sits_on_line_no}`;
  return itemCode ? `line ${itemCode}` : 'line';
}

const SPLIT_COLUMNS = (itemCode: string | null | undefined): ColumnDef<OrderInquiryLineSplit>[] => [
  {
    accessorKey: 'qty',
    header: () => <span className="block text-right">Qty</span>,
    size: 80,
    meta: { headerTitle: 'Qty' },
    cell: ({ row }) => (
      <span className="block text-right tabular-nums">{formatInquiryQty(row.original.qty)}</span>
    ),
  },
  {
    accessorKey: 'delivery_date',
    header: 'Date',
    // dd/mm/yyyy plus the header's own grip and padding, unclipped at 1280.
    size: 130,
    meta: { headerTitle: 'Date' },
    cell: ({ row }) => splitDate(row.original.delivery_date),
  },
  {
    accessorKey: 'change',
    header: 'Change',
    size: 150,
    meta: { headerTitle: 'Change' },
    cell: ({ row }) => (
      <OrderInquiryVerbPill
        verb={row.original.change === 'CANCEL BALANCE' ? 'CANCEL_BALANCE' : row.original.change}
      />
    ),
  },
  {
    id: 'sits_on',
    header: 'Sits on',
    size: 160,
    meta: { headerTitle: 'Sits on' },
    cell: ({ row }) => {
      const text = sitsOnText(row.original, itemCode);
      return (
        <span className="block truncate text-muted-foreground" title={text}>
          {text}
        </span>
      );
    },
  },
  {
    accessorKey: 'remark',
    header: 'Remark',
    size: 240,
    meta: { headerTitle: 'Remark' },
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.remark}>
        {row.original.remark}
      </span>
    ),
  },
];

function LineSplitsGrid({
  splits,
  itemCode,
}: {
  splits: OrderInquiryLineSplit[];
  itemCode: string | null | undefined;
}) {
  const columns = React.useMemo(() => SPLIT_COLUMNS(itemCode), [itemCode]);
  const table = useReactTable({
    columns,
    data: splits,
    getRowId: (split) => split.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });
  return (
    <div className="mb-2 max-w-3xl">
      <DataGrid
        table={table}
        recordCount={splits.length}
        tableLayout={{
          width: 'fixed',
          columnsResizable: true,
          columnsDraggable: false,
          scrollerMaxHeight: false,
        }}
      >
        <DataGridTable />
      </DataGrid>
    </div>
  );
}

/** OI-LINE-SPLIT (UAC D3): the splits of the line, read-only: Qty, Date, Change, Sits on,
 * Remark. Nothing when the line is not split. */
function LineSplitsList({
  splits,
  itemCode,
}: {
  splits: OrderInquiryLineSplit[];
  itemCode: string | null | undefined;
}) {
  if (splits.length === 0) return null;
  return <LineSplitsGrid splits={splits} itemCode={itemCode} />;
}

function reserveChipText(staged: StagedReserveEntry): string {
  if (staged.kind === 'amend') return `Amend to ${staged.qty}`;
  // No location staged (the server defaults it): no dangling "@".
  if (staged.qty > 0 && staged.locationLabel) return `Reserve ${staged.qty} @ ${staged.locationLabel}`;
  return `Reserve ${staged.qty}`;
}

export function ReserveActionsCell({
  row,
  staged,
  onTickReserve,
  onRejectReserve,
  onEditReserve,
  onAmendReserve,
  onUndoStaged,
  canAmend = true,
}: {
  row: OrderInquiryWorklistRow;
  staged?: StagedReserveEntry;
  onTickReserve?: (row: OrderInquiryWorklistRow) => void;
  onRejectReserve?: (row: OrderInquiryWorklistRow) => void;
  onEditReserve?: (row: OrderInquiryWorklistRow) => void;
  onAmendReserve?: (row: OrderInquiryWorklistRow) => void;
  onUndoStaged?: (rowId: string) => void;
  canAmend?: boolean;
}) {
  if (staged) {
    const chipText = reserveChipText(staged);
    return (
      <div className="flex min-w-0 items-center gap-1.5">
        <span
          title={chipText}
          className="min-w-0 truncate rounded-full border border-dashed border-muted-foreground/50 px-2 py-0.5 text-xs text-muted-foreground"
        >
          {chipText}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 px-1.5 text-xs"
          onClick={() => onUndoStaged?.(row.id)}
        >
          Undo
        </Button>
      </div>
    );
  }
  if (row.reserve_state === 'requested') {
    return (
      <div className="flex items-center gap-1">
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="size-6"
          aria-label="Reserve"
          title="Reserve the full requested qty at the default pool"
          onClick={() => onTickReserve?.(row)}
        >
          <Check className="size-3.5" aria-hidden />
        </Button>
        {/* OI-BULK-RESERVE (AC-BR-09): Reserve 0 + one reason, the line's own copy of the
            strip's Decide > Reject... */}
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="size-6"
          aria-label="Reject"
          title="Reject"
          onClick={() => onRejectReserve?.(row)}
        >
          <X className="size-3.5" aria-hidden />
        </Button>
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="size-6"
          aria-label="Edit reserve"
          title="Edit reserve"
          onClick={() => onEditReserve?.(row)}
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      </div>
    );
  }
  // 6e.4 (AC-RS-83b): a declined line (CS answered 0) gets the same Amend as a reserved
  // one, so "0 -> up" stays reachable. Its reserve history is the Reserve tab of the
  // line's one History dialog now (owner ruling 26 Sep, G3), not an icon of its own.
  // The cross-OI Lines page draws no Amend (OI-BULK-RESERVE v4): `canAmend={false}`.
  if (canAmend && (row.reserve_state === 'reserved' || row.reserve_state === 'declined')) {
    return (
      <div className="flex items-center gap-1">
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          className="size-6"
          aria-label="Amend reserve"
          title="Amend reserve"
          onClick={() => onAmendReserve?.(row)}
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      </div>
    );
  }
  return null;
}

/**
 * W1: the worklist PO cell's own confirmed / proposed marks (`DraftMark` in
 * `orderInquiryWorklistColumns.tsx`): `CircleCheck` emerald once purchasing confirmed,
 * `CircleDashed` muted while part of the line still waits.
 */
function LineConfirmationMark({ line }: { line: OrderInquiryLine }) {
  const confirmation = lineConfirmationOf(line);
  if (confirmation.kind === 'none') return null;
  if (confirmation.kind === 'confirmed') {
    const when = confirmation.at ? ` on ${formatDateInMalaysia(confirmation.at)}` : '';
    const label = `Confirmed by ${confirmation.by ?? 'Purchasing'}${when}`;
    return (
      <span
        data-testid="line-confirmed-mark"
        role="img"
        title={label}
        aria-label={label}
        className="inline-flex"
      >
        <CircleCheck className="size-3.5 shrink-0 text-emerald-600" aria-hidden />
      </span>
    );
  }
  const label = `${confirmation.confirmed} of ${confirmation.total} rows confirmed`;
  return (
    <span
      data-testid="line-partly-confirmed-mark"
      role="img"
      title={label}
      aria-label={label}
      className="inline-flex"
    >
      <CircleDashed className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
    </span>
  );
}

/** AC-ND-17: a footer total over every loaded line (the tab loads the whole set at once,
 * so there is no server page this could disagree with). */
function LineFooter({
  rows,
  field,
}: {
  rows: OrderInquiryLineRow[];
  field: 'soQty' | 'requested' | 'taken' | 'remaining';
}) {
  const totals = lineFooterTotals(rows.map(lineOf));
  return <span className="tabular-nums">{formatInquiryQty(String(totals[field]))}</span>;
}

const QUANTITY_COLUMNS: {
  id: string;
  title: string;
  field: 'soQty' | 'requested' | 'taken' | 'remaining';
}[] = [
  // S2: `soQty` is the server's `so_line_qty`, the sales order grid's own Qty.
  { id: 'so_qty', title: 'SO Qty', field: 'soQty' },
  { id: 'requested', title: 'Requested', field: 'requested' },
  { id: 'taken', title: 'Taken', field: 'taken' },
  { id: 'remaining', title: 'Remaining', field: 'remaining' },
];

export function useOrderInquiryHeaderLinesColumns({
  canReserve,
  stagedByRowId,
  onTickReserve,
  onRejectReserve,
  onEditReserve,
  onAmendReserve,
  onLineHistoryClick,
  onUndoStaged,
  attachmentsByLine,
  canEditAttachments = false,
}: {
  /** AC-RS-83/83c: gates the reserve icons inside the State cell - a viewer without
   * `projects.order_inquiries.reserve` sees the pills only. */
  canReserve?: boolean;
  /** 6e.2: this line's own staged (not yet committed) decision, keyed by row id -
   * `OrderInquiryDetail.tsx` owns the map, this column only renders over it. */
  stagedByRowId?: Record<string, StagedReserveEntry>;
  /** AC-RS-84: the tick - stages the full requested qty at the default pool, no dialog. */
  onTickReserve?: (row: OrderInquiryWorklistRow) => void;
  /** OI-BULK-RESERVE (AC-BR-09): the x on a requested line - opens "Reject 1 line". */
  onRejectReserve?: (row: OrderInquiryWorklistRow) => void;
  /** AC-RS-85: the pencil on a requested line - opens `ReserveLineForm` in reserve mode. */
  onEditReserve?: (row: OrderInquiryWorklistRow) => void;
  /** AC-RS-86: the pencil on a reserved line - opens `ReserveLineForm` in amend mode. */
  onAmendReserve?: (row: OrderInquiryWorklistRow) => void;
  /** AC-ND-13/14 (owner ruling 26 Sep, G3): opens the line's one History dialog. */
  onLineHistoryClick?: (line: OrderInquiryLine) => void;
  /** Drops a staged decision, restoring the two icons. */
  onUndoStaged?: (rowId: string) => void;
  /**
   * #1312 (AC-U6): every line's own clarification files, keyed by CORE sales-order
   * line id - `OrderInquiryLinesTab` owns the ONE lookup call this reads over
   * (`useSoLineAttachmentLookup`), the same shape the fulfilment board's own list
   * view passes into its columns. This is a pure renderer over it, like every other
   * prop here - no hook call of its own, since a cell function is invoked directly
   * by this file's own unit test (`orderInquiryHeaderLinesColumns.test.tsx`), outside
   * a real React render pass, where a hook call would break the Rules of Hooks.
   */
  attachmentsByLine?: SoLineAttachmentsByLine;
  /** #1312 (Q6): `projects.projects.edit` - gates the lightbox's own upload/remove. */
  canEditAttachments?: boolean;
} = {}): ColumnDef<OrderInquiryLineRow>[] {
  return React.useMemo<ColumnDef<OrderInquiryLineRow>[]>(() => {
    const columns: ColumnDef<OrderInquiryLineRow>[] = [
      // `PLAN-oi-request-cs-reserve.md` 3.9 (AC-RS-40): the board's own stock grid, a
      // chevron away - purchasing used to open the fulfilment board just to check BRW.
      {
        id: 'expand',
        header: () => <span className="sr-only">Expand</span>,
        cell: ({ row }) => (
          <Button
            type="button"
            mode="icon"
            variant="ghost"
            size="sm"
            className="size-6"
            aria-label={`${row.getIsExpanded() ? 'Hide' : 'Show'} stock for ${
              row.original.item_code ?? 'this line'
            }`}
            aria-expanded={row.getIsExpanded()}
            onClick={(event) => {
              event.stopPropagation();
              row.toggleExpanded();
            }}
          >
            {row.getIsExpanded() ? (
              <ChevronDown className="size-3.5" aria-hidden />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden />
            )}
          </Button>
        ),
        size: 44,
        minSize: 44,
        enableSorting: false,
        enableResizing: false,
        enableHiding: false,
        meta: {
          headerTitle: 'Expand',
          expandedContent: (line: OrderInquiryLineRow) => (
            <div className="px-3 py-2">
              <LineSplitsList splits={line.splits ?? []} itemCode={line.item_code} />
              {line.product_id ? (
                <OrderInquiryStockGrid
                  productId={line.product_id}
                  location={line.location ?? null}
                />
              ) : (
                <p className="text-xs text-muted-foreground">
                  No product resolved for this line yet.
                </p>
              )}
            </div>
          ),
        },
      },
      buildSelectColumn<OrderInquiryLineRow>({
        rowLabel: (row) => `Select ${row.original.item_code ?? 'line'}`,
      }),
      // AC-ND-3 (owner ruling 26 Sep, G4): the sales order's own line No., replacing the
      // SO line column - the header already names the sales order.
      {
        id: 'line_no',
        accessorFn: (row) => lineOf(row).lineNo ?? Number.POSITIVE_INFINITY,
        header: ({ column }) => <DataGridColumnHeader title="No." column={column} />,
        size: 72,
        meta: { headerTitle: 'No.', skeleton: <Skeleton className="h-4 w-6" /> },
        cell: ({ row }) => {
          const lineNo = lineOf(row.original).lineNo;
          return lineNo == null ? (
            <span className="text-muted-foreground">-</span>
          ) : (
            <span className="tabular-nums">{lineNo}</span>
          );
        },
      },
      {
        accessorKey: 'item_code',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        size: 220,
        meta: { headerTitle: 'Product', skeleton: <Skeleton className="h-4 w-32" /> },
        // AC-DP-03, owner ruling 21 Sep: code only, one line - never the second
        // `product_name` line the worklist's own cell prints for a real row.
        // OI-LINE-SPLIT (UAC D3): a split line reads "Split N" beside its code.
        cell: ({ row }) => (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 truncate">
              <ItemCodeCell row={row.original} codeOnly />
            </span>
            <SplitChip row={row.original} />
          </span>
        ),
      },
      // W1 (PR #1266, owner hand test 26 Sep): the line's own confirmed mark, in the gap
      // the owner boxed between Product and SO Qty. Not sortable: no other icon column on
      // this grid sorts.
      {
        id: 'confirmation',
        header: () => <span className="sr-only">Confirmed</span>,
        size: 44,
        minSize: 44,
        enableSorting: false,
        enableResizing: false,
        // Review N4: a blank header with a lone drag grip reads as stray chrome.
        meta: { headerTitle: 'Confirmed', draggable: false },
        cell: ({ row }) => <LineConfirmationMark line={lineOf(row.original)} />,
      },
      // AC-ND-3..5b (owner ruling 26 Sep, G4): SO Qty, Requested, Taken, Remaining - the
      // line's own sums (`orderInquiryLineFold.ts`), no Was / now annotation (G1).
      ...QUANTITY_COLUMNS.map(
        ({ id, title, field }): ColumnDef<OrderInquiryLineRow> => ({
          id,
          accessorFn: (row) => lineOf(row)[field],
          header: ({ column }) => <DataGridColumnHeader title={title} column={column} />,
          size: id === 'requested' || id === 'remaining' ? 120 : 100,
          meta: { headerTitle: title, skeleton: <Skeleton className="h-4 w-10" /> },
          cell: ({ row }) => {
            // A row that names no sales order line has no SO Qty (L17).
            const value = lineOf(row.original)[field];
            return (
              <span className="tabular-nums">
                {value == null ? '-' : formatInquiryQty(String(value))}
              </span>
            );
          },
          footer: ({ table }) => (
            <LineFooter
              rows={table.getPrePaginationRowModel().rows.map((r) => r.original)}
              field={field}
            />
          ),
        }),
      ),
      {
        accessorKey: 'delivery_date',
        header: ({ column }) => <DataGridColumnHeader title="Delivery date" column={column} />,
        size: 140,
        meta: { headerTitle: 'Delivery date', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => <DeliveryDateCell row={row.original} />,
      },
      {
        accessorKey: 'supplier',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        size: 170,
        meta: { headerTitle: 'Supplier', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => <SupplierCell row={row.original} />,
      },
      {
        id: 'po_number',
        accessorFn: (row) => lineDocumentsOf(lineOf(row), 'po')[0]?.document ?? '',
        header: ({ column }) => <DataGridColumnHeader title="PO" column={column} />,
        size: 200,
        meta: { headerTitle: 'PO' },
        cell: ({ row }) => <LineDocumentsCell line={lineOf(row.original)} kind="po" />,
      },
      {
        id: 'spo_number',
        accessorFn: (row) => lineDocumentsOf(lineOf(row), 'spo')[0]?.document ?? '',
        header: ({ column }) => <DataGridColumnHeader title="SPO" column={column} />,
        size: 200,
        meta: { headerTitle: 'SPO' },
        cell: ({ row }) => <LineDocumentsCell line={lineOf(row.original)} kind="spo" />,
      },
      // AC-LT-07: the SAME Suggested column the worklist carries, right after SPO. It
      // reads the primary row's suggestions (S2 wires a line-level read).
      orderInquirySuggestedColumn() as ColumnDef<OrderInquiryLineRow>,
      {
        accessorKey: 'location',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        size: 130,
        meta: { headerTitle: 'Location', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) => <LocationCell row={row.original} />,
      },
      {
        accessorKey: 'verb',
        header: ({ column }) => <DataGridColumnHeader title="Instruction" column={column} />,
        size: 200,
        meta: { headerTitle: 'Instruction', skeleton: <Skeleton className="h-4 w-24" /> },
        // G5: the line's most urgent instruction (CANCEL_BALANCE > CHANGE_SO > DELAY >
        // ADVANCE > the primary row's own verb). The pill only, never the shared cell's
        // note (i): G1 moves the note ("Replaces 2 used ...") to History's Why (review
        // B2). A cancelled line has nothing to instruct, so it reads "-" (review N1).
        cell: ({ row }) => {
          const line = lineOf(row.original);
          if (line.lineCancelled) return <span className="text-muted-foreground">-</span>;
          return <OrderInquiryVerbPill verb={line.instructionRow.verb} />;
        },
      },
      {
        // AC-DT-6 (`PLAN-oi-decision-trail-ui.md`): the same Raised column the worklist
        // carries - `RaisedCell` is shared so the same row reads the same way on both
        // screens. Hidden by default on BOTH now (round 2 ruling); `accessorFn` is what
        // the column picker keys "can this be listed" on
        // (`data-grid-column-visibility.tsx`), so a bare `id` + `cell` made this column
        // impossible to ever turn back on.
        id: 'raise_event',
        accessorFn: (row) => raisedKindLabel(row) ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Raised via" column={column} />,
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Raised via', skeleton: <Skeleton className="h-4 w-24" /> },
        cell: ({ row }) => <RaisedCell row={row.original} />,
      },
      // `PLAN-oi-request-cs-reserve.md` 6e.2 (AC-RS-83): the pill is plain text.
      {
        // PR #1266 review nit: sorts on the line's own State, what the cell shows, not
        // the primary row's raw state (To confirm, Line cancelled and Nothing to buy
        // sorted as raised / placed). Same id, so saved column preferences still apply.
        id: 'state',
        accessorFn: (row) => lineOf(row).state,
        header: ({ column }) => <DataGridColumnHeader title="State" column={column} />,
        // AC-RS-83c (owner, 24 Sep: "this pen can put right next to state?"): the
        // reserve icons sit in this cell, right of the pill - no separate column, which
        // saved column preferences appended after Location. Wide enough for the pill
        // plus a staged chip and Undo. No `minSize`: a user who drags it narrower than
        // the icons is choosing that, and can drag it back out again (owner, 24 Sep).
        // OI-BULK-RESERVE: +28 for the x between the tick and the pen.
        size: canReserve ? 438 : 220,
        meta: { headerTitle: 'State' },
        cell: ({ row }) => {
          const line = lineOf(row.original);
          const primary = line.primary;
          const reserveState = line.liveRows.length > 0 ? primary.reserve_state : null;
          const pill =
            reserveState === 'requested' ||
            reserveState === 'reserved' ||
            reserveState === 'declined' ? (
              <ReservePill
                reserveState={reserveState}
                reservedQty={primary.reserved_qty}
                requestedQty={primary.requested_qty}
              />
            ) : (
              <OrderInquiryLineStatePill state={line.state} />
            );
          // AC-ND-13 (owner ruling 26 Sep, G3): ONE History icon on every line - rows,
          // decisions and reserve history behind one dialog - never gated on a core line
          // or on `canReserve`.
          const history = (
            <Button
              type="button"
              mode="icon"
              variant="ghost"
              size="sm"
              className="size-6 shrink-0"
              aria-label="History"
              title="History"
              onClick={(event) => {
                event.stopPropagation();
                onLineHistoryClick?.(line);
              }}
            >
              <HistoryIcon className="size-3.5" aria-hidden />
            </Button>
          );
          // #1312 (AC-U6): the same paperclip the fulfilment board carries, on every
          // line that names a core sales-order line - a row raised before AutoCount
          // reconciled it (`core_line_id` null) has nowhere to file a clarification
          // against, same rule the board's own list view follows.
          const coreLineId = row.original.core_line_id;
          const attachments = coreLineId ? (attachmentsByLine?.[coreLineId] ?? []) : null;

          if (!canReserve || line.liveRows.length === 0) {
            return (
              <div className="flex min-w-0 items-center gap-1">
                <span className="shrink-0">{pill}</span>
                {history}
                {coreLineId ? (
                  <SoLineAttachmentsButton
                    lineId={coreLineId}
                    label={`${row.original.so_number ?? ''} L${line.lineNo ?? ''} ${row.original.item_code ?? ''}`}
                    attachments={attachments ?? []}
                    canEdit={canEditAttachments}
                  />
                ) : null}
              </div>
            );
          }
          return (
            <div className="flex min-w-0 items-center gap-1">
              <span className="shrink-0">{pill}</span>
              {history}
              {coreLineId ? (
                <SoLineAttachmentsButton
                  lineId={coreLineId}
                  label={`${row.original.so_number ?? ''} L${line.lineNo ?? ''} ${row.original.item_code ?? ''}`}
                  attachments={attachments ?? []}
                  canEdit={canEditAttachments}
                />
              ) : null}
              <ReserveActionsCell
                row={primary}
                staged={stagedByRowId?.[primary.id]}
                onTickReserve={onTickReserve}
                onRejectReserve={onRejectReserve}
                onEditReserve={onEditReserve}
                onAmendReserve={onAmendReserve}
                onUndoStaged={onUndoStaged}
              />
            </div>
          );
        },
      },
    ];

    return columns;
  }, [
    canReserve,
    stagedByRowId,
    onTickReserve,
    onRejectReserve,
    onEditReserve,
    onAmendReserve,
    onLineHistoryClick,
    onUndoStaged,
    attachmentsByLine,
    canEditAttachments,
  ]);
}
