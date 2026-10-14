'use client';

import * as React from 'react';
import Link from 'next/link';
import { ExternalLink } from 'lucide-react';
import type { ColumnDef } from '@tanstack/react-table';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Badge } from '@/components/ui/badge';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { formatDateInMalaysia } from '@/lib/helpers';
import { statusPillClass } from '@/lib/status-pill';
import {
  useOrderInquiryPoDetail,
  useOrderInquirySpoDetail,
} from '../../_shared/hooks/useOrderInquiry';
import { formatInquiryQty } from '../../_shared/lib/orderInquiryWorklist';
import type {
  OrderInquiryPoDetailLine,
  OrderInquirySpoDetailLine,
} from '../../_shared/types/orderInquiry.types';
import { BookSoCell, bookSoSortValue } from '@/app/(protected)/scm/components/BookSoCell';
import { sameDisplayText } from '@/lib/product-display';

/**
 * ONE document, read-only, in a real dialog (R9; the captain, 27 Aug: "the popup on the
 * PO number is bad UI").
 *
 * It replaced a `Popover`, which the grid's own scroll container clipped, could only ever
 * open a purchase order, and left an SPO number as dead text. A dialog carries both
 * books, scrolls inside itself, closes on Escape and fits 375.
 *
 * Read-only with exactly one way out: "Open document". A lightbox that offered actions
 * would be a second place to act on a document, and this page's actions are all bulk.
 */
export function OrderInquiryDocumentDialog({
  kind,
  document,
  poId,
  poLineId,
  spoLineId,
  suggestedLineId,
  open,
  onOpenChange,
  highlightLines,
}: {
  kind: 'po' | 'spo';
  /** `202607-S0105` or `SPO-2026/08-0015`. Never an id: it is what the buyer quotes. */
  document: string;
  /** Addresses the purchase order. Null on an SPO, which is addressed by its number. */
  poId?: string | null;
  /**
   * Issue #1215 point 2: the line the OPENING row's own REAL link sits on, so the PO
   * lines grid can highlight it - a PO with two lines of the same item made the panel's
   * Item column alone ambiguous about which one. Ignored on an SPO document.
   */
  poLineId?: string | null;
  /**
   * R15 (owner rulings, 25 Sep 2026, hand test on stack C): the mirror of `poLineId`
   * for the SPO lightbox - the `spo_allocations` row the OPENING row's own real link
   * sits on (`link.spo_allocation_id`), never re-derived on the frontend by product or
   * source PO number. Ignored on a PO document.
   */
  spoLineId?: string | null;
  /**
   * R11 (owner rulings, 24 Sep 2026): the line a SUGGESTED link names
   * (`OrderInquirySuggestedLink.po_line_id`), opened from the Suggested cell - the same
   * highlight idiom as `poLineId`, and both can show at once (a row can hold a real
   * link on one line and a suggestion for another on the same document).
   */
  suggestedLineId?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * R31b (stock debt lane): the SPO line numbers a CALLER's own line drew from - marks
   * the matching row with a "Linked" badge and offers a "Go to linked line" jump. SPO
   * only (an SPO line's own number is what this names); the PO body ignores it.
   */
  highlightLines?: number[];
}) {
  // R16 (owner rulings, 25 Sep 2026): "1 button to quickly jump to the linked line" -
  // a PO with 178 lines left the linked one, on page 7, unreachable without paging by
  // hand. The linked/suggested line's own id, whichever this dialog was opened for; a
  // real link always wins the LABEL over a suggestion (the two callers never set both
  // at once in practice, but a row can hold both, R11).
  const linkedLineId = kind === 'po' ? (poLineId ?? null) : (spoLineId ?? null);
  // R31b (stock debt lane): `highlightLines` names a line by NUMBER, with no id of its
  // own for the header button to hold until the SPO data loads and `SpoBody` resolves
  // it against the document's own lines (its `highlightLineRowId`). Should fix 4
  // (review round 3): a placeholder used to unblock the button the moment the caller
  // named a line at all, whether or not any row on the document actually carried it -
  // an enabled Go to that scrolls to nothing reads as broken. `SpoBody` reports what
  // it resolved (`null` while loading or on no match) through `onHighlightResolved`
  // below, so the button stays disabled until a real row backs it.
  const hasHighlightLines = kind === 'spo' && Boolean(highlightLines && highlightLines.length);
  const [resolvedHighlightLineId, setResolvedHighlightLineId] = React.useState<string | null>(null);
  const highlightedLineId =
    linkedLineId ?? suggestedLineId ?? (hasHighlightLines ? resolvedHighlightLineId : null);
  // "Go to suggested line" only when the dialog was opened from the Suggested cell -
  // a real link, or nothing highlighted at all, both read the default label.
  const goToLabel = !linkedLineId && suggestedLineId ? 'Go to suggested line' : 'Go to linked line';
  // Incremented on every press - PoBody/SpoBody re-run their scroll-into-view effect
  // off this, even when the page itself does not change (the reader presses it again
  // after scrolling away by hand).
  const [goToNonce, setGoToNonce] = React.useState(0);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl" data-testid={`document-detail-${document}`}>
        <DialogHeader className="flex-row items-start justify-between gap-3 space-y-0">
          <div>
            <DialogTitle className="tabular-nums">{document}</DialogTitle>
            <DialogDescription>
              {kind === 'po' ? 'Purchase order' : 'Shipping order'}
            </DialogDescription>
          </div>
          <GoToLinkedLineButton
            label={goToLabel}
            disabled={!highlightedLineId}
            onClick={() => setGoToNonce((current) => current + 1)}
          />
        </DialogHeader>
        <DialogBody className="max-h-[70vh] overflow-y-auto">
          {kind === 'po' ? (
            <PoBody
              poId={poId ?? null}
              poLineId={poLineId ?? null}
              suggestedLineId={suggestedLineId ?? null}
              goToLineId={highlightedLineId}
              goToNonce={goToNonce}
              open={open}
            />
          ) : (
            <SpoBody
              spoNumber={document}
              spoLineId={spoLineId ?? null}
              suggestedLineId={suggestedLineId ?? null}
              goToLineId={highlightedLineId}
              goToNonce={goToNonce}
              open={open}
              highlightLines={highlightLines}
              onHighlightResolved={setResolvedHighlightLineId}
            />
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

/**
 * R16: one button, both lightboxes, same label rule and same disabled state - reused
 * rather than written twice so the two screens can never drift about wording. Wrapped
 * in a `Tooltip` only while disabled (the data-grid-list-toolbar idiom): a `<span
 * tabIndex={0}>` around the disabled `<button>` so the reason is still reachable by
 * keyboard and screen reader, since a real `disabled` button swallows focus and hover.
 */
function GoToLinkedLineButton({
  label,
  disabled,
  onClick,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  const button = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      data-testid="document-detail-go-to-line"
      disabled={disabled}
      onClick={onClick}
      className="shrink-0"
    >
      {label}
    </Button>
  );
  if (!disabled) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0}>{button}</span>
      </TooltipTrigger>
      <TooltipContent>No linked or suggested line to go to</TooltipContent>
    </Tooltip>
  );
}

/**
 * R16: jumps `PanelDataGrid` to the page holding `lineId` (its own `focusRowId`, so no
 * second pagination state is built) and scrolls that row into view. `nonce` re-fires
 * the scroll on every press, even when the page does not change - `PanelDataGrid`'s own
 * jump is keyed off `focusRowId` changing, which a same-value re-press would not do.
 *
 * The row to scroll to is found by the SAME `data-linked-line`/`data-suggested-line`
 * attribute the highlight already sets (`rowAttributes` below), inside the caller's own
 * container ref - never a fresh id lookup, so this can never disagree with what is
 * actually highlighted. Retried across a few animation frames because `PanelDataGrid`'s
 * own page jump is a SECOND state update (its internal `useEffect` runs after this
 * component's), so the target row is not always in the DOM yet on the frame this fires.
 */
function useGoToHighlightedLine(containerRef: React.RefObject<HTMLElement | null>, nonce: number) {
  React.useEffect(() => {
    if (!nonce) return;
    let cancelled = false;
    let attempts = 0;
    const tryScroll = () => {
      if (cancelled) return;
      const el = containerRef.current?.querySelector(
        '[data-linked-line="true"], [data-suggested-line="true"]',
      );
      if (el) {
        el.scrollIntoView({ block: 'center' });
        return;
      }
      attempts += 1;
      if (attempts < 8) requestAnimationFrame(tryScroll);
    };
    requestAnimationFrame(tryScroll);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);
}

/**
 * The document number as it sits in the grid: a button that opens the lightbox, for
 * BOTH kinds. The dialog is mounted only once it has been asked for, so a page of forty
 * rows does not carry forty dialogs.
 */
export function OrderInquiryDocumentLink({
  kind,
  document,
  poId,
  poLineId,
  spoLineId,
  suggestedLineId,
  highlightLines,
}: {
  kind: 'po' | 'spo';
  document: string;
  poId?: string | null;
  poLineId?: string | null;
  /** R15: the `spo_allocations` row this REAL link sits on. Ignored for kind `po`. */
  spoLineId?: string | null;
  suggestedLineId?: string | null;
  highlightLines?: number[];
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <button
        type="button"
        data-testid={`document-detail-trigger-${document}`}
        className="block max-w-full truncate rounded-sm font-medium tabular-nums text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title={document}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
      >
        {document}
      </button>
      {open ? (
        <OrderInquiryDocumentDialog
          kind={kind}
          document={document}
          poId={poId}
          poLineId={poLineId}
          spoLineId={spoLineId}
          suggestedLineId={suggestedLineId}
          open
          onOpenChange={setOpen}
          highlightLines={highlightLines}
        />
      ) : null}
    </>
  );
}

function LoadingBody() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-sm font-medium">{children}</dd>
    </div>
  );
}

function NotStated() {
  return <span className="font-normal text-muted-foreground">Not stated</span>;
}

function SkuCellContent({ sku, name }: { sku?: string | null; name?: string | null }) {
  return (
    <div className="min-w-0">
      <span className="block truncate font-medium" title={sku ?? ''}>
        {sku || <span className="text-muted-foreground">Unresolved</span>}
      </span>
      {name && !sameDisplayText(name, sku) ? (
        <span className="block truncate text-muted-foreground" title={name}>
          {name}
        </span>
      ) : null}
    </div>
  );
}

function LocationCellContent({ location }: { location?: string | null }) {
  return (
    <span className="block truncate" title={location ?? undefined}>
      {location || <span className="text-muted-foreground">no location</span>}
    </span>
  );
}

/**
 * The columns behind slice B (`PLAN-scm-oi-reserving-feedback-8sep.md`): the CRUD
 * standard's grid, never a bare `<table>` - a PO with 89 lines was unreadable in one.
 * Module-level, not `useMemo`'d in the body: nothing here closes over a prop.
 */
const PO_LINE_COLUMNS: ColumnDef<OrderInquiryPoDetailLine>[] = [
  {
    id: 'sku',
    accessorFn: (line) => line.sku ?? '',
    header: ({ column }) => <DataGridColumnHeader title="SKU" column={column} />,
    cell: ({ row }) => (
      <SkuCellContent sku={row.original.sku} name={row.original.product_name} />
    ),
    size: 220,
    meta: { headerTitle: 'SKU' },
  },
  {
    accessorKey: 'qty_ordered',
    header: ({ column }) => (
      <DataGridColumnHeader title="Ordered" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end tabular-nums">
        {formatInquiryQty(row.original.qty_ordered)}
      </span>
    ),
    size: 100,
    meta: { headerTitle: 'Ordered' },
  },
  {
    accessorKey: 'qty_received',
    header: ({ column }) => (
      <DataGridColumnHeader title="Received" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end tabular-nums">
        {formatInquiryQty(row.original.qty_received)}
      </span>
    ),
    size: 100,
    meta: { headerTitle: 'Received' },
  },
  {
    accessorKey: 'remaining',
    header: ({ column }) => (
      <DataGridColumnHeader title="Remaining" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end font-medium tabular-nums">
        {formatInquiryQty(row.original.remaining)}
      </span>
    ),
    size: 110,
    meta: { headerTitle: 'Remaining' },
  },
  {
    // Issue #1215 point 2: every order inquiry row's own placement on this line,
    // summed - the panel named only the DOCUMENT before, and a PO with two lines of
    // the same item could not say which one held the quantity.
    id: 'allocated',
    accessorFn: (line) => line.allocated ?? '0',
    header: ({ column }) => (
      <DataGridColumnHeader title="Allocated" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end tabular-nums">
        {formatInquiryQty(row.original.allocated ?? '0')}
      </span>
    ),
    size: 100,
    meta: { headerTitle: 'Allocated' },
  },
  {
    id: 'location',
    accessorFn: (line) => line.location ?? '',
    header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
    cell: ({ row }) => <LocationCellContent location={row.original.location} />,
    size: 140,
    meta: { headerTitle: 'Location' },
  },
  {
    id: 'book_so',
    // The AutoCount book's own linkage (`PLAN-scm-book-linkage-on-document-lines.md`),
    // read off the line's own `from_so_line_ref`. The SAME fact and the SAME component
    // `PurchaseOrderDetail.tsx`'s own S/O column uses, so one fact has one presentation
    // on both screens.
    accessorFn: bookSoSortValue,
    header: ({ column }) => <DataGridColumnHeader title="S/O" column={column} />,
    cell: ({ row }) => <BookSoCell line={row.original} />,
    size: 150,
    meta: { headerTitle: 'S/O' },
  },
];

/**
 * R31b: a FACTORY, not a module constant like `PO_LINE_COLUMNS` beside it - the SKU
 * cell's own "Linked" badge depends on `highlightLines`, a prop, so the columns are
 * rebuilt (memoized in `SpoBody`) whenever the caller's own set of linked lines changes.
 */
function buildSpoLineColumns(
  linkedLineNumbers: Set<number>,
): ColumnDef<OrderInquirySpoDetailLine>[] {
  return [
    {
      id: 'sku',
      accessorFn: (line) => line.sku ?? '',
      header: ({ column }) => <DataGridColumnHeader title="SKU" column={column} />,
      cell: ({ row }) => (
        <div className="flex min-w-0 items-center gap-1.5">
          <SkuCellContent sku={row.original.sku} name={row.original.product_name} />
          {row.original.spo_line_number != null &&
            linkedLineNumbers.has(row.original.spo_line_number) && (
              <Badge size="sm" variant="secondary" appearance="light" className="shrink-0">
                Linked
              </Badge>
            )}
        </div>
      ),
      size: 220,
      meta: { headerTitle: 'SKU' },
    },
    {
      accessorKey: 'allocated',
    header: ({ column }) => (
      <DataGridColumnHeader title="Allocated" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end tabular-nums">
        {formatInquiryQty(row.original.allocated)}
      </span>
    ),
    size: 100,
    meta: { headerTitle: 'Allocated' },
  },
  {
    accessorKey: 'received',
    header: ({ column }) => (
      <DataGridColumnHeader title="Received" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end tabular-nums">
        {formatInquiryQty(row.original.received)}
      </span>
    ),
    size: 100,
    meta: { headerTitle: 'Received' },
  },
  {
    accessorKey: 'remaining',
    header: ({ column }) => (
      <DataGridColumnHeader title="Remaining" column={column} className="justify-end" />
    ),
    cell: ({ row }) => (
      <span className="block text-end font-medium tabular-nums">
        {formatInquiryQty(row.original.remaining)}
      </span>
    ),
    size: 110,
    meta: { headerTitle: 'Remaining' },
  },
  {
    id: 'location',
    accessorFn: (line) => line.location ?? '',
    header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
    cell: ({ row }) => <LocationCellContent location={row.original.location} />,
    size: 140,
    meta: { headerTitle: 'Location' },
  },
  {
    // Owner's 9 Sep feedback: "if we link by SPO, where do we see the PO number of
    // this SPO?" - the document lightbox is the other surface a buyer meets an SPO
    // on, so it gets the same fact the backing-documents dialog does. Plain text,
    // never a link yet - a later slice decides where it goes.
    id: 'source_po_number',
    accessorFn: (line) => line.source_po_number ?? '',
    header: ({ column }) => <DataGridColumnHeader title="Source PO" column={column} />,
    cell: ({ row }) => (
      <span className="block truncate" title={row.original.source_po_number ?? undefined}>
        {row.original.source_po_number || <span className="text-muted-foreground">-</span>}
      </span>
    ),
    size: 140,
    meta: { headerTitle: 'Source PO' },
  },
  ];
}

/** Both SKU and location, one input (AC-B2/AC-B3) - case-insensitive substring, client-side. */
function searchOfLine(line: { sku?: string | null; product_name?: string | null; location?: string | null }) {
  return `${line.sku ?? ''} ${line.product_name ?? ''} ${line.location ?? ''}`;
}

function PoBody({
  poId,
  poLineId,
  suggestedLineId,
  goToLineId,
  goToNonce = 0,
  open,
}: {
  poId: string | null;
  poLineId?: string | null;
  suggestedLineId?: string | null;
  /** R16: the id `PanelDataGrid.focusRowId` jumps its page to on a Go to press. */
  goToLineId?: string | null;
  goToNonce?: number;
  open: boolean;
}) {
  const { data, isLoading, isError, error } = useOrderInquiryPoDetail(poId ?? undefined, {
    enabled: open && Boolean(poId),
  });
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  useGoToHighlightedLine(containerRef, goToNonce);

  if (!poId) {
    return (
      <p className="text-sm text-muted-foreground">
        This link does not reach a purchase order in the system.
      </p>
    );
  }
  if (isLoading) return <LoadingBody />;
  if (isError || !data) {
    return (
      <p className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Could not load this purchase order.'}
      </p>
    );
  }

  // The NAME, not the code: the code is one unbroken token that truncates into nothing a
  // person can read. The code rides on the title.
  const supplier = data.supplier_name || data.supplier_code || null;
  const supplierTitle =
    [data.supplier_name, data.supplier_code].filter(Boolean).join(' - ') || undefined;

  return (
    <div className="space-y-5" ref={containerRef}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <dl className="grid min-w-0 flex-1 grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
          <Field label="Supplier">
            <span title={supplierTitle}>{supplier ?? <NotStated />}</span>
          </Field>
          <Field label="Status">
            <span
              className={`inline-flex items-center rounded px-1.5 py-0.5 text-2xs font-medium capitalize ${statusPillClass(data.status)}`}
            >
              {data.status}
            </span>
          </Field>
          <Field label="Expected">
            {data.expected_date ? formatDateInMalaysia(data.expected_date) : <NotStated />}
          </Field>
        </dl>
        {/* AC-B5: a NEW tab, so the lightbox and the list behind it are both still there
            on return - the whole reason this stayed a lightbox rather than a full page. */}
        <Link
          href={`/scm/purchase-orders/${data.id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          Open document
          <ExternalLink className="size-3.5" aria-hidden />
        </Link>
      </div>

      <PanelDataGrid<OrderInquiryPoDetailLine>
        title="Lines"
        columns={PO_LINE_COLUMNS}
        rows={data.lines}
        getRowId={(line) => line.id ?? ''}
        listingKey="projects.projects.view::order-inquiry-po-lines"
        emptyTitle="This purchase order carries no lines."
        searchOf={searchOfLine}
        searchPlaceholder="Search product or location..."
        pageSize={10}
        // The DialogBody already owns the scroll viewport (overflow-y-auto).
        scrollerMaxHeight={false}
        // R16: reuses the grid's own pagination state - no second grid, no new
        // primitive. Only set once the reader has actually pressed Go to (`goToNonce`),
        // so opening the dialog never jumps the page on its own.
        focusRowId={goToNonce ? (goToLineId ?? null) : null}
        // Should fix 1 (review round 2): `goToLineId` never changes between
        // presses - it is the one highlighted line for this dialog's whole life -
        // so without the request key a second Go to press after paging away found
        // `PanelDataGrid`'s own guard already satisfied and did nothing.
        focusRequestKey={goToNonce}
        // Issue #1215 point 2: highlight the line the OPENING row's own REAL link sits
        // on - a PO with two lines of the same item is exactly the case the plain Item
        // column could not tell apart. R13 (owner rulings, 24 Sep 2026): the lines grid
        // is the WHOLE answer now - no panel underneath states it a second way. R11:
        // a SUGGESTED line (`suggestedLineId`) gets the SAME highlight idiom, and both
        // can be set at once - a row can hold a real link on one line and a suggestion
        // for another on the same document.
        rowClassName={(line) =>
          (poLineId && line.id === poLineId) || (suggestedLineId && line.id === suggestedLineId)
            ? 'bg-primary/10'
            : undefined
        }
        rowAttributes={(line) => ({
          ...(poLineId && line.id === poLineId ? { 'data-linked-line': 'true' } : {}),
          ...(suggestedLineId && line.id === suggestedLineId
            ? { 'data-suggested-line': 'true' }
            : {}),
        })}
      />
    </div>
  );
}

/** `getRowId`'s own id for one line - the row's own `id` when the book states one (the
 *  usual case, and what `spoLineId`/`suggestedLineId` themselves are), else a stand-in
 *  keyed by `spo_line_number` or by SKU/location - an R31b caller (stock debt) may know
 *  only the line NUMBER, never the row's id. The SAME id `focusRowId` below names, so a
 *  jump and a row can never disagree about which one they mean. Single parameter,
 *  matching `PanelDataGrid`'s own `getRowId` signature exactly. */
function spoLineRowId(line: OrderInquirySpoDetailLine): string {
  if (line.id) return line.id;
  return line.spo_line_number != null
    ? `line-${line.spo_line_number}`
    : `row-${line.sku ?? ''}-${line.location ?? ''}`;
}

function SpoBody({
  spoNumber,
  spoLineId,
  suggestedLineId,
  goToLineId,
  goToNonce = 0,
  open,
  highlightLines,
  onHighlightResolved,
}: {
  spoNumber: string;
  /**
   * R15 (owner rulings, 25 Sep 2026, hand test on stack C): the `spo_allocations` row
   * the OPENING row's own real link sits on - the exact mirror of the PO lightbox's
   * `poLineId`, resolved server-side (`link.spo_allocation_id`), never by matching
   * product or source PO number here.
   */
  spoLineId?: string | null;
  suggestedLineId?: string | null;
  /** R16: the id `PanelDataGrid.focusRowId` jumps its page to on a Go to press. */
  goToLineId?: string | null;
  goToNonce?: number;
  open: boolean;
  /**
   * R31b (stock debt lane): the SPO line numbers a CALLER's own line drew from - marks
   * the matching row with a "Linked" badge and rides the SAME go-to/highlight path a
   * real `spoLineId` does (`highlightLineRowId` below), rather than a second mechanism.
   * Ignored on the PO body.
   */
  highlightLines?: number[];
  /**
   * Should fix 4 (review round 3): reports what `highlightLineRowId` below resolved
   * to - `null` while the document is still loading, or once it is in and no line on
   * it carries any of `highlightLines` - so the header's Go to button (which mounts
   * BEFORE this body's own data does) can stay disabled until a real row backs it.
   */
  onHighlightResolved?: (rowId: string | null) => void;
}) {
  const { data, isLoading, isError } = useOrderInquirySpoDetail(spoNumber, { enabled: open });
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  useGoToHighlightedLine(containerRef, goToNonce);

  const linkedLineNumbers = React.useMemo(() => new Set(highlightLines ?? []), [highlightLines]);
  const columns = React.useMemo(() => buildSpoLineColumns(linkedLineNumbers), [linkedLineNumbers]);
  // R31b: resolve `highlightLines` (a NUMBER) to the matching row's own id, once the
  // data is in, so it can ride the exact highlight/go-to path a real `spoLineId` does.
  const highlightLineRowId = React.useMemo(() => {
    if (!linkedLineNumbers.size) return null;
    const match = (data?.lines ?? []).find(
      (line) => line.spo_line_number != null && linkedLineNumbers.has(line.spo_line_number),
    );
    return match ? spoLineRowId(match) : null;
  }, [data, linkedLineNumbers]);
  React.useEffect(() => {
    onHighlightResolved?.(highlightLineRowId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightLineRowId]);
  const effectiveSpoLineId = spoLineId ?? highlightLineRowId;
  const effectiveGoToLineId = spoLineId ?? suggestedLineId ?? highlightLineRowId ?? goToLineId ?? null;

  if (isLoading) return <LoadingBody />;
  if (isError || !data) {
    // A number no allocation carries answers 404, which is the honest reading of a
    // document this system does not hold - a shipping order the book has never named.
    return (
      <p className="text-sm text-muted-foreground">
        This shipping order could not be found.
      </p>
    );
  }

  return (
    <div className="space-y-5" ref={containerRef}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Field label="Supplier">{data.supplier_name || <NotStated />}</Field>
        <Field label="ETA">
          {data.eta ? formatDateInMalaysia(data.eta) : <NotStated />}
        </Field>
        <Field label="Shipment">{data.shipment_ref || <NotStated />}</Field>
        <Field label="Container">{data.container_no || <NotStated />}</Field>
      </dl>

      <PanelDataGrid<OrderInquirySpoDetailLine>
        title="Lines"
        columns={columns}
        rows={data.lines}
        getRowId={spoLineRowId}
        listingKey="projects.projects.view::order-inquiry-spo-lines"
        emptyTitle="This shipping order carries no lines."
        searchOf={searchOfLine}
        searchPlaceholder="Search product or location..."
        pageSize={10}
        scrollerMaxHeight={false}
        // R16: same idiom as the PO lightbox - the grid's own pagination state, only
        // engaged once the reader presses Go to.
        focusRowId={goToNonce ? effectiveGoToLineId : null}
        // Should fix 1 (review round 2): same reasoning as the PO lightbox above -
        // a second press must jump again even though `goToLineId` never changes.
        focusRequestKey={goToNonce}
        // R15/R31b: the SPO lightbox highlights its own linked/suggested/counted line
        // exactly the way the PO lightbox already does - `spoLineId` is the OI row's
        // real link's own `spo_allocation_id`; `highlightLineRowId` is the same idiom
        // resolved from a caller's `highlightLines` line number (stock debt), never a
        // re-match by product or source PO number.
        rowClassName={(line) =>
          (effectiveSpoLineId && spoLineRowId(line) === effectiveSpoLineId) ||
          (suggestedLineId && line.id === suggestedLineId)
            ? 'bg-primary/10'
            : undefined
        }
        rowAttributes={(line) => ({
          ...(effectiveSpoLineId && spoLineRowId(line) === effectiveSpoLineId
            ? { 'data-linked-line': 'true' }
            : {}),
          ...(suggestedLineId && line.id === suggestedLineId
            ? { 'data-suggested-line': 'true' }
            : {}),
        })}
      />
    </div>
  );
}

/**
 * R17 (owner rulings, 25 Sep 2026, hand test on stack C): a "via SPO" PO number -
 * `source_po_number` on an spo-kind link, never a real po-kind link of its own - opens
 * the PO lightbox for that source PO rather than reading dead text (review round 1's
 * should-fix 4 fix, reversed by this ruling). `purchaseOrderId` is resolved on the
 * SERVER now (review round 2 Should fix 3, `links_for_rows`, one batched
 * `PurchaseOrder.po_number IN (...)` for the whole page) - by `SPOAllocation.po_line_id`
 * traced to its own header first, then by `from_po_number` itself when there is no such
 * FK, which is the ordinary book-fed case. The client no longer scans the worklist for
 * it (`useOrderInquiryPoIdByNumber`, retired - it could never answer for a PO reached
 * ONLY through an SPO, since that PO holds no `po`-kind link of its own on any row).
 * Shared by the worklist's backing-documents dialog and the OI detail Lines tab, so the
 * two screens can never disagree about how this number opens.
 */
export function ViaSpoPoNumber({
  poNumber,
  purchaseOrderId,
}: {
  poNumber: string;
  purchaseOrderId?: string | null;
}) {
  return <OrderInquiryDocumentLink kind="po" document={poNumber} poId={purchaseOrderId ?? null} />;
}

export default OrderInquiryDocumentDialog;
